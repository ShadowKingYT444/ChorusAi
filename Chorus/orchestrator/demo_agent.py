from __future__ import annotations

import hashlib
import random
import re
from time import perf_counter

import anyio

from orchestrator.models import CompletionResult, JobRecord, SlotRoundAudit

DEMO_COMPLETION_SCHEME = "demo://"
SYNTHETIC_COMPLETION_SCHEME = "synthetic://"

SCRIPTED_TRIGGER = "rural clinic"
DEVELOPER_INCIDENT_TRIGGER = "midnight auth outage"
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

_STOPWORDS = {
    "a","an","the","and","or","but","if","in","on","at","to","of","for","with",
    "is","are","was","were","be","been","being","do","does","did","has","have","had",
    "we","you","they","it","this","that","these","those","our","your","their",
    "from","as","by","into","about","over","under","than","then","so","because",
    "should","would","could","can","will","shall","may","might","must",
    "what","when","where","why","how","who","which","whose",
    "not","no","yes","vs","versus","between","among","via","per","plus",
    "i","me","my","us","them","there","here",
    "also","just","only","more","most","less","least","much","many","some","any",
    "like","such","etc","eg","ie",
}


_PERSONA_FRAMES: dict[str, dict[str, list[str]]] = {
    "skeptical": {
        "openings": [
            "Before anyone commits to {focus}, I want the failure modes on paper. In a risk-weighted read the question is not whether this can work on a good day; it is what the worst plausible day looks like and who is holding the pager when it arrives.",
            "My first reaction to {focus} is to ask where the landmines are. The enthusiasm in a framing like this usually hides two or three load-bearing assumptions that will quietly decide the outcome, and those deserve daylight before any schedule gets drawn.",
            "I read {focus} as a risk-management problem first, design problem second. The interesting work is identifying the conditions under which this blows up, because those conditions define the guardrails you actually need.",
            "Let me sharpen the question around {focus}. The real prompt is not 'does this work,' it is 'what has to be true for this to work, and what happens to us if any of those premises turn out to be wrong.'",
            "Teams wrestling with a decision like {focus} tend to underestimate tail risk. I want to name, concretely, the three scenarios that would make a rational observer say this was obviously a bad bet in hindsight.",
        ],
        "bodies": [
            "Write down the non-negotiables. I would require three things before green-lighting anything: an explicit list of conditions under which we do NOT proceed, a measurable rollback criterion with a number attached, and an owner who is accountable if those criteria fire. Without those, this becomes a one-way door with nobody watching it.",
            "Treat the first slice as a shadow deployment. Run the new path in parallel with the existing one, compare outputs, and do not let the new system affect a real decision until you have at least two weeks of divergence data. That is the cheapest way to discover that you misjudged {topic_noun} before it costs you anything real.",
            "Harden the dependency map. Every external service, data source, or human approval that sits on the critical path here is a potential outage in disguise. I want each dependency tagged with its historical failure rate and a written fallback, not an optimistic 'we will handle it if it breaks.'",
            "Budget for the ugly middle. The failure mode I see repeatedly is that phase one works, phase three works, but phase two leaves the system half-migrated for longer than planned. Assume the transition state lasts twice as long as you estimate and ensure it is survivable on its own terms.",
            "Stand up the observability before the feature. If you cannot answer 'is this currently worse than what we had yesterday' within fifteen minutes, you are flying blind. Dashboards, alerts, and a documented on-call runbook are not nice-to-haves; they are the gate.",
            "Do not let excitement substitute for evidence. I want a written note from whoever is closest to the failure mode that says, in plain language, what they would need to see to change their mind. If nobody can articulate that, the team has not done the adversarial thinking yet.",
        ],
        "follow_ups": [
            "That position would be more convincing with a concrete abort criterion attached. Enthusiasm is cheap; a number that triggers a rollback is what earns my trust on {topic_noun}.",
            "The optimistic read is tempting, but it skips the question of who eats the loss when the assumption breaks. I want a named owner of each risk before we move on.",
            "I can live with moving forward, provided the first two weeks are instrumented enough that a regression surfaces in hours, not quarters. Otherwise we will learn the hard way.",
            "The dissent there is closer to my read than the consensus. A non-obvious failure mode is still a failure mode, and a plan that cannot survive the contrarian framing is not ready.",
        ],
    },
    "optimistic": {
        "openings": [
            "There is a practical, unglamorous path through {focus} that ships value in weeks, not quarters. I want to bias the plan toward the smallest usable version and let momentum do the rest.",
            "Reading {focus}, I see a version of this you could put in front of real users in under two weeks if you are ruthless about scope. The trick is resisting the urge to solve every edge case before anyone touches it.",
            "My starting assumption on {focus} is that the cost of waiting is higher than the cost of shipping something imperfect. A working narrow slice teaches you more in a week than a planning doc does in a month.",
            "I want to reframe {focus} as a sequencing question. What is the absolute minimum that proves the idea, what does it unlock, and what does the next slice after that look like? Everything flows from answering those three in order.",
            "The opportunity in {focus} is bigger than most teams admit once you stop treating it as all-or-nothing. A staged rollout, even an ugly one, beats a perfect launch that never happens.",
        ],
        "bodies": [
            "Define the smallest shippable slice in writing. Pick one user, one workflow, one success metric, and carve out a two-week window to get that one slice into production. Everything outside that box goes into a 'phase two' doc and stops being a distraction.",
            "Set up the feedback loop before you write the feature. A channel where early users can report pain, a weekly review of what they actually did versus what you assumed they would do, and a willingness to scrap pieces quickly. That loop is worth more than another planning sprint.",
            "Invest in the on-ramp. Most adoption failures in {topic_noun} are not about the core idea; they are about the first fifteen minutes feeling clunky. Polish the starting experience hard and the middle takes care of itself.",
            "Pick the partner team that already wants this. Enthusiasm is a resource. Run the first iteration with someone who has been asking for exactly this, ship it to them, collect the wins, and use those wins as the internal case study for broader rollout.",
            "Embrace the parallel approach. Ship the new path alongside the existing one, instrument both, let users self-select, and let data end the debate rather than another meeting. This is cheaper and faster than trying to decide in advance.",
            "Resist the urge to over-build the platform before you have a product. Two weeks of duct tape that proves the flow is worth shipping beats a month of framework work that proves nothing. You can always harden later; you cannot recover lost time.",
        ],
        "follow_ups": [
            "The caution there is fair but overfitted to a worst case we have not actually observed yet. I would rather ship a thin slice and learn than model the risk in the abstract.",
            "I agree with the direction and want to push it harder: what is the thirty-day version of this that a real user could touch, and what would we learn from shipping it?",
            "The shared thread is solid. My addition is a forcing function: a public commit date for the first slice, because open-ended timelines on {topic_noun} quietly stretch into nothing.",
            "I will take the risk framing on board, but I want to answer it with instrumentation rather than delay. Ship narrow, measure hard, widen only when the numbers support it.",
        ],
    },
    "analytical": {
        "openings": [
            "Let me decompose {focus} into the components that actually move the outcome. I count roughly three: the system-under-change, the measurable criterion we care about, and the set of external constraints that bound the solution space.",
            "The cleanest way into {focus} is to ask what, specifically, we are optimizing for and what we are willing to trade against it. Without those two numbers written down, every downstream decision becomes opinion dressed as analysis.",
            "I want to model {focus} before I take a position on it. There is a dominant variable here and a set of secondary variables, and most of the disagreement I expect to see is really disagreement about which is which.",
            "My entry point on {focus} is measurement. Name the metric that would tell us, in four weeks, whether this was the right call. If the team cannot converge on one number, the problem is not the proposal; it is that we have not agreed on what success looks like.",
            "Structurally, {focus} looks like a problem with one real bottleneck and a lot of noise around it. Isolating the bottleneck is ninety percent of the work; acting on it once isolated is comparatively mechanical.",
        ],
        "bodies": [
            "Pin down the measurable criterion first. If the decision is 'do X versus Y,' there must be a primary metric (latency, cost per unit, error rate, conversion, whatever fits {topic_noun}) and a ceiling on acceptable regression for every secondary metric. Write those down, get sign-off, then evaluate against them.",
            "Separate capability questions from operational questions. 'Can the system do this' is answerable in a prototype in days. 'Will it hold up at production volume, under failure conditions, with the real data distribution' is an entirely different investigation and deserves its own budget.",
            "Run a bounded experiment before a bounded commitment. Design the smallest test that distinguishes the proposal from its alternatives on the primary metric, pre-register what result would change your mind, then execute it. Most debates about {topic_noun} die quietly once the numbers arrive.",
            "Model the cost curve, not just the current cost. What matters is how the cost of operating this scales with load, with team size, and with time, not where it starts. A cheap system today that scales super-linearly is worse than an expensive system whose cost is flat.",
            "Instrument the unknowns before you touch them. Every assumption in the plan should map to a signal you can observe after launch. If an assumption has no corresponding signal, you have no way to detect when it becomes wrong, and it will.",
            "Be explicit about the reference class. What other teams have attempted this kind of move, what did it actually cost them, and how long did it take? Outside-view base rates beat inside-view optimism almost every time on questions like this.",
        ],
        "follow_ups": [
            "That framing is directionally right, but it needs a number. What, specifically, would we measure, and what threshold would flip the decision? Without that, we are reasoning about vibes.",
            "The peer synthesis is reasonable. My refinement is to attach a falsifier: name the observation that would make us abandon this plan, and agree in advance that we would act on it.",
            "The dissent there is actually a measurement problem in disguise. Once we commit to the primary metric and its tolerance band, the disagreement resolves or it crystallizes into a real design split.",
            "I would tighten the rule rather than broaden it. Fewer, more precise criteria produce better decisions than a long checklist nobody enforces on {topic_noun}.",
        ],
    },
    "contrarian": {
        "openings": [
            "The assumption everyone seems to be taking for granted in {focus} is that the framing itself is correct. I am not sure it is, and I think the non-obvious reframing is where the real answer lives.",
            "Before we pick a side on {focus}, I want to name the load-bearing premise: the entire conversation rests on treating this as an X-versus-Y choice. What if it is neither, and the actual lever is elsewhere?",
            "My read on {focus} is that the interesting question is two steps upstream of the one being asked. The team is optimizing a variable that was chosen by accident, and nobody has audited whether it is the right variable.",
            "I want to push back on the default framing of {focus}. The standard answer here is the one that sounds sophisticated in a meeting, and that is exactly why it deserves suspicion.",
            "Everyone arguing about {focus} is accepting the same unstated constraint. Relax that constraint and the problem changes shape entirely; the obvious solution becomes less obvious and a better one comes into view.",
        ],
        "bodies": [
            "Question the necessity. Half the plans I have seen for {topic_noun} become unnecessary when you ask whether the underlying need is real or inherited. Spend a day steelmanning 'do nothing and reinvest the effort elsewhere' before you commit to the action.",
            "Invert the metric. The team is trying to maximize something; ask instead what the equivalent thing to minimize is, and whether minimizing it changes which approach wins. Often a reframe from 'fastest' to 'most recoverable when wrong' flips the answer.",
            "Look at who benefits from the current framing. Every proposal has a constituency. When the case for {topic_noun} is loudest from the people who would run it, that does not make it wrong, but it does mean an outside voice needs to score the alternative.",
            "Consider the null strategy: what if we explicitly refused to solve this at the system level and pushed it to a product or policy decision instead? Tooling tends to grow where a clearer upstream choice would have been cheaper.",
            "Flip the direction of the arrow. If the plan is to pull capability inward, ask what pushing it outward would look like, and vice versa. The default is rarely chosen; it is inherited, and the inherited default has no evidence attached to it.",
            "Name the thing nobody wants to say out loud. There is usually one constraint in {topic_noun} (politics, a sunk cost, an ego) that is silently shaping the options. Putting it in writing does not solve it, but it stops it from warping the decision invisibly.",
        ],
        "follow_ups": [
            "The consensus is converging too quickly for my taste. When four voices agree this fast on {topic_noun}, it usually means we have skipped the step of interrogating the framing.",
            "I want to take the dissenting thread further. The point is not that the mainstream view is wrong, it is that the mainstream view has not yet earned its confidence here.",
            "Fine, but notice what the agreement assumes: that the question as posed is the right question. I am still not convinced of that, and I would spend another hour stress-testing it before committing resources.",
            "The peer framing is clean but conventional. The unlock is probably the option nobody has proposed yet, and I would rather sit with the discomfort of not-deciding than ratify the first coherent answer.",
        ],
    },
}


