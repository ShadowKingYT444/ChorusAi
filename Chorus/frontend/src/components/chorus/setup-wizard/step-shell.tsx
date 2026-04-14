'use client'

import { motion } from 'framer-motion'
import type { ReactNode } from 'react'

interface Props {
  icon: ReactNode
  eyebrow: string
  title: string
  subtitle?: string
  children: ReactNode
}

export function StepShell({ icon, eyebrow, title, subtitle, children }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.55rem' }}>
        <div
          aria-hidden
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 36,
            height: 36,
            borderRadius: 8,
            background: 'linear-gradient(135deg, rgba(180,200,255,0.22), rgba(200,180,255,0.06))',
            border: '1px solid rgba(255,255,255,0.1)',
            color: 'rgba(255,255,255,0.92)',
          }}
        >
          {icon}
        </div>
        <p
          style={{
            fontSize: 10.5,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.42)',
            fontFamily: 'var(--font-geist-mono), monospace',
            margin: 0,
          }}
        >
          {eyebrow}
        </p>
        <h2
          style={{
            fontSize: '1.45rem',
            fontWeight: 600,
            letterSpacing: '-0.01em',
            color: 'rgba(255,255,255,0.95)',
            margin: 0,
            lineHeight: 1.15,
          }}
        >
          {title}
        </h2>
        {subtitle && (
          <p
            style={{
              fontSize: 13.5,
              lineHeight: 1.55,
              color: 'rgba(255,255,255,0.58)',
              margin: 0,
            }}
          >
            {subtitle}
          </p>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>{children}</div>
    </motion.div>
  )
}
