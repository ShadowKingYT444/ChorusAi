"""
Chorus agent node runner.

Connects to the signaling server as a peer, listens for `job_envelope` messages,
calls the local Ollama instance, and returns `job_response` messages.

Usage:
    python -m agent_backend.node

Environment overrides:
    CHORUS_SIGNALING_URL=ws://localhost:8000/ws/signaling
    CHORUS_OLLAMA_URL=http://localhost:11434
    CHORUS_MODEL=qwen2.5:0.5b
    CHORUS_NUM_AGENTS=3
    CHORUS_PEER_ID=my-stable-name
    CHORUS_INSTANCE_ID=gpu-0
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("chorus.node")

SIGNALING_URL = os.environ.get("CHORUS_SIGNALING_URL", "ws://localhost:8000/ws/signaling")
OLLAMA_BASE_URL = os.environ.get("CHORUS_OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("CHORUS_MODEL", "qwen2.5:0.5b")
MAX_TOKENS = int(os.environ.get("CHORUS_MAX_TOKENS", "256"))
TEMPERATURE = float(os.environ.get("CHORUS_TEMPERATURE", "0.7"))
OLLAMA_TIMEOUT = float(os.environ.get("CHORUS_OLLAMA_TIMEOUT", "30.0"))
HEARTBEAT_INTERVAL = float(os.environ.get("CHORUS_HEARTBEAT_INTERVAL", "20.0"))
RECONNECT_DELAY = float(os.environ.get("CHORUS_RECONNECT_DELAY", "3.0"))
NUM_AGENTS = int(os.environ.get("CHORUS_NUM_AGENTS", "1"))
CHORUS_PEER_ID = os.environ.get("CHORUS_PEER_ID", "").strip()
CHORUS_INSTANCE_ID = os.environ.get("CHORUS_INSTANCE_ID", "").strip()

POLICY = (
    "Respond in 2-3 sentences. Be direct and specific. "
    "Do not repeat the prompt, list instructions, or explain your reasoning process."
)


async def _call_ollama(
    *,
    prompt: str,
    persona: str,
    job_id: str,
    peer_id: str,
) -> tuple[str | None, int, str | None]:
    url = OLLAMA_BASE_URL.rstrip("/") + "/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": f"{persona}\n\n{POLICY}"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "user": f"{job_id}:{peer_id}",
    }
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            response = await client.post(url, json=payload)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if response.status_code // 100 != 2:
            return None, latency_ms, f"http_{response.status_code}"
        data = response.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content")
        return text, latency_ms, None if text else "missing_content"
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - start) * 1000)
        return None, latency_ms, str(exc)


async def _run_agent(peer_id: str) -> None:
    while True:
        try:
            logger.info("[%s] connecting to %s", peer_id, SIGNALING_URL)
            async with websockets.connect(SIGNALING_URL) as ws:
                await _agent_session(ws, peer_id)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            logger.warning(
                "[%s] disconnected: %s; retrying in %.0fs",
                peer_id,
                exc,
                RECONNECT_DELAY,
            )
        except asyncio.CancelledError:
            logger.info("[%s] shutting down", peer_id)
            return
        await asyncio.sleep(RECONNECT_DELAY)


async def _agent_session(ws: ClientConnection, peer_id: str) -> None:
    await ws.send(
        json.dumps(
            {
                "type": "register",
                "peer_id": peer_id,
                "model": MODEL,
                "protocol_version": "1",
                "status": "idle",
            }
        )
    )

    raw_ack = await ws.recv()
    ack: dict[str, Any] = json.loads(raw_ack) if isinstance(raw_ack, (str, bytes)) else {}
    if ack.get("type") != "registered":
        logger.error("[%s] unexpected registration response: %s", peer_id, ack)
        return

    heartbeat_task = asyncio.create_task(_heartbeat_loop(ws, peer_id))
    try:
        async for raw in ws:
            try:
                message = _parse_json(raw)
            except Exception:  # noqa: BLE001
                logger.debug("[%s] ignored non-JSON message", peer_id)
                continue

            msg_type = message.get("type")
            if msg_type == "job_envelope":
                asyncio.create_task(_handle_job(ws, peer_id, message))
            elif msg_type in {
                "peer_count",
                "registered",
                "status_updated",
                "heartbeat_ack",
                "peer_gossip_ack",
            }:
                continue
            elif msg_type == "error":
                logger.warning("[%s] server error: %s", peer_id, message.get("error"))
            else:
                logger.debug("[%s] unhandled message type: %s", peer_id, msg_type)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _handle_job(ws: ClientConnection, peer_id: str, envelope: dict[str, Any]) -> None:
    job_id = str(envelope.get("job_id", "unknown"))
    prompt = str(envelope.get("prompt", ""))
    persona = str(envelope.get("persona", "You are a helpful assistant."))
    prompter_id = str(envelope.get("from_peer_id", ""))

    await _safe_send(ws, {"type": "set_status", "status": "busy"})
    text, latency_ms, error = await _call_ollama(
        prompt=prompt,
        persona=persona,
        job_id=job_id,
        peer_id=peer_id,
    )

    response: dict[str, Any] = {
        "type": "job_response",
        "job_id": job_id,
        "peer_id": peer_id,
        "prompter_id": prompter_id,
        "model": MODEL,
        "latency_ms": latency_ms,
    }
    if CHORUS_INSTANCE_ID:
        response["instance_id"] = CHORUS_INSTANCE_ID
    if text is not None:
        response["text"] = text
    else:
        response["error"] = error

    await _safe_send(ws, response)
    await _safe_send(ws, {"type": "set_status", "status": "idle"})


async def _heartbeat_loop(ws: ClientConnection, peer_id: str) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        sent = await _safe_send(
            ws,
            {
                "type": "heartbeat",
                "status": "idle",
                "timestamp": time.time(),
            },
        )
        if not sent:
            logger.debug("[%s] heartbeat failed; connection likely closed", peer_id)
            return


async def _safe_send(ws: ClientConnection, payload: dict[str, Any]) -> bool:
    try:
        await ws.send(json.dumps(payload))
        return True
    except Exception:  # noqa: BLE001
        return False


def _parse_json(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


async def main() -> None:
    if NUM_AGENTS < 1:
        raise ValueError("CHORUS_NUM_AGENTS must be >= 1")
    if NUM_AGENTS > 1 and CHORUS_PEER_ID:
        logger.warning(
            "CHORUS_PEER_ID is set but CHORUS_NUM_AGENTS=%d; ignoring it so each agent gets a unique id",
            NUM_AGENTS,
        )
    if NUM_AGENTS == 1 and CHORUS_PEER_ID:
        agent_ids = [CHORUS_PEER_ID]
    else:
        agent_ids = [f"agent-{uuid.uuid4().hex[:8]}" for _ in range(NUM_AGENTS)]

    tasks = [asyncio.create_task(_run_agent(peer_id)) for peer_id in agent_ids]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
