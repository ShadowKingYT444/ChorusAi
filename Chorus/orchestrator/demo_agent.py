from __future__ import annotations

import hashlib
import random
import re
from time import perf_counter

import anyio

from orchestrator.models import CompletionResult, JobRecord

DEMO_COMPLETION_SCHEME = "demo://"
SYNTHETIC_COMPLETION_SCHEME = "synthetic://"

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
