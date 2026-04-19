"""
Chorus-shaped call to Ollama's OpenAI-compatible POST /v1/chat/completions endpoint.

Primary API: `complete_chorus` - pass persona, context, prompt, etc.; get assistant text back.
Pass `return_raw=True` to also receive the parsed upstream JSON dict (e.g. for HTTP debug responses).
"""

from __future__ import annotations

import os
from typing import Any, Literal, overload

import httpx

OLLAMA_MODEL = "qwen2.5:0.5b"

CHAT_COMPLETIONS_URL = os.environ.get(
    "OLLAMA_HOST",
    "http://127.0.0.1:11434",
).rstrip("/") + "/v1/chat/completions"

DEFAULT_MODEL = OLLAMA_MODEL


class ChorusInvokeError(Exception):
    """Upstream HTTP error, bad JSON, or missing choices[0].message.content."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        upstream_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.upstream_body = upstream_body


def _build_user_message(*, context: str, prompt: str, data: str | None) -> str:
    parts = [f"### Context\n{context.strip()}", f"### Prompt\n{prompt.strip()}"]
    if data is not None and data.strip():
        parts.append(f"### Data\n{data.strip()}")
    return "\n\n".join(parts)


def _build_openai_payload(
    *,
    persona: str,
    context: str,
    prompt: str,
    data: str | None,
    policy: str,
    model: str,
    max_tokens: int,
    temperature: float,
    job_id: str,
    slot_id: str,
) -> dict[str, Any]:
    system_text = f"{persona.strip()}\n\n{policy.strip()}"
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": _build_user_message(context=context, prompt=prompt, data=data)},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "user": f"{job_id}:{slot_id}",
    }


def _build_headers(
    *,
    job_id: str,
    slot_id: str,
    round_no: int,
    authorization: str | None,
) -> dict[str, str]:
    h: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Chorus-Job-Id": job_id,
        "X-Chorus-Slot-Id": slot_id,
        "X-Chorus-Round": str(round_no),
    }
    if authorization:
        h["Authorization"] = authorization
    return h


def _parse_completion_content(data: dict[str, Any]) -> str | None:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    msg = choices[0].get("message") or {}
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    return content if isinstance(content, str) else None


async def _exchange_chorus(
    *,
    persona: str,
    context: str,
    prompt: str,
    data: str | None,
    policy: str,
    model: str,
    max_tokens: int,
    temperature: float,
    job_id: str,
    slot_id: str,
    round_no: int,
    chat_completions_url: str,
    authorization: str | None,
    timeout: float,
) -> tuple[str, dict[str, Any]]:
    payload = _build_openai_payload(
        persona=persona,
        context=context,
        prompt=prompt,
        data=data,
        policy=policy,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        job_id=job_id,
        slot_id=slot_id,
    )
    headers = _build_headers(
        job_id=job_id,
        slot_id=slot_id,
        round_no=round_no,
        authorization=authorization,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(chat_completions_url, json=payload, headers=headers)
    except httpx.RequestError as e:
        raise ChorusInvokeError(f"Upstream request failed: {e}", http_status=None) from e

    body_preview = r.text[:8000] if r.text else None
    if r.status_code >= 400:
        raise ChorusInvokeError(
            f"Upstream HTTP {r.status_code}",
            http_status=r.status_code,
            upstream_body=body_preview,
        )

    if not r.headers.get("content-type", "").startswith("application/json"):
        raise ChorusInvokeError(
            "Upstream did not return application/json",
            http_status=r.status_code,
            upstream_body=body_preview,
        )

    try:
        parsed: dict[str, Any] = r.json()
    except ValueError as e:
        raise ChorusInvokeError(
            "Upstream returned invalid JSON",
            http_status=r.status_code,
            upstream_body=body_preview,
        ) from e

    text = _parse_completion_content(parsed)
    if text is None:
        raise ChorusInvokeError(
            "Missing choices[0].message.content in upstream JSON",
            http_status=r.status_code,
            upstream_body=body_preview,
        )
    return text, parsed


@overload
async def complete_chorus(
    *,
    persona: str,
    context: str,
    prompt: str,
    data: str | None = None,
    policy: str = "Answer concisely in plain UTF-8 text only.",
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.7,
    job_id: str = "test-job",
    slot_id: str = "test-slot",
    round_no: int = 1,
    chat_completions_url: str | None = None,
    authorization: str | None = None,
    timeout: float = 120.0,
    return_raw: Literal[False] = False,
) -> str: ...


@overload
async def complete_chorus(
    *,
    persona: str,
    context: str,
    prompt: str,
    data: str | None = None,
    policy: str = "Answer concisely in plain UTF-8 text only.",
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.7,
    job_id: str = "test-job",
    slot_id: str = "test-slot",
    round_no: int = 1,
    chat_completions_url: str | None = None,
    authorization: str | None = None,
    timeout: float = 120.0,
    return_raw: Literal[True],
) -> tuple[str, dict[str, Any]]: ...


async def complete_chorus(
    *,
    persona: str,
    context: str,
    prompt: str,
    data: str | None = None,
    policy: str = "Answer concisely in plain UTF-8 text only.",
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.7,
    job_id: str = "test-job",
    slot_id: str = "test-slot",
    round_no: int = 1,
    chat_completions_url: str | None = None,
    authorization: str | None = None,
    timeout: float = 120.0,
    return_raw: bool = False,
) -> str | tuple[str, dict[str, Any]]:
    """
    POST a Chorus-shaped chat completion to `chat_completions_url` (default: CHAT_COMPLETIONS_URL).

    Returns the assistant string from choices[0].message.content, or `(text, full_json)` if
    `return_raw=True`.

    Raises:
        ChorusInvokeError: network failure, non-2xx upstream, or unreadable completion JSON.
    """
    url = chat_completions_url or CHAT_COMPLETIONS_URL
    m = model if model is not None else DEFAULT_MODEL
    text, raw = await _exchange_chorus(
        persona=persona,
        context=context,
        prompt=prompt,
        data=data,
        policy=policy,
        model=m,
        max_tokens=max_tokens,
        temperature=temperature,
        job_id=job_id,
        slot_id=slot_id,
        round_no=round_no,
        chat_completions_url=url,
        authorization=authorization,
        timeout=timeout,
    )
    if return_raw:
        return text, raw
    return text