def is_demo_completion_base(value: str) -> bool:
    v = value.strip().lower()
    return v.startswith(DEMO_COMPLETION_SCHEME) or v.startswith(SYNTHETIC_COMPLETION_SCHEME)


def _stable_rng(*parts: object) -> random.Random:
    material = "||".join(str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest, "big", signed=False))


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

def scripted_demo_answer_for(prompt: str, slot_id: str) -> str | None:
    if SCRIPTED_TRIGGER not in prompt.lower():
        return None
    return SCRIPTED_DEMO_ANSWERS.get(slot_id)

def _citation_tail(valid_slots: list[SlotRoundAudit], preferred: list[str]) -> str:
    seen: list[str] = []
    available = {slot.slot_id for slot in valid_slots}
    for slot_id in preferred:
        if slot_id in available and slot_id not in seen:
            seen.append(slot_id)
    for slot in valid_slots:
        if slot.slot_id not in seen:
            seen.append(slot.slot_id)
        if len(seen) >= 4:
            break
    return " ".join(f"[{slot_id}]" for slot_id in seen[:4])

def _keywords(prompt: str, limit: int = 3) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-\+']{2,}", prompt)
    seen: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in _STOPWORDS:
            continue
        if low in {t.lower() for t in seen}:
            continue
        seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


