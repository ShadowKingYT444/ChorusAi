from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from orchestrator.auth import require_workspace_http, require_workspace_websocket
from orchestrator.broadcast_completions import post_chat_completion
from orchestrator.engine import RoundEngine
from orchestrator.lifespan import lifespan
from orchestrator.logconfig import RequestIdMiddleware, configure_logging
from orchestrator.metrics import METRICS
from orchestrator.ratelimit import check_rate_limit
from orchestrator.models import (
    BroadcastAssignment,
    BroadcastEnvelopeMessage,
    BroadcastInvokeRequest,
    BroadcastPlanRequest,
    BroadcastPlanResponse,
    ClusterEdge,
    ClusterEntry,
    ClusterStats,
    ClustersResponse,
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
    SlotRegistration,
)
from orchestrator.crypto import verify_b64
from orchestrator.identity import get_orchestrator_keypair
from orchestrator.store import JobStore, PeerRegistry

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Chorus Signaling", version="0.2.0", lifespan=lifespan)
orchestrator_keypair = get_orchestrator_keypair()
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
    local_origin_regex = r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$"
    # Match browser Origin for Next.js (or any port) on typical private LAN ranges.
    lan_origin_regex = (
        r"^https?://("
        r"192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r")(:[0-9]+)?$"
    )
    # Default to allowing any *.vercel.app preview/production domain so a
    # frontend deploy works without requiring ORC_CORS_ORIGIN_REGEX.
    vercel_origin_regex = r"^https://([a-z0-9-]+\.)*vercel\.app$"
    extra = os.getenv("ORC_CORS_ORIGIN_REGEX", "").strip()
    regex_parts = [local_origin_regex, vercel_origin_regex]
    if lan:
        regex_parts.append(lan_origin_regex)
    if extra:
        regex_parts.append(extra)
    regex = "|".join(f"(?:{part})" for part in regex_parts if part)

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
# RequestIdMiddleware must be added AFTER CORSMiddleware so it runs first for outgoing responses.
app.add_middleware(RequestIdMiddleware)


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

    # One assignment per peer (cycling through personas if peers > catalog).
    # Old behaviour iterated personas not peers, which caused duplicate work
    # on the same peer or left peers idle.
    for i, peer_id in enumerate(target_peer_ids):
        persona = persona_catalog[i % len(persona_catalog)] if persona_catalog else ""
        assignments.append(
            BroadcastAssignment(
                peer_id=peer_id,
                persona_index=i,
                persona=persona,
            )
        )

    logger.info("Created plan %s with %d assignments across %d peers", job_id, len(assignments), len(target_peer_ids))
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
    pubkey: str | None = None,
    verified: bool = False,
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
        pubkey=pubkey,
        verified=verified,
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


def _parse_csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _is_supported_auto_route(address: str) -> bool:
    stripped = address.strip()
    return stripped.startswith("demo://") or stripped.startswith("http://") or stripped.startswith("https://")


def _anchor_registrations() -> list[tuple[str, SlotRegistration]]:
    urls = _parse_csv_env("ORC_ANCHOR_COMPLETION_BASE_URLS")
    bearer_tokens = _parse_csv_env("ORC_ANCHOR_BEARER_TOKENS")
    out: list[tuple[str, SlotRegistration]] = []
    for index, url in enumerate(urls, start=1):
        if not _is_supported_auto_route(url):
            continue
        bearer_token: str | None = None
        if len(bearer_tokens) == 1:
            bearer_token = bearer_tokens[0]
        elif index - 1 < len(bearer_tokens):
            bearer_token = bearer_tokens[index - 1]
        out.append(
            (
                f"anchor-{index}",
                SlotRegistration(
                    completion_base_url=url,
                    bearer_token=bearer_token,
                    external_participant_id=f"anchor-{index}",
                ),
            )
        )
    return out


