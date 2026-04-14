# Repository Guidelines

## Project Structure & Module Organization
The working project lives in `Chorus/`. Core backend logic is split across `orchestrator/` (FastAPI signaling, broadcast, embeddings, payout, store), `agent_backend/` (agent node runtime and sample invoke app), and `distlm/` (model code). The Next.js UI lives in `frontend/`, with routes under `src/app`, shared UI in `src/components`, hooks in `src/hooks`, and helpers in `src/lib`. Use `scripts/` for demos, data prep, and training helpers. Keep tests in `tests/`; `tests/fixtures/echo_agent.py` is the integration stub server.

## Build, Test, and Development Commands
Run commands from `Chorus/` unless noted otherwise.

- `python -m pip install -e .` installs the base backend package.
- `python -m pip install -e ".[dev]"` adds pytest support; add extras only when needed (`agent`, `train`, `dolly`).
- `python -m uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --reload` starts the orchestrator locally.
- `python -m uvicorn tests.fixtures.echo_agent:app --host 127.0.0.1 --port 8010` runs the echo agent used in local end-to-end testing.
- `python scripts/run_distlm_sim.py --context "..." --prompt "..." --agents 3 --rounds 2 --payout 100` runs the local simulation path.
- `cd frontend && npm install && npm run dev` starts the Next.js UI; use `npm run build` or `npm run lint` before shipping frontend changes.

## Coding Style & Naming Conventions
Python uses 4-space indentation, type hints, and `snake_case` for modules, functions, and tests. Keep protocol and API models explicit rather than passing loose dictionaries across boundaries. In the frontend, follow the existing TypeScript style: React exports in `PascalCase`, component files in kebab-case such as `top-bar.tsx`, and hooks named `use-*.ts`. Frontend linting is enforced with `frontend/eslint.config.mjs`; no dedicated Python formatter is configured here, so match surrounding style closely.

## Testing Guidelines
Use `pytest -q` for the default suite and `pytest tests/ -v` for targeted debugging. Integration coverage is marked with `@pytest.mark.integration`; run `pytest tests/test_e2e_full_system.py -v -m integration` when you touch orchestration, networking, or agent registration. Name new tests `test_*.py`. Keep the fast default embedding backend from `tests/conftest.py` unless a test explicitly needs MiniLM behavior.

## Commit & Pull Request Guidelines
This snapshot does not include `.git` metadata, so no local commit convention can be inferred from history. Use short imperative commit subjects and keep each commit scoped to one change. PRs should state the affected area (`orchestrator`, `agent_backend`, `frontend`, or `scripts`), list commands run, call out any env var changes, and include screenshots for UI updates. Do not commit secrets from `.env` or `frontend/.env.local`.
