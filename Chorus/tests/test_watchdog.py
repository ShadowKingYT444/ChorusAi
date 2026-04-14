"""Watchdog residual (R−P) vs P cosine gate."""

from __future__ import annotations

import pytest

from orchestrator.models import PruneStatus
from orchestrator.watchdog import Watchdog


def test_residual_high_cosine_flags_bad() -> None:
    wd = Watchdog(min_chars=1, max_bad_streak=5)
    # P and R such that d = R − P is parallel to P ⇒ cos(d, P) ≈ 1.
    p = [1.0, 0.0, 0.0]
    r = [2.0, 0.0, 0.0]
    status, streak, notes = wd.evaluate(
        text="hello there x",
        error=None,
        duplicate_in_round=False,
        current_bad_streak=0,
        prompt_embedding=p,
        response_embedding=r,
    )
    assert status == PruneStatus.suspect
    assert streak == 1
    assert any("residual_prompt_cosine" in n for n in notes)


def test_residual_low_cosine_valid() -> None:
    wd = Watchdog(min_chars=1, max_bad_streak=5)
    p = [1.0, 0.0, 0.0]
    r = [0.0, 1.0, 0.0]
    d = [r[i] - p[i] for i in range(3)]
    # d = [-1, 1, 0]; cos with P = -1 / sqrt(2) < 0.8
    from orchestrator.embeddings import EmbeddingService

    assert EmbeddingService.cosine_similarity(d, p) < 0.8

    status, streak, notes = wd.evaluate(
        text="hello there x",
        error=None,
        duplicate_in_round=False,
        current_bad_streak=0,
        prompt_embedding=p,
        response_embedding=r,
    )
    assert status == PruneStatus.valid
    assert streak == 0
    assert not any("residual_prompt_cosine" in n for n in notes)


def test_skips_residual_when_error() -> None:
    wd = Watchdog(min_chars=1, max_bad_streak=5)
    p = [1.0, 0.0, 0.0]
    r = [2.0, 0.0, 0.0]
    status, _streak, notes = wd.evaluate(
        text=None,
        error="timeout",
        duplicate_in_round=False,
        current_bad_streak=0,
        prompt_embedding=p,
        response_embedding=r,
    )
    assert status != PruneStatus.valid
    assert not any("residual_prompt_cosine" in n for n in notes)
