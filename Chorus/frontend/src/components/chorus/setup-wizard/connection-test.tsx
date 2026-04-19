'use client'

import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { useCallback, useState } from 'react'
import { isLoopbackOllamaHost, isPrivateLanIpv4 } from '@/lib/lan/chat-proxy-allow'
import { normalizeOpenAIChatCompletionsUrl } from '@/lib/lan/normalize-openai-chat-url'

// Tunnel mode prefers the same-origin proxy first, then falls back to a direct
// browser call when the proxy path cannot use the supplied public URL.
import { MODEL_PUBLIC_URL_KEY, setSavedModelVerified } from '@/lib/api/orchestrator'

function persistVerifiedModelBase(base: string): void {
  if (typeof window === 'undefined') return
  const trimmed = base.trim()
  if (!trimmed) return
  try {
    localStorage.setItem(MODEL_PUBLIC_URL_KEY, trimmed)
  } catch {
    /* localStorage may be disabled — non-fatal */
  }
}

export type TestMode = 'lan' | 'tunnel'
export type TestPhase = 'idle' | 'running' | 'ok' | 'error'

interface Props {
  mode: TestMode
  /** For LAN mode: a bare LAN IP (e.g. `192.168.1.10`) or full URL. For tunnel: the https tunnel URL. */
  target: string
  model: string
  onResult?: (ok: boolean) => void
}

interface ChatCompletionShape {
  choices?: Array<{ message?: { content?: string | null } }>
  error?: string | { message?: string }
}

function extractErrorMessage(raw: string): string {
  try {
    const parsed = JSON.parse(raw) as ChatCompletionShape
    if (typeof parsed.error === 'string') return parsed.error
    if (parsed.error && typeof parsed.error === 'object' && typeof parsed.error.message === 'string') {
      return parsed.error.message
    }
  } catch {
    /* fall through */
  }
  return raw.slice(0, 400)
}

function extractCompletionText(raw: string): string {
  try {
    const parsed = JSON.parse(raw) as ChatCompletionShape
    return parsed.choices?.[0]?.message?.content?.trim() ?? '(empty)'
  } catch {
    return '(empty)'
  }
}

