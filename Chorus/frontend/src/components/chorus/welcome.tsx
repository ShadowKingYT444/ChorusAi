'use client'

import { motion } from 'framer-motion'
import { Flame, MessageCircleQuestion, Rocket, Scale, Sparkles } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import type { NetworkStatus } from '@/hooks/use-network-status'
import { getSavedOllamaIp, isOrchestratorConfigured } from '@/lib/api/orchestrator'
import { isDemoMode } from '@/lib/runtime/demo-mode'

const SUGGESTIONS: { icon: React.ReactNode; title: string; prompt: string }[] = [
  {
    icon: <Scale className="w-3.5 h-3.5" />,
    title: 'Debate',
    prompt: 'Debate whether our team should migrate from Postgres to CockroachDB.',
  },
  {
    icon: <MessageCircleQuestion className="w-3.5 h-3.5" />,
    title: 'Interrogate',
    prompt: 'Stress-test this plan: launch a referral program in 14 days with $5k budget.',
  },
  {
    icon: <Flame className="w-3.5 h-3.5" />,
    title: 'Brainstorm',
    prompt: 'Generate 10 wildly different names for a distributed AI company.',
  },
  {
    icon: <Sparkles className="w-3.5 h-3.5" />,
    title: 'Synthesize',
    prompt: 'Summarize the tradeoffs between mixture-of-experts and distillation.',
  },
]

interface Props {
  status: NetworkStatus
  onPick: (prompt: string) => void
}

export function ChorusWelcome({ status, onPick }: Props) {
  const [needsSetup, setNeedsSetup] = useState<boolean>(false)

  useEffect(() => {
    if (isDemoMode()) {
      setNeedsSetup(false)
      return
    }
    setNeedsSetup(!isOrchestratorConfigured() && !getSavedOllamaIp())
  }, [])

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-8">
      {needsSetup && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 200, damping: 24 }}
          className="w-full max-w-2xl mb-6"
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
              className="shrink-0 grid place-items-center w-9 h-9 rounded-lg"
              style={{
                background:
                  'linear-gradient(135deg, rgba(180,200,255,0.35), rgba(255,255,255,0.05))',
                border: '1px solid rgba(255,255,255,0.12)',
              }}
            >
              <Rocket className="w-4 h-4 text-white/90" />
            </div>
            <div className="flex flex-col min-w-0 flex-1">
              <span
                className="font-sans text-[13.5px] text-white/95 tracking-tight"
                style={{ fontWeight: 600 }}
              >
                Connect your node
              </span>
              <span className="font-sans text-[12px] text-white/60 leading-relaxed">
                Install Ollama locally and wire it into Chorus in a few minutes.
              </span>
            </div>
            <span
              className="shrink-0 px-3 py-1.5 rounded-md font-sans text-[12px] text-white/95 transition-colors group-hover:bg-white/15"
              style={{
                background: 'rgba(255,255,255,0.1)',
                border: '1px solid rgba(255,255,255,0.18)',
                fontWeight: 600,
              }}
            >
              Get started
            </span>
          </Link>
        </motion.div>
      )}
      <motion.div
        initial={{ opacity: 0, y: 10, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: 'spring', stiffness: 200, damping: 24 }}
        className="flex flex-col items-center gap-3 mb-10"
      >
        <div
          className="relative w-14 h-14 rounded-2xl grid place-items-center"
          style={{
            background: 'linear-gradient(135deg, rgba(180,200,255,0.35), rgba(200,180,255,0.1))',
            border: '1px solid rgba(255,255,255,0.1)',
            boxShadow: '0 20px 60px -20px rgba(180,200,255,0.45)',
          }}
        >
          <Sparkles className="w-6 h-6 text-white/95" />
          <motion.div
            aria-hidden
            className="absolute inset-0 rounded-2xl"
            style={{ border: '1px solid rgba(180,200,255,0.5)' }}
            animate={{ scale: [1, 1.25, 1.4], opacity: [0.6, 0.2, 0] }}
            transition={{ duration: 2.6, repeat: Infinity, ease: 'easeOut' }}
          />
        </div>

        <h1
          className="font-sans text-white/95 tracking-tight text-center"
          style={{ fontSize: 'clamp(1.6rem, 3.2vw, 2.4rem)', fontWeight: 600, lineHeight: 1.1 }}
        >
          What should the chorus debate?
        </h1>
        <p className="font-sans text-[13.5px] text-white/55 text-center max-w-md leading-relaxed">
          Send one prompt. {status.online > 0 ? status.online : 'Many'} agents reply in parallel,
          critique each other, and converge on an answer.
        </p>
      </motion.div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-2xl">
        {SUGGESTIONS.map((s, i) => (
          <motion.button
            key={s.title}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.08 + i * 0.05 }}
            onClick={() => onPick(s.prompt)}
            className="group text-left rounded-xl px-4 py-3 transition-all"
            style={{
              background: 'rgba(255,255,255,0.025)',
              border: '1px solid rgba(255,255,255,0.06)',
            }}
          >
            <div className="flex items-center gap-2 mb-1.5 text-white/70 group-hover:text-white transition-colors">
              {s.icon}
              <span className="font-mono text-[10.5px] tracking-[0.1em] uppercase">
                {s.title}
              </span>
            </div>
            <span className="font-sans text-[13px] text-white/85 leading-relaxed">
              {s.prompt}
            </span>
          </motion.button>
        ))}
      </div>
    </div>
  )
}
