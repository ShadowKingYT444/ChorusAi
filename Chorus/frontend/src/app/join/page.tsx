'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getEffectiveOrchestratorBase,
  getOrCreateJoinTabPeerId,
  getOrchestratorBaseOverride,
  getPeers,
  isOrchestratorConfigured,
  openSignalingSocket,
  setOrchestratorBaseOverride,
  suggestLocalOrchestratorBase,
  type PeerEntry,
  type SignalingServerEvent,
} from '@/lib/api/orchestrator'
import { isPrivateLanIpv4 } from '@/lib/lan/chat-proxy-allow'
import { normalizeOpenAIChatCompletionsUrl } from '@/lib/lan/normalize-openai-chat-url'

type Phase = 'idle' | 'connecting' | 'connected' | 'error'

const MODEL_PUBLIC_URL_KEY = 'chorus_model_public_url'
const WORKER_INSTANCE_SESSION_KEY = 'chorus_join_worker_instance'
const DEFAULT_CHAT_MODEL =
  typeof process !== 'undefined' && process.env.NEXT_PUBLIC_DISTLM_CHAT_MODEL?.trim()
    ? process.env.NEXT_PUBLIC_DISTLM_CHAT_MODEL.trim()
    : 'qwen2.5:0.5b'

function formatChatFetchError(err: unknown, uiOrigin: string): string {
  const m = err instanceof Error ? err.message : String(err)
  const low = m.toLowerCase()
  if (m === 'Failed to fetch' || low.includes('failed to fetch') || low.includes('networkerror')) {
    return [
      'Failed to fetch (browser blocked or could not reach Ollama).',
      `Allow this UI origin in Ollama: OLLAMA_ORIGINS=${uiOrigin} (then restart Ollama).`,
      'If the page is https:// but Ollama is http://, use http:// for the UI or a TLS tunnel.',
      'Tip: use your LAN IP as the model base (e.g. http://192.168.x.x:11434) so this app can proxy from the Next server and avoid browser CORS.',
    ].join(' ')
  }
  return m
}