export function ConnectionTest({ mode, target, model, onResult }: Props) {
  const [phase, setPhase] = useState<TestPhase>('idle')
  const [message, setMessage] = useState<string>('')
  const [tip, setTip] = useState<string>('')

  const runTest = useCallback(async () => {
    setPhase('running')
    setMessage('')
    setTip('')
    setSavedModelVerified(false)

    const trimmed = target.trim()
    if (!trimmed) {
      setPhase('error')
      setMessage('Enter a target first.')
      onResult?.(false)
      return
    }

    const body = {
      model,
      messages: [{ role: 'user', content: 'say OK' }],
      max_tokens: 5,
      temperature: 0,
    }

    // Detect whether we're on a deployed host (Vercel, etc.) vs localhost dev.
    const isDeployed =
      typeof window !== 'undefined' &&
      window.location.hostname !== 'localhost' &&
      window.location.hostname !== '127.0.0.1' &&
      window.location.hostname !== ''

    try {
      if (mode === 'lan') {
        const hostForBase = /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}:11434`
        let host: string
        let completionUrl: string
        try {
          host = new URL(hostForBase).hostname
          completionUrl = normalizeOpenAIChatCompletionsUrl(hostForBase)
        } catch {
          setPhase('error')
          setMessage('Invalid LAN address.')
          onResult?.(false)
          return
        }
        const isLoopback = isLoopbackOllamaHost(host)
        const isLan = isPrivateLanIpv4(host)

        // Block loopback/LAN on deployed hosts — the proxy runs on Vercel, not the user's PC.
        if (isDeployed && (isLoopback || isLan)) {
          setPhase('error')
          setMessage(
            isLoopback
              ? 'Cannot reach 127.0.0.1 from a hosted site.'
              : `Cannot reach ${host} (a private LAN address) from a hosted site.`,
          )
          setTip(
            [
              'You are on a deployed Chorus instance — the connection test runs on a remote server that cannot reach your local machine.',
              'To connect your local Ollama, go back and select "Remote access via tunnel", then use ngrok or cloudflared to give Ollama a public URL.',
              'Run: ngrok http 11434 — then paste the https URL it gives you.',
            ].join(' '),
          )
          onResult?.(false)
          return
        }

        if (!isLoopback && !isLan) {
          setTip(
            'Use `127.0.0.1` if Ollama is on this same PC. Use `192.168.x.x` only if Ollama is on a different machine on your Wi-Fi/LAN.',
          )
        }
        const res = await fetch('/api/local-chat-completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ targetBase: hostForBase, allowLoopback: isLoopback, ...body }),
        })
        const raw = await res.text()
        if (!res.ok) {
          setPhase('error')
          setMessage(`HTTP ${res.status}: ${extractErrorMessage(raw)}`)
          if (res.status === 502) {
            setTip(
              isLoopback
                ? [
                    'You chose the same PC path, so this should work without ngrok.',
                    '1. Make sure Ollama is open on this computer.',
                    '2. In PowerShell run: `Invoke-WebRequest http://127.0.0.1:11434/api/tags`.',
                    '3. If that fails, Ollama is not running yet. Start it and retry.',
                    '4. If it works, retry this test.',
                  ].join(' ')
                : [
                    `The Next server could not open ${completionUrl}.`,
                    '1. On the Ollama machine, run: `Invoke-WebRequest http://127.0.0.1:11434/api/tags`.',
                    '2. If that works, expose Ollama to your LAN with `OLLAMA_HOST=0.0.0.0`, then fully quit and relaunch Ollama.',
                    '3. On the machine running `npm run dev`, run: `Invoke-WebRequest http://YOUR-IP:11434/api/tags`.',
                    '4. If that times out, fix Windows Firewall for TCP 11434 or use `127.0.0.1` if Chorus and Ollama are on the same PC.',
                  ].join(' '),
            )
          }
          onResult?.(false)
          return
        }
        const text = extractCompletionText(raw)
        setPhase('ok')
        setMessage(text)
        setSavedModelVerified(true)
        persistVerifiedModelBase(hostForBase)
        setTip(
          isLoopback
            ? 'Confirmed: Chorus can reach Ollama on this same computer.'
            : 'Confirmed: the machine running Chorus can reach your Ollama node over the network.',
        )
        onResult?.(true)
        return
      }

      // tunnel mode — route through server-side proxy to avoid CORS / ngrok interstitial
      const tunnelBase = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`
      const completionUrl = normalizeOpenAIChatCompletionsUrl(tunnelBase)
      if (!completionUrl) {
        setPhase('error')
        setMessage('Invalid tunnel URL.')
        setTip('Paste the full public HTTPS URL from ngrok or cloudflared.')
        onResult?.(false)
        return
      }

      let tunnelHost: string
      try {
        tunnelHost = new URL(completionUrl).hostname
      } catch {
        setPhase('error')
        setMessage('Invalid tunnel URL.')
        setTip('Paste the full public HTTPS URL from ngrok or cloudflared.')
        onResult?.(false)
        return
      }

      if (isLoopbackOllamaHost(tunnelHost) || isPrivateLanIpv4(tunnelHost)) {
        setPhase('error')
        setMessage(`Tunnel mode expects the public HTTPS tunnel URL, not ${tunnelHost}.`)
        setTip(
          [
            'Run ngrok or cloudflared on the Ollama machine and paste the public URL it prints.',
            'Examples: `https://abc-123.ngrok-free.app` or `https://random-words.trycloudflare.com`.',
            'Do not paste `http://127.0.0.1:11434` or a `192.168.x.x` address in tunnel mode.',
          ].join(' '),
        )
        onResult?.(false)
        return
      }

      const runDirectTunnelCall = async (proxyContext?: string) => {
        try {
          const directRes = await fetch(completionUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          })
          const directRaw = await directRes.text()
          if (!directRes.ok) {
            setPhase('error')
            setMessage(`HTTP ${directRes.status}: ${extractErrorMessage(directRaw)}`)
            setTip(
              [
                proxyContext ? `Proxy note: ${proxyContext}` : '',
                'The browser reached your tunnel, but Ollama still rejected the request.',
                directRes.status === 403
                  ? 'HTTP 403 almost always means ngrok is not rewriting the Host header. Restart ngrok with: ngrok http 11434 --host-header=localhost:11434 — Ollama blocks tunnel hostnames by default to prevent DNS rebinding.'
                  : 'Confirm Ollama is running, the tunnel points at port 11434, and OLLAMA_ORIGINS includes this Chorus UI origin.',
              ]
                .filter(Boolean)
                .join(' '),
            )
            onResult?.(false)
            return
          }
          setPhase('ok')
          setMessage(extractCompletionText(directRaw))
          setSavedModelVerified(true)
          persistVerifiedModelBase(tunnelBase)
          setTip(
            proxyContext
              ? 'Confirmed: the browser reached your tunnel directly even though the server-side proxy could not use it.'
              : 'Confirmed: your tunnel is live and Ollama responded successfully.',
          )
          onResult?.(true)
        } catch (directErr) {
          const directMsg = directErr instanceof Error ? directErr.message : String(directErr)
          setPhase('error')
          setMessage(directMsg)
          setTip(
            [
              proxyContext ? `Proxy note: ${proxyContext}` : '',
              'The direct browser call failed, which usually means OLLAMA_ORIGINS is missing this Chorus UI origin or the tunnel is down.',
              'Restart Ollama with OLLAMA_ORIGINS set to this site URL, then confirm the tunnel is still forwarding to localhost:11434.',
            ]
              .filter(Boolean)
              .join(' '),
          )
          setSavedModelVerified(false)
          onResult?.(false)
        }
      }

      const res = await fetch('/api/local-chat-completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targetBase: tunnelBase, ...body }),
      })
      const raw = await res.text()
      if (!res.ok) {
        const proxyError = extractErrorMessage(raw)
        if (res.status === 400 || res.status === 502 || res.status === 503) {
          await runDirectTunnelCall(proxyError)
          return
        }
        setPhase('error')
        setMessage(`HTTP ${res.status}: ${proxyError}`)
        if (res.status === 403) {
          setTip(
            'Ollama returned 403. This is DNS-rebinding protection — ngrok must rewrite the Host header. Restart ngrok with: ngrok http 11434 --host-header=localhost:11434 (paste the new URL above, then retest).',
          )
        }
        onResult?.(false)
        return
      }
      const text = extractCompletionText(raw)
      setPhase('ok')
      setMessage(text)
      setSavedModelVerified(true)
      persistVerifiedModelBase(tunnelBase)
      setTip('Confirmed: your tunnel is live and Ollama responded successfully.')
      onResult?.(true)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setPhase('error')
      setMessage(msg)
      if (/failed to fetch|networkerror/i.test(msg)) {
        setTip(
          mode === 'tunnel'
            ? [
                '"Failed to fetch" — the server-side proxy could not reach your tunnel.',
                '1. Confirm ngrok or cloudflared is running.',
                '2. Confirm Ollama is running on the same machine.',
                '3. Try opening your tunnel URL in a new browser tab to verify it loads.',
              ].join(' ')
            : 'The Next server could not reach Ollama. Check OLLAMA_HOST=0.0.0.0 and firewall for port 11434.',
        )
      }
      setSavedModelVerified(false)
      onResult?.(false)
    }
  }, [mode, target, model, onResult])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      <button
        type="button"
        onClick={runTest}
        disabled={phase === 'running'}
        style={{
          alignSelf: 'flex-start',
          padding: '0.55rem 1.1rem',
          borderRadius: 4,
          border: 'none',
          background: phase === 'running' ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.92)',
          color: '#050508',
          fontWeight: 600,
          fontSize: 13,
          cursor: phase === 'running' ? 'wait' : 'pointer',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.45rem',
        }}
      >
        {phase === 'running' && <Loader2 size={14} className="animate-spin" />}
        {phase === 'running' ? 'Testing…' : phase === 'ok' ? 'Retest' : 'Run test'}
      </button>

      {phase === 'ok' && (
        <div
          role="status"
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.55rem',
            padding: '0.7rem 0.85rem',
            borderRadius: 5,
            border: '1px solid rgba(143,212,168,0.35)',
            background: 'rgba(30,60,40,0.35)',
            color: 'rgba(200,240,210,0.95)',
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          <CheckCircle2 size={16} style={{ color: '#8fd4a8', flexShrink: 0, marginTop: 1 }} />
          <div>
            <div style={{ fontWeight: 600, marginBottom: 2 }}>Connection OK</div>
            <div
              style={{
                fontFamily: 'var(--font-geist-mono), monospace',
                fontSize: 11.5,
                color: 'rgba(210,240,220,0.78)',
                wordBreak: 'break-word',
              }}
            >
              {message}
            </div>
          </div>
        </div>
      )}

      {phase === 'error' && (
        <div
          role="alert"
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '0.55rem',
            padding: '0.7rem 0.85rem',
            borderRadius: 5,
            border: '1px solid rgba(246,168,154,0.35)',
            background: 'rgba(60,30,30,0.3)',
            color: '#f6a89a',
            fontSize: 13,
            lineHeight: 1.5,
          }}
        >
          <XCircle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
          <div>
            <div style={{ fontWeight: 600, marginBottom: 2 }}>Connection failed</div>
            <div style={{ color: 'rgba(246,200,190,0.85)', wordBreak: 'break-word' }}>{message}</div>
            {tip && (
              <div style={{ marginTop: 6, color: 'rgba(255,220,210,0.72)', fontSize: 12 }}>{tip}</div>
            )}
          </div>
        </div>
      )}

      {phase !== 'error' && tip && (
        <p style={{ fontSize: 12, color: 'rgba(255,210,160,0.78)', lineHeight: 1.5 }}>{tip}</p>
      )}
    </div>
  )
}