def _topic_noun(prompt: str) -> str:
    kws = _keywords(prompt, limit=2)
    if not kws:
        return "this problem"
    if len(kws) == 1:
        return kws[0]
    return f"{kws[0]} and {kws[1]}"


def _paraphrase_clause(prompt: str) -> str:
    cleaned = " ".join(prompt.strip().split())
    trimmed = cleaned if len(cleaned) <= 140 else (cleaned[:140].rsplit(" ", 1)[0].strip() or cleaned[:140])
    return '"' + trimmed.rstrip("?.! ") + '"'


def build_demo_final_answer(*, job: JobRecord, valid_slots: list[SlotRoundAudit]) -> str:
    if not valid_slots:
        return ""

    if DEVELOPER_INCIDENT_TRIGGER in job.spec.prompt.lower():
        return (
            "Use the chorus as a read-only incident board for the human on-call, not an autonomous deploy bot: "
            "one voice reconstructs blast radius, one challenges rollback risk, one proposes the smallest reversible "
            "mitigation, and one preserves the dissent that could save you from a second outage. Require citations to "
            "logs, tests, or flags before any recommendation gets weight, prefer rollback or feature-flag disable "
            "before a live patch, and keep one accountable engineer for the final action. If the swarm cannot explain "
            "the safest move in one screen, it has not earned permission to touch production. "
            + _citation_tail(valid_slots, ["atlas-skeptic", "quasar-engineer", "ember-pragmatist", "vesper-ethicist"])
        )

    rng = _stable_rng("final", job.job_id, job.spec.prompt, tuple(s.slot_id for s in valid_slots))
    focus = _paraphrase_clause(job.spec.prompt)
    topic = _topic_noun(job.spec.prompt)
    buckets = {_persona_bucket(s.persona) for s in valid_slots}

    opener_pool = [
        f"On the question of {focus}, the chorus does not land on a single slogan, but it does converge on a shared operating shape.",
        f"Pulling the threads together on {focus}, the strongest answer is not one recommendation but a small set of principles the voices agree on once the surface disagreement is stripped away.",
        f"Across the voices that weighed in on {focus}, a coherent picture emerges: not a prescription, but a way of holding the decision that survives scrutiny from more than one angle.",
    ]
    close_pool = [
        f"The concrete next step: pick the narrowest slice of {topic} that can be instrumented end to end, agree in advance on the metric and the abort criterion, and ship that slice before reopening the debate.",
        f"If there is one action to take this week, it is to write down the success metric and the abort criterion for {topic} in a single page, get it signed, and let that page govern the next decision rather than another round of argument.",
        f"The action the reader can take now is to draft the two-page plan for {topic} that names the target metric, the rollback trigger, the owner, and the first measurable checkpoint, then circulate it for adversarial review before anyone writes code.",
    ]

    principles: list[str] = []
    if "skeptical" in buckets:
        principles.append(
            f"name the failure mode and the rollback criterion in writing before committing, because the cost of an ugly middle state on {topic} is almost always underestimated"
        )
    if "optimistic" in buckets:
        principles.append(
            f"define and ship the smallest usable slice within a short, public deadline, because a narrow version of {topic} in production teaches more than another month of planning"
        )
    if "analytical" in buckets or not principles:
        principles.append(
            f"pick one primary metric with an explicit tolerance band, and measure against it rather than against opinion, so the decision on {topic} can be settled by data rather than by seniority"
        )
    if "contrarian" in buckets:
        principles.append(
            f"spend an hour steelmanning the framing itself before committing resources, because the most expensive mistakes on {topic} come from solving the wrong question confidently"
        )
    if len(principles) < 3:
        principles.append(
            f"instrument the assumptions, not just the outputs, so that when a premise behind {topic} becomes false you find out in hours rather than in a post-mortem"
        )
    principles = principles[:4]

    dissent_line = ""
    if "contrarian" in buckets or "skeptical" in buckets:
        dissent_line = (
            " The dissenting voice in the room is not noise; it is the reason this answer has any hope of surviving "
            "contact with reality. Treat it as the stress test that earned the rest of the plan its credibility."
        )

    opener = _pick_one(rng, opener_pool)
    close = _pick_one(rng, close_pool)

    principles_prose = (
        "Four operating principles fall out of the discussion. First, "
        + principles[0]
        + "."
    )
    if len(principles) >= 2:
        principles_prose += " Second, " + principles[1] + "."
    if len(principles) >= 3:
        principles_prose += " Third, " + principles[2] + "."
    if len(principles) >= 4:
        principles_prose += " Fourth, " + principles[3] + "."

    body = (
        f"{opener} The disagreement is real, but it lives inside a narrow band: the voices differ on pace and framing, "
        f"not on what a responsible team does next about {topic}."
    )

    tail = _citation_tail(valid_slots, [])
    return f"{body}\n\n{principles_prose}{dissent_line}\n\n{close} {tail}".strip()


