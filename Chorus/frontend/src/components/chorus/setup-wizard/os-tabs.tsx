'use client'

import { CodeBlock } from './code-block'

export type OsKey = 'macos' | 'windows' | 'linux'

export const OS_LABELS: Record<OsKey, string> = {
  macos: 'macOS',
  windows: 'Windows',
  linux: 'Linux',
}

interface Props {
  value: OsKey
  onChange: (v: OsKey) => void
  commands: Record<OsKey, { code: string; label?: string; note?: string }>
}

export function OsTabs({ value, onChange, commands }: Props) {
  const active = commands[value]
  return (
    <div>
      <div
        role="tablist"
        aria-label="Operating system"
        style={{
          display: 'flex',
          gap: '0.4rem',
          marginBottom: '0.65rem',
          flexWrap: 'wrap',
        }}
      >
        {(Object.keys(OS_LABELS) as OsKey[]).map((key) => {
          const isActive = key === value
          return (
            <button
              type="button"
              key={key}
              role="tab"
              aria-selected={isActive}
              onClick={() => onChange(key)}
              style={{
                padding: '0.38rem 0.75rem',
                borderRadius: 4,
                border: isActive
                  ? '1px solid rgba(255,255,255,0.22)'
                  : '1px solid rgba(255,255,255,0.08)',
                background: isActive ? 'rgba(255,255,255,0.1)' : 'transparent',
                color: isActive ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.55)',
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                cursor: 'pointer',
                transition: 'all 140ms ease',
              }}
            >
              {OS_LABELS[key]}
            </button>
          )
        })}
      </div>
      <CodeBlock code={active.code} label={active.label ?? OS_LABELS[value]} />
      {active.note && (
        <p
          style={{
            marginTop: '0.55rem',
            fontSize: 12,
            color: 'rgba(255,255,255,0.48)',
            lineHeight: 1.5,
          }}
        >
          {active.note}
        </p>
      )}
    </div>
  )
}

export function detectOs(): OsKey {
  if (typeof navigator === 'undefined') return 'macos'
  const ua = navigator.userAgent.toLowerCase()
  if (ua.includes('mac')) return 'macos'
  if (ua.includes('win')) return 'windows'
  return 'linux'
}
