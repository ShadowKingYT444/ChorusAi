# Demo Fake Agents

`python -m scripts.demo_fake_agents` is the demo harness for Chorus.

It now covers three real app paths:

- Network view: registers five fake peers on `/ws/signaling`, so `/peers` and the graph view show a live fleet.
- Join/live signaling flow: each fake peer answers `job_request` and `job_envelope` messages over WebSocket, so `/join` demos do not stall on silent peers.
- Feed/results flow: by default it creates one real `/jobs` run and registers slots with `demo://<peer_id>`, which drives the orchestrator's built-in demo completion path and populates `/ws/jobs/{job_id}`, `/jobs/{job_id}`, and `/chats` through the normal backend path.

## Typical local flow

From `Chorus/`:

```bash
ORC_EMBEDDING_BACKEND=hash python -m uvicorn orchestrator.main:app --host 127.0.0.1 --port 8000
python -m scripts.demo_fake_agents --signaling ws://127.0.0.1:8000/ws/signaling
```

On PowerShell:

```powershell
$env:ORC_EMBEDDING_BACKEND='hash'
python -m uvicorn orchestrator.main:app --host 127.0.0.1 --port 8000
python -m scripts.demo_fake_agents --signaling ws://127.0.0.1:8000/ws/signaling
```

That will:

1. Register five peers.
2. Auto-create one real demo job.
3. Keep peers online so the UI can show network presence and signaling replies.

`ORC_EMBEDDING_BACKEND=hash` is recommended for local demos so the first job does
not stall on MiniLM model download/warm-up.

## Useful flags

```bash
python -m scripts.demo_fake_agents --help
```

Common options:

- `--demo-jobs 0`: presence-only mode.
- `--demo-jobs 2`: create two completed jobs so `/chats` has more history.
- `--job-prompt "..." --job-context "..."`: use a custom first demo job.
- `--exit-after-jobs`: create the demo jobs, wait for completion, then exit. Useful for smoke-style verification.
- `--base-url https://your-host`: override HTTP base if it cannot be derived from `--signaling`.

## Optional seeded history

If you want a local database to start with completed chats before running the live harness:

```bash
python -m scripts.seed_demo --db-path ./chorus.db
```

That only writes SQLite rows; it does not keep peers online. Use it when you want `/chats` populated before the live demo starts.
