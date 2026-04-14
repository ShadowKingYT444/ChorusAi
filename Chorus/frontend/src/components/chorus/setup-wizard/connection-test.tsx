'use client'

import { CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { useCallback, useState } from 'react'
import { isPrivateLanIpv4 } from '@/lib/lan/chat-proxy-allow'
import { normalizeOpenAIChatCompletionsUrl } from '@/lib/lan/normalize-openai-chat-url'

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

export function ConnectionTest({ mode, target, model, onResult }: Props) {
  const [phase, setPhase] = useState<TestPhase>('idle')
  const [message, setMessage] = useState<string>('')
  const [tip, setTip] = useState<string>('')

  const runTest = useCallback(async () => {
    setPhase('running')
    setMessage('')
    setTip('')

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

    const uiIsHttps = typeof window !== 'undefined' && window.location.protocol === 'https:'

    try {
      if (mode === 'lan') {
        const hostForBase = /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}:11434`
        let host: string
        try {
          host = new URL(hostForBase).hostname
        } catch {
          setPhase('error')
          setMessage('Invalid LAN address.')
          onResult?.(false)
          return
        }
        if (!isPrivateLanIpv4(host)) {
          setTip(
            'Warning: this does not look like a private LAN IPv4 (10.x, 172.16–31.x, 192.168.x). The Next proxy will refuse it — use your LAN IP from `ipconfig` / `hostname -I`.',
          )
        }
        const res = await fetch('/api/local-chat-completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ targetBase: hostForBase, ...body }),
        })
        const raw = await res.text()
        if (!res.ok) {
          setPhase('error')
          setMessage(`HTTP ${res.status}: ${extractErrorMessage(raw)}`)
          if (res.status === 502) {
            setTip(
              'The Next server could not reach Ollama. Check OLLAMA_HOST=0.0.0.0, that Ollama is running, and that the firewall allows port 11434.',
            )
          }
          onResult?.(false)
          return
        }
        const parsed = JSON.parse(raw) as ChatCompletionShape
        const text = parsed.choices?.[0]?.message?.content?.trim() ?? '(empty)'
        setPhase('ok')
        setMessage(text)
        onResult?.(true)
        return
      }

      // tunnel mode — direct browser fetch
      const url = normalizeOpenAIChatCompletionsUrl(trimmed)
      if (!url) {
        setPhase('error')
        setMessage('Invalid tunnel URL.')
        onResult?.(false)
        return
      }
      if (uiIsHttps && url.startsWith('http://')) {
        setTip(
          'Mixed-content warning: this page is HTTPS but your tunnel is HTTP. Browsers will block the request. Use an https ngrok/cloudflared URL.',
        )
      }
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const raw = await res.text()
      if (!res.ok) {
        setPhase('error')
        setMessage(`HTTP ${res.status}: ${extractErrorMessage(raw)}`)
        if (res.status === 403) {
          const origin = typeof window !== 'undefined' ? window.location.origin : ''
          setTip(`Set OLLAMA_ORIGINS=${origin} on the Ollama machine and restart it.`)
        }
        onResult?.(false)
        return
      }
      const parsed = JSON.parse(raw) as ChatCompletionShape
      const text = parsed.choices?.[0]?.message?.content?.trim() ?? '(empty)'
      setPhase('ok')
      setMessage(text)
      onResult?.(true)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setPhase('error')
      setMessage(msg)
      if (/failed to fetch|networkerror/i.test(msg)) {
        const origin = typeof window !== 'undefined' ? window.location.origin : ''
        setTip(
          mode === 'tunnel'
            ? `Browser blocked the fetch. Set OLLAMA_ORIGINS=${origin} on the Ollama machine and confirm the tunnel URL is live.`
            : 'The Next server could not reach Ollama. Check OLLAMA_HOST=0.0.0.0 and firewall for port 11434.',
        )
      }
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
