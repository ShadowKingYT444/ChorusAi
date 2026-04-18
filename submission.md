## Inspiration

We kept asking: why does every AI query funnel through one company's GPU cluster? A single centralized endpoint means a single point of failure, a single perspective, and a single entity capturing all the value. Meanwhile, millions of consumer GPUs sit idle running Ollama models overnight.

Chorus started from a simple question: **what if LLMs could debate each other?** Not as a gimmick, but as an inference primitive. Ensemble methods crush single-model accuracy in classical ML. Mixture-of-experts architectures dominate modern LLM design. Yet when it comes to *serving* answers, we still ask one model, one time, and hope for the best. We wanted multi-round, multi-perspective reasoning where disagreement is a feature, not a bug, and where every contributor earns a fair share.

The deeper inspiration is economic. Open-source models are now good enough to compete on many tasks, but there's no marketplace connecting people who *run* them to people who *need* answers. Chorus is that marketplace: distributed inference with built-in incentive alignment.

## What it does

Chorus is a **distributed LLM swarm orchestration platform**. Users submit a prompt and a bounty. The orchestrator recruits peer-hosted agents, each running their own local model (via Ollama), assigns each a unique persona (skeptic, optimist, analyst, contrarian), and runs multiple rounds of debate.

**Round 1** &mdash; every agent answers the prompt independently.

**Rounds 2+** &mdash; the orchestrator embeds all responses using sentence-transformers (all-MiniLM-L6-v2, 384-dim), computes a k-nearest-neighbor graph in embedding space, and injects each agent's *nearest* neighbor (consensus voice) and *furthest* neighbor (dissenting voice) into its next-round context. Agents adapt their reasoning based on what others said.

After the final round, the system:

1. **Scores impact** &mdash; each agent earns consensus points ($C_i$, how often cited as nearest neighbor) and dissent points ($F_i$, how often cited as furthest neighbor).
2. **Settles payouts** &mdash; 75% of the bounty pool is split equally (fairness floor); the remaining 25% is allocated proportionally by weighted impact:

$$\text{payout}_i = \underbrace{0.75 \cdot \frac{\text{pool}}{n}}_{\text{floor}} + \underbrace{0.25 \cdot \text{pool} \cdot \frac{w_c \cdot C_i + w_f \cdot F_i}{\sum_j (w_c \cdot C_j + w_f \cdot F_j)}}_{\text{impact bonus}}$$

where $w_c = 1.0$ and $w_f = 0.5$.

3. **Signs a cryptographic receipt** &mdash; the orchestrator Ed25519-signs the settlement JSON so any participant can verify the payout was computed honestly.
4. **Synthesizes a final answer** &mdash; a moderator pass combines the strongest contributions into one coherent response.

A real-time Next.js dashboard lets you watch the debate unfold: live WebSocket streams of agent responses, an interactive 3D node graph (Three.js + simplex noise) visualizing the consensus topology, and Recharts panels showing per-round metrics.

## How we built it

**Three-tier architecture:**

| Layer | Stack | Role |
|-------|-------|------|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Three.js, Framer Motion, Recharts, shadcn/ui | Dashboard, 3D consensus graph, real-time event stream |
| Orchestrator | Python 3.11, FastAPI, aiosqlite, httpx, numpy, sentence-transformers, cryptography | Job coordination, embedding-space kNN, watchdog, payout settlement, Ed25519 signing |
| Agent peers | Ollama (qwen2.5:0.5b default), FastAPI agent runner, WebSocket signaling | Local LLM inference, persona-conditioned completions |

**Key implementation decisions:**

- **OpenAI-compatible agent protocol.** Every peer exposes `POST /v1/chat/completions`. This means *any* OpenAI-compatible endpoint (Ollama, vLLM, llama.cpp, commercial APIs) works as a Chorus peer with zero code changes.
- **Dual embedding backends.** Production uses sentence-transformers/all-MiniLM-L6-v2 for semantic similarity. CI/testing uses a SHAKE256 hash-based backend that produces deterministic 384-dim vectors with no ML dependencies. Swappable via one env var (`ORC_EMBEDDING_BACKEND`).
- **Watchdog heuristics.** Before scoring, completions are vetted for quality: short outputs (<12 chars), exact duplicates, refusal patterns, and a novel *residual prompt cosine* check &mdash; if `cos(\mathbf{r} - \mathbf{p}, \mathbf{p}) > 0.8` (where $\mathbf{r}$ is the response embedding and $\mathbf{p}$ is the prompt embedding), the response is likely echoing the prompt rather than adding value. Two consecutive failures trigger slot pruning.
- **Sandboxed invoker.** SSRF guards block metadata endpoints and private ranges. An optional host allowlist (`ORC_ALLOWED_HOSTS`) and per-peer semaphores (default concurrency = 2) prevent abuse.
- **Largest remainder rounding.** Payout amounts are converted to integer cents, floored, and remaining cents distributed by highest fractional remainder &mdash; guaranteeing the pool sums exactly with no rounding loss.

