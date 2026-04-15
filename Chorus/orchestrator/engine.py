from __future__ import annotations

import asyncio
import os
import re
import time
from collections import Counter

import anyio
import httpx

from orchestrator.broadcast_completions import normalize_completion_url
from orchestrator.embeddings import EmbeddingService
from orchestrator.invoker import AgentInvoker, OLLAMA_MODEL
from orchestrator.metrics import METRICS
from orchestrator.identity import get_orchestrator_keypair
from orchestrator.models import JobRecord, JobStatus, PruneStatus, RoundAudit, SlotRoundAudit
from orchestrator.payout import attach_receipt, compute_settlement
from orchestrator.store import JobStore
from orchestrator.watchdog import Watchdog

MERGE_SYSTEM_PROMPT = (
    "You are the Chorus moderator. Given peer answers from N agents, produce ONE final "
    "answer of at most 120 words that synthesizes the strongest points. Cite the slots "
    "you used with square-bracket tags like [slot-0]. Be direct, skip preamble."
)
_CITATION_RE = re.compile(r"\[(slot-[A-Za-z0-9_\-]+)\]")


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

        METRICS.inc("jobs_started_total")
        try:
            for round_index in range(1, job.spec.rounds + 1):
                job.current_round = round_index
                await self.store.emit(
                    job_id,
                    "round_started",
                    {"round": round_index},
                )
                round_t0 = time.perf_counter()
                round_audit = await self._run_round(job, round_index)
                round_latency_ms = (time.perf_counter() - round_t0) * 1000
                METRICS.observe("round_latency_ms", round_latency_ms)
                METRICS.inc("rounds_completed_total")
                for slot_audit in round_audit.slots.values():
                    METRICS.inc("rounds_slots_total")
                    if slot_audit.prune_status == PruneStatus.pruned:
                        METRICS.inc("rounds_slots_pruned")
                job.rounds_data.append(round_audit)
                await self.store.update_job(job)

            await self._merge_answer(job)

            METRICS.inc("jobs_completed_total")
            job.status = JobStatus.completed
            job.settlement_preview = compute_settlement(job)
            try:
                attach_receipt(
                    job.settlement_preview,
                    job.job_id,
                    get_orchestrator_keypair(),
                )
            except Exception:  # noqa: BLE001
                # Receipt attachment is best-effort; never block settlement.
                pass
            await self.store.update_job(job)
            await self.store.emit(
                job_id,
                "job_done",
                {
                    "round": job.spec.rounds,
                    "payload": {
                        "settlement_preview": job.settlement_preview,
                        "final_answer": job.final_answer,
                        "citations": job.citations,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            METRICS.inc("jobs_failed_total")
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

    async def _merge_answer(self, job: JobRecord) -> None:
        """Call a moderator completion to synthesize one final answer from last round.

        Best-effort: on any failure falls back to highest-c_impact slot completion.
        Emits `final_answer` event; never blocks settlement.
        """
        if not job.rounds_data:
            return

        last_round = job.rounds_data[-1]
        valid_slots: list[SlotRoundAudit] = [
            a for a in last_round.slots.values()
            if a.completion and a.prune_status != PruneStatus.pruned
        ]
        if not valid_slots:
            return

        answers_block = "\n\n".join(
            f"[{a.slot_id}] {(a.completion or '').strip()}" for a in valid_slots
        )
        merge_url_env = os.getenv("ORC_MERGE_URL", "").strip()
        target_url: str | None = None
        if merge_url_env:
            target_url = normalize_completion_url(merge_url_env)
        else:
            for slot_id, slot in job.slots.items():
                if slot.status != PruneStatus.pruned:
                    target_url = normalize_completion_url(str(slot.registration.completion_base_url))
                    break

        final_text: str | None = None
        if target_url is not None:
            payload = {
                "model": os.getenv("ORC_MERGE_MODEL", OLLAMA_MODEL),
                "messages": [
                    {"role": "system", "content": MERGE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"### Prompt\n{job.spec.prompt}\n\n"
                            f"### Peer answers\n{answers_block}\n\n"
                            "### Produce the final answer now."
                        ),
                    },
                ],
                "max_tokens": int(os.getenv("ORC_MERGE_MAX_TOKENS", "256")),
                "temperature": float(os.getenv("ORC_MERGE_TEMPERATURE", "0.3")),
                "user": f"merge:{job.job_id}",
            }
            headers = {"Content-Type": "application/json", "X-Chorus-Job-Id": job.job_id, "X-Chorus-Merge": "1"}
            timeout_s = float(os.getenv("ORC_MERGE_TIMEOUT_S", "30"))
            try:
                start = time.perf_counter()
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    resp = await client.post(target_url, headers=headers, json=payload)
                if resp.status_code // 100 == 2:
                    data = resp.json()
                    final_text = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content")
                    )
                _ = int((time.perf_counter() - start) * 1000)
            except Exception:  # noqa: BLE001
                final_text = None

        if not final_text:
            best = max(valid_slots, key=lambda a: a.impact_c)
            final_text = (best.completion or "").strip()

        final_text = (final_text or "").strip()
        cited = [slot_id for slot_id in _CITATION_RE.findall(final_text)]
        if not cited:
            cited = [a.slot_id for a in valid_slots[:3]]

        job.final_answer = final_text
        job.citations = cited
        await self.store.update_job(job)
        await self.store.emit(
            job.job_id,
            "final_answer",
            {"round": job.spec.rounds, "payload": {"text": final_text, "citations": cited}},
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
