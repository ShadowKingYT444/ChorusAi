# Chorus — Distributed LLM Swarm

Chorus is a web UI + Python orchestrator for running prompts against a swarm of
peer-hosted local LLMs (Ollama / OpenAI-compatible endpoints). The Next.js
frontend lives under `Chorus/frontend/`; the Python signaling / orchestrator
backend lives under `Chorus/orchestrator/` and the agent runtime under
`Chorus/agent_backend/`.

## One-click deploy (frontend only)

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2FShadowKingYT444%2FChorusAi&project-name=chorus&repository-name=chorus&root-directory=Chorus%2Ffrontend&env=NEXT_PUBLIC_ORCHESTRATOR_BASE_URL&envDescription=Optional%20signaling%20backend%20URL)

Steps:

1. Click **Deploy** above.
2. On Vercel's import screen, confirm **Root Directory = `Chorus/frontend`**
   (the button pre-fills it, but double-check — this is the #1 cause of
   404 NOT_FOUND after deploy).
3. (Optional) Set `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` to your hosted
   orchestrator URL. If you leave it blank, visitors can paste a URL into the
   `/join` page and it will be stored in their browser session.

### Fixing an existing deployment that returns 404 NOT_FOUND

A 404 on the deployment root almost always means the Vercel project's
**Root Directory** is not pointing at the Next.js app.

1. Open the project in the Vercel dashboard.
2. **Settings → General → Root Directory** → click **Edit**.
3. Set it to `Chorus/frontend` and save.
4. **Deployments** tab → pick the latest deploy → **⋯ → Redeploy**
   (uncheck "use existing build cache").

Build command, install command, and output directory should all be left on
their Next.js defaults.

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
