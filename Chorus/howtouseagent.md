# Running DistLM Peer Agents

## Prerequisites

1. Ollama running with `qwen2.5:0.5b`:
   ```bash
   ollama pull qwen2.5:0.5b
   OLLAMA_HOST=0.0.0.0 ollama serve   # bind to all interfaces so peers can reach you
   ```

2. Orchestrator running:
   ```bash
   uvicorn orchestrator.main:app --reload --port 8000
   ```

## Run peer agents

```bash
# 1 agent (connects to local orchestrator)
python -m agent_backend.node

# 10 agents in parallel
python -m agent_backend.node --count 10

# Register your LAN IP so the master sees it
python -m agent_backend.node --count 10 --ip 10.232.35.143

# Point at a remote orchestrator
python -m agent_backend.node --server http://10.232.35.143:8000 --count 5
```

Each agent will:
- Connect to the signaling server via WebSocket
- Register as a peer (visible in the frontend)
- Wait for a `job_envelope` (prompt broadcast from the master)
- Call local Ollama (`qwen2.5:0.5b`) with the prompt
- Send the response back to the prompter
- Auto-reconnect if disconnected
