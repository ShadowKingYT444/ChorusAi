from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from orchestrator.broadcast_completions import post_chat_completion
from orchestrator.engine import RoundEngine
from orchestrator.models import (
    BroadcastAssignment,
    BroadcastEnvelopeMessage,
    BroadcastInvokeRequest,
    BroadcastPlanRequest,
    BroadcastPlanResponse,
    CreateJobRequest,
    CreateJobResponse,
    HeartbeatMessage,
    JobAckMessage,
    JobPublicView,
    JobRequestMessage,
    JobResponseMessage,
    JoinPeerRef,
    JoinRequestMessage,
    MeshConnectAcceptMessage,
    MeshConnectRequestMessage,
    OperatorView,
    PeerEntry,
    PeerGossipMessage,
    PeersResponse,
    RegisterMessage,
    RegisterAgentsRequest,
    RegisterAgentsResponse,
    RelayMessage,
    SetAddressMessage,
    SetStatusMessage,
)
from orchestrator.store import JobStore, PeerRegistry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("orchestrator")

app = FastAPI(title="Chorus Signaling", version="0.2.0")
registry = PeerRegistry()
job_store = JobStore()
round_engine = RoundEngine(job_store)
_conn_lock = asyncio.Lock()
_ws_by_peer_id: dict[str, WebSocket] = {}
_peer_id_by_ws_key: dict[int, str] = {}
_active_websockets: set[WebSocket] = set()
# Buffer job_response payloads so the prompter can fetch missed ones after reconnecting.
_job_response_buffer: dict[str, list[dict]] = {}
_JOB_BUFFER_MAX = 500  # max responses stored per job

DEFAULT_PERSONA_CATALOG = [
    "You are a skeptic. Challenge assumptions and list likely failure modes.",
    "You are an optimist. Find paths to success with concrete execution steps.",
    "You are an analyst. Quantify tradeoffs, costs, and probability where possible.",
    "You are a contrarian. Surface hidden second-order effects and unpopular risks.",
]

