from __future__ import annotations

import asyncio
from collections import Counter

import anyio

from orchestrator.embeddings import EmbeddingService
from orchestrator.invoker import AgentInvoker
from orchestrator.models import JobRecord, JobStatus, PruneStatus, RoundAudit, SlotRoundAudit
from orchestrator.payout import compute_settlement
from orchestrator.store import JobStore
from orchestrator.watchdog import Watchdog


class RoundEngine:
    def __init__(self, store: JobStore) -> None:
        self.store = store
        self.embedder = EmbeddingService()
        self.invoker = AgentInvoker()
        self.watchdog = Watchdog()
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start_job(self, job: JobRecord) -> bool:
        if job.job_id in self._tasks and not self._tasks[job.job_id].done():
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._tasks[job.job_id] = loop.create_task(self._run_job(job.job_id))
        return True

    async def _run_job(self, job_id: str) -> None:
        job = await self.store.get_job(job_id)
        if job is None:
            return
        job.status = JobStatus.running
        await self.store.update_job(job)

        try:
            for round_index in range(1, job.spec.rounds + 1):
                job.current_round = round_index
                await self.store.emit(
                    job_id,
                    "round_started",
                    {"round": round_index},
                )
                round_audit = await self._run_round(job, round_index)
                job.rounds_data.append(round_audit)
                await self.store.update_job(job)

            job.status = JobStatus.completed
            job.settlement_preview = compute_settlement(job)
            await self.store.update_job(job)
            await self.store.emit(
                job_id,
                "job_done",
                {"round": job.spec.rounds, "payload": {"settlement_preview": job.settlement_preview}},
            )
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.failed
            job.error = str(exc)
            await self.store.update_job(job)
            await self.store.emit(job_id, "job_failed", {"payload": {"error": str(exc)}})

    async def _run_round(self, job: JobRecord, round_index: int) -> RoundAudit:
        active_slots = {
            slot_id: slot
            for slot_id, slot in job.slots.items()
            if slot.status != PruneStatus.pruned
        }
        if not active_slots:
            return RoundAudit(round=round_index, slots={})

        personas = {
            slot_id: self._build_persona(slot_id, round_index, job.job_id)
            for slot_id in active_slots
        }

        contexts = {
            slot_id: self._build_context(job, slot_id, round_index)
            for slot_id in active_slots
        }

        call_tasks = []
        slot_ids = list(active_slots.keys())
        for slot_id in slot_ids:
            slot = active_slots[slot_id]
            call_tasks.append(
                self.invoker.invoke(
                    job=job,
                    slot_id=slot_id,
                    registration=slot.registration,
                    round_index=round_index,
                    persona=personas[slot_id],
                    context_text=contexts[slot_id],
                )
            )

        responses = [await task for task in call_tasks]
        texts = [resp.text.strip() for resp in responses if resp.text]
        dup_counts = Counter(texts)

        prompt_plain = job.spec.prompt.strip()
        watchdog_batch = [prompt_plain]
        for _slot_id, response in zip(slot_ids, responses):
            if response.text and not response.error:
                watchdog_batch.append(response.text.strip())
            else:
                watchdog_batch.append("")
        watchdog_vecs = await anyio.to_thread.run_sync(self.embedder.embed_batch, watchdog_batch)
        prompt_emb = watchdog_vecs[0]
        response_embs = dict(zip(slot_ids, watchdog_vecs[1:], strict=True))

        slots_audit: dict[str, SlotRoundAudit] = {}
        embeddings: dict[str, list[float]] = {}

        for slot_id, response in zip(slot_ids, responses):
            slot_runtime = job.slots[slot_id]
            is_dup = bool(response.text and dup_counts[response.text.strip()] > 1)
            status, bad_streak, notes = self.watchdog.evaluate(
                text=response.text,
                error=response.error,
                duplicate_in_round=is_dup,
                current_bad_streak=slot_runtime.bad_streak,
                prompt_embedding=prompt_emb,
                response_embedding=response_embs[slot_id],
            )
            slot_runtime.bad_streak = bad_streak
            slot_runtime.status = status

            slots_audit[slot_id] = SlotRoundAudit(
                slot_id=slot_id,
                persona=personas[slot_id],
                completion=response.text,
                finish_reason=response.finish_reason,
                embedding_id=None,
                embedding=None,
                prune_status=status,
                watchdog_notes=notes,
                impact_c=slot_runtime.c_impact,
                impact_f=slot_runtime.f_impact,
                error=response.error,
            )

            await self.store.emit(
                job.job_id,
                "agent_line",
                {
                    "round": round_index,
                    "slot_id": slot_id,
                    "payload": {
                        "status": status.value,
                        "latency_ms": response.latency_ms,
                        "snippet": (response.text or "")[:140],
                    },
                },
            )

        # Reuse per-slot response vectors from the watchdog batch (same model as kNN).
        for slot_id, response in zip(slot_ids, responses):
            audit = slots_audit[slot_id]
            if response.text and audit.prune_status != PruneStatus.pruned:
                vec = response_embs[slot_id]
                embeddings[slot_id] = vec
                sid = slots_audit[slot_id]
                sid.embedding = vec
                sid.embedding_id = f"emb:{round_index}:{slot_id}"

        nearest_edges, furthest_edges = self._compute_edges_and_impact(job, embeddings)
        for src, dst in nearest_edges:
            await self.store.emit(
                job.job_id,
                "edge",
                {"round": round_index, "slot_id": src, "payload": {"from": src, "to": dst, "kind": "nearest"}},
            )
        for src, dst in furthest_edges:
            await self.store.emit(
                job.job_id,
                "edge",
                {"round": round_index, "slot_id": src, "payload": {"from": src, "to": dst, "kind": "furthest"}},
            )

        for slot_id in slot_ids:
            slots_audit[slot_id].impact_c = job.slots[slot_id].c_impact
            slots_audit[slot_id].impact_f = job.slots[slot_id].f_impact

        return RoundAudit(
            round=round_index,
            slots=slots_audit,
            nearest_edges=nearest_edges,
            furthest_edges=furthest_edges,
        )

    def _build_persona(self, slot_id: str, round_index: int, job_salt: str) -> str:
        seed = abs(hash(f"{slot_id}:{round_index}:{job_salt}")) % 3
        catalog = [
            "You are analytical. Use explicit assumptions and concise logic.",
            "You are creative. Explore alternatives and produce practical options.",
            "You are skeptical. Stress-test claims and identify edge cases.",
        ]
        return catalog[seed]

    def _build_context(self, job: JobRecord, slot_id: str, round_index: int) -> str:
        slot = job.slots[slot_id]
        if round_index == 1 or not job.rounds_data:
            return job.spec.context
        prev_round = job.rounds_data[-1]
        nearest = None
        furthest = None
        for src, dst in prev_round.nearest_edges:
            if src == slot_id:
                nearest = prev_round.slots.get(dst)
                break
        for src, dst in prev_round.furthest_edges:
            if src == slot_id:
                furthest = prev_round.slots.get(dst)
                break
        context_parts = [job.spec.context]
        if nearest and nearest.completion:
            context_parts.append(f"A peer said: {nearest.completion[:200]}")
        if furthest and furthest.completion:
            context_parts.append(f"A dissenting peer said: {furthest.completion[:200]}")
        built = "\n\n".join(context_parts)
        slot.last_context = built
        return built

    def _compute_edges_and_impact(
        self, job: JobRecord, vectors: dict[str, list[float]]
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        slot_ids = list(vectors.keys())
        if len(slot_ids) < 2:
            return [], []

        nearest_edges: list[tuple[str, str]] = []
        furthest_edges: list[tuple[str, str]] = []
        for target in slot_ids:
            nearest_id = None
            furthest_id = None
            nearest_dist = float("inf")
            furthest_dist = -1.0
            for candidate in slot_ids:
                if candidate == target:
                    continue
                dist = self.embedder.cosine_distance(vectors[candidate], vectors[target])
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_id = candidate
                if dist > furthest_dist:
                    furthest_dist = dist
                    furthest_id = candidate
            if nearest_id is not None:
                nearest_edges.append((target, nearest_id))
                job.slots[nearest_id].c_impact += 1.0
            if furthest_id is not None:
                furthest_edges.append((target, furthest_id))
                job.slots[furthest_id].f_impact += 1.0
        return nearest_edges, furthest_edges
