"""
Demo-only fake agents.

Spawns 5 stand-in peers that connect to a live Chorus orchestrator over
WebSocket, register as ordinary agents, and reply to job_envelope messages
with hand-written, persona-specific answers. There is no Ollama call; the
"thinking" delay is simulated.

Usage:
    python -m scripts.demo_fake_agents \\
        --signaling wss://your-orchestrator.example.com/ws/signaling

Or with env var:
    CHORUS_SIGNALING_URL=ws://localhost:8000/ws/signaling \\
        python -m scripts.demo_fake_agents

The agents will fire their long, scripted answers when the user prompt
contains the trigger phrase (case-insensitive, substring match) defined
in TRIGGER_PHRASE below. Any other prompt gets a short generic fallback
so the orchestrator does not flag them as broken.

Stop with Ctrl-C. The agents go offline and the demo is over -- nothing
to roll back on the server side.
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

TRIGGER_PHRASE = "rural clinic"

FALLBACK_TEXT = (
    "I am a demo peer running locally for the Chorus showcase. For this prompt I will defer "
    "to the live agents on the network -- ask the triage-deployment question to see the "
    "scripted multi-perspective response this node was prepared for."
)


@dataclass
class FakeAgent:
    peer_id: str
    model: str
    persona_label: str
    min_latency_ms: int
    max_latency_ms: int
    scripted_answer: str


AGENTS: list[FakeAgent] = [
    FakeAgent(
        peer_id="atlas-skeptic",
        model="llama3.1:8b",
        persona_label="skeptic",
        min_latency_ms=900,
        max_latency_ms=1400,
        scripted_answer=(
            "Before you ship anything, write down the failure modes you are willing to accept "
            "and the ones that end the project. An AI triage tool in a rural clinic with "
            "intermittent internet is a high-consequence deployment: a wrong 'low acuity' "
            "label on a sepsis presentation, a hallucinated drug interaction, or a silent "
            "model fallback during an outage can cost a life. None of those failures are "
            "abstract -- they happen in published case reports of LLM-assisted triage already.\n\n"
            "Three concrete things I would require before go-live. First, an explicit 'do not "
            "use' list: chest pain in adults, pregnancy bleeding, pediatric fever under three "
            "months, suicidal ideation, anything the model has not been red-teamed against. "
            "Second, a hard floor on confidence -- if the model is below it, the workflow "
            "must hand off to a clinician, not pick a best guess. Third, an offline-only "
            "audit log on the device that captures the input, the model output, the override "
            "the clinician made, and the eventual outcome when known. Without that loop you "
            "cannot tell whether the tool is helping or quietly drifting.\n\n"
            "I would also push back on framing this as 'AI triage' at all. Call it "
            "decision support. The clinician is responsible. The tool's job is to surface "
            "patterns, suggest questions, flag red flags -- not to issue a disposition. "
            "That framing changes the regulatory surface, the consent conversation, and "
            "the legal exposure if something goes wrong.\n\n"
            "If you cannot meet those conditions in the next ninety days, do not deploy. "
            "Run a six-month shadow study where the tool produces recommendations that are "
            "logged but never shown to the clinician, then compare against the actual "
            "dispositions. That is the cheapest way to find out if your model is good "
            "enough without putting any patient at risk."
        ),
    ),
    FakeAgent(
        peer_id="halcyon-clinician",
        model="meditron:7b",
        persona_label="clinician",
        min_latency_ms=1100,
        max_latency_ms=1700,
        scripted_answer=(
            "Speaking from clinic floor experience: the thing that determines whether a "
            "triage tool gets used is not its accuracy on benchmarks, it is whether it "
            "fits the eight-minute encounter and the workflow the nurse already has. If "
            "the nurse has to type the chief complaint twice, retype vitals, and wait for "
            "a model response longer than it takes to walk to the next room, the tool will "
            "be open in a tab and ignored within a week.\n\n"
            "Design for the actual patient population. Rural clinics see a different "
            "distribution than the academic centers most medical LLMs are trained on: "
            "more agricultural injuries, more late-presentation chronic disease, more "
            "patients who minimize symptoms because the next clinic is two hours away. "
            "If your training and evaluation data are mostly urban tertiary-care notes, "
            "your sensitivity for the conditions that actually walk through this door "
            "will be wrong. Validate on local charts before deployment, not after.\n\n"
            "Build the tool around three concrete jobs that nurses say take time and "
            "have a real chance of being missed. For most rural clinics those are: "
            "(1) screening for sepsis criteria in adults presenting with vague malaise, "
            "(2) flagging pediatric dehydration severity, and (3) catching medication "
            "interactions in polypharmacy patients on chronic disease regimens. Solve "
            "those three crisply and you have value. Try to be a generalist diagnostic "
            "assistant and you will be mediocre at all of them.\n\n"
            "One thing the engineering team will under-budget: the tool needs a "
            "graceful 'I don't know' that the clinician can document and bill against. "
            "If the model bails out and the nurse has nothing to put in the chart, they "
            "will stop using it. Give them a structured 'AI tool returned no recommendation, "
            "clinician judgment used' note that integrates with the EHR. That single "
            "feature determines adoption."
        ),
    ),
    FakeAgent(
        peer_id="quasar-engineer",
        model="qwen2.5:7b",
        persona_label="engineer",
        min_latency_ms=750,
        max_latency_ms=1200,
        scripted_answer=(
            "Architecturally this is an offline-first problem with intermittent sync, "
            "which is a well-understood pattern but one teams routinely get wrong by "
            "starting with cloud and bolting on offline later. Start the other way: "
            "design every workflow to function with no internet for a full clinic day, "
            "then layer sync as an enhancement.\n\n"
            "Concrete stack. Run a quantized 7B-class clinical model on a small "
            "edge device per clinic -- a Mac mini, a Jetson Orin, or a refurbished "
            "tower with a single consumer GPU all work. Inference at four-bit quant "
            "fits in 8GB of VRAM and gives you sub-two-second latency on the kinds of "
            "structured prompts triage uses. Wrap it in a thin local API the front-end "
            "calls; the front-end never knows whether the model is local or remote.\n\n"
            "For sync: every model call gets a content-addressed log entry with the "
            "input hash, output hash, model version, and timestamp. When the link comes "
            "up, push the log to a central store with deduplication. This gives you the "
            "audit trail the regulators want and the dataset you need to evaluate model "
            "drift, with zero coupling to the live patient encounter. The clinic does not "
            "wait on anything cloud-side to function.\n\n"
            "Two failure modes engineering teams miss. First, model updates: how does a "
            "new version reach a clinic with a flaky 3G uplink, and how do you roll back "
            "if it regresses on local cases? Build a signed-update channel with "
            "atomic switch and a one-command rollback before you ship the first model. "
            "Second, observability: you cannot SSH into a clinic device. Every device "
            "needs a small daemon that buffers structured telemetry locally and ships "
            "it during sync windows, with enough detail to diagnose 'the model said "
            "something weird at 14:30 yesterday' from a thousand miles away.\n\n"
            "Skip Kubernetes. Skip microservices. One binary per device, systemd, "
            "and a sync agent. The complexity budget belongs to the model and the "
            "clinical workflow, not the platform."
        ),
    ),
    FakeAgent(
        peer_id="vesper-ethicist",
        model="gemma2:9b",
        persona_label="ethicist",
        min_latency_ms=1300,
        max_latency_ms=1900,
        scripted_answer=(
            "The ethical frame people reach for first is informed consent, but in a "
            "rural clinic that often collapses into a checkbox at intake. The deeper "
            "questions are about equity and recourse. Who is harmed if the tool is "
            "wrong, and what do they do about it?\n\n"
            "Equity question: a model trained predominantly on data from well-resourced "
            "health systems will encode their patterns -- including which complaints get "
            "taken seriously. There is a real risk that an AI triage tool deployed in a "
            "rural setting systematically under-triages presentations that are common in "
            "the local population but underrepresented in training data. Indigenous and "
            "rural patients already face documented disparities in pain assessment and "
            "diagnostic delay. A tool that amplifies those patterns is worse than no "
            "tool. You need a pre-deployment fairness evaluation stratified by the "
            "actual demographics of the clinic, not just an aggregate accuracy number.\n\n"
            "Recourse question: when the tool contributes to a bad outcome, what is the "
            "patient's path to redress? In most current deployments the answer is "
            "nothing -- the vendor disclaims liability, the clinician owns the decision, "
            "and the patient bears the loss. That is not ethically tenable for a "
            "publicly funded rural deployment. Before launch, write down the "
            "incident-response process: who reviews adverse events involving the tool, "
            "how they get reported to the patient and the regulator, and what triggers "
            "a deployment pause. If you cannot answer those three questions in writing "
            "today, you are not ready.\n\n"
            "Consent should be opt-in, plain-language, and revocable in the same visit. "
            "'We use a computer tool to help the nurse think about your symptoms. You "
            "can decline and your care will not change.' If declining changes care, the "
            "consent is not real. And the model output should be visible to the "
            "patient on request -- they have a right to know what the machine said "
            "about them.\n\n"
            "None of this is anti-AI. Done well, decision support in under-resourced "
            "settings is one of the highest-leverage uses of this technology. Done "
            "carelessly it widens the exact gap it claims to close."
        ),
    ),
    FakeAgent(
        peer_id="ember-pragmatist",
        model="mistral:7b",
        persona_label="pragmatist",
        min_latency_ms=850,
        max_latency_ms=1300,
        scripted_answer=(
            "Strip the launch down to one clinic, one chief complaint, one shift. That "
            "is the smallest unit that produces real signal. Pick the clinic with the "
            "most engaged nurse-in-charge, not the one with the best infrastructure -- "
            "adoption beats hardware. Pick the chief complaint where local outcomes are "
            "worst and the diagnostic algorithm is well established (adult sepsis "
            "screening is a strong default). Run it for one shift type before you "
            "expand to nights or weekends, where staffing and presentation patterns "
            "shift hard.\n\n"
            "Six-week pilot, three milestones. Week two: tool is in the workflow, nurses "
            "have used it on at least thirty real encounters, you have telemetry on "
            "latency, override rate, and abandonment. Week four: you have a structured "
            "review of every case where the tool's recommendation differed from the "
            "nurse's disposition, with a clinician reviewer adjudicating. Week six: "
            "go/no-go meeting with three preset criteria -- override rate below a "
            "threshold you set in advance, no adverse event attributable to the tool, "
            "qualitative nurse feedback supportive of expansion. Document the criteria "
            "before week one. Do not let success be defined retrospectively.\n\n"
            "Budget realistically. The model itself is the cheap part. Real costs: "
            "clinician time for adjudication (budget two hours per week per pilot site), "
            "EHR integration work (always larger than estimated -- triple your first "
            "guess), training and change management (an afternoon session plus weekly "
            "office hours for the first month), and an on-call engineer who picks up "
            "the phone when the device locks up at 3am. The on-call alone will cost "
            "more than the GPU.\n\n"
            "Do not try to monetize during the pilot. Do not promise the funder "
            "'national rollout in twelve months' -- you do not yet know if it works. "
            "What you are buying with this pilot is the right to make a credible claim "
            "about effect size in this specific setting. That is a far more valuable "
            "asset for the next round of funding or a regulatory conversation than a "
            "ten-clinic deployment that nobody has measured."
        ),
    ),
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
        "[%s] registered as %s (%s); peers on network: %s",
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
            if msg_type == "job_envelope":
                asyncio.create_task(_handle_job(ws, agent, message))
            elif msg_type == "error":
                logger.warning("[%s] server error: %s", agent.peer_id, message.get("error"))
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _handle_job(ws: ClientConnection, agent: FakeAgent, envelope: dict[str, Any]) -> None:
    job_id = str(envelope.get("job_id", "unknown"))
    prompt = str(envelope.get("prompt", ""))
    prompter_id = str(envelope.get("from_peer_id", ""))

    await _safe_send(ws, {"type": "set_status", "status": "busy"})

    triggered = TRIGGER_PHRASE.lower() in prompt.lower()
    text = agent.scripted_answer if triggered else FALLBACK_TEXT

    latency_ms = random.randint(agent.min_latency_ms, agent.max_latency_ms)
    await asyncio.sleep(latency_ms / 1000.0)

    response: dict[str, Any] = {
        "type": "job_response",
        "job_id": job_id,
        "peer_id": agent.peer_id,
        "prompter_id": prompter_id,
        "model": agent.model,
        "latency_ms": latency_ms,
        "text": text,
    }
    await _safe_send(ws, response)
    await _safe_send(ws, {"type": "set_status", "status": "idle"})
    logger.info(
        "[%s] answered job %s (%s, %dms, %d chars)",
        agent.peer_id,
        job_id[:8],
        "scripted" if triggered else "fallback",
        latency_ms,
        len(text),
    )


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
    logger.info(
        "Spawning %d demo peers against %s. Trigger phrase: %r",
        len(AGENTS),
        signaling_url,
        TRIGGER_PHRASE,
    )
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