**Deployment:** One-click Vercel deploy for the frontend, Railway (Docker, Alpine Python 3.11) for the orchestrator with a 5-minute health check timeout to account for model warmup.

## Challenges we ran into

**Embedding-space consensus is noisy with small models.** Our default peer model (qwen2.5:0.5b) produces short, sometimes repetitive responses. Early iterations had the kNN graph collapse into a single cluster where every agent was everyone else's nearest neighbor. We tuned by capping injected context to 200 characters and raising the watchdog's residual-cosine threshold to filter echo responses.

**WebSocket fanout at scale.** The orchestrator streams events to the frontend via per-job WebSocket channels. When we tested with 20+ concurrent agents, event ordering became non-deterministic and the UI rendered stale state. We solved this with buffered replay &mdash; late-joining subscribers receive the full event history &mdash; and sequenced event IDs.

**Cross-platform development.** Building on Windows with a Linux-targeting Docker deployment surfaced path separator issues, `chmod` no-ops for Ed25519 key files, and shell quoting mismatches. We added platform guards and tested both locally and in Railway's container environment.

**Cold-start latency.** sentence-transformers loads ~90MB of weights on first embed call. On Railway's free tier, the first job after a cold deploy takes 15-20 seconds. We mitigated this with the hash embedding backend for demos and a /health endpoint that pre-warms the model on startup.

**Incentive game theory.** Getting the payout formula right required iteration. Pure consensus weighting encouraged groupthink. Pure dissent weighting rewarded nonsense. The 75/25 floor-plus-impact split with $w_f = 0.5 \cdot w_c$ was the balance that rewarded both alignment *and* novel perspectives without perverse incentives.

## Accomplishments that we're proud of

- **The kNN context injection loop actually works.** Agents genuinely refine their answers across rounds when shown what peers said. Round 3 responses are measurably more nuanced than round 1.
- **Cryptographic settlement receipts.** Every payout is Ed25519-signed and independently verifiable. This is the foundation for trustless, auditable AI marketplaces.
- **The 3D consensus graph.** Fibonacci-sphere node layout, cosine-distance edges, flash-on-message animations, and interactive orbit controls &mdash; it makes distributed inference *tangible*. You can see clusters form and outliers emerge in real time.
- **Zero vendor lock-in.** Any OpenAI-compatible endpoint is a valid peer. Ollama, vLLM, llama.cpp, or GPT-4 behind a proxy &mdash; all work without modification.
- **The watchdog's residual prompt cosine.** A simple but effective heuristic for detecting when an LLM is parroting the question back instead of reasoning. We haven't seen this technique used elsewhere.

## What we learned

- **Embedding-space operations are surprisingly powerful for orchestration.** kNN over completion embeddings is a cheap, model-agnostic way to measure agreement and disagreement without any fine-tuning or prompt engineering.
- **Incentive design is harder than systems design.** The payout formula went through five iterations. Small weight changes ($w_f$ from 0.3 to 0.5) dramatically shifted agent behavior in multi-round games.
- **Small models need guardrails.** 0.5B parameter models produce creative output but require aggressive watchdog filtering. The quality floor matters more than the quality ceiling when you're aggregating across many agents.
- **WebSocket architecture needs replay from day one.** Retrofitting event replay onto a fire-and-forget stream was painful. Design for late joiners up front.
- **One-click deploy is a feature, not a nice-to-have.** The Vercel deploy button and Railway Dockerfile got us from "works on my machine" to "try it yourself" in minutes. That accessibility matters for hackathon demos and for real adoption.

## What's next for Chorus

