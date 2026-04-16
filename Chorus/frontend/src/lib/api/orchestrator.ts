export type JobStatus = 'pending' | 'running' | 'completed' | 'failed'
export type PruneStatus = 'valid' | 'suspect' | 'pruned'
export type PeerStatus = 'idle' | 'busy'

// Legacy result/audit types are kept so existing UI adapters compile in Phase 1-2 mode.
export interface PayoutBreakdown {
  floor: number
  consensus_bonus: number
  dissent_bonus: number
  total: number
}

export interface SettlementReceipt {
  signature: string
  issued_at: number
  pubkey: string
}

export interface SettlementPreview {
  total_pool: number
  eligible_agents: number
  floor_each: number
  extra_pool: number
  impact_weights: Record<string, number>
  payouts: Record<string, number>
  payout_breakdown?: Record<string, PayoutBreakdown>
  receipt?: SettlementReceipt | null
}

export interface SlotRoundAudit {
  slot_id: string
  persona: string
  completion: string | null
  finish_reason?: string | null
  embedding_id?: string | null
  embedding?: number[] | null
  prune_status: PruneStatus
  watchdog_notes: string[]
  impact_c: number
  impact_f: number
  error?: string | null
}

export interface RoundAudit {
  round: number
  slots: Record<string, SlotRoundAudit>
  nearest_edges: [string, string][]
  furthest_edges: [string, string][]
}

export interface OperatorView {
  job_id: string
  status: JobStatus
  current_round: number | null
  error: string | null
  rounds: RoundAudit[]
  settlement_preview: SettlementPreview | null
}

export interface WsAgentLineEvent {
  type: 'agent_line'
  round: number
  slot_id: string
  payload: {
    status: PruneStatus
    latency_ms: number
    snippet: string
  }
}

export interface WsEdgeEvent {
  type: 'edge'
  round: number
  slot_id: string
  payload: {
    from: string
    to: string
    kind: 'nearest' | 'furthest'
  }
}

export interface WsRoundStartedEvent {
  type: 'round_started'
  round: number
}

export interface WsJobDoneEvent {
  type: 'job_done'
  round: number
  payload: {
    settlement_preview: SettlementPreview | null
    final_answer?: string | null
    citations?: string[] | null
    receipt?: { signature: string; issued_at: number } | null
  }
}

export interface WsJobFailedEvent {
  type: 'job_failed'
  payload: { error: string }
}

export interface WsFinalAnswerEvent {
  type: 'final_answer'
  round: number
  payload: {
    text: string
    citations: string[]
  }
}

export type JobStreamEvent =
  | WsRoundStartedEvent
  | WsAgentLineEvent
  | WsEdgeEvent
  | WsFinalAnswerEvent
  | WsJobDoneEvent
  | WsJobFailedEvent

export interface PeerEntry {
  peer_id: string
  address?: string | null
  model: string
  joined_at: number
  status: PeerStatus
  pubkey?: string | null
  verified?: boolean
}

export interface PeersResponse {
  count: number
  peers: PeerEntry[]
}

export interface CreateJobRequest {
  context: string
  prompt: string
  agent_count: number
  rounds: number
  payout: number
  embedding_model_version?: string | null
}

export interface CreateJobResponse {
  job_id: string
  status: JobStatus
}

export interface SlotRegistration {
  completion_base_url: string
  bearer_token?: string | null
  external_participant_id?: string | null
}

export interface RegisterAgentsRequest {
  slots: Record<string, SlotRegistration>
}

export interface RegisterAgentsResponse {
  ok: boolean
  registered_slots: string[]
}

export interface BroadcastPlanRequest {
  prompt: string
  timeout_ms?: number
  persona_catalog?: string[]
  target_peer_ids?: string[]
}

export interface BroadcastAssignment {
  peer_id: string
  persona_index: number
  persona: string
}

