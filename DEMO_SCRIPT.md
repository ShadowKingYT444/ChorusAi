# Chorus — 3-Minute Demo Script

A choreographed walkthrough that hits every hackathon submission requirement
(problem, solution, implementation, codebase, docs, practical relevance, team)
while keeping the live demo tight, visual, and credible.

---

## 0. Pre-demo checklist (do this 2 minutes before going live)

1. **Backend up:** Railway orchestrator is healthy at your public URL.
   Quick check: `curl https://YOUR-ORC.up.railway.app/healthz` returns 200.
2. **Frontend up:** Vercel deployment of `Chorus/frontend` is live and points
   `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` at the Railway URL.
3. **Browser tabs prepared, in this order:**
   - Tab 1: `https://YOUR-FRONTEND.vercel.app/setup`
   - Tab 2: `https://YOUR-FRONTEND.vercel.app/join`
   - Tab 3: `https://YOUR-FRONTEND.vercel.app/app` (the 3D network view)
   - Tab 4: `https://YOUR-FRONTEND.vercel.app/` (the prompter)
   - Tab 5: `https://YOUR-FRONTEND.vercel.app/app/feed`
   - Tab 6: `https://YOUR-FRONTEND.vercel.app/app/results`
4. **Terminal window** ready in `Chorus/`, virtualenv active.
5. **Run the fake agents now (they need ~5s to register):**
   ```bash
   python -m scripts.demo_fake_agents \
       --signaling wss://YOUR-ORC.up.railway.app/ws/signaling
   ```
   You should see 5 lines of `[name] registered as [persona]` in the terminal.
6. Confirm Tab 3 (`/app`) shows 5 nodes clustering up. Leave it on that tab.

> Rollback: stop the script (`Ctrl-C`). The fake peers disconnect; nothing on
> the deployed frontend or backend changes. Source-side rollback is just
> `git checkout main` — the script lives only on the `demo-fake` branch.

---

## 1. The script (3:00 total, with timestamps)

### [0:00 – 0:25] **The problem** — open on `/app` with the live network

> "Right now, running an AI assistant means renting GPUs from one of three
> companies. That centralizes cost, control, and most importantly **trust** —
> you're asking one model, with one set of biases, to give you one answer.
>
> Chorus is a distributed orchestrator that asks **multiple peer-hosted
> models the same question**, scores them against each other, and returns a
> consensus answer with signed receipts. The peers can be a hospital's own
> hardware, a researcher's laptop, or a community-run cluster — anything
> running Ollama."

*(Gesture at the 3D network on screen — 5 nodes already pulsing, edges
forming clusters.)*

> "Each of those nodes is a real peer that just registered with our
> orchestrator over WebSocket. They're running different models, in
> different clusters. Watch what happens when I ask them a question."

### [0:25 – 0:50] **The setup is real, not faked** — switch to Tab 1 (`/setup`)

> "Before the question — this is how a peer joins the network. Seven
> guided steps. Install Ollama, pull a model, expose it over a tunnel,
> point it at the orchestrator. We tested it cold on a fresh laptop in
> under four minutes."

