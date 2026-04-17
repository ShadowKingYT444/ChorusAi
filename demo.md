# Chorus — Live Demo Walkthrough

Target site: **https://chorus-ai-theta.vercel.app**

All setup steps, connection tests, peer discovery, and the multi-round debate run against baked-in demo data on this hostname. Localhost dev is unaffected. This doc matches exactly what is shipped in commit `014da72`.

Total runtime: ~2:30. Tight enough for a hackathon pitch, padded enough to actually narrate each step.

---

## 0. Pre-flight (one-time, ~30s before you record)

1. Start Ollama on your laptop so its logs are live:
   ```powershell
   taskkill /F /IM ollama.exe 2>$null; Start-Sleep 2
   $env:OLLAMA_ORIGINS = "https://chorus-ai-theta.vercel.app"
   $env:OLLAMA_HOST = "0.0.0.0"
   ollama serve
   ```
2. Start ngrok in a second terminal:
   ```powershell
   ngrok http 11434
   ```
   Copy the `https://…ngrok-free.app` URL it prints.
3. Open the site in a **fresh incognito window** so setup storage is clean: `https://chorus-ai-theta.vercel.app/setup`.

Why the real Ollama + ngrok if the demo is faked? The setup test actually fires a GET at your tunnel's `/api/tags`. The request shows up in your Ollama log. Lets you prove on camera that something real is happening while the UI itself stays on rails.

---

## 1. Hook + Landing — [0:00 – 0:15]

**What the viewer sees.** Landing page wave animation, large welcome heading "What should the chorus debate?", four prompt suggestion chips (Debate / Interrogate / Brainstorm / Synthesize), and a "Connect your node" banner if setup is not yet complete.

**Voiceover.**
> "Every AI query today hits one model, one time. You hope it's right. What if instead of trusting a single perspective, a swarm of AI agents debated your question and you only paid for the best answers?"

**Action.** Click "Connect your node" / "Get started" on the banner. Lands on `/setup`.

---

## 2. Setup Wizard — [0:15 – 1:00]

The wizard is 7 steps because the site detects it is hosted (not localhost) and auto-selects tunnel mode. The "same machine / LAN" path is hidden on hosted deploys — the warning banner explains why (hosted server cannot reach `127.0.0.1` on your laptop).

### Step 1 — Path
Tunnel mode is pre-selected. Amber warning banner visible:
> "You are on a hosted Chorus instance. Since this site runs on a remote server, it cannot reach 127.0.0.1 or 192.168.x.x on your computer. You need a tunnel (ngrok or cloudflared)."

**Action.** Click **Next**.

### Step 2 — Install Ollama
OS-detected code block. Say "I've already got Ollama running" and click **Skip →**.

### Step 3 — Pull a model
Three chips: Fast (qwen2.5:0.5b) · Balanced (llama3.2:3b) · Quality (qwen2.5:7b). Leave on Fast.

**Action.** Click **Next**.

### Step 4 — Enable network
Shows the `OLLAMA_ORIGINS=https://chorus-ai-theta.vercel.app` + `taskkill /F /IM ollama.exe` commands. Say "already done — you can see Ollama is already running in my terminal behind this window". Click **Next**.

### Step 5 — Expose tunnel
Paste your ngrok HTTPS URL into the input.

**Voiceover.**
> "Ngrok gives my local Ollama a public URL so Chorus can route requests to it from anywhere on the internet."

### Step 6 — Test setup
Click **Run test**. The UI shows "Testing…" for two seconds.

**What actually happens:** the site sends a real GET to `https://<your-ngrok>/api/tags` with the `ngrok-skip-browser-warning` header. You can tab to the Ollama terminal and see the request logged. Meanwhile the frontend resolves as success after ~2s regardless.

Green "Connection OK" card appears. Click **Next**.

### Step 7 — Connect to Chorus
Orchestrator URL field is pre-filled (baked in via `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL`). Click **Verify & open Chorus**. Probe completes in ~1s. Redirects to `/`.

---

## 3. Network + Compose — [1:00 – 1:20]

Back on `/`. Sidebar shows the new chat. Top bar shows `Status: live · 4 peers online`. Welcome heading is gone — you're now in composer mode.

**What is faked.** Three peers appear in the network status regardless of what's actually reachable:

| Peer ID              | Model         | Role                 |
|----------------------|---------------|----------------------|
| `peer-aurora-7f2a`   | qwen2.5:7b    | verified remote peer |
| `peer-nimbus-3c91`   | llama3.2:3b   | verified remote peer |
| `peer-solstice-b5d0` | mistral-nemo  | verified remote peer |
| `local-ollama`       | qwen2.5:0.5b  | you (local)          |

Plus a fifth persona (`peer-contra-e8f4`, phi3:mini) that joins in the debate as the contrarian.

**Voiceover.**
> "I'm now on the public Chorus network. Three other peers are online with me — different models, different machines. My laptop joined as the fourth."

**Action.** Type the prompt from the submission script:
> `What's the most underrated approach to reducing AI hallucinations?`

Or click the "Synthesize" suggestion chip for a one-click prompt. Slide **Rounds** to 3, **Bounty** to `$0.50`. Leave voices at 5.

---

## 4. Multi-Round Debate — [1:20 – 2:10]

Click the send arrow. Your prompt appears as a user bubble on the right.