export interface BroadcastPlanResponse {
  job_id: string
  expected_peers: number
  timeout_ms: number
  target_peer_ids: string[]
  assignments: BroadcastAssignment[]
}

export type SignalingServerEvent =
  | { type: 'registered'; peer: PeerEntry; peer_count: number }
  | { type: 'peer_count'; count: number; peers: PeerEntry[] }
  | { type: 'heartbeat_ack'; peer_id: string; status: PeerStatus; timestamp?: number | null }
  | { type: 'status_updated'; status: PeerStatus }
  | {
      type: 'broadcast_started'
      ok: boolean
      job_id?: string
      expected_peers?: number
      delivered_peers?: number
      delivered_peer_ids?: string[]
      timeout_ms?: number
      assignments?: BroadcastAssignment[]
      error?: string
    }
  | { type: 'job_envelope'; job_id: string; prompt: string; persona: string; persona_index: number; timeout_ms: number; from_peer_id: string }
  | {
      type: 'job_request'
      job_id: string
      prompt: string
      timeout_ms: number
      prompter_id: string
      your_persona?: string
    }
  | { type: 'address_updated'; peer: PeerEntry }
  | {
      type: 'job_response'
      job_id: string
      peer_id: string
      prompter_id: string
      text?: string
      model?: string
      latency_ms?: number
      error?: string
      /** Distinct worker on the same `peer_id` (optional). */
      instance_id?: string | null
    }
  | { type: 'relay'; from_peer_id: string; payload: Record<string, unknown> }
  | { type: 'relay_ack'; ok: boolean; to_peer_id: string; error?: string }
  | { type: 'error'; error: string; detail?: string }

const DEFAULT_TIMEOUT_MS = 30_000

/** Browser-persisted override so LAN guests can use `http://<host-ip>:8000` without rebuilding. */
export const ORCHESTRATOR_BASE_SESSION_KEY = 'chorus_orchestrator_override'
export const ORCHESTRATOR_BASE_LOCAL_KEY = 'chorus_orchestrator_base'
export const MODEL_PUBLIC_URL_KEY = 'chorus_model_public_url'
export const MODEL_NAME_KEY = 'chorus_model_name'
export const MODEL_SETUP_VERIFIED_KEY = 'chorus_model_setup_verified'

function normalizeOrchestratorBase(url: string): string {
  let s = url.trim().replace(/\/+$/, '')
  if (!s) return s
  // Add a scheme if missing. Bare domains/IPs would otherwise be treated as
  // relative paths by `fetch()` and resolve against the current Vercel origin
  // (which returns 404 for unrelated paths — the most common cause of
  // "/health 404" reports).
  if (!/^https?:\/\//i.test(s)) {
    const host = s.split('/')[0]
    const isLoopback = host === 'localhost' || host === '127.0.0.1' || host === '::1'
    const isPrivateIpv4 = /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host)
    s = `${isLoopback || isPrivateIpv4 ? 'http' : 'https'}://${s}`
  }
  return s
}

export function getOrchestratorBaseOverride(): string | null {
  if (typeof window === 'undefined') return null
  const raw =
    sessionStorage.getItem(ORCHESTRATOR_BASE_SESSION_KEY)?.trim() ??
    localStorage.getItem(ORCHESTRATOR_BASE_LOCAL_KEY)?.trim()
  return raw ? normalizeOrchestratorBase(raw) : null
}

export function setOrchestratorBaseOverride(url: string | null): void {
  if (typeof window === 'undefined') return
  if (!url?.trim()) {
    sessionStorage.removeItem(ORCHESTRATOR_BASE_SESSION_KEY)
    localStorage.removeItem(ORCHESTRATOR_BASE_LOCAL_KEY)
    return
  }
  const normalized = normalizeOrchestratorBase(url)
  sessionStorage.setItem(ORCHESTRATOR_BASE_SESSION_KEY, normalized)
  localStorage.setItem(ORCHESTRATOR_BASE_LOCAL_KEY, normalized)
}