async function replyWithLocalModel(opts: {
  ws: WebSocket
  peerId: string
  modelLabel: string
  modelPublicUrl: string
  jobId: string
  prompterId: string
  userPrompt: string
  systemPersona: string
  /** Optional sub-worker id so multiple agents behind one mesh peer show as separate feed rows. */
  instanceId?: string | null
}): Promise<'text' | 'error'> {
  const { ws, peerId, modelLabel, modelPublicUrl, jobId, prompterId, userPrompt, systemPersona, instanceId } = opts
  const inst = instanceId?.trim()
  const send = (payload: Record<string, unknown>) => {
    if (ws.readyState !== WebSocket.OPEN) return
    const body: Record<string, unknown> = { type: 'job_response', ...payload }
    if (inst) body.instance_id = inst
    ws.send(JSON.stringify(body))
  }
  const base = modelPublicUrl.trim()
  if (!base) {
    send({
      job_id: jobId,
      peer_id: peerId,
      prompter_id: prompterId,
      error: 'join:no_model_url - set Public model API base above',
      latency_ms: 0,
    })
    return 'error'
  }
  const url = normalizeOpenAIChatCompletionsUrl(base)
  if (!url) {
    send({
      job_id: jobId,
      peer_id: peerId,
      prompter_id: prompterId,
      error: 'join:invalid_model_url',
      latency_ms: 0,
    })
    return 'error'
  }
  const t0 = performance.now()
  const uiOrigin = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:3000'
  const openaiBody = {
    model: DEFAULT_CHAT_MODEL,
    messages: [
      { role: 'system', content: `${systemPersona}\n\nReply in a few sentences; be direct.` },
      { role: 'user', content: userPrompt },
    ],
    max_tokens: 512,
    temperature: 0.7,
  }

  let host: string
  try {
    host = new URL(url).hostname
  } catch {
    send({
      job_id: jobId,
      peer_id: peerId,
      prompter_id: prompterId,
      error: 'join:invalid_model_url',
      latency_ms: Math.round(performance.now() - t0),
    })
    return 'error'
  }

  async function readCompletionResponse(res: Response): Promise<'text' | 'error'> {
    const latency_ms = Math.round(performance.now() - t0)
    if (!res.ok) {
      const detail = await res.text()
      let snippet = detail.slice(0, 1200)
      try {
        const j = JSON.parse(detail) as { error?: string }
        if (typeof j.error === 'string') snippet = j.error.slice(0, 1200)
      } catch {
        /* keep text */
      }
      send({
        job_id: jobId,
        peer_id: peerId,
        prompter_id: prompterId,
        error: `http_${res.status}: ${snippet}`,
        latency_ms,
      })
      return 'error'
    }
    const data = (await res.json()) as {
      choices?: Array<{ message?: { content?: string | null } }>
    }
    const text = data?.choices?.[0]?.message?.content?.trim() ?? ''
    send({
      job_id: jobId,
      peer_id: peerId,
      prompter_id: prompterId,
      text: text || '(empty completion)',
      model: modelLabel,
      latency_ms,
    })
    return 'text'
  }

  try {
    if (isPrivateLanIpv4(host)) {
      const proxyRes = await fetch('/api/local-chat-completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targetBase: base, ...openaiBody }),
      })
      if (proxyRes.ok) {
        return readCompletionResponse(proxyRes)
      }
      const errText = await proxyRes.text()
      if (proxyRes.status === 503) {
        /* NEXT_LAN_CHAT_PROXY=0 - use browser only */
      } else {
        const tryBrowserFallback =
          proxyRes.status === 502 &&
          /ECONNREFUSED|ETIMEDOUT|ENOTFOUND|Could not reach Ollama|fetch failed/i.test(errText)
        if (tryBrowserFallback) {
          try {
            const directRes = await fetch(url, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(openaiBody),
            })
            return readCompletionResponse(directRes)
          } catch (be) {
            send({
              job_id: jobId,
              peer_id: peerId,
              prompter_id: prompterId,
              error: [
                'The Next dev PC could not open TCP to Ollama (proxy 502); browser fallback also failed.',
                formatChatFetchError(be, uiOrigin),
                'If Ollama runs on another machine, set OLLAMA_HOST=0.0.0.0 there and ensure this URL is reachable from the PC running npm run dev (firewall). If only your browser can reach Ollama (e.g. Tailscale on this laptop), set OLLAMA_ORIGINS=',
                uiOrigin,
                ' and use the browser path.',
              ].join(' '),
              latency_ms: Math.round(performance.now() - t0),
            })
            return 'error'
          }
        }
        return readCompletionResponse(new Response(errText, { status: proxyRes.status }))
      }
    }

    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(openaiBody),
    })
    return readCompletionResponse(res)
  } catch (e) {
    send({
      job_id: jobId,
      peer_id: peerId,
      prompter_id: prompterId,
      error: formatChatFetchError(e, uiOrigin),
      latency_ms: Math.round(performance.now() - t0),
    })
    return 'error'
  }
}

