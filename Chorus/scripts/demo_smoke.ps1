# demo_smoke.ps1
# Asserts orchestrator + echo agent + persistence work end-to-end.
# Ollama is NOT required - smoke uses the echo fixture agent and auth-protected
# auto routing through managed anchors.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$OrcPort  = if ($env:ORC_PORT)  { [int]$env:ORC_PORT }  else { 18765 }
$EchoPort = if ($env:ECHO_PORT) { [int]$env:ECHO_PORT } else { 18766 }
$OrcUrl   = "http://127.0.0.1:$OrcPort"
$EchoUrl  = "http://127.0.0.1:$EchoPort"
$SmokeDb  = if ($env:SMOKE_DB) { $env:SMOKE_DB } else { Join-Path $RepoRoot "chorus_smoke.db" }
$WorkspaceId = if ($env:SMOKE_WORKSPACE_ID) { $env:SMOKE_WORKSPACE_ID } else { "smoke-workspace" }
$WorkspaceToken = if ($env:SMOKE_WORKSPACE_TOKEN) { $env:SMOKE_WORKSPACE_TOKEN } else { "smoke-token" }
$AuthHeaders = @{
  Authorization = "Bearer $WorkspaceToken"
  "X-Chorus-Workspace" = $WorkspaceId
}

foreach ($suffix in @("", "-shm", "-wal")) {
  $p = "$SmokeDb$suffix"
  if (Test-Path $p) { Remove-Item -Force $p -ErrorAction SilentlyContinue }
}

$LogDir = Join-Path $RepoRoot ".smoke_logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OrcLog  = Join-Path $LogDir "orchestrator.log"
$EchoLog = Join-Path $LogDir "echo.log"

$OrcProc  = $null
$EchoProc = $null

