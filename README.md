# Chorus — Distributed LLM Swarm

Chorus is a web UI + Python orchestrator for running prompts against a swarm of
peer-hosted local LLMs (Ollama / OpenAI-compatible endpoints). The Next.js
frontend lives under `Chorus/frontend/`; the Python signaling / orchestrator
backend lives under `Chorus/orchestrator/` and the agent runtime under
`Chorus/agent_backend/`.

## One-click deploy (frontend only)

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/YOUR_USERNAME/YOUR_REPO&project-name=chorus&repository-name=chorus&root-directory=Chorus%2Ffrontend&env=NEXT_PUBLIC_ORCHESTRATOR_BASE_URL&envDescription=Optional%20signaling%20backend%20URL)

Steps:

1. Push this repo to GitHub (replace `YOUR_USERNAME/YOUR_REPO` in the button
   URL above with your repo slug).
2. Click **Deploy**.
3. On Vercel's import screen, confirm **Root Directory = `Chorus/frontend`**
   (the button pre-fills it, but double-check).
4. (Optional) Set `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` to your hosted
   orchestrator URL. If you leave it blank, visitors can paste a URL into the
   `/join` page and it will be stored in their browser session.

See [`Chorus/frontend/.env.example`](Chorus/frontend/.env.example) for the full
list of supported env vars.

## Backend (host it yourself)

The Python orchestrator is **not** deployed by the Vercel button — Vercel is
frontend-only. Host it somewhere reachable over HTTP(S) + WebSocket:

- Code: [`Chorus/orchestrator/`](Chorus/orchestrator/)
- After hosting, set `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` in Vercel to its URL,
  **or** leave it unset and let each user paste a URL at `/join`.

## User onboarding

New users should visit `/setup` in the deployed app — the wizard walks through
installing Ollama, pulling a model, and joining the swarm.

## Repo layout

```
Chorus/
  frontend/        Next.js 16 + React 19 UI (deployed to Vercel)
  orchestrator/    Python signaling / broadcast server (self-host)
  agent_backend/   Python agent runtime
```
