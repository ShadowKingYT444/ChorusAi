#!/usr/bin/env bash
# Chorus - start all three services
# Usage: ./start.sh
# Stop:  Ctrl+C kills all background jobs

set -e

PUBLIC_IP="66.129.246.4"
VENV=".venv/bin"

echo "==> Starting Chorus (public IP: $PUBLIC_IP)"
echo ""

# Load Python env vars
export $(grep -v '^#' .env | grep -v '^$' | xargs)

# ── 1. Signaling server ───────────────────────────────────────────────────────
echo "[1/3] Signaling server   → http://$PUBLIC_IP:8000  (ws://$PUBLIC_IP:8000/ws/signaling)"
$VENV/uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 &
SIGNAL_PID=$!

# Give it a moment to bind
sleep 1

# ── 2. Agent node runner ──────────────────────────────────────────────────────
echo "[2/3] Agent node runner  → connecting to ws://localhost:8000/ws/signaling"
CHORUS_SIGNALING_URL="ws://localhost:8000/ws/signaling" \
CHORUS_OLLAMA_URL="http://localhost:11434" \
CHORUS_MODEL="qwen2.5:0.5b" \
CHORUS_NUM_AGENTS=3 \
  $VENV/python -m agent_backend.node &
NODE_PID=$!

# ── 3. Frontend ───────────────────────────────────────────────────────────────
echo "[3/3] Frontend           → http://$PUBLIC_IP:3000"
cd frontend
npm run dev -- --hostname 0.0.0.0 --port 3000 &
FRONTEND_PID=$!
cd ..

echo ""
echo "All services started."
echo "  Signaling:  http://$PUBLIC_IP:8000"
echo "  Frontend:   http://$PUBLIC_IP:3000"
echo "  Ollama:     http://localhost:11434 (must already be running)"
echo ""
echo "  NOTE: Make sure your router forwards TCP ports 3000 and 8000 to $(ipconfig getifaddr en0 2>/dev/null || echo '10.232.35.22')"
echo ""
echo "Press Ctrl+C to stop all services."

cleanup() {
  echo ""
  echo "Stopping services..."
  kill $SIGNAL_PID $NODE_PID $FRONTEND_PID 2>/dev/null || true
  wait 2>/dev/null
  echo "Done."
}
trap cleanup INT TERM

wait
