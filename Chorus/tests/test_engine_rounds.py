from __future__ import annotations

import pytest

from orchestrator.engine import RoundEngine
from orchestrator.models import CompletionResult, CreateJobRequest, RegisterAgentsRequest, SlotRegistration
from orchestrator.store import JobStore


class _FakeInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def invoke(
        self,
        *,
        job,
        slot_id: str,
        registration,
        round_index: int,
        persona: str,
        context_text: str,
    ) -> CompletionResult:
        self.calls.append((slot_id, round_index))
        return CompletionResult(
            ok=True,
            text=f"{slot_id} round {round_index} answer with enough content",
            finish_reason="stop",
            latency_ms=1,
        )


class _FakeEmbedder:
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(index + 1), 1.0] for index, _ in enumerate(texts)]

    @staticmethod
    def cosine_distance(left: list[float], right: list[float]) -> float:
        return abs(left[0] - right[0])


@pytest.mark.anyio
async def test_run_job_executes_all_rounds_before_merge() -> None:
    store = JobStore()
    engine = RoundEngine(store)
    fake_invoker = _FakeInvoker()
    engine.invoker = fake_invoker
    engine.embedder = _FakeEmbedder()

    merge_rounds: list[list[int]] = []

    async def fake_merge(job) -> None:
        merge_rounds.append([audit.round for audit in job.rounds_data])
        job.final_answer = "merged after all rounds"
        job.citations = ["slot-a"]

    engine._merge_answer = fake_merge  # type: ignore[method-assign]

    job = await store.create_job(
        CreateJobRequest(
            context="ctx",
            prompt="prompt",
            agent_count=2,
            rounds=2,
            payout=0.0,
        )
    )
    await store.register_agents(
        job.job_id,
        RegisterAgentsRequest(
            slots={
                "slot-a": SlotRegistration(completion_base_url="demo://slot-a"),
                "slot-b": SlotRegistration(completion_base_url="demo://slot-b"),
            }
        ),
    )

    await engine._run_job(job.job_id)

    final_job = await store.get_job(job.job_id)
    assert final_job is not None
    assert [audit.round for audit in final_job.rounds_data] == [1, 2]
    assert sorted(slot_id for slot_id, round_index in fake_invoker.calls if round_index == 1) == [
        "slot-a",
        "slot-b",
    ]
    assert sorted(slot_id for slot_id, round_index in fake_invoker.calls if round_index == 2) == [
        "slot-a",
        "slot-b",
    ]
    assert merge_rounds == [[1, 2]]

@pytest.mark.anyio
async def test_demo_fallback_merge_uses_real_slot_citations() -> None:
    store = JobStore()
    engine = RoundEngine(store)
    fake_invoker = _FakeInvoker()
    engine.invoker = fake_invoker
    engine.embedder = _FakeEmbedder()

    job = await store.create_job(
        CreateJobRequest(
            context="ctx",
            prompt=(
                "In a live midnight auth outage, should we trust a chorus of local coding agents "
                "to choose between rollback, feature-flag disable, or hotfix, and what exact "
                "operating model keeps that from becoming a disaster?"
            ),
            agent_count=2,
            rounds=2,
            payout=0.0,
        )
    )
    await store.register_agents(
        job.job_id,
        RegisterAgentsRequest(
            slots={
                "atlas-skeptic": SlotRegistration(completion_base_url="demo://atlas-skeptic"),
                "quasar-engineer": SlotRegistration(completion_base_url="demo://quasar-engineer"),
            }
        ),
    )

    await engine._run_job(job.job_id)

    final_job = await store.get_job(job.job_id)
    assert final_job is not None
    assert final_job.final_answer is not None
    assert "[atlas-skeptic]" in final_job.final_answer
    assert "[quasar-engineer]" in final_job.final_answer
    assert set(final_job.citations) >= {"atlas-skeptic", "quasar-engineer"}