function Stop-Proc($p) {
  if ($null -ne $p -and -not $p.HasExited) {
    try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
}

function Fail([string]$msg) {
  Write-Host "SMOKE FAIL: $msg" -ForegroundColor Red
  if (Test-Path $OrcLog)  { Write-Host "--- orchestrator log (tail) ---"; Get-Content $OrcLog -Tail 40 }
  if (Test-Path $EchoLog) { Write-Host "--- echo log (tail) ---";         Get-Content $EchoLog -Tail 40 }
  Stop-Proc $OrcProc
  Stop-Proc $EchoProc
  exit 1
}

foreach ($c in @("python")) {
  if (-not (Get-Command $c -ErrorAction SilentlyContinue)) {
    Fail "missing required command: $c"
  }
}
foreach ($cmdlet in @("Invoke-RestMethod", "Start-Process", "Stop-Process")) {
  if (-not (Get-Command $cmdlet -ErrorAction SilentlyContinue)) {
    Fail "missing required cmdlet: $cmdlet"
  }
}

function Wait-Http([string]$url, [int]$timeoutS, [System.Diagnostics.Process]$proc) {
  $deadline = (Get-Date).AddSeconds($timeoutS)
  while ((Get-Date) -lt $deadline) {
    try {
      Invoke-RestMethod -Uri $url -TimeoutSec 2 | Out-Null
      return $true
    } catch { }
    if ($proc -and $proc.HasExited) { return $false }
    Start-Sleep -Milliseconds 250
  }
  return $false
}

Write-Host "[smoke] starting echo agent on :$EchoPort"
$env:PYTHONPATH = $RepoRoot
$EchoProc = Start-Process -FilePath "python" `
  -ArgumentList @("-m","uvicorn","tests.fixtures.echo_agent:app","--host","127.0.0.1","--port","$EchoPort","--log-level","warning") `
  -WorkingDirectory $RepoRoot `
  -RedirectStandardOutput $EchoLog -RedirectStandardError "$EchoLog.err" `
  -PassThru -WindowStyle Hidden

if (-not (Wait-Http "$EchoUrl/health" 15 $EchoProc)) { Fail "echo agent /health never returned 200" }

Write-Host "[smoke] starting orchestrator on :$OrcPort (db=$SmokeDb)"
$env:CHORUS_DB_PATH = $SmokeDb
$env:ORC_CORS_ORIGINS = "*"
$env:ORC_EMBEDDING_BACKEND = "hash"
$env:ORC_REQUIRE_WORKSPACE_AUTH = "1"
$env:ORC_ALLOW_BOOTSTRAP_WORKSPACE = "1"
$env:ORC_BOOTSTRAP_WORKSPACE_ID = $WorkspaceId
$env:ORC_BOOTSTRAP_TOKEN = $WorkspaceToken
$env:ORC_ANCHOR_COMPLETION_BASE_URLS = "$EchoUrl,$EchoUrl,$EchoUrl"
$OrcProc = Start-Process -FilePath "python" `
  -ArgumentList @("-m","uvicorn","orchestrator.main:app","--host","127.0.0.1","--port","$OrcPort","--log-level","warning") `
  -WorkingDirectory $RepoRoot `
  -RedirectStandardOutput $OrcLog -RedirectStandardError "$OrcLog.err" `
  -PassThru -WindowStyle Hidden

if (-not (Wait-Http "$OrcUrl/health" 15 $OrcProc)) { Fail "orchestrator /health never returned 200" }

$createBody = @{
  context     = "smoke"
  prompt      = "Review whether this launch plan is ready to ship."
  agent_count = 3
  rounds      = 2
  payout      = 100
} | ConvertTo-Json -Compress

try {
  $createResp = Invoke-RestMethod -Method Post -Uri "$OrcUrl/jobs" -Headers $AuthHeaders -ContentType "application/json" -Body $createBody
} catch { Fail "POST /jobs failed: $_" }

$JobId = $createResp.job_id
if (-not $JobId) { Fail "no job_id in /jobs response" }
if ($createResp.workspace_id -ne $WorkspaceId) { Fail "workspace_id mismatch on create" }
if ($createResp.shadow_credit_cost -ne 6) { Fail "unexpected shadow_credit_cost on create: $($createResp.shadow_credit_cost)" }
Write-Host "[smoke] job_id=$JobId"

$regBody = @{ routing_mode = "auto" } | ConvertTo-Json -Compress -Depth 5
try {
  $regResp = Invoke-RestMethod -Method Post -Uri "$OrcUrl/jobs/$JobId/agents" -Headers $AuthHeaders -ContentType "application/json" -Body $regBody
} catch { Fail "POST /jobs/$JobId/agents failed: $_" }
if ($null -eq $regResp.registered_slots -or $regResp.registered_slots.Count -ne 3) {
  Fail "expected 3 auto-routed slots, got: $($regResp | ConvertTo-Json -Depth 5)"
}

Write-Host "[smoke] polling job status..."
$deadline = (Get-Date).AddSeconds(60)
$jobResp  = $null
while ((Get-Date) -lt $deadline) {
  try { $jobResp = Invoke-RestMethod -Uri "$OrcUrl/jobs/$JobId" -Headers $AuthHeaders } catch { $jobResp = $null }
  if ($null -ne $jobResp) {
    if ($jobResp.status -eq "completed") { break }
    if ($jobResp.status -eq "failed")    { Fail "job failed: $($jobResp | ConvertTo-Json -Depth 5)" }
  }
  Start-Sleep -Milliseconds 500
}
if ($null -eq $jobResp -or $jobResp.status -ne "completed") {
  Fail "job did not complete in 60s (status=$($jobResp.status))"
}
if ($jobResp.routing_mode -ne "auto") {
  Fail "expected routing_mode=auto, got: $($jobResp.routing_mode)"
}
if ($jobResp.shadow_credit_cost -ne 6) {
  Fail "unexpected shadow_credit_cost on job: $($jobResp.shadow_credit_cost)"
}

try {
  $chatResp = Invoke-RestMethod -Uri "$OrcUrl/chats/$JobId" -Headers $AuthHeaders
} catch { Fail "GET /chats/$JobId failed: $_" }
if (-not $chatResp.final_answer -or $chatResp.final_answer.Length -eq 0) {
  Fail "empty final_answer in /chats/$JobId"
}

$sig = $null
if ($jobResp.settlement_preview -and $jobResp.settlement_preview.receipt) {
  $sig = $jobResp.settlement_preview.receipt.signature
}
if (-not $sig -or $sig.Length -eq 0) {
  Fail "missing settlement_preview.receipt.signature"
}
if ($jobResp.settlement_preview.shadow_credits -ne 6) {
  Fail "unexpected settlement shadow_credits: $($jobResp.settlement_preview.shadow_credits)"
}
Write-Host ("[smoke] final_answer_len={0} sig_len={1}" -f $chatResp.final_answer.Length, $sig.Length)

try {
  $chatsResp = Invoke-RestMethod -Uri "$OrcUrl/chats" -Headers $AuthHeaders
} catch { Fail "GET /chats failed: $_" }
if ($null -eq $chatsResp.chats -or $chatsResp.chats.Count -lt 1) {
  Fail "/chats is empty"
}
Write-Host "[smoke] chats_len=$($chatsResp.chats.Count)"

Stop-Proc $OrcProc
Stop-Proc $EchoProc

Write-Host "SMOKE OK  job=$JobId status=completed" -ForegroundColor Green
exit 0
