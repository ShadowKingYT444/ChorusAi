from __future__ import annotations

import hashlib
import random
import re
from time import perf_counter

import anyio

from orchestrator.models import CompletionResult, JobRecord

DEMO_COMPLETION_SCHEME = "demo://"
SYNTHETIC_COMPLETION_SCHEME = "synthetic://"

SCRIPTED_TRIGGER = "rural clinic"
SCRIPTED_DEMO_ANSWERS: dict[str, str] = {
    "atlas-skeptic": (
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
    "halcyon-clinician": (
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
    "quasar-engineer": (
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
    "vesper-ethicist": (
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
    "ember-pragmatist": (
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
}

_NEAREST_RE = re.compile(r"A peer said:\s*(.+?)(?:\n\n|$)", re.DOTALL)
_FURTHEST_RE = re.compile(r"A dissenting peer said:\s*(.+?)(?:\n\n|$)", re.DOTALL)

_ROLE_STYLES = {
    "skeptical": {
        "openers": [
            "I would not trust a single-pass answer here.",
            "The failure mode is obvious: the model sounds certain before it is correct.",
            "The cheapest improvement is to stop rewarding confident guesses.",
        ],
        "round_one": [
            "Put a lightweight verifier between generation and output, and block any claim that cannot be grounded in retrieved evidence or a consistency check.",
            "Force the answer to earn specificity: if the model cannot point to evidence, it should downgrade confidence instead of improvising facts.",
            "Treat unsupported detail as a bug. A second pass that looks for unverifiable nouns, dates, and numbers removes most embarrassing hallucinations.",
        ],
        "follow_up": [
            "I would keep the consensus thread, but only if every concrete claim survives a retrieval or rules-based verification pass.",
            "The shared direction is useful, but the system still needs a hard gate that rejects specifics the swarm cannot back up.",
            "The debate is helping, yet the real safeguard is a policy that turns uncertainty into explicit fallback language instead of fiction.",
        ],
    },
    "optimistic": {
        "openers": [
            "The good news is that this is fixable without a giant model.",
            "There is a practical path to better quality here.",
            "The fastest win is to add one cheap coordination step.",
        ],
        "round_one": [
            "Split the task into propose, verify, and challenge roles so one agent answers, one checks evidence, and one searches for counterexamples before the final merge.",
            "Make the model collaborate with itself: draft the answer, retrieve the support, then revise against a critic before the user ever sees it.",
            "A small chorus works well when each agent has one job. Specialization gives you better answers without expensive inference.",
        ],
        "follow_up": [
            "I would lean into the emerging consensus and formalize it as a draft-plus-review workflow with one explicit challenger in the loop.",
            "The current direction is strong because it raises quality through role separation instead of brute-force model size.",
            "The swarm is converging on the right shape: keep the reviewer and challenger active so the final answer feels both fast and trustworthy.",
        ],
    },
    "analytical": {
        "openers": [
            "The bottleneck is not generation; it is validation.",
            "The highest-leverage change is procedural, not architectural.",
            "The system needs a better quality filter, not just more tokens.",
        ],
        "round_one": [
            "Use retrieval-backed assertions plus calibrated confidence scoring, then suppress any sentence whose support falls below the acceptance threshold.",
            "Measure hallucination risk at the claim level. Entity-heavy statements should face stricter verification than generic planning advice.",
            "Run a cheap disagreement check across multiple drafts and only preserve facts that survive both evidence lookup and cross-agent scrutiny.",
        ],
        "follow_up": [
            "The best synthesis is to preserve the shared retrieval-first direction while quantifying which claims actually deserve to survive the merge.",
            "Consensus is useful here because it exposes stable facts, while the outlier view is a reminder to score confidence at the claim level.",
            "I would convert the discussion into a pipeline: retrieve, draft, challenge, then redact unsupported specifics before release.",
        ],
    },
    "contrarian": {
        "openers": [
            "The underrated move is to make the model slower at the decision point, not bigger overall.",
            "Most teams chase model size when the real fix is interface design.",
            "The counterintuitive answer is to reduce how much the model is allowed to improvise.",
        ],
        "round_one": [
            "Constrain the response format so the model must separate facts, assumptions, and speculation. Hallucinations survive when every sentence has the same visual weight.",
            "Ask for fewer, better claims. Tight schemas and explicit unknown fields beat open-ended prose when accuracy matters.",
            "Instead of adding another giant model, remove the situations where the model has permission to guess. Product constraints outperform scaling in many workflows.",
        ],
        "follow_up": [
            "I would keep the consensus mechanism, but the bigger unlock is a UI that surfaces uncertainty instead of flattening everything into fluent prose.",
            "The shared answer is improving, although the dissent matters because it points to product-level guardrails rather than model-level heroics.",
            "The swarm is useful, but the strongest intervention is still constraint design: fewer free-form guesses, more visible uncertainty.",
        ],
    },
}


def is_demo_completion_base(value: str) -> bool:
    v = value.strip().lower()
    return v.startswith(DEMO_COMPLETION_SCHEME) or v.startswith(SYNTHETIC_COMPLETION_SCHEME)


def _stable_rng(*parts: object) -> random.Random:
    material = "||".join(str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big", signed=False))


def _pick_one(rng: random.Random, items: list[str]) -> str:
    return items[rng.randrange(len(items))]


def _persona_bucket(persona: str) -> str:
    lowered = persona.lower()
    if "skeptic" in lowered:
        return "skeptical"
    if "optimist" in lowered:
        return "optimistic"
    if "contrarian" in lowered:
        return "contrarian"
    return "analytical"


def _focus_text(prompt: str) -> str:
    cleaned = " ".join(prompt.strip().split())
    if not cleaned:
        return "the prompt"
    if len(cleaned) <= 110:
        return cleaned
    cut = cleaned[:110].rsplit(" ", 1)[0].strip()
    return cut or cleaned[:110]


def _extract_snippet(pattern: re.Pattern[str], context_text: str) -> str | None:
    match = pattern.search(context_text)
    if not match:
        return None
    snippet = " ".join(match.group(1).split())
    if not snippet:
        return None
    return snippet[:100].rstrip(" ,.;:")


def _compose_demo_text(
    *,
    job: JobRecord,
    slot_id: str,
    round_index: int,
    persona: str,
    context_text: str,
) -> str:
    rng = _stable_rng(job.job_id, slot_id, round_index, persona, job.spec.prompt)
    style = _ROLE_STYLES[_persona_bucket(persona)]
    focus = _focus_text(job.spec.prompt)
    nearest = _extract_snippet(_NEAREST_RE, context_text)
    furthest = _extract_snippet(_FURTHEST_RE, context_text)

    opener = _pick_one(rng, style["openers"])
    if round_index <= 1:
        body = _pick_one(rng, style["round_one"])
        tail = (
            f"Applied to \"{focus}\", that gives the chorus a cleaner first answer with less room for confident drift."
        )
        return f"{opener} {body} {tail}"

    body = _pick_one(rng, style["follow_up"])
    context_bits: list[str] = []
    if nearest:
        context_bits.append(f'The strongest shared thread is "{nearest}".')
    if furthest:
        context_bits.append(f'The dissent worth preserving is "{furthest}".')
    if not context_bits:
        context_bits.append(
            "The prior round added enough signal to refine the answer instead of repeating the first draft."
        )
    close = (
        f"For \"{focus}\", I would carry that into the next round as a concrete operating rule, not just a nice-sounding principle."
    )
    return f"{opener} {' '.join(context_bits)} {body} {close}"


async def invoke_demo_completion(
    *,
    job: JobRecord,
    slot_id: str,
    round_index: int,
    persona: str,
    context_text: str,
) -> CompletionResult:
    rng = _stable_rng("latency", job.job_id, slot_id, round_index)
    start = perf_counter()
    await anyio.sleep(0.18 + rng.random() * 0.42)
    scripted = SCRIPTED_DEMO_ANSWERS.get(slot_id)
    if scripted is not None and SCRIPTED_TRIGGER in job.spec.prompt.lower() and round_index <= 1:
        text = scripted
    else:
        text = _compose_demo_text(
            job=job,
            slot_id=slot_id,
            round_index=round_index,
            persona=persona,
            context_text=context_text,
        )
    latency_ms = int((perf_counter() - start) * 1000)
    return CompletionResult(
        ok=True,
        text=text,
        finish_reason="stop",
        latency_ms=latency_ms,
    )
