# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository shape

The entire working project lives in `Chorus/`. The repo root only holds deploy
artifacts (`Dockerfile`, `railway.toml`, `README.md`, `submission.md`). Do all
development from inside `Chorus/`.

```
Chorus/
  orchestrator/    FastAPI signaling + round engine (self-hosted Python service)
  agent_backend/   Agent node runtime that connects back to the orchestrator
  frontend/        Next.js 16 + React 19 UI, deployed to Vercel
  tests/           Pytest suite (unit + @pytest.mark.integration)
  scripts/         Simulation, training, and demo helpers
  bingusLM/        Model code (`distlm` package per AGENTS.md)
```

## Commands

Run from `Chorus/` unless noted.

### Backend (Python, 3.11+)

```
python -m pip install -e ".[dev]"                              # base + pytest
python -m pip install -e ".[dev,agent,ml,dolly,train]"         # all extras
python -m uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --reload
python -m uvicorn tests.fixtures.echo_agent:app --host 127.0.0.1 --port 8010
pytest -q                                                       # default suite
pytest tests/test_e2e_full_system.py -v -m integration          # integration only
pytest tests/test_invoker.py::test_name -v                      # single test
```

`tests/conftest.py` forces `ORC_EMBEDDING_BACKEND=hash` and
`ORC_RATELIMIT_BYPASS=1` for speed/determinism — keep that unless a test
explicitly needs MiniLM.

### Frontend (Next.js 16, React 19)

```
cd frontend
npm install
npm run dev            # next dev
npm run build          # next build
npm run lint           # eslint (flat config at eslint.config.mjs)
```

No dedicated Python formatter is configured; match surrounding style.

### Local end-to-end

The signaling backend + a fake agent run as two uvicorn processes. A scripted
smoke path is `scripts/demo_smoke.sh` (bash) / `scripts/demo_smoke.ps1`.
`scripts/run_distlm_sim.py` runs the in-process simulation without networking.

### Deploy

Frontend deploys to Vercel with **Root Directory = `Chorus/frontend`** (not the
repo root — wrong root is the #1 cause of 404 NOT_FOUND). Backend deploys from
the repo-root `Dockerfile` (Railway: `railway.toml`, expects a `/data` volume
for the SQLite DB and Ed25519 key).

## Architecture

Chorus is a **peer swarm orchestrator**: browsers (prompters) submit jobs, a
central Python signaling server fans the prompt out to peer-hosted LLMs
(Ollama / OpenAI-compatible), runs scored rounds, and returns a merged answer
with signed receipts.

### Orchestrator (`orchestrator/`)

- `main.py` — FastAPI app. Holds the WebSocket connection registry
  (`_ws_by_peer_id`, `_active_websockets`) and a per-job response buffer so
  prompters can reconnect and replay missed `job_response` payloads.
- `engine.py` — `RoundEngine` drives jobs through rounds: dispatches to peers
  via `AgentInvoker`, scores responses with `EmbeddingService`, applies the
  `Watchdog`, produces a moderator merge (uses `MERGE_SYSTEM_PROMPT`), and
  attaches signed receipts via `payout.attach_receipt`.
- `store.py` — `PeerRegistry` (in-memory peer state) + `JobStore` (SQLite via
  `aiosqlite`). DB path from `CHORUS_DB_PATH`.
- `broadcast_completions.py` / `invoker.py` — HTTP clients that hit each peer's
  OpenAI-compatible `/chat/completions` endpoint.
- `identity.py` + `crypto.py` — Ed25519 signing. Key file path from
  `ORC_KEY_PATH`; an ephemeral key is generated if the file is missing (fine
  for dev, not portable across restarts).
- `ratelimit.py` — `POST /jobs` rate limiter, bypassed via
  `ORC_RATELIMIT_BYPASS=1` in tests.
- `models.py` — Pydantic models shared by HTTP and WebSocket handlers. Keep
  wire-format changes here rather than passing loose dicts.
- `lifespan.py` + `logconfig.py` — FastAPI lifespan (startup/shutdown) and
  structured request-id logging.

### Agent backend (`agent_backend/`)

Peer-side runtime. `node.py` dials the orchestrator WebSocket, registers the
peer, responds to `job_request` messages by calling the local LLM, and sends
back `job_response`. `agent_invoke.py` exposes the OpenAI-compatible invoke
surface used in tests. `identity.py` manages the peer's Ed25519 key.

### Frontend (`frontend/src/`)

Next.js App Router. Routes live under `src/app/` (including `/setup`, `/join`,
`/app`, and `/api/*` route handlers). Shared UI in `src/components/`, hooks in
`src/hooks/` (`use-*.ts`), helpers in `src/lib/`. The base URL of the
orchestrator comes from `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` at build time;
if unset, users paste a URL at `/join` which is cached in session storage.

### Environment variables

| Var | Where | Purpose |
|---|---|---|
| `ORC_CORS_ORIGINS` | orchestrator | Comma-separated allowed origins. Required for a hosted frontend to call the backend. |
| `CHORUS_DB_PATH` | orchestrator | SQLite path (default `chorus.db`, Dockerfile sets `/data/chorus.db`). |
| `ORC_KEY_PATH` | orchestrator | Ed25519 signing key path. |
| `ORC_LAN_MODE` | orchestrator | `1` for local/LAN dev, `0` for public deploys. |
| `ORC_OPERATOR_TOKEN` | orchestrator | Bearer token gating operator endpoints. |
| `ORC_EMBEDDING_BACKEND` | orchestrator | `hash` (fast, default in tests/Docker) or `minilm`. |
| `ORC_RATELIMIT_BYPASS` | orchestrator | Set to `1` in tests. |
| `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` | frontend | Orchestrator URL baked in at build. |

## Conventions

- Python: 4-space indent, type hints, `snake_case`. Keep protocol/API models
  as Pydantic types in `orchestrator/models.py`; don't pass loose dicts across
  module boundaries.
- Frontend: React exports in `PascalCase`, component files kebab-case
  (`top-bar.tsx`), hooks named `use-*.ts`. Lint via `frontend/eslint.config.mjs`.
- Tests: files named `test_*.py`. Use `@pytest.mark.integration` for full-stack
  tests that spawn processes / hit the ASGI app. `tests/fixtures/echo_agent.py`
  is the canonical stub peer.