async def _resolve_auto_slots(
    *,
    agent_count: int,
    target_peer_ids: list[str] | None,
) -> dict[str, SlotRegistration]:
    resolved: dict[str, SlotRegistration] = {}
    for slot_id, registration in _anchor_registrations():
        if len(resolved) >= agent_count:
            break
        resolved[slot_id] = registration

    peers = await registry.list_peers()
    allow = set(target_peer_ids) if target_peer_ids else None
    candidates = [
        peer
        for peer in peers
        if peer.address
        and _is_supported_auto_route(str(peer.address))
        and (allow is None or peer.peer_id in allow)
    ]
    candidates.sort(key=lambda peer: (not peer.verified, peer.status.value != "idle", -peer.last_seen, peer.peer_id))
    for peer in candidates:
        if len(resolved) >= agent_count:
            break
        slot_id = peer.peer_id if peer.peer_id not in resolved else f"peer-{peer.peer_id}"
        resolved[slot_id] = SlotRegistration(
            completion_base_url=str(peer.address).strip(),
            external_participant_id=peer.peer_id,
        )

    if len(resolved) != agent_count:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "insufficient_auto_routes",
                "requested_agents": agent_count,
                "resolved_agents": len(resolved),
            },
        )
    return resolved


async def _close_job_ws(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except TypeError:
        await websocket.close(code=code)


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


@app.get("/identity")
async def identity() -> dict[str, str]:
    """Return the orchestrator's Ed25519 public key (base64)."""
    return {"pubkey": orchestrator_keypair.pubkey_b64()}


@app.get("/ready")
async def ready() -> JSONResponse:
    db = getattr(app.state, "db", None)
    if db is None:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    ok = await db.ready_check()
    if not ok:
        return JSONResponse({"status": "fail"}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.get("/chats")
async def get_chats(request: Request, limit: int = 20, offset: int = 0) -> dict:
    principal = require_workspace_http(request)
    db = getattr(app.state, "db", None)
    if db is None:
        return {"chats": []}
    return {"chats": await db.list_chats(limit=limit, offset=offset, workspace_id=principal.workspace_id)}


@app.get("/chats/{job_id}")
async def get_chat(job_id: str, request: Request) -> dict:
    principal = require_workspace_http(request)
    db = getattr(app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    chat = await db.get_chat(job_id, workspace_id=principal.workspace_id)
    if not chat:
        raise HTTPException(status_code=404, detail="not_found")
    return chat


async def _refresh_live_gauges() -> None:
    """Pull current live counts into METRICS.gauges at request time."""
    METRICS.gauge("active_peers", float(await registry.count()))
    jobs_running = 0
    jobs_in_flight = 0
    async with job_store._lock:  # type: ignore[attr-defined]
        for job in job_store._jobs.values():  # type: ignore[attr-defined]
            if job.status.value == "running":
                jobs_running += 1
            if job.status.value in ("running", "pending"):
                jobs_in_flight += 1
    METRICS.gauge("jobs_running", float(jobs_running))
    METRICS.gauge("jobs_in_flight", float(jobs_in_flight))


@app.get("/metrics", response_class=PlainTextResponse)
async def get_metrics_prom() -> PlainTextResponse:
    await _refresh_live_gauges()
    return PlainTextResponse(METRICS.render_prom(), media_type="text/plain; version=0.0.4")


@app.get("/metrics.json")
async def get_metrics_json() -> dict:
    await _refresh_live_gauges()
    return METRICS.snapshot()


def _require_operator_token(header_value: str | None) -> None:
    expected = os.getenv("ORC_OPERATOR_TOKEN", "").strip()
    if expected and header_value != expected:
        raise HTTPException(status_code=403, detail="invalid operator token")


@app.post("/jobs", response_model=CreateJobResponse)
async def create_job(req: CreateJobRequest, request: Request) -> CreateJobResponse:
    principal = require_workspace_http(request)
    client_ip = request.client.host if request.client else "unknown"
    retry_after = await check_rate_limit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )
    job = await job_store.create_job(req, workspace_id=principal.workspace_id)
    METRICS.inc("shadow_credits_reserved_total", float(job.shadow_credit_cost))
    return CreateJobResponse(
        job_id=job.job_id,
        status=job.status,
        workspace_id=job.workspace_id,
        shadow_credit_cost=job.shadow_credit_cost,
    )


@app.post("/jobs/{job_id}/agents", response_model=RegisterAgentsResponse)
async def register_job_agents(job_id: str, req: RegisterAgentsRequest, request: Request) -> RegisterAgentsResponse:
    principal = require_workspace_http(request)
    job = await job_store.get_job(job_id, workspace_id=principal.workspace_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    if req.routing_mode == "manual" and not req.slots:
        raise HTTPException(status_code=400, detail="manual routing requires slots")
    if req.routing_mode == "auto" and req.slots:
        raise HTTPException(status_code=400, detail="auto routing must not include manual slots")

    if req.slots:
        resolved_req = req
        routing_mode = "manual"
    else:
        routing_mode = "auto"
        resolved_req = RegisterAgentsRequest(
            slots=await _resolve_auto_slots(
                agent_count=job.spec.agent_count,
                target_peer_ids=req.target_peer_ids,
            ),
            routing_mode="auto",
            target_peer_ids=req.target_peer_ids,
        )
    try:
        job = await job_store.register_agents(
            job_id,
            resolved_req,
            workspace_id=principal.workspace_id,
            routing_mode=routing_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    started = round_engine.start_job(job)
    if not started:
        await round_engine._run_job(job.job_id)
    return RegisterAgentsResponse(ok=True, registered_slots=sorted(resolved_req.slots.keys()))


@app.get("/jobs/{job_id}", response_model=JobPublicView)
async def get_job(job_id: str, request: Request) -> JobPublicView:
    principal = require_workspace_http(request)
    view = await job_store.public_view(job_id, workspace_id=principal.workspace_id)
    if view is None:
        raise HTTPException(status_code=404, detail="job not found")
    return view


@app.get("/jobs/{job_id}/operator", response_model=OperatorView)
async def get_job_operator(
    job_id: str,
    request: Request,
    x_operator_token: str | None = Header(default=None),
) -> OperatorView:
    principal = require_workspace_http(request)
    _require_operator_token(x_operator_token)
    view = await job_store.operator_view(job_id, workspace_id=principal.workspace_id)
    if view is None:
        raise HTTPException(status_code=404, detail="job not found")
    return view


@app.get("/jobs/{job_id}/responses")
async def get_job_responses(job_id: str, request: Request) -> dict:
    """Return all buffered job_response payloads for a job (for reconnect recovery)."""
    principal = require_workspace_http(request)
    job = await job_store.get_job(job_id, workspace_id=principal.workspace_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, "responses": _job_response_buffer.get(job_id, [])}


@app.get("/jobs/{job_id}/response-summary")
async def get_job_response_summary(job_id: str, request: Request) -> dict:
    """Count buffered `job_response` rows - use to verify multiple workers per peer (`instance_id`)."""
    principal = require_workspace_http(request)
    job = await job_store.get_job(job_id, workspace_id=principal.workspace_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
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
    try:
        principal = require_workspace_websocket(websocket)
    except HTTPException as exc:
        code = 4401 if exc.status_code == 401 else 4403
        await _close_job_ws(websocket, code, str(exc.detail))
        return

    job = await job_store.get_job(job_id, workspace_id=principal.workspace_id)
    if job is None:
        await _close_job_ws(websocket, 4404, "job_not_found")
        return

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


@app.get("/clusters", response_model=ClustersResponse)
async def list_clusters() -> ClustersResponse:
    """Return cluster + edge data derived from the live peer registry and job-response buffer."""
    peers = await registry.list_peers()
    known_peer_ids: set[str] = {p.peer_id for p in peers}

    # Group peers by model.
    by_model: dict[str, list[str]] = {}
    for p in peers:
        by_model.setdefault(p.model, []).append(p.peer_id)

    clusters: list[ClusterEntry] = []
    for model, pids in by_model.items():
        if not pids:
            continue
        sorted_pids = sorted(pids)
        clusters.append(
            ClusterEntry(
                id=f"model:{model}",
                label=model,
                kind="model",
                peer_ids=sorted_pids,
                size=len(sorted_pids),
            )
        )
    clusters.sort(key=lambda c: (-c.size, c.label))

    # Co-job edges: count jobs where each pair of peers co-appeared.
    pair_counts: dict[tuple[str, str], int] = {}
    for job_id, responses in _job_response_buffer.items():
        unique_peers = {
            str(row.get("peer_id"))
            for row in responses
            if row.get("peer_id") is not None
        }
        # Keep only peers still known to the registry.
        unique_peers &= known_peer_ids
        sorted_peers = sorted(unique_peers)
        for i in range(len(sorted_peers)):
            for j in range(i + 1, len(sorted_peers)):
                a, b = sorted_peers[i], sorted_peers[j]
                pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1

    edges: list[ClusterEdge] = [
        ClusterEdge(source=a, target=b, weight=float(count), kind="co_job")
        for (a, b), count in pair_counts.items()
    ]
    edges.sort(key=lambda e: (e.source, e.target))

    stats = ClusterStats(
        total_peers=len(peers),
        total_clusters=len(clusters),
        total_edges=len(edges),
        total_jobs_observed=len(_job_response_buffer),
    )
    return ClustersResponse(clusters=clusters, edges=edges, stats=stats)



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
    dropped = 0
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
        if len(buf) >= _JOB_BUFFER_MAX:
            buf.pop(0)
            buf.append(payload)
            dropped += 1
        else:
            buf.append(payload)
    if dropped:
        await job_store.emit(
            req.job_id,
            "buffer_dropped",
            {"payload": {"dropped": dropped, "buffer_size": _JOB_BUFFER_MAX}},
        )
        logger.warning(
            "broadcast_invoke buffer overflow for job=%s dropped=%d", req.job_id, dropped,
        )
    return {"job_id": req.job_id, "invoked": len(results), "results": results}


@app.websocket("/ws/signaling")
async def ws_signaling(websocket: WebSocket) -> None:
    await websocket.accept()
    async with _conn_lock:
        _active_websockets.add(websocket)
    
    # Get client IP for logging
    client_host = websocket.client.host if websocket.client else "unknown"
    logger.info("New connection from %s. Total active: %d", client_host, len(_active_websockets))

    current_peer_id: str | None = None
    try:
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")

            if msg_type == "register":
                payload = RegisterMessage.model_validate(message)
                current_peer_id = payload.peer_id
                # Soft-mode signature verification: unsigned agents still register.
                verified = False
                if payload.pubkey and payload.signed_ts and payload.ts is not None:
                    verified = verify_b64(
                        payload.pubkey,
                        f"{payload.peer_id}:{payload.ts}",
                        payload.signed_ts,
                    )
                logger.info(
                    "Registering %s (%s) from %s verified=%s",
                    current_peer_id, payload.model, client_host, verified,
                )
                entry = await _register_peer(
                    websocket,
                    peer_id=payload.peer_id,
                    model=payload.model,
                    address=payload.address,
                    protocol_version=payload.protocol_version,
                    pubkey=payload.pubkey,
                    verified=verified,
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
                logger.debug("Broadcasting job_ack for %s to %d active sockets", payload.job_id, len(target_ws_list))
                for target_ws in target_ws_list:
                    await _safe_send_json(target_ws, payload_dict)
                continue

            if msg_type == "job_response":
                payload = JobResponseMessage.model_validate(message)
                payload_dict = payload.model_dump()
                # Buffer so prompter can fetch missed responses after reconnecting.
                buf = _job_response_buffer.setdefault(payload.job_id, [])
                if len(buf) >= _JOB_BUFFER_MAX:
                    # FIFO trim, then append; emit observability event.
                    buf.pop(0)
                    buf.append(payload_dict)
                    await job_store.emit(
                        payload.job_id,
                        "buffer_dropped",
                        {"payload": {"dropped": 1, "buffer_size": _JOB_BUFFER_MAX}},
                    )
                    logger.warning(
                        "job_response buffer overflow for job=%s (max=%d)",
                        payload.job_id, _JOB_BUFFER_MAX,
                    )
                else:
                    buf.append(payload_dict)
                async with _conn_lock:
                    target_ws_list = list(_active_websockets)
                logger.debug("Broadcasting job_response from %s to %d active sockets", payload.peer_id, len(target_ws_list))
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
        logger.info("Disconnect from %s", client_host)
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