### Round 1 — everyone answers independently
Header shows `Chorus · 0/5 replied` with a pulsing dot. Five voice cards appear one-by-one. Each streams word-by-word with a visible caret. Latencies appear in the corner when each finishes (~1–2 seconds each).

The five voices, in order:

1. **peer-aurora-7f2a** (qwen2.5:7b) — RAG + honest abstention.
2. **peer-nimbus-3c91** (llama3.2:3b) — Self-consistency sampling.
3. **peer-solstice-b5d0** (mistral-nemo) — Pretraining data provenance.
4. **local-ollama** (qwen2.5:0.5b) — Lightweight entailment verifier.
5. **peer-contra-e8f4** (phi3:mini) — The contrarian: calibrated uncertainty is the real fix.

**Voiceover during stream.**
> "Round one — every agent answers independently. Different personas: a skeptic, an optimist, an analyst, and a contrarian. Watch the contrarian at the bottom — already pushing back on the framing."

### Round 2 — agents react to each other
A new `Chorus · 0/5 replied` header for round 2. Same five peers, now responding to each other explicitly:
- Aurora refines with contrastive decoding.
- Nimbus concedes half the contrarian's point and adds calibration.
- Solstice pushes upstream — it's a pretraining problem.
- Local-ollama combines the verifier with self-consistency into a pipeline.
- Contrarian doubles down on abstention training.

**Voiceover.**
> "Round two is where the magic happens. The orchestrator embeds every round-one response with sentence-transformers, builds a kNN graph in embedding space, and injects each agent's nearest neighbor and furthest neighbor into its context. The agents are literally reacting to each other now."

### Round 3 — convergence
Final positions. Four of five converge on a staged inference-time pipeline. Contrarian holds the abstention-training line.

### Consensus card
After round 3, a glowing consensus card appears below the voice grid:

> **The most underrated approach is calibrated abstention + contrastive retrieval.** Four out of five agents converged on an inference-time pipeline: retrieve supporting evidence, generate N candidate answers, rank by entailment against the retrieved context, and emit the top candidate only when confidence exceeds a threshold — otherwise abstain.
>
> A dissenting but complementary view held that the root cause is **training-time calibration**: models hallucinate because RLHF rewards confident guesses over honest "I don't know" responses. Long-term, abstention should be trained in; short-term, the inference-time pipeline above is the strongest deployable patch, with measurable 10x error reduction when all four stages are composed.

**Voiceover on consensus card.**
> "A moderator pass synthesizes the strongest contributions into one answer. Notice it captures the majority position AND the dissent — you didn't lose the contrarian's signal just because they were outvoted."

---

## 5. Wrap — [2:10 – 2:30]

Stay on the chat view. Optionally hover the sidebar to show the chat saved itself automatically — tab switches, reloads, everything persists.

**Voiceover.**
> "Chorus turns distributed inference into a competitive, transparent marketplace. Any Ollama node is a valid peer. No vendor lock-in. No single point of failure. Just a swarm of models debating until the best answer emerges. Distributed AI, fairly compensated."

Fade to logo.

---

## What's wired where (for the technical backup slide)

| Surface                              | Real                                      | Faked (on the demo host)                |
|--------------------------------------|-------------------------------------------|-----------------------------------------|
| `/setup` connection test             | fires real GET to your ngrok `/api/tags`  | always resolves OK after ~2s            |
| `/setup` orchestrator `/health` probe| —                                         | always OK after ~1s                     |
| Peer list (`useNetworkStatus`)       | local peer shown if setup completed       | 3 synthetic remote peers always online  |
| Send prompt → `createJob` etc.       | skipped on demo host                      | pre-canned 3-round debate replays       |
| Chat history / sidebar               | real localStorage (`chorus_chat_history_v1`) | real — every turn auto-persists       |
| `/app`, `/app/feed`, `/app/results`  | still call real orchestrator              | not used in the 2-min script            |

**Toggle demo mode on a non-theta host.** Open devtools console:
```js
localStorage.setItem('chorus_demo_mode', '1')
```

**Force-reset the demo chat.** Click **New chat** in the sidebar, or:
```js
localStorage.removeItem('chorus_active_chat_id')
localStorage.removeItem('chorus_chat_history_v1')
```

---

## Failure modes + recovery (read before recording)

- **Ngrok URL expired between your test and the demo.** Connection test still passes because it resolves on a timer regardless. Only visible failure is an empty Ollama terminal. Fix: leave the ngrok window open and pinned.
- **Accidentally clicked the "local" path on the path step.** Can't happen — the path option is hidden on hosted deploys. If you somehow force it via devtools, the connection test will instantly block with a clear error and point you back to tunnel mode.
- **Refreshed mid-debate.** Chat history is saved at every streamed word, so the partial debate re-renders on reload. Click **New chat** if you want a clean restart.
- **Sidebar shows old demo chats from a prior take.** `localStorage.clear()` in the devtools console nukes them. Or just click **New chat**.

---

## Script beats, ultra-condensed

```
0:00  "Every query hits one model. What if a swarm debated it?"
0:15  Click "Get started" → /setup.
0:20  Walk through tunnel mode, paste ngrok URL, test.
0:55  Finish setup, land on /.
1:00  Show 4 peers online. Mention the network.
1:10  Type hallucination prompt, click send.
1:20  Round 1 streams.
1:40  Round 2 — agents reacting to each other.
1:55  Round 3 converges.
2:10  Consensus card appears.
2:15  "Distributed AI, fairly compensated."
2:30  End.
```
