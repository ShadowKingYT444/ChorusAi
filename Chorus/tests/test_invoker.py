from __future__ import annotations

import pytest

from orchestrator.broadcast_completions import normalize_completion_url
from orchestrator.invoker import AgentInvoker


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


def _invoker(allow_local: bool = False) -> AgentInvoker:
    inv = AgentInvoker()
    inv.allow_local = allow_local
    inv.allowed_hosts = set()
    return inv


def test_ssrf_blocks_ipv4_loopback_literal() -> None:
    inv = _invoker(allow_local=False)
    with pytest.raises(ValueError):
        inv._validate_target("http://127.0.0.1:8080/v1/chat/completions")


def test_ssrf_blocks_ipv6_loopback() -> None:
    inv = _invoker(allow_local=False)
    with pytest.raises(ValueError):
        inv._validate_target("http://[::1]:8080/v1/chat/completions")


def test_ssrf_blocks_ipv4_mapped_ipv6_loopback() -> None:
    inv = _invoker(allow_local=False)
    with pytest.raises(ValueError):
        inv._validate_target("http://[::ffff:127.0.0.1]/v1/chat/completions")


def test_ssrf_blocks_rfc1918_private_ranges() -> None:
    inv = _invoker(allow_local=False)
    for addr in ("10.0.0.5", "172.16.0.5", "192.168.1.5"):
        with pytest.raises(ValueError):
            inv._validate_target(f"http://{addr}/v1/chat/completions")


def test_ssrf_blocks_link_local_metadata() -> None:
    inv = _invoker(allow_local=False)
    with pytest.raises(ValueError):
        inv._validate_target("http://169.254.169.254/v1/chat/completions")


def test_ssrf_allows_public_host_when_no_allowlist() -> None:
    inv = _invoker(allow_local=False)
    inv._validate_target("https://example.com/v1/chat/completions")


def test_ssrf_allow_local_permits_loopback() -> None:
    inv = _invoker(allow_local=True)
    inv._validate_target("http://127.0.0.1:11434/v1/chat/completions")
    inv._validate_target("http://[::1]:11434/v1/chat/completions")


def test_ssrf_allowlist_rejects_non_matching_host() -> None:
    inv = _invoker(allow_local=False)
    inv.allowed_hosts = {"example.com"}
    with pytest.raises(ValueError):
        inv._validate_target("https://evil.example.net/v1/chat/completions")
