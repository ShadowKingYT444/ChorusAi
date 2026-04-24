from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from orchestrator.models import (
    CreateJobRequest,
    JobPublicView,
    JobRecord,
    JobSpec,
    JobStatus,
    OperatorView,
    PeerEntry,
    PeerStatus,
    RegisterAgentsRequest,
    SlotRuntime,
)

if TYPE_CHECKING:
    from orchestrator.db import ChorusDB


def _schedule(coro) -> None:
    """Fire-and-forget: schedule coro on running loop, silently drop if none."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        loop.create_task(coro)
    except Exception:  # noqa: BLE001
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass


class PeerRegistry:
    """In-memory live peer table for discovery/signaling."""

    def __init__(self) -> None:
        self._peers: dict[str, PeerEntry] = {}
        self._known_peers: dict[str, PeerEntry] = {}
        self._lock = asyncio.Lock()
        self._db: "ChorusDB | None" = None

    def attach_db(self, db: "ChorusDB") -> None:
        self._db = db

    def _mirror_peer(self, entry: PeerEntry) -> None:
        if self._db is None:
            return
        _schedule(self._db.mirror_peer(entry.model_copy(deep=True)))

    def _mirror_remove(self, peer_id: str) -> None:
        if self._db is None:
            return
        _schedule(self._db.remove_peer(peer_id))

    async def register(
        self,
        peer_id: str,
        model: str,
        *,
        supported_models: list[str] | None = None,
        address: str | None = None,
        protocol_version: str = "1",
        status: PeerStatus = PeerStatus.idle,
        pubkey: str | None = None,
        verified: bool = False,
    ) -> PeerEntry:
        async with self._lock:
            now = time.time()
            entry = PeerEntry(
                peer_id=peer_id,
                address=address,
                model=model,
                supported_models=list(supported_models or []),
                protocol_version=protocol_version,
                joined_at=now,
                last_seen=now,
                status=status,
                pubkey=pubkey,
                verified=verified,
            )
            self._peers[peer_id] = entry
            self._known_peers[peer_id] = entry.model_copy(deep=True)
            self._mirror_peer(entry)
            return entry

    async def unregister(self, peer_id: str) -> None:
        async with self._lock:
            existing = self._peers.pop(peer_id, None)
            if existing is not None:
                existing.last_seen = time.time()
                self._known_peers[peer_id] = existing.model_copy(deep=True)
        self._mirror_remove(peer_id)

    async def set_status(self, peer_id: str, status: PeerStatus) -> PeerEntry | None:
        async with self._lock:
            entry = self._peers.get(peer_id)
            if entry is None:
                return None
            entry.status = status
            entry.last_seen = time.time()
            self._known_peers[peer_id] = entry.model_copy(deep=True)
            self._mirror_peer(entry)
            return entry

    async def set_address(self, peer_id: str, address: str | None) -> PeerEntry | None:
        async with self._lock:
            entry = self._peers.get(peer_id)
            if entry is None:
                return None
            entry.address = address.strip() if address and address.strip() else None
            entry.last_seen = time.time()
            self._known_peers[peer_id] = entry.model_copy(deep=True)
            self._mirror_peer(entry)
            return entry

    async def touch(self, peer_id: str) -> PeerEntry | None:
        async with self._lock:
            entry = self._peers.get(peer_id)
            if entry is None:
                return None
            entry.last_seen = time.time()
            self._known_peers[peer_id] = entry.model_copy(deep=True)
            self._mirror_peer(entry)
            return entry

    async def list_peers(self) -> list[PeerEntry]:
        async with self._lock:
            return [entry.model_copy(deep=True) for entry in self._peers.values()]

    async def count(self) -> int:
        async with self._lock:
            return len(self._peers)

    async def get_peer_ids(self) -> list[str]:
        async with self._lock:
            return list(self._peers.keys())

    async def list_known_peers(self, max_age_s: float = 300.0) -> list[PeerEntry]:
        cutoff = time.time() - max_age_s
        async with self._lock:
            return [
                e.model_copy(deep=True)
                for e in self._known_peers.values()
                if e.last_seen >= cutoff
            ]

    async def merge_known_peers(self, peers: list[PeerEntry]) -> None:
        merged: list[PeerEntry] = []
        async with self._lock:
            for p in peers:
                existing = self._known_peers.get(p.peer_id)
                if existing is None or p.last_seen > existing.last_seen:
                    copy = p.model_copy(deep=True)
                    self._known_peers[p.peer_id] = copy
                    merged.append(copy)
        for entry in merged:
            self._mirror_peer(entry)


class JobStore:
    """In-memory job state plus lightweight event fan-out for local runs."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()
        self._event_log: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._db: "ChorusDB | None" = None
        self._seq: dict[str, int] = {}
        self._workspace_usage: dict[str, dict[str, int]] = defaultdict(
            lambda: {"jobs_created": 0, "shadow_credits_reserved": 0}
        )
        self._terminal_seen: set[str] = set()
        self._terminal_callbacks: list[Any] = []

    def on_terminal(self, callback: Any) -> None:
        self._terminal_callbacks.append(callback)

    def attach_db(self, db: "ChorusDB") -> None:
        self._db = db

    def _mirror_job(self, record: JobRecord) -> None:
        if self._db is None:
            return
        _schedule(self._db.mirror_job(record.model_copy(deep=True)))

    async def create_job(
        self,
        req: CreateJobRequest | JobSpec,
        *,
        workspace_id: str = "local-dev",
        job_id: str | None = None,
    ) -> JobRecord:
        spec = req if isinstance(req, JobSpec) else JobSpec.model_validate(req.model_dump())
        shadow_credit_cost = int(spec.agent_count * spec.rounds)
        resolved_job_id = (job_id or "").strip() or str(uuid.uuid4())
        job = JobRecord(
            job_id=resolved_job_id,
            workspace_id=workspace_id,
            spec=spec,
            shadow_credit_cost=shadow_credit_cost,
        )
        async with self._lock:
            if resolved_job_id in self._jobs:
                raise ValueError("job already exists")
            self._jobs[job.job_id] = job
            usage = self._workspace_usage[workspace_id]
            usage["jobs_created"] += 1
            usage["shadow_credits_reserved"] += shadow_credit_cost
        self._mirror_job(job)
        return job

    async def get_job(self, job_id: str, workspace_id: str | None = None) -> JobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if workspace_id is not None and job.workspace_id != workspace_id:
                return None
            return job

    async def update_job(self, job: JobRecord) -> None:
        async with self._lock:
            self._jobs[job.job_id] = job
            is_terminal = job.status in {JobStatus.completed, JobStatus.failed}
            first_terminal = is_terminal and job.job_id not in self._terminal_seen
            if first_terminal:
                self._terminal_seen.add(job.job_id)
            callbacks = list(self._terminal_callbacks) if first_terminal else []
        self._mirror_job(job)
        for cb in callbacks:
            try:
                cb(job.job_id)
            except Exception:  # noqa: BLE001
                pass

    async def register_agents(
        self,
        job_id: str,
        req: RegisterAgentsRequest,
        *,
        workspace_id: str | None = None,
        routing_mode: str | None = None,
    ) -> JobRecord | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if workspace_id is not None and job.workspace_id != workspace_id:
                return None
            if len(req.slots) != job.spec.agent_count:
                raise ValueError(
                    f"expected {job.spec.agent_count} slots, received {len(req.slots)}"
                )
            job.slots = {
                slot_id: SlotRuntime(slot_id=slot_id, registration=registration)
                for slot_id, registration in req.slots.items()
            }
            job.routing_mode = routing_mode or req.routing_mode or job.routing_mode
            job.error = None
            self._jobs[job_id] = job
        self._mirror_job(job)
        return job

    async def public_view(self, job_id: str, workspace_id: str | None = None) -> JobPublicView | None:
        job = await self.get_job(job_id, workspace_id=workspace_id)
        if job is None:
            return None
        return JobPublicView(
            job_id=job.job_id,
            workspace_id=job.workspace_id,
            status=job.status,
            current_round=job.current_round,
            error=job.error,
            shadow_credit_cost=job.shadow_credit_cost,
            routing_mode=job.routing_mode,
            settlement_preview=job.settlement_preview,
            final_answer=job.final_answer,
            citations=job.citations,
            attachment_ids=list(job.spec.attachment_ids),
            completion_model=job.spec.completion_model,
        )

    async def operator_view(self, job_id: str, workspace_id: str | None = None) -> OperatorView | None:
        job = await self.get_job(job_id, workspace_id=workspace_id)
        if job is None:
            return None
        return OperatorView(
            job_id=job.job_id,
            workspace_id=job.workspace_id,
            status=job.status,
            current_round=job.current_round,
            error=job.error,
            shadow_credit_cost=job.shadow_credit_cost,
            routing_mode=job.routing_mode,
            rounds=job.rounds_data,
            settlement_preview=job.settlement_preview,
            final_answer=job.final_answer,
            citations=job.citations,
            attachment_ids=list(job.spec.attachment_ids),
            completion_model=job.spec.completion_model,
        )

    async def workspace_summary(self, workspace_id: str) -> dict[str, int]:
        async with self._lock:
            usage = dict(self._workspace_usage.get(workspace_id, {}))
        if not usage:
            return {"jobs_created": 0, "shadow_credits_reserved": 0}
        return {
            "jobs_created": int(usage.get("jobs_created", 0)),
            "shadow_credits_reserved": int(usage.get("shadow_credits_reserved", 0)),
        }

    async def emit(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = {"type": event_type, **payload}
        ts = time.time()
        async with self._lock:
            self._event_log[job_id].append(event)
            self._seq[job_id] = self._seq.get(job_id, 0) + 1
            seq = self._seq[job_id]
            subscribers = list(self._subscribers.get(job_id, set()))
        if self._db is not None:
            _schedule(self._db.append_event(job_id, seq, ts, event_type, dict(payload)))
        for queue in subscribers:
            await queue.put(event)

    async def subscribe(self, job_id: str) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            self._subscribers[job_id].add(queue)
            history = list(self._event_log.get(job_id, []))
        return queue, history

    async def unsubscribe(self, job_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(job_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(job_id, None)

    async def record_share(
        self,
        *,
        job_id: str,
        peer_id: str,
        wallet: str | None,
        round_index: int,
        tokens_in: int,
        tokens_out: int,
        wall_ms: int,
        cost_uc: int,
        signed_receipt: str | None = None,
    ) -> None:
        """Record a per-peer, per-round compute share (shadow mode)."""
        if self._db is None:
            return
        _schedule(
            self._db.insert_share(
                job_id=job_id,
                peer_id=peer_id,
                wallet=wallet,
                round_index=round_index,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                wall_ms=wall_ms,
                cost_uc=cost_uc,
                signed_receipt=signed_receipt,
            )
        )

    async def list_shares(self, job_id: str) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        return await self._db.fetch_shares(job_id)

    async def create_payment(self, job_id: str, quoted_amount_uc: int) -> None:
        if self._db is None:
            return
        await self._db.insert_payment(job_id, quoted_amount_uc, status="quoted")

    async def mark_payment_funded(
        self, job_id: str, *, payer_wallet: str, tx_deposit: str
    ) -> None:
        if self._db is None:
            return
        await self._db.mark_payment_funded(
            job_id, payer_wallet=payer_wallet, tx_deposit=tx_deposit
        )

    async def get_payment(self, job_id: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        return await self._db.fetch_payment(job_id)

    async def settle_payment(self, job_id: str) -> dict[str, Any]:
        """Aggregate shares for `job_id`, persist settlement, return the split."""
        from orchestrator.billing import ComputeShare, split_payout

        shares_raw = await self.list_shares(job_id)
        shares = [
            ComputeShare(
                peer_id=str(r["peer_id"]),
                wallet_address=r.get("wallet_address"),
                cost_uc=int(r.get("compute_cost_uc") or 0),
            )
            for r in shares_raw
        ]
        split = split_payout(shares)
        if self._db is not None:
            split["settled"] = await self._db.finalize_payment(
                job_id,
                final_amount_uc=int(split["subtotal_uc"]),
                platform_fee_uc=int(split["platform_fee_uc"]),
            )
        else:
            split["settled"] = False
        return split
