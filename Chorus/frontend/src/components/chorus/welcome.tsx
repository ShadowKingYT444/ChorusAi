'use client'

import { motion } from 'framer-motion'
import { FileText, Rocket, ShieldAlert, Sparkles } from 'lucide-react'
import Link from 'next/link'
import type { NetworkStatus } from '@/hooks/use-network-status'
import { getSavedOllamaIp, isOrchestratorConfigured } from '@/lib/api/orchestrator'

const STARTER_PROMPTS = [
  {
    label: 'Launch plan',
    icon: <Rocket className="h-3.5 w-3.5" />,
    prompt:
      'Review this launch plan. Identify blockers, weak rollback criteria, missing instrumentation, and the single most important change before launch.',
  },
  {
    label: 'RFC',
    icon: <FileText className="h-3.5 w-3.5" />,
    prompt:
      'Review this RFC. Surface the strongest arguments for approval, the sharpest objections, missing evidence, and your final recommendation.',
  },
  {
    label: 'Risk memo',
    icon: <ShieldAlert className="h-3.5 w-3.5" />,
    prompt:
      'Review this plan through a risk lens. Identify underestimated failure modes, mitigation gaps, and what should be escalated now.',
  },
]

interface Props {
  status: NetworkStatus
  onPickPrompt: (prompt: string) => void
}

export function ChorusWelcome({ status, onPickPrompt }: Props) {
  const needsSetup = !isOrchestratorConfigured() && !getSavedOllamaIp()

  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-8">
      {needsSetup && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 200, damping: 24 }}
          className="mb-6 w-full max-w-3xl"
        >
          <Link
            href="/setup"
            className="group flex items-center gap-3 rounded-xl px-4 py-3.5 transition-all"
            style={{
              background:
                'linear-gradient(135deg, rgba(180,200,255,0.12), rgba(200,180,255,0.06))',
              border: '1px solid rgba(180,200,255,0.28)',
              boxShadow: '0 20px 60px -28px rgba(180,200,255,0.55)',
            }}
          >
            <div
              className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
              style={{
                background:
                  'linear-gradient(135deg, rgba(180,200,255,0.35), rgba(255,255,255,0.05))',
                border: '1px solid rgba(255,255,255,0.12)',
              }}
            >
              <Rocket className="h-4 w-4 text-white/90" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-sans text-[13.5px] font-semibold text-white/95">
                Finish setup before opening the workspace
              </div>
              <div className="font-sans text-[12px] leading-relaxed text-white/60">
                Connect Ollama, verify the control plane, and enter your workspace token in setup first.
              </div>
            </div>
            <span
              className="shrink-0 rounded-md px-3 py-1.5 font-sans text-[12px] font-semibold text-white/95 transition-colors group-hover:bg-white/15"
              style={{
                background: 'rgba(255,255,255,0.1)',
                border: '1px solid rgba(255,255,255,0.18)',
              }}
            >
              Open setup
            </span>
          </Link>
        </motion.div>
      )}

      <motion.div
        initial={{ opacity: 0, y: 10, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: 'spring', stiffness: 200, damping: 24 }}
        className="mb-8 flex max-w-2xl flex-col items-center gap-3"
      >
        <div
          className="relative grid h-14 w-14 place-items-center rounded-2xl"
          style={{
            background: 'linear-gradient(135deg, rgba(180,200,255,0.35), rgba(200,180,255,0.1))',
            border: '1px solid rgba(255,255,255,0.1)',
            boxShadow: '0 20px 60px -20px rgba(180,200,255,0.45)',
          }}
        >
          <Sparkles className="h-6 w-6 text-white/95" />
        </div>

        <h1
          className="text-center font-sans tracking-tight text-white/95"
          style={{ fontSize: 'clamp(1.6rem, 3.2vw, 2.4rem)', fontWeight: 600, lineHeight: 1.1 }}
        >
          Paste what you want reviewed
        </h1>
        <p className="max-w-xl text-center font-sans text-[13.5px] leading-relaxed text-white/55">
          Chorus runs a private reviewer swarm against your brief and returns a direct verdict with support, objections, and blind spots.
        </p>
        <div className="text-center font-mono text-[10.5px] uppercase tracking-[0.12em] text-white/38">
          {status.online > 0 ? `${status.online} reviewer${status.online === 1 ? '' : 's'} ready` : 'capacity warming up'}
        </div>
      </motion.div>

      <div className="grid w-full max-w-3xl gap-3 sm:grid-cols-3">
        {STARTER_PROMPTS.map((entry, index) => (
          <motion.button
            key={entry.label}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.08 + index * 0.05 }}
            onClick={() => onPickPrompt(entry.prompt)}
            className="group rounded-xl px-4 py-4 text-left transition-all"
            style={{
              background: 'rgba(255,255,255,0.025)',
              border: '1px solid rgba(255,255,255,0.06)',
            }}
          >
            <div className="mb-2 flex items-center gap-2 text-white/70 transition-colors group-hover:text-white">
              {entry.icon}
              <span className="font-mono text-[10.5px] uppercase tracking-[0.1em]">
                {entry.label}
              </span>
            </div>
            <div className="font-sans text-[12px] leading-relaxed text-white/58">
              {entry.prompt}
            </div>
          </motion.button>
        ))}
      </div>
    </div>
  )
}
