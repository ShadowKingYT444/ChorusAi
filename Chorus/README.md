# Chorus

Chorus is a private AI review room for launch decisions, RFCs, architecture tradeoffs, and risk checks. The product combines a Next.js frontend with a FastAPI orchestrator that routes prompts across a managed agent pool, streams round-by-round progress, and produces a final decision report with citations and settlement metadata.

## What ships today

- Workspace-scoped jobs with bearer-token auth on HTTP and WebSocket routes
- Review templates for RFC, launch, architecture, and risk workflows
- Review modes for quick checks, decision memos, and audit-style passes
- Auto routing through managed anchors or registered peers
- Live feed, results, and chat history backed by the orchestrator
- Shadow credit accounting based on `agent_count * rounds`

## Repository layout

| Path | Purpose |
|------|---------|
| `frontend/` | Next.js product UI intended for Vercel |
| `orchestrator/` | FastAPI control plane, round engine, auth, payout, and persistence |
| `tests/` | Backend unit and integration coverage |
| `scripts/demo_smoke.ps1` | Local smoke test for the protected orchestrator flow |
| `tests/fixtures/echo_agent.py` | Minimal OpenAI-compatible agent for local validation |

## Local backend setup

Requirements:

- Python 3.11+
- Node 20+ for the frontend

Install the backend from `Chorus/`:

```bash
python -m pip install -e ".[dev]"
```

Start a local echo agent:

```bash
python -m uvicorn tests.fixtures.echo_agent:app --host 127.0.0.1 --port 18766
```

Start the orchestrator:

```bash
set ORC_EMBEDDING_BACKEND=hash
set ORC_REQUIRE_WORKSPACE_AUTH=1
set ORC_BOOTSTRAP_WORKSPACE_ID=local-dev
set ORC_BOOTSTRAP_TOKEN=chorus-local-dev-token
set ORC_ANCHOR_COMPLETION_BASE_URLS=http://127.0.0.1:18766,http://127.0.0.1:18766,http://127.0.0.1:18766
python -m uvicorn orchestrator.main:app --host 127.0.0.1 --port 8000
```

The bootstrap workspace is suitable for local demos only. For a real deployment, set `ORC_WORKSPACE_TOKENS` with explicit workspace-to-token mappings and rotate those secrets outside the repo.

## Frontend setup

Create `frontend/.env.local`:

```bash
NEXT_PUBLIC_ORCHESTRATOR_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_CHORUS_WORKSPACE_ID=local-dev
NEXT_PUBLIC_CHORUS_WORKSPACE_TOKEN=chorus-local-dev-token
```

Then run:

```bash
cd frontend
npm install
npm run dev
```

For Vercel, deploy the `frontend/` app and provide the same `NEXT_PUBLIC_*` environment variables in the Vercel project settings. The Python orchestrator still needs its own runtime and public base URL.

## Smoke test

From `Chorus/`, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\demo_smoke.ps1
```

The smoke script starts a local orchestrator plus echo agent, authenticates with a bootstrap workspace, registers auto-routed anchors, waits for completion, verifies `/jobs`, `/chats/{job_id}`, and `/chats`, and fails fast with tailed logs if anything breaks.

## Test commands

Backend tests:

```bash
pytest tests/test_workspace_auth.py tests/test_e2e_full_system.py tests/test_engine_rounds.py tests/test_signaling_phases12.py tests/test_clusters_endpoint.py
```

Frontend build:

```bash
cd frontend
npm run build
```

## Deployment notes

- `frontend/vercel.json` is configured for a standard Next.js deployment.
- The frontend expects a reachable orchestrator URL and, for protected environments, workspace credentials.
- The orchestrator accepts manual slot registration, but the product flow is built around auto routing through `ORC_ANCHOR_COMPLETION_BASE_URLS` and/or discovered peers.
- Generated databases, local keys, and swarm state should stay uncommitted.

## Known gaps

- The orchestrator is still optimized for single-tenant or tightly controlled multi-tenant deployments.
- Durable production-grade persistence, billing rails, and deep operational telemetry still need hardening beyond the current productization pass.
