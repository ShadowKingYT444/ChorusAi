"""
Demo-only fake agents.

Spawns 5 stand-in peers that connect to a live Chorus orchestrator over
WebSocket and register with `address=demo://<peer_id>`. The orchestrator's
built-in `demo://` completion path then serves long, hand-written answers
from orchestrator/demo_agent.py (SCRIPTED_DEMO_ANSWERS) when the prompt
contains the trigger phrase ("rural clinic"). Otherwise it falls back to
the orchestrator's built-in persona-coloured demo text.

Because each peer registers with a non-empty `address`, the prompter UI
counts them in its voice cap. Job dispatch goes via the HTTP completion
path (which short-circuits to demo logic for `demo://` URLs); the
WebSocket connection here exists only to keep the peer "online" in the
network registry and visualization.

Usage (PowerShell):
    cd Chorus
    python -m scripts.demo_fake_agents --signaling "wss://YOUR-ORC/ws/signaling"

Usage (bash):
    cd Chorus
    python -m scripts.demo_fake_agents \
        --signaling wss://YOUR-ORC.up.railway.app/ws/signaling

Stop with Ctrl-C. The peers disconnect; nothing on the server changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("chorus.demo")

DEFAULT_SIGNALING_URL = os.environ.get(
    "CHORUS_SIGNALING_URL", "ws://localhost:8000/ws/signaling"
)
HEARTBEAT_INTERVAL = 20.0
RECONNECT_DELAY = 3.0


@dataclass
class FakeAgent:
    peer_id: str
    model: str
    persona_label: str  # one of: skeptical, optimistic, analytical, contrarian


# Peer ids MUST match SCRIPTED_DEMO_ANSWERS keys in orchestrator/demo_agent.py
# so the orchestrator returns the matching scripted answer on the demo trigger.
AGENTS: list[FakeAgent] = [
    FakeAgent(peer_id="atlas-skeptic",      model="llama3.1:8b",  persona_label="skeptical"),
    FakeAgent(peer_id="halcyon-clinician",  model="meditron:7b",  persona_label="analytical"),
    FakeAgent(peer_id="quasar-engineer",    model="qwen2.5:7b",   persona_label="analytical"),
    FakeAgent(peer_id="vesper-ethicist",    model="gemma2:9b",    persona_label="contrarian"),
    FakeAgent(peer_id="ember-pragmatist",   model="mistral:7b",   persona_label="optimistic"),
]


async def _run_agent(signaling_url: str, agent: FakeAgent) -> None:
    while True:
        try:
            logger.info("[%s] connecting to %s", agent.peer_id, signaling_url)
            async with websockets.connect(signaling_url) as ws:
                await _agent_session(ws, agent)
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            logger.warning(
                "[%s] disconnected: %s; retrying in %.0fs",
                agent.peer_id,
                exc,
                RECONNECT_DELAY,
            )
        except asyncio.CancelledError:
            logger.info("[%s] shutting down", agent.peer_id)
            return
        await asyncio.sleep(RECONNECT_DELAY)


async def _agent_session(ws: ClientConnection, agent: FakeAgent) -> None:
    register_payload: dict[str, Any] = {
        "type": "register",
        "peer_id": agent.peer_id,
        "model": agent.model,
        "address": f"demo://{agent.peer_id}",
        "protocol_version": "1",
        "status": "idle",
    }
    await ws.send(json.dumps(register_payload))

    raw_ack = await ws.recv()
    ack: dict[str, Any] = json.loads(raw_ack) if isinstance(raw_ack, (str, bytes)) else {}
    if ack.get("type") != "registered":
        logger.error("[%s] unexpected registration response: %s", agent.peer_id, ack)
        return
    logger.info(
        "[%s] registered (%s, %s); peers on network: %s",
        agent.peer_id,
        agent.persona_label,
        agent.model,
        ack.get("peer_count"),
    )

    heartbeat_task = asyncio.create_task(_heartbeat_loop(ws, agent))
    try:
        async for raw in ws:
            try:
                message = _parse_json(raw)
            except Exception:
                continue
            msg_type = message.get("type")
            if msg_type == "error":
                logger.warning("[%s] server error: %s", agent.peer_id, message.get("error"))
            # All other inbound traffic (peer_count, registered, heartbeat_ack,
            # job_envelope, etc.) is ignored: the orchestrator's demo:// HTTP
            # path produces this peer's answers without our involvement.
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _heartbeat_loop(ws: ClientConnection, agent: FakeAgent) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        sent = await _safe_send(
            ws,
            {"type": "heartbeat", "status": "idle", "timestamp": time.time()},
        )
        if not sent:
            return


async def _safe_send(ws: ClientConnection, payload: dict[str, Any]) -> bool:
    try:
        await ws.send(json.dumps(payload))
        return True
    except Exception:
        return False


def _parse_json(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


async def _run(signaling_url: str) -> None:
    logger.info("Spawning %d demo peers against %s", len(AGENTS), signaling_url)
    tasks = [asyncio.create_task(_run_agent(signaling_url, agent)) for agent in AGENTS]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 5 fake demo agents against a Chorus orchestrator.")
    parser.add_argument(
        "--signaling",
        default=DEFAULT_SIGNALING_URL,
        help=f"WebSocket URL of the orchestrator (default: {DEFAULT_SIGNALING_URL})",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.signaling))
    except KeyboardInterrupt:
        logger.info("Demo agents stopped.")


if __name__ == "__main__":
    main()
