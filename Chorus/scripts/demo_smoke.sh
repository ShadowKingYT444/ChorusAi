#!/usr/bin/env bash
# demo_smoke.sh
# Asserts orchestrator + echo agent + persistence work end-to-end.
# Ollama is NOT required — smoke uses the echo fixture agent.
set -u

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

ORC_PORT="${ORC_PORT:-18765}"
ECHO_PORT="${ECHO_PORT:-18766}"
ORC_URL="http://127.0.0.1:${ORC_PORT}"
ECHO_URL="http://127.0.0.1:${ECHO_PORT}"
SMOKE_DB="${SMOKE_DB:-${REPO_ROOT}/chorus_smoke.db}"

# Use a disposable DB so smoke never pollutes the dev DB.
rm -f "${SMOKE_DB}" "${SMOKE_DB}-shm" "${SMOKE_DB}-wal" 2>/dev/null || true

LOG_DIR="${REPO_ROOT}/.smoke_logs"
mkdir -p "${LOG_DIR}"
ORC_LOG="${LOG_DIR}/orchestrator.log"
ECHO_LOG="${LOG_DIR}/echo.log"

ORC_PID=""
ECHO_PID=""

cleanup() {
  local ec=$?
  if [ -n "${ORC_PID}" ] && kill -0 "${ORC_PID}" 2>/dev/null; then
    kill "${ORC_PID}" 2>/dev/null || true
    wait "${ORC_PID}" 2>/dev/null || true
  fi
  if [ -n "${ECHO_PID}" ] && kill -0 "${ECHO_PID}" 2>/dev/null; then
    kill "${ECHO_PID}" 2>/dev/null || true
    wait "${ECHO_PID}" 2>/dev/null || true
  fi
  exit $ec
}
trap cleanup EXIT INT TERM

fail() {
  echo "SMOKE FAIL: $*" >&2
  echo "--- orchestrator log (tail) ---" >&2
  tail -n 40 "${ORC_LOG}" 2>/dev/null >&2 || true
  echo "--- echo log (tail) ---" >&2
  tail -n 40 "${ECHO_LOG}" 2>/dev/null >&2 || true
  exit 1
}

need() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

need curl
need python

# Pick a python launcher (python3 / python / py -3).
PY="python"
if ! python -c "import sys" >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1; then
    PY="python3"
  elif command -v py >/dev/null 2>&1; then
    PY="py -3"
  else
    fail "no working python on PATH"
  fi
fi

# --- 1. Start orchestrator ---
echo "[smoke] starting orchestrator on :${ORC_PORT} (db=${SMOKE_DB})"
CHORUS_DB_PATH="${SMOKE_DB}" ORC_CORS_ORIGINS="*" \
  $PY -m uvicorn orchestrator.main:app \
  --host 127.0.0.1 --port "${ORC_PORT}" --log-level warning \
  >"${ORC_LOG}" 2>&1 &
ORC_PID=$!

# Wait up to 15s for /health.
for i in $(seq 1 60); do
  if curl -fs "${ORC_URL}/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${ORC_PID}" 2>/dev/null; then
    fail "orchestrator exited before becoming healthy"
  fi
  sleep 0.25
done
curl -fs "${ORC_URL}/health" >/dev/null 2>&1 || fail "orchestrator /health never returned 200"

# --- 2. Start echo agent fixture ---
echo "[smoke] starting echo agent on :${ECHO_PORT}"
PYTHONPATH="${REPO_ROOT}" \
  $PY -m uvicorn tests.fixtures.echo_agent:app \
  --host 127.0.0.1 --port "${ECHO_PORT}" --log-level warning \
  >"${ECHO_LOG}" 2>&1 &
ECHO_PID=$!

for i in $(seq 1 60); do
  if curl -fs "${ECHO_URL}/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${ECHO_PID}" 2>/dev/null; then
    fail "echo agent exited before becoming healthy"
  fi
  sleep 0.25
done
curl -fs "${ECHO_URL}/health" >/dev/null 2>&1 || fail "echo agent /health never returned 200"

# --- 3. Create job ---
CREATE_BODY='{"context":"smoke","prompt":"Describe a compiler.","agent_count":3,"rounds":2,"payout":100}'
CREATE_RESP="$(curl -fs -X POST "${ORC_URL}/jobs" \
  -H "Content-Type: application/json" \
  -d "${CREATE_BODY}")" || fail "POST /jobs failed"
JOB_ID="$(printf '%s' "${CREATE_RESP}" | $PY -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')"
[ -n "${JOB_ID}" ] || fail "no job_id in /jobs response: ${CREATE_RESP}"
echo "[smoke] job_id=${JOB_ID}"

# --- 4. Register 3 slots pointing at echo agent ---
REG_BODY="$($PY -c "
import json
base = '${ECHO_URL}/v1'
slots = {f'slot-{i}': {'completion_base_url': base} for i in range(3)}
print(json.dumps({'slots': slots}))
")"
curl -fs -X POST "${ORC_URL}/jobs/${JOB_ID}/agents" \
  -H "Content-Type: application/json" \
  -d "${REG_BODY}" >/dev/null || fail "POST /jobs/{id}/agents failed"

# --- 5. Poll until completed (up to 60s) ---
echo "[smoke] polling job status…"
STATUS=""
JOB_JSON=""
for i in $(seq 1 120); do
  JOB_JSON="$(curl -fs "${ORC_URL}/jobs/${JOB_ID}" || true)"
  STATUS="$(printf '%s' "${JOB_JSON}" | $PY -c 'import sys,json
try: print(json.load(sys.stdin).get("status",""))
except: print("")' 2>/dev/null)"
  if [ "${STATUS}" = "completed" ]; then
    break
  fi
  if [ "${STATUS}" = "failed" ]; then
    fail "job failed: ${JOB_JSON}"
  fi
  sleep 0.5
done
[ "${STATUS}" = "completed" ] || fail "job did not complete in 60s (status=${STATUS}) body=${JOB_JSON}"

# --- 6. Assert final_answer + signed receipt ---
# Receipt lives on /jobs/{id} (settlement_preview.receipt.signature).
# final_answer is exposed on /chats/{id} (the persistent view).
CHAT_JSON="$(curl -fs "${ORC_URL}/chats/${JOB_ID}")" || fail "GET /chats/{id} failed"
$PY - <<PYEOF || fail "assertions on response failed"
import json, sys
job  = json.loads('''${JOB_JSON}''')
chat = json.loads('''${CHAT_JSON}''')
fa = chat.get("final_answer")
assert fa and len(fa) > 0, f"empty final_answer in /chats/id: {chat!r}"
sp = job.get("settlement_preview") or {}
rc = sp.get("receipt") or {}
sig = rc.get("signature")
assert sig and len(sig) > 0, f"missing settlement_preview.receipt.signature: {sp!r}"
print(f"[smoke] final_answer_len={len(fa)} sig_len={len(sig)}")
PYEOF

# --- 7. Assert /chats has at least one entry ---
CHATS_JSON="$(curl -fs "${ORC_URL}/chats")" || fail "GET /chats failed"
$PY - <<PYEOF || fail "/chats assertion failed"
import json
body = json.loads('''${CHATS_JSON}''')
chats = body.get("chats") or []
assert len(chats) >= 1, f"/chats empty: {body!r}"
print(f"[smoke] chats_len={len(chats)}")
PYEOF

echo "SMOKE OK  job=${JOB_ID} status=${STATUS}"
exit 0
