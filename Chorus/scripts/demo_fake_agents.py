"""
Chorus demo harness.

This script keeps five fake peers online in the signaling registry and can also
drive real demo traffic through the orchestrator:

- Network view: peers stay registered over `/ws/signaling`.
- Join/live signaling demo: peers answer `job_request` and `job_envelope`.
- Feed/results demo: optionally create real `/jobs` runs whose slots use
  `demo://<peer_id>` so the orchestrator exercises its built-in demo completion
  path and emits normal round/job events.

Usage (PowerShell):
    cd Chorus
    python -m scripts.demo_fake_agents --signaling "ws://127.0.0.1:8000/ws/signaling"

Usage (bash):
    cd Chorus
    python -m scripts.demo_fake_agents \
        --signaling "wss://YOUR-ORC.up.railway.app/ws/signaling"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

from orchestrator.demo_agent import SCRIPTED_DEMO_ANSWERS, SCRIPTED_TRIGGER

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
DEFAULT_JOB_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class FakeAgent:
    peer_id: str
    model: str
    persona_label: str  # skeptical | optimistic | analytical | contrarian


@dataclass(frozen=True)
class DemoScenario:
    label: str
    context: str
    prompt: str
    rounds: int = 2
    payout: float = 100.0


AGENTS: list[FakeAgent] = [
    FakeAgent(peer_id="atlas-skeptic", model="llama3.1:8b", persona_label="skeptical"),
    FakeAgent(peer_id="halcyon-clinician", model="meditron:7b", persona_label="analytical"),
    FakeAgent(peer_id="quasar-engineer", model="qwen2.5:7b", persona_label="analytical"),
    FakeAgent(peer_id="vesper-ethicist", model="gemma2:9b", persona_label="contrarian"),
    FakeAgent(peer_id="ember-pragmatist", model="mistral:7b", persona_label="optimistic"),
]

DEFAULT_SCENARIOS: list[DemoScenario] = [
    DemoScenario(
        label="rural-clinic",
        context=(
            "The product team wants to pilot an AI triage assistant in a rural "
            "clinic with intermittent internet, one on-site nurse, and limited "
            "budget for hardware support."
        ),
        prompt=(
            "What should we do before shipping an AI triage workflow for a rural "
            "clinic, and what is the smallest safe pilot?"
        ),
    ),
    DemoScenario(
        label="hallucination-guardrails",
        context=(
            "A support assistant currently uses one local model and produces "
            "fluent but occasionally invented product details. The team wants "
            "better quality without paying for large hosted models."
        ),
        prompt=(
            "What Chorus-style changes reduce hallucinations fastest without "
            "adding a much larger model?"
        ),
    ),
]

_PERSONA_REPLY_STYLES: dict[str, list[str]] = {
    "skeptical": [
        "I would put a hard gate in front of the risky path and force escalation when confidence is low.",
        "The fastest way to lose trust is silent failure, so surface uncertainty instead of smoothing it over.",
        "Add an audit trail first; otherwise you will not know whether the demo is helping or just sounding polished.",
    ],
    "optimistic": [
        "There is a practical pilot here if we keep the scope narrow and measure real operator behavior from day one.",
        "The win is not a giant model upgrade; it is better coordination and a cleaner workflow around the answer.",
        "Treat the first demo as a trust-building exercise with one concrete job, one operator, and fast feedback.",
    ],
    "analytical": [
        "The bottleneck is validation, so the demo should show retrieval, comparison, and a visible final synthesis.",
        "Use one pass to draft, one to challenge, and one to merge; that makes the system behavior legible to the audience.",
        "Instrument latency, disagreement, and final citations so the demo highlights process instead of raw prose.",
    ],
    "contrarian": [
        "The underrated improvement is product constraint design: fewer free-form guesses and clearer fallback states.",
        "Do not let fluent output masquerade as certainty; make the demo visibly expose what the system does not know.",
        "Most teams over-focus on model size when tighter response rules would improve the experience faster.",
    ],
}


class DemoCoordinator:
    def __init__(self, expected_peer_ids: list[str]) -> None:
        self._expected = set(expected_peer_ids)
        self._registered: set[str] = set()
        self._lock = asyncio.Lock()
        self.ready = asyncio.Event()
        self.launched_job_ids: list[str] = []

    async def mark_registered(self, peer_id: str) -> None:
        async with self._lock:
            self._registered.add(peer_id)
            if self._expected.issubset(self._registered):
                self.ready.set()

    async def add_job_id(self, job_id: str) -> None:
        async with self._lock:
            self.launched_job_ids.append(job_id)


def _derive_base_url(signaling_url: str) -> str:
    parsed = urlsplit(signaling_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    path = parsed.path or ""
    if path.endswith("/ws/signaling"):
        path = path[: -len("/ws/signaling")]
    path = path.rstrip("/")
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _parse_json(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


async def _safe_send(ws: ClientConnection, payload: dict[str, Any]) -> bool:
    try:
        await ws.send(json.dumps(payload))
        return True
    except Exception:
        return False


def _focus_text(prompt: str) -> str:
    cleaned = " ".join(prompt.split())
    return cleaned[:120].rstrip(" ,.;:")


def _make_signaling_reply(
    *,
    agent: FakeAgent,
    prompt: str,
    persona_hint: str | None,
) -> str:
    lowered = prompt.lower()
    if SCRIPTED_TRIGGER in lowered:
        scripted = SCRIPTED_DEMO_ANSWERS.get(agent.peer_id)
        if scripted:
            return scripted

    focus = _focus_text(prompt) or "the operator's request"
    style_bank = _PERSONA_REPLY_STYLES[agent.persona_label]
    opening = style_bank[abs(hash((agent.peer_id, focus))) % len(style_bank)]
    persona_text = ""
    if persona_hint:
        persona_text = f" Persona brief: {persona_hint.strip()}"
    return (
        f"{opening} Applied to {focus!r}, I would keep the answer specific, show "
        f"where peer disagreement matters, and end with one operator action.{persona_text}"
    )


async def _reply_to_signaling_job(
    ws: ClientConnection,
    *,
    agent: FakeAgent,
    job_id: str,
    prompter_id: str,
    prompt: str,
    persona_hint: str | None,
) -> None:
    await _safe_send(ws, {"type": "set_status", "status": "busy"})
    await _safe_send(
        ws,
        {
            "type": "job_ack",
            "job_id": job_id,
            "peer_id": agent.peer_id,
            "prompter_id": prompter_id,
        },
    )

    latency_ms = 240 + (abs(hash((job_id, agent.peer_id, prompt))) % 420)
    await asyncio.sleep(latency_ms / 1000.0)
    text = _make_signaling_reply(agent=agent, prompt=prompt, persona_hint=persona_hint)
    await _safe_send(
        ws,
        {
            "type": "job_response",
            "job_id": job_id,
            "peer_id": agent.peer_id,
            "prompter_id": prompter_id,
            "text": text,
            "model": agent.model,
            "latency_ms": latency_ms,
            "instance_id": "demo-script",
        },
    )
    await _safe_send(ws, {"type": "set_status", "status": "idle"})


async def _heartbeat_loop(ws: ClientConnection, agent: FakeAgent) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        sent = await _safe_send(
            ws,
            {"type": "heartbeat", "status": "idle", "timestamp": time.time()},
        )
        if not sent:
            return


async def _agent_session(
    ws: ClientConnection,
    *,
    agent: FakeAgent,
    coordinator: DemoCoordinator,
) -> None:
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
    ack: dict[str, Any] = _parse_json(raw_ack) if isinstance(raw_ack, (str, bytes)) else {}
    if ack.get("type") != "registered":
        logger.error("[%s] unexpected registration response: %s", agent.peer_id, ack)
        return

    await coordinator.mark_registered(agent.peer_id)
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
                continue
            if msg_type == "registered":
                await coordinator.mark_registered(agent.peer_id)
                continue
            if msg_type == "job_envelope":
                logger.info("[%s] replying to signaling envelope job=%s", agent.peer_id, message.get("job_id"))
                await _reply_to_signaling_job(
                    ws,
                    agent=agent,
                    job_id=str(message.get("job_id") or ""),
                    prompter_id=str(message.get("from_peer_id") or ""),
                    prompt=str(message.get("prompt") or ""),
                    persona_hint=message.get("persona"),
                )
                continue
            if msg_type == "job_request":
                logger.info("[%s] replying to signaling request job=%s", agent.peer_id, message.get("job_id"))
                await _reply_to_signaling_job(
                    ws,
                    agent=agent,
                    job_id=str(message.get("job_id") or ""),
                    prompter_id=str(message.get("prompter_id") or ""),
                    prompt=str(message.get("prompt") or ""),
                    persona_hint=message.get("your_persona"),
                )
                continue
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _run_agent(
    signaling_url: str,
    *,
    agent: FakeAgent,
    coordinator: DemoCoordinator,
) -> None:
    while True:
        try:
            logger.info("[%s] connecting to %s", agent.peer_id, signaling_url)
            async with websockets.connect(signaling_url) as ws:
                await _agent_session(ws, agent=agent, coordinator=coordinator)
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


async def _wait_for_health(client: httpx.AsyncClient, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            response = await client.get("/health")
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("orchestrator /health did not become ready")


def _scenario_set(args: argparse.Namespace) -> list[DemoScenario]:
    scenarios = list(DEFAULT_SCENARIOS)
    if args.job_prompt:
        custom = DemoScenario(
            label="custom",
            context=args.job_context or "Live Chorus demo run.",
            prompt=args.job_prompt,
            rounds=args.rounds,
            payout=args.payout,
        )
        scenarios.insert(0, custom)
    return scenarios[: args.demo_jobs]


async def _run_demo_job(
    client: httpx.AsyncClient,
    *,
    scenario: DemoScenario,
    timeout_s: float,
) -> tuple[str, dict[str, Any]]:
    create_resp = await client.post(
        "/jobs",
        json={
            "context": scenario.context,
            "prompt": scenario.prompt,
            "agent_count": len(AGENTS),
            "rounds": scenario.rounds,
            "payout": scenario.payout,
            "embedding_model_version": "demo-script",
        },
    )
    create_resp.raise_for_status()
    job_id = str(create_resp.json()["job_id"])

    slots_payload = {
        agent.peer_id: {"completion_base_url": f"demo://{agent.peer_id}"}
        for agent in AGENTS
    }
    register_resp = await client.post(f"/jobs/{job_id}/agents", json={"slots": slots_payload})
    register_resp.raise_for_status()
    logger.info("[demo-job:%s] launched for scenario=%s", job_id, scenario.label)

    deadline = time.monotonic() + timeout_s
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        job_resp = await client.get(f"/jobs/{job_id}")
        job_resp.raise_for_status()
        latest = job_resp.json()
        status = latest.get("status")
        if status == "completed":
            break
        if status == "failed":
            raise RuntimeError(f"demo job {job_id} failed: {latest}")
        await asyncio.sleep(0.5)

    if latest.get("status") != "completed":
        raise RuntimeError(
            f"demo job {job_id} timed out after {timeout_s:.0f}s. "
            "For a cold local orchestrator, use ORC_EMBEDDING_BACKEND=hash "
            "to avoid MiniLM warm-up/download delays during demos."
        )

    operator_resp = await client.get(f"/jobs/{job_id}/operator")
    operator_resp.raise_for_status()
    operator = operator_resp.json()
    final_answer = str(operator.get("final_answer") or "").strip()
    logger.info(
        "[demo-job:%s] completed; final_answer=%s",
        job_id,
        (final_answer[:140] + "...") if len(final_answer) > 140 else final_answer,
    )
    return job_id, operator


async def _launch_demo_jobs(
    *,
    base_url: str,
    coordinator: DemoCoordinator,
    args: argparse.Namespace,
) -> None:
    await coordinator.ready.wait()
    scenarios = _scenario_set(args)
    if not scenarios:
        logger.info("Presence-only mode: peers will stay online, but no /jobs runs will be created.")
        return

    logger.info("All demo peers registered; using orchestrator base %s", base_url)
    async with httpx.AsyncClient(base_url=base_url, timeout=args.http_timeout) as client:
        await _wait_for_health(client, timeout_s=15.0)
        for scenario in scenarios:
            job_id, _operator = await _run_demo_job(
                client,
                scenario=scenario,
                timeout_s=args.job_timeout,
            )
            await coordinator.add_job_id(job_id)
        chats_count = None
        try:
            chats_resp = await client.get("/chats")
            if chats_resp.status_code == 200:
                chats_count = len((chats_resp.json() or {}).get("chats") or [])
        except httpx.HTTPError:
            chats_count = None

    if chats_count is None:
        logger.info("Created %d demo job(s): %s", len(coordinator.launched_job_ids), coordinator.launched_job_ids)
    else:
        logger.info(
            "Created %d demo job(s): %s (current /chats count=%s)",
            len(coordinator.launched_job_ids),
            coordinator.launched_job_ids,
            chats_count,
        )


def _report_background_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("background demo job launcher failed")


async def _run(args: argparse.Namespace) -> None:
    base_url = args.base_url or _derive_base_url(args.signaling)
    coordinator = DemoCoordinator(expected_peer_ids=[agent.peer_id for agent in AGENTS])

    logger.info("Spawning %d demo peers against %s", len(AGENTS), args.signaling)
    agent_tasks = [
        asyncio.create_task(_run_agent(args.signaling, agent=agent, coordinator=coordinator))
        for agent in AGENTS
    ]

    launcher_task: asyncio.Task[None] | None = None
    if args.demo_jobs > 0:
        launcher_task = asyncio.create_task(
            _launch_demo_jobs(base_url=base_url, coordinator=coordinator, args=args)
        )
        if args.exit_after_jobs:
            try:
                await launcher_task
            finally:
                for task in agent_tasks:
                    task.cancel()
                await asyncio.gather(*agent_tasks, return_exceptions=True)
            return
        launcher_task.add_done_callback(_report_background_result)

    try:
        await asyncio.gather(*agent_tasks)
    except asyncio.CancelledError:
        for task in agent_tasks:
            task.cancel()
        await asyncio.gather(*agent_tasks, return_exceptions=True)
        if launcher_task is not None:
            launcher_task.cancel()
            await asyncio.gather(launcher_task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Chorus fake peers, signaling replies, and optional real demo jobs."
    )
    parser.add_argument(
        "--signaling",
        default=DEFAULT_SIGNALING_URL,
        help=f"WebSocket URL of the orchestrator signaling endpoint (default: {DEFAULT_SIGNALING_URL})",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="HTTP base URL for the orchestrator; defaults to the signaling URL with ws->http and /ws/signaling removed.",
    )
    parser.add_argument(
        "--demo-jobs",
        type=int,
        default=1,
        help="How many real /jobs runs to create after peers register (default: 1, use 0 for presence-only).",
    )
    parser.add_argument(
        "--job-prompt",
        default=None,
        help="Optional custom prompt for the first auto-created demo job.",
    )
    parser.add_argument(
        "--job-context",
        default=None,
        help="Optional custom context for --job-prompt.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=2,
        help="Rounds for a custom auto-created demo job (default: 2).",
    )
    parser.add_argument(
        "--payout",
        type=float,
        default=100.0,
        help="Payout for a custom auto-created demo job (default: 100).",
    )
    parser.add_argument(
        "--job-timeout",
        type=float,
        default=DEFAULT_JOB_TIMEOUT_S,
        help=f"Seconds to wait for each auto-created demo job (default: {DEFAULT_JOB_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=30.0,
        help="Per-request HTTP timeout when creating/polling demo jobs (default: 30).",
    )
    parser.add_argument(
        "--exit-after-jobs",
        action="store_true",
        help="Exit once the auto-created demo jobs complete; useful for verification scripts.",
    )
    args = parser.parse_args()

    if args.demo_jobs < 0:
        parser.error("--demo-jobs must be >= 0")
    if args.rounds < 1:
        parser.error("--rounds must be >= 1")

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.info("Demo agents stopped.")


if __name__ == "__main__":
    main()
