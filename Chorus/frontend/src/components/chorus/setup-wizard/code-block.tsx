'use client'

import { Check, Copy } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

interface Props {
  code: string
  language?: string
  label?: string
}

export function CodeBlock({ code, language = 'bash', label }: Props) {
  const [copied, setCopied] = useState(false)

  const onCopy = useCallback(async () => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) return
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
    } catch {
      /* ignore */
    }
  }, [code])

  useEffect(() => {
    if (!copied) return
    const id = setTimeout(() => setCopied(false), 1400)
    return () => clearTimeout(id)
  }, [copied])

  return (
    <div
      style={{
        borderRadius: 6,
        border: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(0,0,0,0.45)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0.4rem 0.65rem 0.4rem 0.85rem',
          borderBottom: '1px solid rgba(255,255,255,0.05)',
          background: 'rgba(255,255,255,0.02)',
        }}
      >
        <span
          style={{
            fontFamily: 'var(--font-geist-mono), monospace',
            fontSize: 10,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.38)',
          }}
        >
          {label ?? language}
        </span>
        <button
          type="button"
          onClick={onCopy}
          aria-label={copied ? 'Copied' : 'Copy to clipboard'}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.35rem',
            padding: '0.25rem 0.55rem',
            borderRadius: 4,
            border: '1px solid rgba(255,255,255,0.1)',
            background: copied ? 'rgba(143,212,168,0.12)' : 'rgba(255,255,255,0.04)',
            color: copied ? '#8fd4a8' : 'rgba(255,255,255,0.7)',
            fontSize: 11,
            fontWeight: 500,
            cursor: 'pointer',
            transition: 'all 140ms ease',
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: '0.85rem 0.95rem',
          fontFamily: 'var(--font-geist-mono), monospace',
          fontSize: 12.5,
          lineHeight: 1.55,
          color: 'rgba(230,238,255,0.92)',
          overflowX: 'auto',
          whiteSpace: 'pre',
        }}
      >
        <code>{code}</code>
      </pre>
    </div>
  )
}
