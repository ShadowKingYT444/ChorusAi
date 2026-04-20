'use client'

import { useCallback, useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { ArrowUp, Paperclip, Sparkles } from 'lucide-react'
import { Textarea } from '@/components/ui/textarea'
import type { NetworkStatus } from '@/hooks/use-network-status'
import { cn } from '@/lib/utils'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  disabled?: boolean
  status: NetworkStatus
  readyPeerCount?: number
  placeholder?: string
  templateLabel: string
  modeLabel: string
  deliverable: string
}

export function ChorusComposer({
  value,
  onChange,
  onSubmit,
  disabled,
  status,
  readyPeerCount,
  placeholder = 'Paste the plan, RFC, or brief you want reviewed…',
  templateLabel,
  modeLabel,
  deliverable,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const hasText = value.trim().length > 0
  const effectiveReady = readyPeerCount ?? status.online
  const canSend = hasText && !disabled

  const autoSize = useCallback(() => {
    const el = ref.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${Math.min(220, el.scrollHeight)}px`
  }, [])

  useEffect(() => {
    autoSize()
  }, [value, autoSize])

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.metaKey) {
      e.preventDefault()
      if (canSend) onSubmit()
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 220, damping: 26 }}
      className="relative w-full"
    >
      <div
        className="relative rounded-2xl overflow-hidden"
        style={{
          background: 'rgba(18,18,22,0.72)',
          border: '1px solid rgba(255,255,255,0.08)',
          boxShadow: '0 18px 60px -20px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.04)',
          backdropFilter: 'blur(14px)',
        }}
      >
        <div
          className="mx-4 mt-3 flex flex-wrap items-center gap-2 border-b border-white/6 pb-2"
        >
          <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-white/72">
            {templateLabel}
          </span>
          <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-white/72">
            {modeLabel}
          </span>
          <span className="font-sans text-[11.5px] text-white/45">
            Output: {deliverable}
          </span>
        </div>

        <div className="flex gap-3 px-4 pt-3 pb-2">
          <Textarea
            ref={ref}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKey}
            placeholder={placeholder}
            rows={1}
            className={cn(
              'flex-1 min-h-[72px] max-h-[220px] resize-none bg-transparent border-none px-0 py-1',
              'font-sans text-[14.5px] text-white/90 placeholder:text-white/35 leading-relaxed',
              'focus:outline-none focus-visible:ring-0 focus-visible:ring-offset-0',
            )}
          />
        </div>

        <div className="flex items-center gap-2 px-3 pb-3 pt-1">
          <button
            type="button"
            aria-label="attach"
            className="p-2 rounded-md text-white/55 hover:text-white hover:bg-white/5 transition-colors"
          >
            <Paperclip className="w-4 h-4" />
          </button>
          <button
            type="button"
            aria-label="enhance prompt"
            className="p-2 rounded-md text-white/55 hover:text-white hover:bg-white/5 transition-colors"
          >
            <Sparkles className="w-4 h-4" />
          </button>

          <div className="flex-1 min-w-0 px-1 font-sans text-[11.5px] text-white/42">
            Focus on the decision, evidence, constraints, and what would change the verdict.
          </div>

          <button
            type="button"
            onClick={() => canSend && onSubmit()}
            disabled={!canSend}
            aria-label="send"
            className="h-9 w-9 grid place-items-center rounded-lg transition-all"
            style={{
              background: canSend ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.06)',
              color: canSend ? '#0b0b0e' : 'rgba(255,255,255,0.35)',
              cursor: canSend ? 'pointer' : 'not-allowed',
              boxShadow: canSend ? '0 6px 22px -6px rgba(180,200,255,0.5)' : 'none',
            }}
          >
            <ArrowUp className="w-4 h-4" strokeWidth={2.4} />
          </button>
        </div>
      </div>

      <div className="mt-2 text-center font-mono text-[10px] text-white/35 tracking-[0.08em]">
        {status.mode === 'live'
          ? effectiveReady > 0
            ? `Review capacity live · ${effectiveReady} reviewer${effectiveReady === 1 ? '' : 's'} visible plus any managed anchors`
            : 'Review capacity live · browser reviewers hidden, managed anchors may still route the job'
          : status.mode === 'unconfigured'
          ? 'No control plane set · open /setup to connect'
          : 'Control plane unreachable · check NEXT_PUBLIC_ORCHESTRATOR_BASE_URL'}
      </div>
    </motion.div>
  )
}