def _cors_middleware_kwargs() -> dict:
    raw = os.getenv("ORC_CORS_ORIGINS")
    if raw is None:
        origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    else:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        if not origins:
            origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

    lan = os.getenv("ORC_LAN_MODE", "1").strip().lower() in ("1", "true", "yes")
    # Match browser Origin for Next.js (or any port) on typical private LAN ranges.
    lan_origin_regex = (
        r"^https?://("
        r"192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r")(:[0-9]+)?$"
    )
    extra = os.getenv("ORC_CORS_ORIGIN_REGEX", "").strip()
    regex = None
    if lan:
        regex = lan_origin_regex if not extra else f"(?:{lan_origin_regex})|(?:{extra})"
    elif extra:
        regex = extra

    kw: dict = {
        "allow_origins": origins,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if regex:
        kw["allow_origin_regex"] = regex
    return kw


app.add_middleware(CORSMiddleware, **_cors_middleware_kwargs())


def _persona_index(peer_id: str, job_id: str, size: int) -> int:
    digest = hashlib.sha256(f"{peer_id}:{job_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


def _make_plan(
    *,
    target_peer_ids: list[str],
    timeout_ms: int,
    persona_catalog: list[str],
    job_id: str,
) -> BroadcastPlanResponse:
    assignments: list[BroadcastAssignment] = []
    if not target_peer_ids:
        return BroadcastPlanResponse(
            job_id=job_id,
            expected_peers=0,
            timeout_ms=timeout_ms,
            target_peer_ids=[],
            assignments=[],
        )

    # Distribute ALL personas in the catalog across available peers (Round-Robin)
    for i, persona in enumerate(persona_catalog):
        peer_id = target_peer_ids[i % len(target_peer_ids)]
        assignments.append(
            BroadcastAssignment(
                peer_id=peer_id,
                persona_index=i,
                persona=persona,
            )
        )

    print(f"[ORCHESTRATOR] Created plan {job_id} with {len(assignments)} assignments across {len(target_peer_ids)} peers")
    return BroadcastPlanResponse(
        job_id=job_id,
        expected_peers=len(target_peer_ids),
        timeout_ms=timeout_ms,
        target_peer_ids=target_peer_ids,
        assignments=assignments,
    )


async def _safe_send_json(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False


async def _broadcast_presence() -> None:
    peers = await registry.list_peers()
    payload = {
        "type": "peer_count",
        "count": len(peers),
        "peers": [p.model_dump() for p in peers],
    }
    async with _conn_lock:
        conns = list(_ws_by_peer_id.items())
    stale_ids: list[str] = []
    for peer_id, ws in conns:
        ok = await _safe_send_json(ws, payload)
        if not ok:
            stale_ids.append(peer_id)
    for peer_id in stale_ids:
        await _disconnect_peer(peer_id)


async def _disconnect_peer(peer_id: str) -> None:
    ws: WebSocket | None = None
    async with _conn_lock:
        ws = _ws_by_peer_id.pop(peer_id, None)
        if ws is not None:
            _peer_id_by_ws_key.pop(id(ws), None)
    await registry.unregister(peer_id)
    if ws is not None:
        try:
            await ws.close()
        except Exception:
            pass


async def _resolve_targets(
    requested: list[str] | None,
    *,
    exclude_peer_id: str | None = None,
) -> list[str]:
    online = await registry.get_peer_ids()
    selected = online if requested is None else [pid for pid in requested if pid in set(online)]
    if exclude_peer_id is not None:
        selected = [pid for pid in selected if pid != exclude_peer_id]
    return selected


async def _register_peer(
    websocket: WebSocket,
    *,
    peer_id: str,
    model: str,
    address: str | None = None,
    protocol_version: str = "1",
) -> PeerEntry:
    async with _conn_lock:
        prev = _ws_by_peer_id.get(peer_id)
        _ws_by_peer_id[peer_id] = websocket
        _peer_id_by_ws_key[id(websocket)] = peer_id
    if prev is not None and prev is not websocket:
        try:
            await prev.close()
        except Exception:
            pass
    entry: PeerEntry = await registry.register(
        peer_id,
        model,
        address=address,
        protocol_version=protocol_version,
    )
    return entry


async def _assigned_mesh_for(peer_id: str, max_targets: int = 5) -> list[JoinPeerRef]:
    peers = await registry.list_peers()
    candidates = [p for p in peers if p.peer_id != peer_id]
    candidates.sort(key=lambda p: (p.status.value != "idle", -p.last_seen))
    selected = candidates[:max_targets]
    return [
        JoinPeerRef(
            peer_id=p.peer_id,
            address=p.address,
            model=p.model,
            last_seen=p.last_seen,
        )
        for p in selected
    ]


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "distlm-signaling",
        "docs": "/docs",
        "health": "/health",
        "ws": "/ws/signaling",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _require_operator_token(header_value: str | None) -> None:
    expected = os.getenv("ORC_OPERATOR_TOKEN", "").strip()
    if expected and header_value != expected:
        raise HTTPException(status_code=403, detail="invalid operator token")


@app.post("/jobs", response_model=CreateJobResponse)
async def create_job(req: CreateJobRequest) -> CreateJobResponse:
    job = await job_store.create_job(req)
    return CreateJobResponse(job_id=job.job_id, status=job.status)


@app.post("/jobs/{job_id}/agents", response_model=RegisterAgentsResponse)
async def register_job_agents(job_id: str, req: RegisterAgentsRequest) -> RegisterAgentsResponse:
    try:
        job = await job_store.register_agents(job_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    started = round_engine.start_job(job)
    if not started:
        await round_engine._run_job(job.job_id)
    return RegisterAgentsResponse(ok=True, registered_slots=sorted(req.slots.keys()))


@app.get("/jobs/{job_id}", response_model=JobPublicView)
async def get_job(job_id: str) -> JobPublicView:
    view = await job_store.public_view(job_id)
    if view is None:
        raise HTTPException(status_code=404, detail="job not found")
    return view


@app.get("/jobs/{job_id}/operator", response_model=OperatorView)
async def get_job_operator(job_id: str, x_operator_token: str | None = Header(default=None)) -> OperatorView:
    _require_operator_token(x_operator_token)
    view = await job_store.operator_view(job_id)
    if view is None:
        raise HTTPException(status_code=404, detail="job not found")
    return view


@app.get("/jobs/{job_id}/responses")
async def get_job_responses(job_id: str) -> dict:
    """Return all buffered job_response payloads for a job (for reconnect recovery)."""
    return {"job_id": job_id, "responses": _job_response_buffer.get(job_id, [])}


@app.get("/jobs/{job_id}/response-summary")
async def get_job_response_summary(job_id: str) -> dict:
    """Count buffered `job_response` rows — use to verify multiple workers per peer (`instance_id`)."""
    buf = _job_response_buffer.get(job_id, [])
    by_peer: dict[str, int] = {}
    by_slot: dict[str, int] = {}
    for row in buf:
        pid = str(row.get("peer_id") or "")
        inst = row.get("instance_id")
        key = f"{pid}#{inst}" if inst else pid
        by_peer[pid] = by_peer.get(pid, 0) + 1
        by_slot[key] = by_slot.get(key, 0) + 1
    return {
        "job_id": job_id,
        "total": len(buf),
        "by_peer_id": by_peer,
        "by_peer_and_instance": by_slot,
    }


@app.websocket("/ws/jobs/{job_id}")
async def ws_job_events(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    queue, history = await job_store.subscribe(job_id)
    try:
        for item in history:
            await websocket.send_json(item)
        while True:
            item = await queue.get()
            await websocket.send_json(item)
    except WebSocketDisconnect:
        pass
    finally:
        await job_store.unsubscribe(job_id, queue)


@app.get("/peers", response_model=PeersResponse)
async def list_peers() -> PeersResponse:
    peers = await registry.list_peers()
    peers_sorted = sorted(peers, key=lambda p: p.peer_id)
    return PeersResponse(count=len(peers_sorted), peers=peers_sorted)



@app.post("/broadcast/plan", response_model=BroadcastPlanResponse)
async def create_broadcast_plan(req: BroadcastPlanRequest) -> BroadcastPlanResponse:
    persona_catalog = req.persona_catalog or DEFAULT_PERSONA_CATALOG
    if not persona_catalog:
        raise HTTPException(status_code=400, detail="persona_catalog must not be empty")
    target_peer_ids = await _resolve_targets(req.target_peer_ids)
    return _make_plan(
        target_peer_ids=target_peer_ids,
        timeout_ms=req.timeout_ms,
        persona_catalog=persona_catalog,
        job_id=str(uuid.uuid4()),
    )


@app.post("/broadcast/invoke_completions")
async def broadcast_invoke_completions(req: BroadcastInvokeRequest) -> dict[str, object]:
    """POST /v1/chat/completions on each online peer that registered a public `address`."""
    persona_catalog = req.persona_catalog or DEFAULT_PERSONA_CATALOG
    if not persona_catalog:
        raise HTTPException(status_code=400, detail="persona_catalog must not be empty")
    peers = await registry.list_peers()
    with_address = [p for p in peers if p.address and str(p.address).strip()]
    if req.target_peer_ids:
        allow = set(req.target_peer_ids)
        with_address = [p for p in with_address if p.peer_id in allow]
    timeout_s = max(1.0, req.timeout_ms / 1000.0)
    tasks = [
        post_chat_completion(
            completion_base_url=str(p.address).strip(),
            persona=persona_catalog[_persona_index(p.peer_id, req.job_id, len(persona_catalog))],
            user_prompt=req.prompt,
            job_id=req.job_id,
            peer_id=p.peer_id,
            timeout_s=timeout_s,
        )
        for p in with_address
    ]
    results: list[dict] = list(await asyncio.gather(*tasks)) if tasks else []
    buf = _job_response_buffer.setdefault(req.job_id, [])
    for res in results:
        payload = {
            "type": "job_response",
            "job_id": req.job_id,
            "peer_id": res["peer_id"],
            "prompter_id": "http-invoke",
            "text": res.get("text") if res.get("ok") else None,
            "model": None,
            "latency_ms": res.get("latency_ms"),
            "error": None if res.get("ok") else (res.get("error") or "invoke_failed"),
            "instance_id": res.get("instance_id"),
        }
        if len(buf) < _JOB_BUFFER_MAX:
            buf.append(payload)
    return {"job_id": req.job_id, "invoked": len(results), "results": results}


@app.websocket("/ws/signaling")
async def ws_signaling(websocket: WebSocket) -> None:
    await websocket.accept()
    async with _conn_lock:
        _active_websockets.add(websocket)
    
    # Get client IP for logging
    client_host = websocket.client.host if websocket.client else "unknown"
    print(f"[ORCHESTRATOR] New connection from {client_host}. Total active: {len(_active_websockets)}")

    current_peer_id: str | None = None
    try:
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "register":
                payload = RegisterMessage.model_validate(message)
                current_peer_id = payload.peer_id
                print(f"[ORCHESTRATOR] Registering {current_peer_id} ({payload.model}) from {client_host}")
                entry = await _register_peer(
                    websocket,
                    peer_id=payload.peer_id,
                    model=payload.model,
                    address=payload.address,
                    protocol_version=payload.protocol_version,
                )
                await registry.set_status(payload.peer_id, payload.status)
                peers_live = await registry.list_peers()
                await websocket.send_json(
                    {
                        "type": "registered",
                        "peer": entry.model_dump(),
                        "peer_count": len(peers_live),
                    }
                )
                await _broadcast_presence()
                continue

            if msg_type == "join_request":
                payload = JoinRequestMessage.model_validate(message)
                current_peer_id = payload.peer_id
                entry = await _register_peer(
                    websocket,
                    peer_id=payload.peer_id,
                    model=payload.model,
                    address=payload.address,
                    protocol_version=payload.protocol_version,
                )
                assigned_mesh = await _assigned_mesh_for(payload.peer_id, max_targets=5)
                known_snapshot = [
                    JoinPeerRef(
                        peer_id=p.peer_id,
                        address=p.address,
                        model=p.model,
                        last_seen=p.last_seen,
                    ).model_dump()
                    for p in await registry.list_known_peers()
                    if p.peer_id != payload.peer_id
                ]
                await websocket.send_json(
                    {
                        "type": "join_accept",
                        "peer": entry.model_dump(),
                        "assigned_mesh": [p.model_dump() for p in assigned_mesh],
                        "known_peers_snapshot": known_snapshot,
                    }
                )
                await _broadcast_presence()
                continue

            if current_peer_id is None:
                await websocket.send_json(
                    {"type": "error", "error": "register_required", "detail": "Send register first."}
                )
                continue

            if msg_type == "set_status":
                payload = SetStatusMessage.model_validate(message)
                updated = await registry.set_status(current_peer_id, payload.status)
                if updated is None:
                    await websocket.send_json({"type": "error", "error": "unknown_peer"})
                    continue
                await websocket.send_json({"type": "status_updated", "status": updated.status.value})
                await _broadcast_presence()
                continue

            if msg_type == "set_address":
                payload = SetAddressMessage.model_validate(message)
                if current_peer_id is None:
                    await websocket.send_json(
                        {"type": "error", "error": "register_required", "detail": "Send register first."}
                    )
                    continue
                updated = await registry.set_address(current_peer_id, payload.address)
                if updated is None:
                    await websocket.send_json({"type": "error", "error": "unknown_peer"})
                    continue
                await websocket.send_json(
                    {
                        "type": "address_updated",
                        "peer": updated.model_dump(),
                    }
                )
                await _broadcast_presence()
                continue

            if msg_type == "heartbeat":
                payload = HeartbeatMessage.model_validate(message)
                updated = await registry.set_status(current_peer_id, payload.status)
                if updated is None:
                    await websocket.send_json({"type": "error", "error": "unknown_peer"})
                    continue
                await websocket.send_json(
                    {
                        "type": "heartbeat_ack",
                        "peer_id": current_peer_id,
                        "status": updated.status.value,
                        "timestamp": payload.timestamp,
                    }
                )
                continue

            if msg_type == "peer_gossip":
                payload = PeerGossipMessage.model_validate(message)
                await registry.merge_known_peers(
                    [
                        PeerEntry(
                            peer_id=p.peer_id,
                            address=p.address,
                            model=p.model,
                            joined_at=p.last_seen,
                            last_seen=p.last_seen,
                        )
                        for p in payload.known_peers
                    ]
                )
                await registry.touch(current_peer_id)
                await websocket.send_json({"type": "peer_gossip_ack", "merged": len(payload.known_peers)})
                continue

            if msg_type == "mesh_connect_request":
                payload = MeshConnectRequestMessage.model_validate(message)
                async with _conn_lock:
                    target_ws = _ws_by_peer_id.get(payload.to_peer_id)
                if target_ws is None:
                    await websocket.send_json(
                        {
                            "type": "mesh_connect_ack",
                            "ok": False,
                            "to_peer_id": payload.to_peer_id,
                            "error": "peer_offline",
                        }
                    )
                    continue
                ok = await _safe_send_json(
                    target_ws,
                    {
                        "type": "mesh_connect_request",
                        "peer_id": payload.peer_id,
                        "address": payload.address,
                        "model": payload.model,
                    },
                )
                await websocket.send_json(
                    {
                        "type": "mesh_connect_ack",
                        "ok": ok,
                        "to_peer_id": payload.to_peer_id,
                        **({} if ok else {"error": "delivery_failed"}),
                    }
                )
                continue

            if msg_type == "mesh_connect_accept":
                payload = MeshConnectAcceptMessage.model_validate(message)
                async with _conn_lock:
                    target_ws = _ws_by_peer_id.get(payload.to_peer_id)
                if target_ws is not None:
                    await _safe_send_json(
                        target_ws,
                        {
                            "type": "mesh_connect_accept",
                            "peer_id": payload.peer_id,
                        },
                    )
                continue

            if msg_type == "relay":
                payload = RelayMessage.model_validate(message)
                async with _conn_lock:
                    target_ws = _ws_by_peer_id.get(payload.to_peer_id)
                if target_ws is None:
                    await websocket.send_json(
                        {
                            "type": "relay_ack",
                            "ok": False,
                            "to_peer_id": payload.to_peer_id,
                            "error": "peer_offline",
                        }
                    )
                    continue
                ok = await _safe_send_json(
                    target_ws,
                    {
                        "type": "relay",
                        "from_peer_id": current_peer_id,
                        "payload": payload.payload,
                    },
                )
                await websocket.send_json(
                    {
                        "type": "relay_ack",
                        "ok": ok,
                        "to_peer_id": payload.to_peer_id,
                        **({} if ok else {"error": "delivery_failed"}),
                    }
                )
                if not ok:
                    await _disconnect_peer(payload.to_peer_id)
                    await _broadcast_presence()
                continue

            if msg_type == "job_request":
                payload = JobRequestMessage.model_validate(message)
                delivered_peer_ids: list[str] = []
                async with _conn_lock:
                    target_map = {peer.peer_id: _ws_by_peer_id.get(peer.peer_id) for peer in payload.peers}
                for target in payload.peers:
                    target_ws = target_map.get(target.peer_id)
                    if target_ws is None:
                        continue
                    ok = await _safe_send_json(
                        target_ws,
                        {
                            "type": "job_request",
                            "job_id": payload.job_id,
                            "prompt": payload.prompt,
                            "timeout_ms": payload.timeout_ms,
                            "prompter_id": payload.prompter_id,
                            "your_persona": target.persona,
                        },
                    )
                    if ok:
                        delivered_peer_ids.append(target.peer_id)
                await websocket.send_json(
                    {
                        "type": "job_dispatch_ack",
                        "job_id": payload.job_id,
                        "expected_peers": len(payload.peers),
                        "delivered_peers": len(delivered_peer_ids),
                        "delivered_peer_ids": delivered_peer_ids,
                    }
                )
                continue

            if msg_type == "job_ack":
                payload = JobAckMessage.model_validate(message)
                payload_dict = payload.model_dump()
                async with _conn_lock:
                    target_ws_list = list(_active_websockets)
                print(f"[ORCHESTRATOR] Broadcasting job_ack for {payload.job_id} to {len(target_ws_list)} active sockets")
                for target_ws in target_ws_list:
                    await _safe_send_json(target_ws, payload_dict)
                continue

            if msg_type == "job_response":
                payload = JobResponseMessage.model_validate(message)
                payload_dict = payload.model_dump()
                # Buffer so prompter can fetch missed responses after reconnecting.
                buf = _job_response_buffer.setdefault(payload.job_id, [])
                if len(buf) < _JOB_BUFFER_MAX:
                    buf.append(payload_dict)
                async with _conn_lock:
                    target_ws_list = list(_active_websockets)
                print(f"[ORCHESTRATOR] Broadcasting job_response from {payload.peer_id} to {len(target_ws_list)} active sockets")
                for target_ws in target_ws_list:
                    await _safe_send_json(target_ws, payload_dict)
                continue

            if msg_type == "broadcast_job":
                payload = BroadcastEnvelopeMessage.model_validate(message)
                persona_catalog = payload.persona_catalog or DEFAULT_PERSONA_CATALOG
                if not persona_catalog:
                    await websocket.send_json(
                        {"type": "broadcast_started", "ok": False, "error": "empty_persona_catalog"}
                    )
                    continue
                job_id = payload.job_id or str(uuid.uuid4())
                targets = await _resolve_targets(payload.target_peer_ids, exclude_peer_id=current_peer_id)
                plan = _make_plan(
                    target_peer_ids=targets,
                    timeout_ms=payload.timeout_ms,
                    persona_catalog=persona_catalog,
                    job_id=job_id,
                )
                delivered_peer_ids: list[str] = []
                async with _conn_lock:
                    target_map = {pid: _ws_by_peer_id.get(pid) for pid in plan.target_peer_ids}
                for assignment in plan.assignments:
                    target_ws = target_map.get(assignment.peer_id)
                    if target_ws is None:
                        continue
                    ok = await _safe_send_json(
                        target_ws,
                        {
                            "type": "job_envelope",
                            "job_id": plan.job_id,
                            "prompt": payload.prompt,
                            "persona": assignment.persona,
                            "persona_index": assignment.persona_index,
                            "timeout_ms": plan.timeout_ms,
                            "from_peer_id": current_peer_id,
                        },
                    )
                    if ok:
                        delivered_peer_ids.append(assignment.peer_id)
                await websocket.send_json(
                    {
                        "type": "broadcast_started",
                        "ok": True,
                        "job_id": plan.job_id,
                        "expected_peers": plan.expected_peers,
                        "delivered_peers": len(delivered_peer_ids),
                        "delivered_peer_ids": delivered_peer_ids,
                        "timeout_ms": plan.timeout_ms,
                        "assignments": [a.model_dump() for a in plan.assignments],
                    }
                )
                continue

            await websocket.send_json({"type": "error", "error": "unknown_message_type"})

    except WebSocketDisconnect:
        print(f"[ORCHESTRATOR] Disconnect from {client_host}")
    finally:
        async with _conn_lock:
            _active_websockets.discard(websocket)
        if current_peer_id is None:
            peer = _peer_id_by_ws_key.pop(id(websocket), None)
            if peer:
                current_peer_id = peer
        if current_peer_id is not None:
            await _disconnect_peer(current_peer_id)
            await _broadcast_presence()