/** Env first at build time; on the client, browser overrides win when set. */
export function getEffectiveOrchestratorBase(): string | null {
  if (typeof window !== 'undefined') {
    const o = getOrchestratorBaseOverride()
    if (o) return o
  }
  const env = process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL?.trim()
  return env ? normalizeOrchestratorBase(env) : null
}

function ensureBaseUrl(): string {
  const baseUrl = getEffectiveOrchestratorBase()
  if (!baseUrl) {
    throw new Error(
      'Signaling base URL is not set. Add NEXT_PUBLIC_ORCHESTRATOR_BASE_URL or set it on the Join page.',
    )
  }
  return baseUrl
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = ensureBaseUrl()
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(new DOMException('Request timed out', 'TimeoutError')), DEFAULT_TIMEOUT_MS)
  try {
    const res = await fetch(`${baseUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    })
    if (!res.ok) {
      const detail = await res.text()
      throw new Error(`${res.status} ${res.statusText}: ${detail}`)
    }
    return (await res.json()) as T
  } finally {
    clearTimeout(timeout)
  }
}

export function isOrchestratorConfigured(): boolean {
  return Boolean(getEffectiveOrchestratorBase())
}

export function getSignalingWsUrl(): string {
  const base = ensureBaseUrl()
  return base.replace(/^http/i, 'ws') + '/ws/signaling'
}

export function getJobEventsWsUrl(jobId: string): string {
  const base = ensureBaseUrl()
  return base.replace(/^http/i, 'ws') + `/ws/jobs/${encodeURIComponent(jobId)}`
}

export function openJobEventsSocket(
  jobId: string,
  handlers: {
    onEvent: (event: JobStreamEvent) => void
    onOpen?: () => void
    onError?: (err: Event | Error) => void
    onClose?: () => void
  },
): WebSocket {
  const ws = new WebSocket(getJobEventsWsUrl(jobId))
  ws.onopen = () => handlers.onOpen?.()
  ws.onmessage = (msg) => {
    try {
      handlers.onEvent(JSON.parse(msg.data) as JobStreamEvent)
    } catch (error) {
      handlers.onError?.(error as Error)
    }
  }
  ws.onerror = (error) => handlers.onError?.(error)
  ws.onclose = () => handlers.onClose?.()
  return ws
}

function newRandomPeerToken(prefix: string): string {
  const tail =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(36).slice(2, 12)}-${Math.random().toString(36).slice(2, 12)}`
  return `${prefix}-${tail}`.toLowerCase().replace(/:/g, '')
}

/**
 * Stable peer id for the **prompter** (home / app / feed) — survives refresh, shared across tabs on purpose
 * so the same browser profile does not look like many prompters.
 */
export function getOrCreatePeerId(): string {
  if (typeof window === 'undefined') return 'ssr-peer'
  const key = 'chorus_peer_id'
  const existing = localStorage.getItem(key)
  if (existing?.trim()) return existing.trim()
  const next = newRandomPeerToken('peer')
  localStorage.setItem(key, next)
  return next
}

const JOIN_TAB_PEER_SESSION_KEY = 'chorus_join_tab_peer_id'

/**
 * Distinct peer id **per browser tab** for `/join`. Orchestrator allows only one WebSocket per `peer_id`;
 * `localStorage` is shared across tabs, so reusing `getOrCreatePeerId()` would collapse every join tab into one peer.
 */
export function getOrCreateJoinTabPeerId(): string {
  if (typeof window === 'undefined') return 'ssr-peer'
  const existing = sessionStorage.getItem(JOIN_TAB_PEER_SESSION_KEY)?.trim()
  if (existing) return existing
  const next = newRandomPeerToken('join')
  sessionStorage.setItem(JOIN_TAB_PEER_SESSION_KEY, next)
  return next
}

const OLLAMA_IP_KEY = 'chorus_ollama_ip'

export function getSavedOllamaIp(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem(OLLAMA_IP_KEY)?.trim() ?? ''
}

export function saveOllamaIp(ip: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(OLLAMA_IP_KEY, ip.trim())
}

export function getSavedModelPublicUrl(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem(MODEL_PUBLIC_URL_KEY)?.trim() ?? ''
}

export function getSavedModelName(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem(MODEL_NAME_KEY)?.trim() ?? ''
}

export function isSavedModelVerified(): boolean {
  if (typeof window === 'undefined') return false
  return localStorage.getItem(MODEL_SETUP_VERIFIED_KEY) === '1'
}

export function setSavedModelVerified(verified: boolean): void {
  if (typeof window === 'undefined') return
  if (verified) localStorage.setItem(MODEL_SETUP_VERIFIED_KEY, '1')
  else localStorage.removeItem(MODEL_SETUP_VERIFIED_KEY)
}

export async function getPeers(): Promise<PeersResponse> {
  return requestJson<PeersResponse>('/peers')
}

export async function createJob(req: CreateJobRequest): Promise<CreateJobResponse> {
  return requestJson<CreateJobResponse>('/jobs', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export async function registerJobAgents(
  jobId: string,
  req: RegisterAgentsRequest,
): Promise<RegisterAgentsResponse> {
  return requestJson<RegisterAgentsResponse>(`/jobs/${encodeURIComponent(jobId)}/agents`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export async function createBroadcastPlan(req: BroadcastPlanRequest): Promise<BroadcastPlanResponse> {
  return requestJson<BroadcastPlanResponse>('/broadcast/plan', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export interface BroadcastInvokeResult {
  job_id: string
  invoked: number
  results: Array<{
    peer_id: string
    ok: boolean
    text?: string | null
    latency_ms?: number
    error?: string | null
  }>
}

export async function invokeBroadcastCompletions(body: {
  job_id: string
  prompt: string
  timeout_ms: number
  persona_catalog?: string[]
  target_peer_ids?: string[]
}): Promise<BroadcastInvokeResult> {
  return requestJson<BroadcastInvokeResult>('/broadcast/invoke_completions', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export type JobResponseRow = {
  type?: string
  job_id: string
  peer_id: string
  prompter_id: string
  text?: string | null
  model?: string | null
  latency_ms?: number | null
  error?: string | null
  instance_id?: string | null
}

export async function getJobResponses(jobId: string): Promise<{ job_id: string; responses: JobResponseRow[] }> {
  return requestJson(`/jobs/${encodeURIComponent(jobId)}/responses`)
}

export async function getJobResponseSummary(jobId: string): Promise<{
  job_id: string
  total: number
  by_peer_id: Record<string, number>
  by_peer_and_instance: Record<string, number>
}> {
  return requestJson(`/jobs/${encodeURIComponent(jobId)}/response-summary`)
}

export function openSignalingSocket(
  peerId: string,
  model: string,
  handlers: {
    onEvent: (event: SignalingServerEvent) => void
    onOpen?: () => void
    onError?: (err: Event | Error) => void
    onClose?: () => void
  },
  address?: string,
): WebSocket {
  const ws = new WebSocket(getSignalingWsUrl())
  ws.onopen = () => {
    const msg: Record<string, unknown> = {
      type: 'register',
      peer_id: peerId,
      model,
    }
    if (address?.trim()) {
      msg.address = address.trim()
    }
    ws.send(JSON.stringify(msg))
    handlers.onOpen?.()
  }
  ws.onmessage = (msg) => {
    try {
      handlers.onEvent(JSON.parse(msg.data) as SignalingServerEvent)
    } catch (error) {
      handlers.onError?.(error as Error)
    }
  }
  ws.onerror = (error) => handlers.onError?.(error)
  ws.onclose = () => handlers.onClose?.()
  return ws
}