def _compose_demo_text(
    *,
    job: JobRecord,
    slot_id: str,
    round_index: int,
    persona: str,
    context_text: str,
) -> str:
    rng = _stable_rng(job.job_id, slot_id, round_index, persona, job.spec.prompt)
    bucket = _persona_bucket(persona)
    frames = _PERSONA_FRAMES[bucket]
    focus = _paraphrase_clause(job.spec.prompt)
    topic = _topic_noun(job.spec.prompt)
    nearest = _extract_snippet(_NEAREST_RE, context_text)
    furthest = _extract_snippet(_FURTHEST_RE, context_text)

    opening = _pick_one(rng, frames["openings"]).format(focus=focus, topic_noun=topic)

    # Pick two distinct body blocks without mutating the pool.
    body_pool = list(frames["bodies"])
    rng.shuffle(body_pool)
    body_a = body_pool[0].format(focus=focus, topic_noun=topic)
    body_b = body_pool[1].format(focus=focus, topic_noun=topic) if len(body_pool) > 1 else ""

    if round_index <= 1:
        paragraph1 = opening
        paragraph2 = body_a
        paragraph3 = body_b
        parts = [p for p in (paragraph1, paragraph2, paragraph3) if p]
        return "\n\n".join(parts)

    reaction_bits: list[str] = []
    if nearest:
        reaction_bits.append(
            f'The nearest peer framed it as "{nearest}", and that is directionally useful but stops a beat short of operational.'
        )
    if furthest:
        reaction_bits.append(
            f'The dissent I am tracking is "{furthest}", and I think it deserves to be taken more seriously than a rebuttal round typically allows.'
        )
    if not reaction_bits:
        reaction_bits.append(
            f"With the first round on the table, the conversation on {topic} is sharper but still a step removed from anything an owner could execute tomorrow."
        )

    follow_up = _pick_one(rng, frames["follow_ups"]).format(focus=focus, topic_noun=topic)

    paragraph1 = f"{opening} {follow_up}"
    paragraph2 = " ".join(reaction_bits)
    paragraph3 = body_a
    return "\n\n".join([paragraph1, paragraph2, paragraph3])


async def invoke_demo_completion(
    *,
    job: JobRecord,
    slot_id: str,
    round_index: int,
    persona: str,
    context_text: str,
) -> CompletionResult:
    rng = _stable_rng("latency", job.job_id, slot_id, round_index)
    scripted = scripted_demo_answer_for(job.spec.prompt, slot_id)
    if scripted is not None and round_index <= 1:
        text = scripted
    else:
        text = _compose_demo_text(
            job=job,
            slot_id=slot_id,
            round_index=round_index,
            persona=persona,
            context_text=context_text,
        )
    start = perf_counter()
    think_s = 0.85 + round_index * 0.22 + rng.random() * 0.95
    transmit_s = min(2.1, max(0.45, len(text) / 340.0))
    await anyio.sleep(think_s + transmit_s)
    latency_ms = int((perf_counter() - start) * 1000)
    return CompletionResult(
        ok=True,
        text=text,
        finish_reason="stop",
        latency_ms=latency_ms,
    )
