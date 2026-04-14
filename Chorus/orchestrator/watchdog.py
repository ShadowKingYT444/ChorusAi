from __future__ import annotations

import os
from typing import Sequence

from orchestrator.embeddings import EmbeddingService
from orchestrator.models import PruneStatus


class Watchdog:
    """
    Heuristic gates on agent completions.

    With MiniLM (or hash) embeddings of **response** (R) and **job prompt** (P),
    forms **difference vector** d = R − P and treats high alignment of d with P as bad:
    if cos_sim(d, P) > ORC_WATCHDOG_RESIDUAL_PROMPT_COS_MAX (default 0.8), the completion
    is flagged like other watchdog failures (suspect / streak → prune).
    """

    def __init__(self, min_chars: int = 12, max_bad_streak: int = 2) -> None:
        self.min_chars = min_chars
        self.max_bad_streak = max_bad_streak
        self.residual_prompt_cos_max = float(os.getenv("ORC_WATCHDOG_RESIDUAL_PROMPT_COS_MAX", "0.8"))

    def evaluate(
        self,
        text: str | None,
        error: str | None,
        duplicate_in_round: bool,
        current_bad_streak: int,
        *,
        prompt_embedding: Sequence[float] | None = None,
        response_embedding: Sequence[float] | None = None,
    ) -> tuple[PruneStatus, int, list[str]]:
        notes: list[str] = []
        bad = False

        if error:
            bad = True
            notes.append(f"invocation_error:{error}")
        elif text is None:
            bad = True
            notes.append("missing_content")
        else:
            stripped = text.strip()
            if len(stripped) < self.min_chars:
                bad = True
                notes.append("short_output")
            if duplicate_in_round:
                bad = True
                notes.append("duplicate_output")
            if self._looks_like_refusal(stripped):
                notes.append("possible_refusal")

        residual_bad = False
        if (
            error is None
            and text
            and text.strip()
            and prompt_embedding is not None
            and response_embedding is not None
            and len(prompt_embedding) == len(response_embedding)
        ):
            p_list = [float(x) for x in prompt_embedding]
            r_list = [float(x) for x in response_embedding]
            diff = [r - p for r, p in zip(r_list, p_list)]
            cos_dp = EmbeddingService.cosine_similarity(diff, p_list)
            if cos_dp > self.residual_prompt_cos_max:
                residual_bad = True
                notes.append(
                    f"residual_prompt_cosine>{self.residual_prompt_cos_max:.2f}:{cos_dp:.4f}"
                )

        bad = bad or residual_bad

        next_streak = current_bad_streak + 1 if bad else 0
        if next_streak >= self.max_bad_streak:
            return PruneStatus.pruned, next_streak, notes
        if bad:
            return PruneStatus.suspect, next_streak, notes
        return PruneStatus.valid, next_streak, notes

    @staticmethod
    def _looks_like_refusal(text: str) -> bool:
        lowered = text.lower()
        refusal_markers = (
            "i can't",
            "i cannot",
            "i'm unable",
            "as an ai",
            "i do not have access",
        )
        return any(marker in lowered for marker in refusal_markers)
