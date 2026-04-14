from __future__ import annotations

from orchestrator.broadcast_completions import normalize_completion_url


def test_normalize_completion_url_preserves_path_prefix() -> None:
    assert (
        normalize_completion_url("https://agent.example.com/distlm")
        == "https://agent.example.com/distlm/v1/chat/completions"
    )


def test_normalize_completion_url_keeps_existing_completion_path() -> None:
    assert (
        normalize_completion_url("https://agent.example.com/api/v1/chat/completions")
        == "https://agent.example.com/api/v1/chat/completions"
    )
