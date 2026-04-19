# Chorus

Distributed LLM swarm. Web UI + Python orchestrator for running prompts across a
bunch of peer-hosted local models (Ollama or any OpenAI-compatible endpoint).

Frontend: `Chorus/frontend/` (Next.js).
Backend: `Chorus/orchestrator/` and `Chorus/agent_backend/` (Python).

## Deploy the frontend

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2FShadowKingYT444%2FChorusAi&project-name=chorus&repository-name=chorus&root-directory=Chorus%2Ffrontend&env=NEXT_PUBLIC_ORCHESTRATOR_BASE_URL&envDescription=Optional%20signaling%20backend%20URL)

Click the button. On the Vercel import screen make sure Root Directory is set
to `Chorus/frontend`. That field is pre-filled but worth double checking, since
a wrong root is the usual reason you get a 404 after deploy.

Optionally set `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` to a hosted backend URL. If
you leave it blank, users just paste a URL at `/join` and it gets cached in
their session.

### If your deploy returns 404 NOT_FOUND

Almost always means the Vercel Root Directory is not pointing at the Next.js
app.

1. Open the project in Vercel.
2. Settings > General > Root Directory > Edit.
3. Set it to `Chorus/frontend` and save.
4. Go to Deployments, pick the latest one, click Redeploy. Uncheck "use
   existing build cache".

Leave build command, install command and output directory at the Next.js
defaults.

Full env var list is in [`Chorus/frontend/.env.example`](Chorus/frontend/.env.example).

## Backend

Vercel only hosts the frontend. You need to run the Python orchestrator
yourself somewhere reachable over HTTP + WebSocket.

Code lives in [`Chorus/orchestrator/`](Chorus/orchestrator/). Once it's up,
either set `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` in Vercel or leave it empty and
have users paste the URL at `/join`.

### Railway

There's a [`Dockerfile`](Dockerfile) and [`railway.toml`](railway.toml) tuned
for Railway in the repo root.

1. New project > Deploy from GitHub repo, point it at this repo.
2. Railway picks up the Dockerfile. First build takes about 3 minutes.
3. Under Settings > Networking > Public Networking, generate a domain. Copy
   the URL (something like `https://chorus-prod.up.railway.app`).
4. Add a Volume under Storage with mount path `/data`. Without this, the
   SQLite job history and the Ed25519 signing key get wiped on every
   redeploy. Identity still works without a volume (ephemeral key on boot)
   but receipts won't be portable.
5. Set environment variables. The one you actually have to set for a hosted
   frontend to reach the backend is `ORC_CORS_ORIGINS`. Without it every
   browser request from Vercel gets blocked by CORS:

   | Env var | Value | Required? |
   |---|---|---|
   | `ORC_CORS_ORIGINS` | `https://<your-vercel-domain>` (comma-separated for multiple) | Yes, for hosted frontend |
   | `CHORUS_DB_PATH` | `/data/chorus.db` | Already set in Dockerfile |
   | `ORC_KEY_PATH` | `/data/orchestrator_ed25519.key` | Already set in Dockerfile |
   | `ORC_LAN_MODE` | `0` for public deploys (default is `1`) | Optional |
   | `ORC_OPERATOR_TOKEN` | Bearer token for operator endpoints | Optional |

6. Back in Vercel, add the env var:

   ```
   NEXT_PUBLIC_ORCHESTRATOR_BASE_URL=https://<your-railway-domain>
   ```

   Apply to Production + Preview and redeploy. `NEXT_PUBLIC_*` vars bake in at
   build time, so you need the rebuild.
7. Go to `/setup` in your Vercel app. The wizard hits `/health` on the
   orchestrator before letting you finish, so if the URL or CORS is off, the
   error on screen tells you what's wrong.

Health check: `GET https://<your-railway-domain>/health` should return
`{"status":"ok"}`. If it doesn't, check the Railway deploy logs.

## Onboarding users

Send new users to `/setup` in the deployed app. The wizard walks them through
installing Ollama, pulling a model, and joining the swarm.

## Layout

```
Chorus/
  frontend/        Next.js 16 + React 19 UI (Vercel)
  orchestrator/    Python signaling + broadcast server (self-host)
  agent_backend/   Python agent runtime
```
