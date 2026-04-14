"""EmbeddingService: hash backend (default under conftest) and geometry helpers."""

from __future__ import annotations

import os

import pytest

from orchestrator.embeddings import EmbeddingService


def test_cosine_distance_identical() -> None:
    v = [0.6, 0.8, 0.0]
    assert EmbeddingService.cosine_distance(v, v) < 1e-9


def test_hash_embed_batch_matches_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORC_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("ORC_HASH_EMBED_DIM", "32")
    svc = EmbeddingService()
    texts = ["hello swarm", "different text"]
    batch = svc.embed_batch(texts)
    assert len(batch) == 2
    assert len(batch[0]) == 32
    assert batch[0] == svc.embed(texts[0])
    assert batch[1] == svc.embed(texts[1])


def test_hash_embed_uses_full_dimension_without_repeating_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORC_EMBEDDING_BACKEND", "hash")
    monkeypatch.setenv("ORC_HASH_EMBED_DIM", "64")
    svc = EmbeddingService()
    vec = svc.embed("full width coverage")
    assert len(vec) == 64
    assert vec[:16] != vec[16:32]
    assert vec[16:32] != vec[32:48]


@pytest.mark.skipif(
    os.getenv("ORC_EMBEDDING_BACKEND", "").lower() != "minilm",
    reason="Set ORC_EMBEDDING_BACKEND=minilm to run MiniLM smoke test (downloads weights).",
)
def test_minilm_embed_small_batch() -> None:
    svc = EmbeddingService()
    out = svc.embed_batch(["hello", "world"])
    assert len(out) == 2
    assert len(out[0]) == 384
    assert len(out[1]) == 384
    d = EmbeddingService.cosine_distance(out[0], out[1])
    assert 0.0 <= d <= 2.0