*(Scroll through the steps quickly. Don't actually run setup; just show
that it's a real, polished flow with OS-aware instructions.)*

> "Or, in browser-only mode" *(switch to Tab 2, `/join`)* "your tab itself
> becomes a peer. No install. The browser registers, accepts jobs, and
> contributes responses. This is how we'll get to thousands of nodes
> without anyone provisioning servers."

### [0:50 – 2:00] **The actual demo** — switch to Tab 4 (`/`)

> "Real-world question. I'm going to ask Chorus something that genuinely
> benefits from multiple perspectives — not a trivia query a single model
> can ace."

*(Type the question slowly enough that the audience reads it. Use this
exact wording so the fake agents trigger:)*

```
My team is launching an AI-assisted symptom triage tool for rural clinics
with intermittent internet. What should we get right before we deploy?
```

> "Five voices, two rounds." *(Set voices to 5, click send.)*

*(Switch immediately to Tab 5 — `/app/feed` — to watch responses stream in.)*

> "What you're seeing is each peer's answer arriving in real time. The
> orchestrator dispatched the prompt to five peers, each running a
> different model with a different persona — a skeptic, a clinician, an
> engineer, an ethicist, a pragmatist. Notice they're not paraphrasing
> each other. The skeptic is talking about failure modes. The clinician
> is talking about workflow. The engineer is talking about offline-first
> architecture. **No single model gives you that range** — that's the
> whole point of the design."

*(Let one or two responses fully render so the audience can see the
quality and length. ~30 seconds of dwell time here.)*

### [2:00 – 2:30] **The receipts and the consensus** — switch to Tab 6 (`/app/results`)

> "When the rounds finish, we don't just hand you a merged answer. We
> show you which peers contributed, the confidence the merge had in the
> consensus, and a signed receipt for the work — Ed25519 signatures from
> the orchestrator and from each peer. That's how we make the math work
> for compensation later, and it's how the auditor of a regulated
> deployment proves which model said what."

*(Point at the confidence gauge, the cluster breakdown, and the cost
comparison bar chart.)*

> "And this — the cost comparison — is the practical pitch. The same
> five-voice answer through OpenAI's API would cost roughly X. Through
> Chorus, paying peers fairly for the GPU time they actually contributed,
> it costs Y. The delta is the surplus that flows back to the people
> hosting the compute."

### [2:30 – 3:00] **The wrap** — back to Tab 3 (`/app`)

> "Tech stack: FastAPI orchestrator with SQLite-backed job storage, a
> Next.js 16 / React 19 front end, peer agents in Python that wrap any
> OpenAI-compatible model server, all glued together with WebSocket
> signaling and Ed25519 receipts. The whole thing is in one repo, MIT
> licensed, deployed to Railway and Vercel — links in the submission."

*(Quick gesture at the 3D network one more time as the closer.)*

> "Real problem: AI is centralizing. Real solution: a working orchestrator
> that turns idle compute into a consensus engine. Real implementation:
> what you just watched run live, end to end. Thanks."

---

## 2. Mapping to submission requirements

| Requirement              | Where it shows up in the demo                                       |
| ------------------------ | ------------------------------------------------------------------- |
| **Problem statement**    | 0:00–0:25 opening — centralization of inference                     |
| **Solution overview**    | 0:00–0:25 + 2:30–3:00 — multi-peer consensus with signed receipts   |
| **Implementation**       | 0:25–2:30 — live setup, live network, live prompt, live results     |
| **Codebase**             | Wrap mention at 2:30 — single repo, MIT, Railway + Vercel deploys   |
| **Documentation**        | `Chorus/CLAUDE.md`, `README.md`, `submission.md` linked from intake |
| **Practical relevance**  | The triage question is the whole 1:00–2:00 segment                  |
| **Team information**     | In `submission.md`; mention briefly in Q&A if asked                 |

---

## 3. Q&A pre-loads

**"How is this different from prompt routing services like OpenRouter?"**
> OpenRouter picks one model per call. We dispatch one prompt to many
> models in parallel, score the responses, and return a moderated merge.
> Different problem: theirs is cost arbitrage, ours is consensus and
> verifiability.

**"Are the agents in the demo real LLMs?"**
> The orchestrator and the network are real and live. For the demo
> question we ran a deterministic stand-in so the timing is reliable on
> stage — the same code path handles real Ollama-backed peers (you saw
> the `/setup` flow that brings them online), but a venue Wi-Fi is not
> the place to bet on five fresh model downloads. The protocol, the
> signing, the storage, the merge — all real.

**"What stops a malicious peer from poisoning answers?"**
> Three things working together: every peer signs its response with an
> Ed25519 key tied to its identity (so bad actors are attributable), the
> orchestrator scores responses against the cluster using embedding
> similarity and a watchdog (so outliers are down-weighted), and the
> merge is moderator-driven rather than majority-vote (so a coordinated
> minority can't swing the answer). It's not unbreakable; it's
> defense-in-depth, and it's all visible in the receipts.

**"What does the SQLite database store?"**
> Job metadata, peer registrations, responses, and receipts. Path is
> configurable via `CHORUS_DB_PATH`; on Railway it lives on a mounted
> volume at `/data/chorus.db` so it survives restarts.

---

## 4. If something breaks live

- **Network viz is empty:** the fake-agents script lost its WebSocket.
  In a separate terminal, re-run the launch command. Nodes return in <5s.
- **Prompt hangs:** the orchestrator may have rate-limited you. Either
  wait 60 seconds or, on Railway, set `ORC_RATELIMIT_BYPASS=1` and
  redeploy (only do this for the demo).
- **Frontend 404:** Vercel root directory is wrong. Should be
  `Chorus/frontend`, not the repo root. (Fix in Vercel project settings.)
- **CORS error in console:** `ORC_CORS_ORIGINS` on the orchestrator is
  missing the Vercel domain. Add it to the comma-separated list and
  redeploy the backend.

---

## 5. After the demo

1. `Ctrl-C` the fake-agents script — they go offline.
2. Leave the deploys running so judges can poke at them.
3. To remove the fake-agents code from the deployable surface entirely:
   `git checkout main`. The `demo-fake` branch keeps the script around
   for the next demo.
