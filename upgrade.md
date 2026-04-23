# Chorus Upgrade Plan

## Executive Summary

Chorus should stop acting like a public "LLM marketplace" demo and become a private swarm review product for high-stakes product and engineering decisions.

The near-term product is:

- paste an RFC, launch plan, architecture proposal, incident review, or policy draft
- run it through a small swarm of role-based reviewers
- get dissent, consensus, and a synthesized verdict
- keep the review record, metrics, and audit trail

The near-term architecture is:

- a control plane that owns routing
- managed anchor workers for baseline quality
- a separate judge/synthesizer tier
- optional tenant-dedicated workers
- optional edge peers only as additive diversity, never as the critical path

The near-term business model is:

- credits based on `agent_count * rounds`
- no public marketplace yet
- no on-chain settlement yet
- no assumption that consumer hardware will supply production-quality inference

The security posture must assume the current codebase is prototype-grade and not safe for an open public network without significant hardening.

## Current Reality

Chorus already has the right primitive:

- multi-round debate
- peer context injection
- live event streaming
- signed settlement previews
- a strong "multiple perspectives" interaction model

Chorus does not yet have the right production shape:

- the browser still participates in worker selection and passes raw endpoint URLs
- the orchestrator directly POSTs to peer-provided addresses
- public REST and WebSocket surfaces are largely unauthenticated
- peer trust is shallow
- payout logic is still demo incentive math, not production metering
- the onboarding flow still assumes manual node setup and public/tunneled model URLs

That means the current repo is a strong prototype for trusted environments, but not a public compute network.

## Product Decision

### What Chorus Should Be

Chorus should be positioned as:

`Private swarm review for high-stakes internal decisions.`

Primary use cases:

- RFC review
- launch readiness review
- architecture tradeoff review
- migration risk review
- incident postmortem review
- product strategy stress test
- security or policy draft critique

Core promise:

- more than one model perspective
- visible disagreement, not fake certainty
- private or tenant-controlled execution
- saved review artifacts

### What Chorus Should Not Be Yet

Do not build these as the primary product in the next phase:

- open public node marketplace
- consumer general-purpose chat app
- on-chain settlement
- token-based supplier economy
- browser agents editing local files
- "100 agents" as the headline value proposition

Those all add major trust, abuse, and product complexity before the core workflow is proven.

## Target Users

Primary users:

- engineering teams
- product teams
- founders
- infra/platform leads
- security-conscious teams that want private review loops

Ideal customer profile:

- already uses AI internally
- already writes specs, RFCs, incident docs, or launch plans
- cares about confidence, review quality, and auditability more than chatbot personality

The first wedge is not "people who want decentralized AI."

The first wedge is "teams who already have important internal decisions and want structured AI critique."

## Product Shape

### V1 Workflow

1. User selects a review template.
2. User pastes a document, prompt, or plan.
3. User chooses review depth:
   - Quick
   - Decision
   - Audit
4. Chorus routes the job to an internal swarm.
5. Chorus returns:
   - key risks
   - dissenting viewpoints
   - synthesized verdict
   - confidence and disagreement markers
   - saved report

### Review Modes

- `Quick`: 3 workers, 1-2 rounds
- `Decision`: 4-6 workers, 2-3 rounds
- `Audit`: 6-8 workers, 3-4 rounds

Do not expose raw worker counts as the main product. Expose outcome tiers.

### Output Format

Every Chorus run should eventually produce:

- executive summary
- strongest argument for the plan
- strongest argument against the plan
- blind spots / failure modes
- final verdict
- evidence or citations when available

## Architecture Decision

### Decision 1: The Control Plane Owns Scheduling

The client should submit intent, not infrastructure.

The frontend must stop selecting peers or passing raw `completion_base_url` values as the normal production path. The browser should send:

- prompt or document
- review mode
- budget
- latency target
- tenant or workspace policy
- privacy/compliance constraints

The control plane should choose workers internally.

### Decision 2: Use a Hybrid Worker Model

Production Chorus should use four worker classes.

#### 1. Anchor Workers

Chorus-managed, always-on, SLA-backed workers.

Purpose:

- guarantee baseline quality
- guarantee jobs can run even with zero community supply
- carry the production workload

#### 2. Tenant-Dedicated Workers

Customer-owned or BYO endpoint workers.

Purpose:

- private inference
- region and compliance scoping
- enterprise trust

#### 3. Judge / Synthesizer Workers

A separate tier for ranking, merging, and safety checks.

Purpose:

- do not let the same class of worker both generate and judge by default
- keep final synthesis quality stable

#### 4. Edge Workers

Browser/LAN/local peers.

Purpose:

- optional diversity
- experimental low-cost capacity
- never the critical path for production

### Decision 3: Production Jobs Must Succeed Without Edge Supply

