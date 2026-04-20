set -u

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

ORC_PORT="${ORC_PORT:-18765}"
ECHO_PORT="${ECHO_PORT:-18766}"
ORC_URL="http://127.0.0.1:${ORC_PORT}"
ECHO_URL="http://127.0.0.1:${ECHO_PORT}"
SMOKE_DB="${SMOKE_DB:-${REPO_ROOT}/chorus_smoke.db}"
WORKSPACE_ID="${SMOKE_WORKSPACE_ID:-local-dev}"
WORKSPACE_TOKEN="${SMOKE_WORKSPACE_TOKEN:-chorus-local-dev-token}"
AUTH_HEADERS=(-H "X-Chorus-Workspace: ${WORKSPACE_ID}" -H "Authorization: Bearer ${WORKSPACE_TOKEN}")

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

if command -v curl.exe >/dev/null 2>&1; then
  CURL="curl.exe"
elif command -v curl >/dev/null 2>&1; then
  CURL="curl"
else
  fail "missing required command: curl"
fi

if command -v python.exe >/dev/null 2>&1; then
  PY="python.exe"
elif command -v py.exe >/dev/null 2>&1; then
  PY="py.exe -3"
elif command -v py >/dev/null 2>&1; then
  PY="py -3"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
elif command -v python >/dev/null 2>&1; then
  PY="python"
else
  fail "no working python on PATH"
fi

echo "[smoke] starting orchestrator on :${ORC_PORT} (db=${SMOKE_DB})"
CHORUS_DB_PATH="${SMOKE_DB}" \
ORC_CORS_ORIGINS="*" \
ORC_EMBEDDING_BACKEND="${SMOKE_EMBEDDING_BACKEND:-hash}" \
ORC_REQUIRE_WORKSPACE_AUTH="1" \
ORC_ALLOW_BOOTSTRAP_WORKSPACE="0" \
ORC_WORKSPACE_TOKENS="${WORKSPACE_ID}=${WORKSPACE_TOKEN}" \
ORC_ALLOW_LOCALHOST="1" \
  $PY -m uvicorn orchestrator.main:app \
  --host 127.0.0.1 --port "${ORC_PORT}" --log-level warning \
  >"${ORC_LOG}" 2>&1 &
ORC_PID=$!

for i in $(seq 1 60); do
  if "${CURL}" -fs "${ORC_URL}/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${ORC_PID}" 2>/dev/null; then
    fail "orchestrator exited before becoming healthy"
  fi
  sleep 0.25
done
"${CURL}" -fs "${ORC_URL}/health" >/dev/null 2>&1 || fail "orchestrator /health never returned 200"

echo "[smoke] starting echo agent on :${ECHO_PORT}"
PYTHONPATH="${REPO_ROOT}" \
  $PY -m uvicorn tests.fixtures.echo_agent:app \
  --host 127.0.0.1 --port "${ECHO_PORT}" --log-level warning \
  >"${ECHO_LOG}" 2>&1 &
ECHO_PID=$!

for i in $(seq 1 60); do
  if "${CURL}" -fs "${ECHO_URL}/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${ECHO_PID}" 2>/dev/null; then
    fail "echo agent exited before becoming healthy"
  fi
  sleep 0.25
done
"${CURL}" -fs "${ECHO_URL}/health" >/dev/null 2>&1 || fail "echo agent /health never returned 200"

CREATE_BODY='{"context":"smoke","prompt":"Describe a compiler.","agent_count":3,"rounds":2,"payout":100}'
CREATE_RESP="$("${CURL}" -fs -X POST \
  "${AUTH_HEADERS[@]}" \
  -H "Content-Type: application/json" \
  "${ORC_URL}/jobs" \
  -d "${CREATE_BODY}")" || fail "POST /jobs failed"
JOB_ID="$(printf '%s' "${CREATE_RESP}" | $PY -c 'import sys,json;print(json.load(sys.stdin)["job_id"])' | tr -d '\r')"
[ -n "${JOB_ID}" ] || fail "no job_id in /jobs response: ${CREATE_RESP}"
echo "[smoke] job_id=${JOB_ID}"

REG_BODY="$($PY - <<PYEOF
import json
base = "${ECHO_URL}/v1"
slots = {f"slot-{i}": {"completion_base_url": base} for i in range(3)}
print(json.dumps({"slots": slots}))
PYEOF
)"
REG_RESP="$("${CURL}" -sS -w $'\n%{http_code}' -X POST \
  "${AUTH_HEADERS[@]}" \
  -H "Content-Type: application/json" \
  "${ORC_URL}/jobs/${JOB_ID}/agents" \
  -d "${REG_BODY}")"
REG_HTTP="$(printf '%s' "${REG_RESP}" | tail -n1)"
REG_BODY_RESP="$(printf '%s' "${REG_RESP}" | sed '$d')"
[ "${REG_HTTP}" = "200" ] || fail "POST /jobs/{id}/agents failed (${REG_HTTP}): ${REG_BODY_RESP}"

echo "[smoke] polling job status..."
STATUS=""
JOB_JSON=""
for i in $(seq 1 120); do
  JOB_JSON="$("${CURL}" -fs "${AUTH_HEADERS[@]}" "${ORC_URL}/jobs/${JOB_ID}" || true)"
  STATUS="$(printf '%s' "${JOB_JSON}" | $PY -c 'import sys,json
try: print(json.load(sys.stdin).get("status",""))
except: print("")' 2>/dev/null | tr -d '\r')"
  if [ "${STATUS}" = "completed" ]; then
    break
  fi
  if [ "${STATUS}" = "failed" ]; then
    fail "job failed: ${JOB_JSON}"
  fi
  sleep 0.5
done
[ "${STATUS}" = "completed" ] || fail "job did not complete in 60s (status=${STATUS}) body=${JOB_JSON}"

CHAT_JSON="$("${CURL}" -fs "${AUTH_HEADERS[@]}" "${ORC_URL}/chats/${JOB_ID}")" || fail "GET /chats/{id} failed"
if ! printf '%s' "${JOB_JSON}" | $PY -c 'import json,sys
job = json.load(sys.stdin)
assert job.get("settlement_preview", {}).get("receipt", {}).get("signature"), job
'; then
  fail "missing settlement receipt signature"
fi
CHAT_LEN="$(printf '%s' "${CHAT_JSON}" | $PY -c 'import json,sys
chat = json.load(sys.stdin)
fa = chat.get("final_answer")
assert fa and len(fa) > 0, f"empty final_answer in /chats/id: {chat!r}"
print(len(fa))
' | tr -d '\r')"
SIG_LEN="$(printf '%s' "${JOB_JSON}" | $PY -c 'import json,sys
job = json.load(sys.stdin)
sig = job.get("settlement_preview", {}).get("receipt", {}).get("signature")
assert sig and len(sig) > 0, "missing settlement receipt signature"
print(len(sig))
' | tr -d '\r')"
echo "[smoke] final_answer_len=${CHAT_LEN} sig_len=${SIG_LEN}"

CHATS_JSON="$("${CURL}" -fs "${AUTH_HEADERS[@]}" "${ORC_URL}/chats")" || fail "GET /chats failed"
CHATS_COUNT="$(printf '%s' "${CHATS_JSON}" | $PY -c 'import json,sys
body = json.load(sys.stdin)
chats = body.get("chats") or []
assert len(chats) >= 1, f"/chats empty: {body!r}"
print(len(chats))
' | tr -d '\r')"
echo "[smoke] chats_len=${CHATS_COUNT}"

echo "SMOKE OK  job=${JOB_ID} status=${STATUS}"
exit 0