export default function JoinLanPage() {
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const [modelLabel, setModelLabel] = useState('lan-browser')
  const [workerInstanceId, setWorkerInstanceId] = useState('')
  const [modelPublicUrl, setModelPublicUrl] = useState('')
  const [hasCheckedSetup, setHasCheckedSetup] = useState(false)
  const [baseInput, setBaseInput] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [peers, setPeers] = useState<PeerEntry[]>([])
  const [myPeerId, setMyPeerId] = useState<string | null>(null)
  const [lastTask, setLastTask] = useState<{
    job_id: string
    prompt: string
    persona?: string
    from_peer_id?: string
    kind: 'envelope' | 'request'
  } | null>(null)
  const [lastReplyNote, setLastReplyNote] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const hbRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const answeredJobKeysRef = useRef<Set<string>>(new Set())
  const modelPublicUrlRef = useRef('')
  const modelLabelRef = useRef('lan-browser')
  const workerInstanceIdRef = useRef('')

  useEffect(() => {
    modelPublicUrlRef.current = modelPublicUrl
  }, [modelPublicUrl])
  useEffect(() => {
    modelLabelRef.current = modelLabel
  }, [modelLabel])
  useEffect(() => {
    workerInstanceIdRef.current = workerInstanceId
  }, [workerInstanceId])

  useEffect(() => {
    const initialBase =
      getOrchestratorBaseOverride() ?? getEffectiveOrchestratorBase() ?? suggestLocalOrchestratorBase() ?? ''
    setBaseInput(initialBase)
    setShowAdvanced(!initialBase)
    setModelPublicUrl(
      typeof window !== 'undefined' ? localStorage.getItem(MODEL_PUBLIC_URL_KEY)?.trim() ?? '' : '',
    )
    setWorkerInstanceId(
      typeof window !== 'undefined' ? sessionStorage.getItem(WORKER_INSTANCE_SESSION_KEY)?.trim() ?? '' : '',
    )
    setHasCheckedSetup(true)
  }, [])

  const effectiveBase = baseInput.trim() || null

  const persistModelUrl = useCallback((url: string) => {
    const t = url.trim()
    if (typeof window !== 'undefined') {
      if (t) localStorage.setItem(MODEL_PUBLIC_URL_KEY, t)
      else localStorage.removeItem(MODEL_PUBLIC_URL_KEY)
    }
  }, [])

  const stopHeartbeat = useCallback(() => {
    if (hbRef.current) {
      clearInterval(hbRef.current)
      hbRef.current = null
    }
  }, [])

  const disconnect = useCallback(() => {
    stopHeartbeat()
    wsRef.current?.close()
    wsRef.current = null
    answeredJobKeysRef.current.clear()
    setPhase('idle')
    setMyPeerId(null)
    setLastReplyNote(null)
  }, [stopHeartbeat])

  const applyPresence = useCallback((msg: SignalingServerEvent) => {
    if (msg.type === 'peer_count') {
      setPeers(msg.peers)
      return
    }
    if (msg.type === 'registered') {
      setPeers((prev) => {
        const p = msg.peer
        if (prev.some((x) => x.peer_id === p.peer_id)) return prev
        return [...prev, p]
      })
    }
    if (msg.type === 'address_updated') {
      setPeers((prev) => prev.map((x) => (x.peer_id === msg.peer.peer_id ? msg.peer : x)))
    }
  }, [])

  const pushAddress = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    const base = modelPublicUrl.trim()
    if (!base) {
      setError('Public model API base is required so other peers can reach your model.')
      return
    }
    if (!normalizeOpenAIChatCompletionsUrl(base)) {
      setError('Public model API base is invalid. Use a full http(s):// URL.')
      return
    }
    setError(null)
    persistModelUrl(base)
    ws.send(
      JSON.stringify({
        type: 'set_address',
        address: base,
      }),
    )
  }, [modelPublicUrl, persistModelUrl])

  const join = useCallback(() => {
    setError(null)
    const trimmed = baseInput.trim()
    const resolvedBase =
      trimmed ||
      getOrchestratorBaseOverride() ||
      getEffectiveOrchestratorBase() ||
      suggestLocalOrchestratorBase() ||
      ''
    if (trimmed) setOrchestratorBaseOverride(trimmed)
    else if (!resolvedBase) {
      setError('Enter the signaling server URL (e.g. http://192.168.1.10:8000).')
      return
    }

    if (!resolvedBase) {
      setError('No signaling URL configured.')
      return
    }

    const publicModelBase = modelPublicUrl.trim()
    if (!publicModelBase) {
      setError('Set your public model API base first. Use your LAN IP or tunnel URL from /setup.')
      return
    }
    if (!normalizeOpenAIChatCompletionsUrl(publicModelBase)) {
      setError('Public model API base is invalid. Use a full http(s):// URL.')
      return
    }

    persistModelUrl(publicModelBase)
    setPhase('connecting')
    const peerId = getOrCreateJoinTabPeerId()
    setMyPeerId(peerId)

    const addr = publicModelBase
    const ws = openSignalingSocket(peerId, modelLabel.trim() || 'lan-browser', {
      onEvent: (event) => {
        if (event.type === 'error') {
          setError(event.detail ?? event.error)
          setPhase('error')
          stopHeartbeat()
          wsRef.current?.close()
          wsRef.current = null
          return
        }
        if (event.type === 'job_envelope') {
          setLastTask({
            job_id: event.job_id,
            prompt: event.prompt,
            persona: event.persona,
            from_peer_id: event.from_peer_id,
            kind: 'envelope',
          })
          const rk = `env:${event.job_id}:${event.from_peer_id}`
          if (!answeredJobKeysRef.current.has(rk)) {
            answeredJobKeysRef.current.add(rk)
            setLastReplyNote(null)
            void replyWithLocalModel({
              ws,
              peerId,
              modelLabel: modelLabelRef.current.trim() || 'lan-browser',
              modelPublicUrl: modelPublicUrlRef.current,
              jobId: event.job_id,
              prompterId: event.from_peer_id,
              userPrompt: event.prompt,
              systemPersona: event.persona ?? 'You are a helpful assistant.',
              instanceId: workerInstanceIdRef.current,
            }).then((outcome) =>
              setLastReplyNote(
                outcome === 'text'
                  ? 'Reply sent to host (WebSocket).'
                  : 'Response sent (includes error detail for the host).',
              ),
            )
          }
        }
        if (event.type === 'job_request') {
          setLastTask({
            job_id: event.job_id,
            prompt: event.prompt,
            persona: event.your_persona,
            from_peer_id: event.prompter_id,
            kind: 'request',
          })
          const rk = `req:${event.job_id}:${event.prompter_id}`
          if (!answeredJobKeysRef.current.has(rk)) {
            answeredJobKeysRef.current.add(rk)
            setLastReplyNote(null)
            void replyWithLocalModel({
              ws,
              peerId,
              modelLabel: modelLabelRef.current.trim() || 'lan-browser',
              modelPublicUrl: modelPublicUrlRef.current,
              jobId: event.job_id,
              prompterId: event.prompter_id,
              userPrompt: event.prompt,
              systemPersona: event.your_persona ?? 'You are a helpful assistant.',
              instanceId: workerInstanceIdRef.current,
            }).then((outcome) =>
              setLastReplyNote(
                outcome === 'text'
                  ? 'Reply sent to host (WebSocket).'
                  : 'Response sent (includes error detail for the host).',
              ),
            )
          }
        }
        applyPresence(event)
        if (event.type === 'registered') {
          setPhase('connected')
          void getPeers()
            .then((res) => setPeers(res.peers))
            .catch(() => {})
        }
      },
      onOpen: () => {
        hbRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(
              JSON.stringify({
                type: 'heartbeat',
                status: 'idle',
                timestamp: Date.now() / 1000,
              }),
            )
          }
        }, 25_000)
      },
      onError: () => {
        setError('WebSocket error - check URL, firewall, and that the host uses --host 0.0.0.0')
        setPhase('error')
        stopHeartbeat()
        try {
          ws.close()
        } catch {
          /* ignore */
        }
        if (wsRef.current === ws) wsRef.current = null
      },
      onClose: () => {
        stopHeartbeat()
        if (wsRef.current === ws) wsRef.current = null
        setPhase((p) => {
          if (p === 'connecting') return 'error'
          if (p === 'connected') return 'idle'
          return p
        })
      },
    }, addr)
    wsRef.current = ws
  }, [baseInput, modelLabel, modelPublicUrl, applyPresence, stopHeartbeat, persistModelUrl])

  useEffect(() => {
    return () => disconnect()
  }, [disconnect])

  useEffect(() => {
    if (phase !== 'connected' || !isOrchestratorConfigured()) return
    let cancelled = false
    const tick = async () => {
      try {
        const res = await getPeers()
        if (!cancelled) setPeers(res.peers)
      } catch {
        /* ignore poll errors */
      }
    }
    void tick()
    const id = setInterval(tick, 4000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [phase])

  return (
    <div
      style={{
        minHeight: '100dvh',
        background: '#050508',
        color: 'rgba(255,255,255,0.92)',
        fontFamily: 'var(--font-geist-sans), system-ui, sans-serif',
        padding: 'clamp(1.25rem, 4vw, 2.5rem)',
      }}
    >
      <div style={{ maxWidth: '34rem', margin: '0 auto' }}>
        <nav
          aria-label="Main"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: '0.4rem',
            marginBottom: '1rem',
          }}
        >
          <Link
            href="/"
            style={{
              fontSize: '12px',
              fontWeight: 600,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '0.45rem 0.85rem',
              borderRadius: 3,
              color: 'rgba(255,255,255,0.55)',
              background: 'transparent',
              border: '1px solid rgba(255,255,255,0.14)',
              textDecoration: 'none',
            }}
          >
            Launch
          </Link>
          <Link
            href="/setup"
            style={{
              fontSize: '12px',
              fontWeight: 600,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '0.45rem 0.85rem',
              borderRadius: 3,
              color: 'rgba(255,255,255,0.55)',
              background: 'transparent',
              border: '1px solid rgba(255,255,255,0.14)',
              textDecoration: 'none',
            }}
          >
            Setup
          </Link>
          <span
            style={{
              fontSize: '12px',
              fontWeight: 600,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '0.45rem 0.85rem',
              borderRadius: 3,
              color: 'rgba(255,255,255,0.92)',
              background: 'rgba(255,255,255,0.12)',
              border: '1px solid rgba(255,255,255,0.22)',
            }}
          >
            Join
          </span>
        </nav>
        <p style={{ fontSize: '11px', letterSpacing: '0.14em', color: 'rgba(255,255,255,0.45)', marginBottom: '0.5rem' }}>
          CHORUS · LAN
        </p>
        <h1 style={{ fontSize: '1.35rem', fontWeight: 600, marginBottom: '0.35rem' }}>Join this network</h1>
        <p style={{ fontSize: '14px', lineHeight: 1.55, color: 'rgba(255,255,255,0.58)', marginBottom: '1.75rem' }}>
          Register this browser on signaling. Set your <strong style={{ color: 'rgba(255,255,255,0.85)' }}>public model
          URL</strong> (ngrok, LAN IP, etc.) so the host can call your OpenAI-compatible{' '}
          <span style={{ fontFamily: 'var(--font-geist-mono), monospace', fontSize: '12px' }}>/v1/chat/completions</span>.
          When the host runs a job, you will see the prompt here.
        </p>

        {hasCheckedSetup && modelPublicUrl === '' && (
          <div
            style={{
              borderRadius: 6,
              border: '1px solid rgba(180,200,255,0.28)',
              background: 'rgba(180,200,255,0.06)',
              padding: '0.8rem 1rem',
              marginBottom: '1rem',
              fontSize: '13px',
              color: 'rgba(255,255,255,0.75)',
              lineHeight: 1.55,
            }}
          >
            Your peer cannot answer jobs until this field is set. Need help setting up Ollama?{' '}
            <Link
              href="/setup"
              style={{
                color: 'rgba(180,210,255,0.95)',
                textDecoration: 'underline',
                fontWeight: 600,
              }}
            >
              Run the setup wizard →
            </Link>
          </div>
        )}

        <div
          style={{
            borderRadius: 6,
            border: '1px solid rgba(255,255,255,0.12)',
            background: 'rgba(255,255,255,0.04)',
            padding: '1.25rem 1.35rem',
            marginBottom: '1rem',
          }}
        >
          <label style={{ display: 'block', fontSize: '12px', color: 'rgba(255,255,255,0.5)', marginBottom: '0.35rem' }}>
            Label (shown in /peers)
          </label>
          <input
            value={modelLabel}
            onChange={(e) => setModelLabel(e.target.value)}
            disabled={phase === 'connecting' || phase === 'connected'}
            style={{
              width: '100%',
              padding: '0.55rem 0.65rem',
              borderRadius: 4,
              border: '1px solid rgba(255,255,255,0.14)',
              background: 'rgba(0,0,0,0.35)',
              color: '#fff',
              fontSize: '14px',
              marginBottom: '1rem',
            }}
          />

          <label style={{ display: 'block', fontSize: '12px', color: 'rgba(255,255,255,0.5)', marginBottom: '0.35rem' }}>
            Worker instance id (optional)
          </label>
          <input
            value={workerInstanceId}
            onChange={(e) => {
              const v = e.target.value
              setWorkerInstanceId(v)
              if (typeof window !== 'undefined') {
                if (v.trim()) sessionStorage.setItem(WORKER_INSTANCE_SESSION_KEY, v.trim())
                else sessionStorage.removeItem(WORKER_INSTANCE_SESSION_KEY)
              }
            }}
            placeholder="e.g. gpu-0, worker-2"
            disabled={phase === 'connecting' || phase === 'connected'}
            style={{
              width: '100%',
              padding: '0.55rem 0.65rem',
              borderRadius: 4,
              border: '1px solid rgba(255,255,255,0.14)',
              background: 'rgba(0,0,0,0.35)',
              color: '#fff',
              fontSize: '14px',
              marginBottom: '0.4rem',
            }}
          />
          <p style={{ fontSize: '11px', lineHeight: 1.45, color: 'rgba(255,255,255,0.38)', marginBottom: '1rem' }}>
            If several logical agents share this mesh connection, give each a distinct id so the host feed can show
            them separately (<code style={{ fontSize: '10px' }}>peer_id#instance_id</code>). Verify counts with{' '}
            <code style={{ fontSize: '10px' }}>GET /jobs/&lt;job_id&gt;/response-summary</code> on the orchestrator.
          </p>

          <label style={{ display: 'block', fontSize: '12px', color: 'rgba(255,255,255,0.5)', marginBottom: '0.35rem' }}>
            Public model API base
          </label>
          <input
            value={modelPublicUrl}
            onChange={(e) => setModelPublicUrl(e.target.value)}
            disabled={phase === 'connecting'}
            placeholder="https://abc.ngrok-free.app or http://203.0.113.1:11434"
            style={{
              width: '100%',
              padding: '0.55rem 0.65rem',
              borderRadius: 4,
              border: '1px solid rgba(255,255,255,0.14)',
              background: 'rgba(0,0,0,0.35)',
              color: '#fff',
              fontSize: '14px',
              marginBottom: '0.45rem',
            }}
          />
          <p style={{ fontSize: '11px', color: 'rgba(255,255,255,0.38)', marginBottom: '1rem' }}>
            Required. Use a LAN URL like <code>http://192.168.x.x:11434</code> or an https tunnel URL.
            Saved in this browser and shared across tabs.
          </p>

          {(showAdvanced || !effectiveBase) && (
            <>
              <label style={{ display: 'block', fontSize: '12px', color: 'rgba(255,255,255,0.5)', marginBottom: '0.35rem' }}>
                Signaling server (HTTP base)
              </label>
              <input
                value={baseInput}
                onChange={(e) => setBaseInput(e.target.value)}
                placeholder="http://192.168.1.10:8000"
                disabled={phase === 'connecting' || phase === 'connected'}
                style={{
                  width: '100%',
                  padding: '0.55rem 0.65rem',
                  borderRadius: 4,
                  border: '1px solid rgba(255,255,255,0.14)',
                  background: 'rgba(0,0,0,0.35)',
                  color: '#fff',
                  fontSize: '14px',
                  marginBottom: '0.5rem',
                }}
              />
              <p style={{ fontSize: '11px', color: 'rgba(255,255,255,0.38)', marginBottom: '1rem' }}>
                Saved in this browser and shared across tabs.
              </p>
            </>
          )}

          {!showAdvanced && effectiveBase && (
            <p style={{ fontSize: '12px', color: 'rgba(255,255,255,0.5)', marginBottom: '1rem' }}>
              Using <span style={{ color: 'rgba(255,255,255,0.88)' }}>{effectiveBase}</span>
              <button
                type="button"
                onClick={() => setShowAdvanced(true)}
                style={{
                  marginLeft: '0.5rem',
                  background: 'none',
                  border: 'none',
                  color: 'rgba(120,180,255,0.95)',
                  cursor: 'pointer',
                  fontSize: '12px',
                  textDecoration: 'underline',
                }}
              >
                Change URL
              </button>
            </p>
          )}

          {error && (
            <p style={{ fontSize: '13px', color: '#f6a89a', marginBottom: '0.85rem' }}>{error}</p>
          )}

          <div style={{ display: 'flex', gap: '0.65rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {phase !== 'connected' ? (
              <button
                type="button"
                onClick={join}
                disabled={phase === 'connecting'}
                style={{
                  padding: '0.6rem 1.15rem',
                  borderRadius: 4,
                  border: 'none',
                  background: phase === 'connecting' ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.92)',
                  color: '#050508',
                  fontWeight: 600,
                  fontSize: '14px',
                  cursor: phase === 'connecting' ? 'wait' : 'pointer',
                }}
              >
                {phase === 'connecting' ? 'Connecting…' : 'Join network'}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={disconnect}
                  style={{
                    padding: '0.6rem 1.15rem',
                    borderRadius: 4,
                    border: '1px solid rgba(255,255,255,0.25)',
                    background: 'transparent',
                    color: 'rgba(255,255,255,0.88)',
                    fontWeight: 600,
                    fontSize: '14px',
                    cursor: 'pointer',
                  }}
                >
                  Leave
                </button>
                <button
                  type="button"
                  onClick={pushAddress}
                  style={{
                    padding: '0.6rem 1rem',
                    borderRadius: 4,
                    border: '1px solid rgba(120,180,255,0.45)',
                    background: 'rgba(120,180,255,0.12)',
                    color: 'rgba(200,220,255,0.95)',
                    fontWeight: 600,
                    fontSize: '13px',
                    cursor: 'pointer',
                  }}
                >
                  Push URL to server
                </button>
              </>
            )}
            <Link
              href="/"
              style={{
                alignSelf: 'center',
                fontSize: '13px',
                color: 'rgba(255,255,255,0.45)',
              }}
            >
              ← Home
            </Link>
          </div>
        </div>

        {lastTask && (
          <div
            style={{
              borderRadius: 6,
              border: '1px solid rgba(120,200,255,0.25)',
              background: 'rgba(30,50,80,0.35)',
              padding: '1rem 1.1rem',
              marginBottom: '1rem',
            }}
          >
            <p style={{ fontSize: '11px', letterSpacing: '0.1em', color: 'rgba(200,220,255,0.65)', marginBottom: '0.35rem' }}>
              LAST PROMPT ({lastTask.kind}) · {lastTask.job_id.slice(0, 8)}…
            </p>
            {lastTask.from_peer_id && (
              <p style={{ fontSize: '12px', color: 'rgba(255,255,255,0.45)', marginBottom: '0.5rem' }}>
                From <span style={{ fontFamily: 'var(--font-geist-mono), monospace' }}>{lastTask.from_peer_id}</span>
                {lastTask.persona ? ` · persona: ${lastTask.persona.slice(0, 120)}${lastTask.persona.length > 120 ? '…' : ''}` : ''}
              </p>
            )}
            <p style={{ fontSize: '14px', lineHeight: 1.55, color: 'rgba(255,255,255,0.9)', whiteSpace: 'pre-wrap' }}>
              {lastTask.prompt}
            </p>
            {lastReplyNote && (
              <p style={{ fontSize: '12px', color: 'rgba(160,220,180,0.9)', marginTop: '0.75rem' }}>{lastReplyNote}</p>
            )}
            <p style={{ fontSize: '11px', color: 'rgba(255,255,255,0.38)', marginTop: '0.6rem', lineHeight: 1.45 }}>
              LAN IPs first use a same-origin Next proxy (no browser CORS). If the dev PC cannot reach Ollama (e.g.
              ECONNREFUSED), the app retries from your browser - then set <code style={{ fontSize: '10px' }}>OLLAMA_ORIGINS</code>{' '}
              to{' '}
              <code style={{ fontSize: '10px' }}>{typeof window !== 'undefined' ? window.location.origin : 'http://…:3000'}</code>
              . Prefer fixing the network path so the Next PC can reach Ollama (<code style={{ fontSize: '10px' }}>OLLAMA_HOST=0.0.0.0</code> on the Ollama machine, firewall).{' '}
              <code style={{ fontSize: '10px' }}>127.0.0.1</code> uses the browser only. Disable the proxy with{' '}
              <code style={{ fontSize: '10px' }}>NEXT_LAN_CHAT_PROXY=0</code>.
            </p>
          </div>
        )}

        <div style={{ fontSize: '13px', color: 'rgba(255,255,255,0.5)' }}>
          <div style={{ marginBottom: '0.35rem' }}>
            Status:{' '}
            <strong style={{ color: phase === 'connected' ? '#8fd4a8' : 'rgba(255,255,255,0.75)' }}>
              {phase === 'connected' ? 'Connected' : phase === 'connecting' ? 'Connecting' : phase === 'error' ? 'Error' : 'Disconnected'}
            </strong>
            {myPeerId && (
              <span style={{ marginLeft: '0.5rem', fontFamily: 'var(--font-geist-mono), monospace', fontSize: '12px' }}>
                id {myPeerId.slice(0, 8)}…
              </span>
            )}
          </div>
          <div style={{ fontSize: '10px', color: 'rgba(255,255,255,0.32)', marginTop: '0.25rem', lineHeight: 1.35 }}>
            Each tab gets its own peer id (sessionStorage). The signaling server keeps one connection per id - multiple
            tabs used to share localStorage and looked like a single peer.
          </div>
          <div style={{ marginBottom: '0.25rem' }}>Peers online: {peers.length}</div>
          <ul style={{ margin: 0, paddingLeft: '1.1rem', maxHeight: '14rem', overflow: 'auto' }}>
            {peers.map((p) => (
              <li key={p.peer_id} style={{ marginBottom: '0.35rem' }}>
                <span style={{ fontFamily: 'var(--font-geist-mono), monospace', fontSize: '12px' }}>{p.peer_id}</span>
                <span style={{ color: 'rgba(255,255,255,0.4)' }}> · </span>
                {p.model}
                {p.address ? (
                  <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.38)', marginTop: '0.15rem', wordBreak: 'break-all' }}>
                    {p.address}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