Chorus cannot depend on random consumer hardware to achieve product quality.

Default production swarm:

- 2 anchor generators
- 1 judge
- optional tenant specialist
- optional edge diversity worker

If all edge supply disappears, the product must still work.

### Decision 4: Replace Raw URL Fan-Out With Work Leases

The current direct invocation model is acceptable for prototype use, but not for a marketplace or production network.

Move toward:

- authenticated worker registration
- capability advertisement
- signed work lease assignment
- worker acknowledgements
- measured usage return
- attestation and health reporting

This avoids the browser acting as scheduler and reduces direct trust in user-supplied endpoints.

## Supply Strategy

### Bootstrap Quality Without Owning Huge Compute

The answer is not "wait until consumers donate enough good GPUs."

The answer is:

- use managed anchors for the critical path
- use tenant-dedicated supply where customers want private inference
- use optional edge workers for diversity only

Suggested default supply mix:

- 70% Chorus-managed anchors
- 20% tenant-dedicated / BYO endpoints
- 10% optional edge supply

That ratio can move later. The rule that matters is:

`managed supply is the default, edge supply is additive`

### What Small Local Models Are Good For

Small local models are weak as the sole answer engine, but still useful for:

- contradiction checks
- failure mode enumeration
- alternative hypotheses
- extraction
- low-cost dissent generation

They should not be marketed as "100 tiny models equals one expert."

## Routing Policy

The router needs both hard filters and soft ranking.

### Hard Filters

- tenant scope
- region
- compliance tags
- trust floor
- workspace policy
- budget ceiling

### Soft Ranking

- price
- latency
- uptime
- recent quality
- timeout/error rate
- diversity contribution
- reputation

### Fallback Order

`anchor -> tenant-dedicated -> edge`

## Trust and Reputation

Current `verified` status is identity bootstrap only. It is not enough for production trust.

Introduce three distinct scores:

- `identity_trust`
- `operational_reputation`
- `quality_reputation`

Suggested policy:

- anchors are always eligible
- tenant-dedicated workers are eligible within tenant scope
- edge workers are excluded from default production unless explicitly enabled

Supplier payouts should eventually depend on:

- measured usage
- uptime
- quality score
- challenge-job performance
- dispute outcomes

Not only on consensus/dissent heuristics.

## Metering and Pricing

### Near-Term Metering

Use `agent-round credits`.

Definition:

- 1 credit = 1 worker participating in 1 round

This is simple, explainable, and compatible with the current system.

Shadow-meter immediately:

- `credits_used = agent_count * rounds`

Also track:

- latency
- prune rate
- completion success
- judge score
- output length
- retries

### Near-Term Packaging

Example packaging:

- `Starter`: 2,000 credits/month
- `Team`: 10,000 credits/month
- overage billed per credit

The exact price can move. The bigger point is:

- bill on workload, not seats, at first
- keep the pricing legible
- avoid token-accurate supplier billing until the control plane and metering are mature

### Supplier Payments

Do not launch real open supplier compensation yet.

Before paying external suppliers, Chorus needs:

- reliable measured usage
- trust tiers
- holdbacks
- dispute handling
- challenge jobs
- fraud detection

## Security and Threat Model

### Top Risks

#### 1. Unauthenticated Control and Data Plane

Current public REST and WebSocket surfaces are too open for internet exposure.

Required response:

- require auth on every route except `/health`
- require authenticated WebSockets
- bind socket sessions to authenticated users/devices/workspaces

#### 2. Malicious Peers

Peers can currently impersonate, poison, or degrade the swarm.

Required response:

- mandatory signed registration
- nonce/timestamp replay protection
- pinned peer keys
- separate user auth from machine peer auth
- trust tiers and revocation

#### 3. SSRF and Network Pivoting

The system currently makes outbound calls to peer/model URLs. That is a core SSRF and lateral-movement risk.

Required response:

- move outbound traffic behind a hardened egress broker
- default-deny destinations
- re-resolve DNS and block private CIDRs by policy
- do not accept arbitrary user/peer-provided URLs in normal production routing

#### 4. Prompt Injection and Peer Poisoning

Peer output becomes input to later rounds. That creates a cross-peer prompt injection surface.

Required response:

- mark peer output as untrusted
- strip or normalize tool-like instructions from peer output
- cap reused context
- use judge prompts that explicitly ignore attempts to alter orchestration policy
- add challenge tests for poisoning behavior

#### 5. Abuse and DoS

Public joins and sockets are easy to flood if not strongly metered.

Required response:

- per-user quotas
- per-peer quotas
- per-workspace budgets
- connection caps
- queue bounds
- message size limits
- heartbeat expiry
- edge WAF/rate limiting

#### 6. Browser Attack Surface

