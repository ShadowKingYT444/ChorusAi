"""HTTP fan-out to peer-registered OpenAI-compatible URLs (signaling / LAN)."""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import httpx

OLLAMA_MODEL = os.getenv("ORC_BROADCAST_MODEL", "qwen2.5:0.5b")
OLLAMA_DEFAULT_PORT = 11434


def _parse_host_allowlist(raw: str) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def normalize_completion_url(raw: str) -> str:
    """Build POST .../v1/chat/completions URL from a host, ngrok URL, or full path."""
    s = raw.strip()
    if not s.startswith(("http://", "https://")):
        s = "http://" + s
    if s.rstrip("/").endswith("/v1/chat/completions"):
        return s.rstrip("/")
    p = urlparse(s)
    scheme = p.scheme or "http"
    netloc = p.netloc
    if not netloc:
        return f"http://127.0.0.1:{OLLAMA_DEFAULT_PORT}/v1/chat/completions"
    path = (p.path or "").rstrip("/")
    root = f"{scheme}://{netloc}"
    if path.endswith("/v1/chat"):
        return f"{root}{path}/completions"
    if path.endswith("/v1"):
        return f"{root}{path}/chat/completions"
    if path:
        return f"{root}{path}/v1/chat/completions"
    if scheme == "http" and p.port is None:
        host = p.hostname or "127.0.0.1"
        return f"http://{host}:{OLLAMA_DEFAULT_PORT}/v1/chat/completions"
    return f"{root}/v1/chat/completions"


def _validate_target(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http/https URLs are allowed")
    if parsed.hostname is None:
        raise ValueError("missing host in completion URL")
    blocked = {"169.254.169.254"}
    allow_local = os.getenv("ORC_ALLOW_LOCALHOST", "1").strip() == "1"
    if not allow_local:
        blocked.update({"localhost", "127.0.0.1"})
    if parsed.hostname in blocked:
        raise ValueError("host blocked by SSRF guard")
    allow = _parse_host_allowlist(os.getenv("ORC_ALLOWED_HOSTS", "").strip())
    if allow and parsed.hostname not in allow:
        raise ValueError("host not in allowlist")


async def post_chat_completion(
    *,
    completion_base_url: str,
    persona: str,
    user_prompt: str,
    job_id: str,
    peer_id: str,
    timeout_s: float,
) -> dict:
    target_url = normalize_completion_url(completion_base_url)
    _validate_target(target_url)
    max_tokens = int(os.getenv("ORC_MAX_TOKENS", "512"))
    temperature = float(os.getenv("ORC_TEMPERATURE", "0.7"))
    headers = {
        "Content-Type": "application/json",
        "X-Chorus-Job-Id": job_id,
        "X-Chorus-Slot-Id": peer_id,
        "X-Chorus-Round": "0",
    }
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": persona
                + "\n\nRespond in 2-3 sentences. Be direct and specific. Do not repeat the prompt verbatim.",
            },
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "user": f"{job_id}:{peer_id}",
    }
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(target_url, headers=headers, json=payload)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if response.status_code // 100 != 2:
            return {
                "peer_id": peer_id,
                "ok": False,
                "error": f"http_{response.status_code}",
                "latency_ms": latency_ms,
            }
        data = response.json()
        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content")
        )
        return {
            "peer_id": peer_id,
            "ok": text is not None,
            "text": text,
            "latency_ms": latency_ms,
            "error": None if text else "missing_content",
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"peer_id": peer_id, "ok": False, "error": str(exc), "latency_ms": latency_ms}