- **On-chain settlement.** Replace the Ed25519 receipt system with smart contract escrow so bounties are locked and payouts are trustlessly enforced. The cryptographic receipt format was designed with this migration in mind.
- **Reputation system.** Track agent performance across jobs &mdash; agents with high historical impact scores get priority placement and higher effective bounty shares.
- **Adaptive round count.** Instead of fixed rounds, use embedding-space convergence (when the kNN graph stabilizes) as a stopping criterion. Stop debating when consensus is reached.
- **Heterogeneous model bonuses.** Weight impact scores by model diversity &mdash; a 70B model agreeing with a 0.5B model is more meaningful than two 0.5B models agreeing.
- **Streaming inference.** Currently agents return full completions. Streaming token-by-token would let the UI show live typing and reduce perceived latency.
- **Privacy-preserving aggregation.** Explore secure aggregation or differential privacy so the orchestrator can compute consensus without seeing raw completions &mdash; enabling use cases with sensitive data.

## Built With

- **Languages:** Python, TypeScript
- **Frontend:** Next.js 16, React 19, Tailwind CSS 4, Three.js, Framer Motion, Recharts, shadcn/ui, simplex-noise
- **Backend:** FastAPI, uvicorn, httpx, aiosqlite, Pydantic
- **ML / Embeddings:** sentence-transformers (all-MiniLM-L6-v2), numpy
- **Cryptography:** Ed25519 (Python `cryptography` library)
- **LLM Inference:** Ollama, qwen2.5
- **Databases:** SQLite (WAL mode)
- **Deployment:** Docker, Railway, Vercel
- **Protocols:** WebSocket, OpenAI-compatible REST API

---

## Demo Script (2 Minutes)

> Target: screen recording with voiceover. Keep transitions tight.

**[0:00 - 0:15] Hook + Problem**

*Show a single ChatGPT prompt returning one answer.*

"Every AI query today goes to one model, one time, and you hope for the best. What if instead of trusting a single perspective, you could have an entire swarm of AI agents debate your question and pay only for the best answers?"

**[0:15 - 0:30] Intro + Architecture**

*Cut to the Chorus landing page. Show the wave background animation and the "What should the chorus debate?" welcome screen.*

"This is Chorus &mdash; a distributed LLM swarm. Users post a prompt with a bounty. Peer-hosted agents running their own local models compete across multiple rounds of debate. An orchestrator scores them by consensus *and* originality, then settles payouts with cryptographic receipts."

**[0:30 - 0:55] Launch a Job**

*Type a prompt: "What's the most underrated approach to reducing AI hallucinations?" Adjust sliders: 5 agents, 3 rounds, $0.50 bounty. Click Launch.*

"I'll ask five agents to debate hallucination reduction. Each gets a different persona &mdash; skeptic, optimist, analyst, contrarian. Three rounds of discussion. Half-dollar bounty."

*Show the launching animation with pulsing dots.*

**[0:55 - 1:20] Watch the Debate**

*Switch to the /app dashboard. Show the 3D node graph lighting up as agents respond. Point to the event feed showing round progression.*

"Round one &mdash; everyone answers independently. Now watch round two. The orchestrator embeds every response, computes a nearest-neighbor graph, and injects the most *similar* and most *different* peer responses into each agent's context. They're reacting to each other now."

*Drag-rotate the 3D graph. Highlight edge connections forming between nodes.*

"These edges represent semantic similarity. Clusters are forming &mdash; but see that outlier? That's a contrarian agent adding a perspective no one else considered."

**[1:20 - 1:45] Settlement + Results**

*Navigate to the results view. Show the payout breakdown table.*

"Three rounds complete. Settlement: 75% of the bounty is split equally &mdash; everyone gets a fair baseline. The remaining 25% goes to agents with the highest *impact* &mdash; measured by how often they were cited as nearest neighbor or furthest neighbor. Consensus *and* dissent both earn rewards."

*Highlight the Ed25519 receipt.*

"Every payout is Ed25519-signed. Any participant can independently verify the orchestrator computed the settlement honestly. This is the foundation for trustless AI marketplaces."

**[1:45 - 2:00] Wrap + Vision**

*Pull back to show the full dashboard with the 3D graph, metrics, and final synthesized answer.*

"Chorus turns distributed inference into a competitive, transparent marketplace. Any Ollama node can be a peer. No vendor lock-in. No single point of failure. Just a swarm of models debating until the best answer emerges. Distributed AI, fairly compensated."

*Fade to Chorus logo.*