The frontend should not become a weak link.

Required response:

- CSP
- `frame-ancestors`
- `X-Content-Type-Options: nosniff`
- strict referrer policy
- no sensitive data in browser storage
- minimize long-lived secrets on the client

#### 7. Supply-Chain Risk

Do not let research/training trust flow into production.

Required response:

- pinned dependencies
- SBOM
- OSV/advisory scanning in CI
- signed builds
- isolate training code and any use of `trust_remote_code`

## If Chorus Ever Gains File Editing or Code Execution

Do not make browser peers or arbitrary workers directly edit user machines.

If Chorus later expands into code execution, it must happen through:

- isolated VM or container sandboxes
- no host filesystem mounts by default
- no Docker socket access
- restricted outbound network
- malware scanning for uploaded artifacts
- ephemeral credentials
- explicit user approval for write actions
- auditable action logs

In other words:

`remote code work must be a separate product surface with a sandboxed execution model`

It should not be bolted onto the current peer network casually.

## Data Model Changes

Replace thin peer metadata with richer worker metadata.

Add fields such as:

- `worker_class`
- `operator_id`
- `tenant_id`
- `region`
- `compliance_tags`
- `model_family`
- `concurrency`
- `price_card`
- `trust_tier`
- `identity_trust`
- `operational_reputation`
- `quality_reputation`
- `attestation`
- `last_health`
- `queue_depth`

Current schemas are too thin for routing, billing, or supplier governance.

## Product Roadmap

### Phase 0: Stabilize and Reframe

Goal:

- treat Chorus as a private alpha product, not an open network

Actions:

- keep deployment private or behind allowlists/VPN
- remove marketplace-first messaging from the landing experience
- replace "bounty/debate" language with "review/stress-test"
- add review templates
- shadow-meter all runs as credits

Exit criteria:

- private teams can run repeatable review workflows
- saved report format is clear
- credits are visible internally

### Phase 1: Control Plane Ownership

Goal:

- make the orchestrator the scheduler

Actions:

- client submits intent only
- make raw slot registration internal/admin-only
- introduce authenticated workspaces
- add authenticated sockets
- remove public peer address exposure from normal user flows

Exit criteria:

- frontend no longer chooses peers for production jobs
- production jobs can run with zero browser/LAN peers

### Phase 2: Managed Anchor and Judge Tier

Goal:

- guarantee baseline quality

Actions:

- add Chorus-managed anchors
- add explicit judge/synthesizer workers
- split generation and synthesis paths
- add routing policy engine

Exit criteria:

- every paid run succeeds without edge peers
- quality is stable across repeated runs

### Phase 3: Metering, Billing, and Quotas

Goal:

- make usage billable before marketplace economics

Actions:

- expose credits in product
- add workspace quotas
- add ledger and billing events
- support plan limits and overages

Exit criteria:

- users understand how runs consume credits
- Chorus can invoice usage cleanly

### Phase 4: Tenant-Dedicated and BYO Supply

Goal:

- support serious private deployments

Actions:

- tenant-scoped workers
- region/compliance routing
- customer-managed endpoints
- policy-aware routing

Exit criteria:

- enterprise/private workflows do not rely on public edge supply

### Phase 5: Optional Edge Marketplace

Goal:

- allow external supplier participation without making it the product foundation

Actions:

- challenge jobs
- payout holdbacks
- worker reputation
- abuse detection
- stake/ban/revocation controls

Exit criteria:

- edge supply improves economics or diversity without hurting reliability

## Non-Negotiable Product Rules

1. The product must be useful with zero community peers.
2. The control plane chooses workers.
3. Generation and judging are separate concerns.
4. Billing is based on workload before it is based on supplier economics.
5. Security hardening must happen before public network expansion.
6. Edge supply is optional, not foundational.

## Immediate Next Moves

### In the Product

- rewrite landing and onboarding copy around review workflows
- add templates for RFC, launch, architecture, and risk review
- replace explicit bounty framing in the primary UX

### In the Backend

- add authenticated workspaces and tokens
- make production job creation and streaming auth-protected
- begin shadow credit accounting
- start refactoring toward orchestrator-owned routing

### In the Security Model

- lock down public routes and sockets
- add signed peer enrollment
- remove raw endpoint exposure from normal workflows
- define egress policy for outbound model calls

### In the Roadmap

- defer open marketplace work
- defer on-chain settlement
- defer consumer chat ambitions
- defer code-editing/network-execution ambitions until sandboxing exists

## Bottom Line

Chorus can become a real product, but not by betting the company on a public decentralized compute marketplace right now.

The practical path is:

- become a private swarm review product first
- build a real control plane
- use managed anchors for quality
- bill with simple credits
- harden security before public network exposure
- treat external compute supply as a later optimization, not the foundation
