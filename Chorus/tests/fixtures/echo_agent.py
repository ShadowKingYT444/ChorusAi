"""
Minimal OpenAI-compatible agent for e2e tests.

Run: python -m uvicorn tests.fixtures.echo_agent:app --host 127.0.0.1 --port <PORT>
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request

app = FastAPI(title="Chorus echo agent")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    slot = request.headers.get("x-chorus-slot-id", "unknown")
    rnd = request.headers.get("x-chorus-round", "?")
    body = await request.json()
    msgs = body.get("messages") or []
    user_len = len(msgs[1].get("content", "")) if len(msgs) > 1 else 0

    mode = os.environ.get("ECHO_AGENT_MODE", "").strip().lower()
    if mode == "short":
        # Triggers orchestrator watchdog `short_output` (< min_chars).
        content = "short"
    elif mode == "refusal":
        content = "I cannot help with that request."
    else:
        # Distinct, long enough for orchestrator watchdog min_chars; differs per slot for kNN.
        content = (
            f"I am agent slot {slot} in round {rnd}. "
            f"I will address the task with concrete steps and cite the provided context (len={user_len}). "
            f"My approach for this slot uses methodology variant {hash(slot) % 997} to avoid duplication. "
            "Step one: restate constraints. Step two: propose a solution. Step three: list risks and mitigations. "
            "This answer is substantive and unique to this participant."
        )
    return {
        "choices": [
            {
                "message": {"content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": len(content)},
    }
