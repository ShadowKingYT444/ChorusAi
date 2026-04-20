'use client'

import { motion } from 'framer-motion'
import { FileText, Rocket, ShieldAlert, Sparkles, Waypoints } from 'lucide-react'
import Link from 'next/link'
import type { NetworkStatus } from '@/hooks/use-network-status'
import { getSavedOllamaIp, isOrchestratorConfigured } from '@/lib/api/orchestrator'
import {
  REVIEW_MODES,
  REVIEW_TEMPLATES,
  type ReviewModeId,
  type ReviewTemplateId,
} from '@/lib/review-config'

const TEMPLATE_ICONS: Record<ReviewTemplateId, React.ReactNode> = {
  rfc: <FileText className="h-3.5 w-3.5" />,
  launch: <Rocket className="h-3.5 w-3.5" />,
  architecture: <Waypoints className="h-3.5 w-3.5" />,
  risk: <ShieldAlert className="h-3.5 w-3.5" />,
}

const SUGGESTIONS: Record<ReviewTemplateId, string> = {
  rfc: 'Review this RFC for self-serve workspace provisioning. What would block approval, what evidence is missing, and what is the recommended decision?',
  launch:
    'Review this launch plan for the private beta. What could fail operationally, what should be delayed, and what conditions must be true before launch?',
  architecture:
    'Review this architecture proposal for control-plane owned routing. Compare tradeoffs, hidden constraints, and the safest implementation path.',
  risk:
    'Review this migration plan through a risk lens. What failure modes are underestimated, which mitigations are weak, and what needs escalation now?',
}

interface Props {
  status: NetworkStatus
  selectedTemplate: ReviewTemplateId
  selectedMode: ReviewModeId
  onPickPrompt: (prompt: string, template?: ReviewTemplateId) => void
  onSelectTemplate: (template: ReviewTemplateId) => void
  onSelectMode: (mode: ReviewModeId) => void
}

export function ChorusWelcome({
  status,
  selectedTemplate,
  selectedMode,
  onPickPrompt,
  onSelectTemplate,
  onSelectMode,
}: Props) {
  const needsSetup = !isOrchestratorConfigured() && !getSavedOllamaIp()

  const selectedTemplateDef =
    REVIEW_TEMPLATES.find((template) => template.id === selectedTemplate) ?? REVIEW_TEMPLATES[0]

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-8">
      {needsSetup && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 200, damping: 24 }}
          className="w-full max-w-3xl mb-6"
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
                Connect your private review stack
              </span>
              <span className="font-sans text-[12px] text-white/60 leading-relaxed">
                Configure Ollama, the control plane, and optional workspace routing before you launch reviews.
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
              Open setup
            </span>
          </Link>
        </motion.div>
      )}

      <motion.div
        initial={{ opacity: 0, y: 10, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: 'spring', stiffness: 200, damping: 24 }}
        className="flex flex-col items-center gap-3 mb-8 max-w-2xl"
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
          What needs review?
        </h1>
        <p className="font-sans text-[13.5px] text-white/55 text-center max-w-xl leading-relaxed">
          Paste an RFC, launch plan, architecture proposal, or risk memo. Chorus runs a private
          reviewer swarm, captures disagreement, and returns a report your team can act on.
        </p>
        <div className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-white/38 text-center">
          {selectedTemplateDef.label} · {REVIEW_MODES.find((mode) => mode.id === selectedMode)?.label}
          {' · '}
          {status.online > 0 ? `${status.online} reviewer${status.online === 1 ? '' : 's'} ready` : 'capacity warming up'}
        </div>
      </motion.div>

      <div className="grid w-full max-w-3xl gap-3 lg:grid-cols-[1.4fr_1fr]">
        <div
          className="rounded-2xl p-4"
          style={{
            background: 'rgba(255,255,255,0.025)',
            border: '1px solid rgba(255,255,255,0.06)',
          }}
        >
          <div className="mb-3 font-mono text-[10px] uppercase tracking-[0.12em] text-white/45">
            Starter Prompts
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {REVIEW_TEMPLATES.map((template, index) => (
              <motion.button
                key={template.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.08 + index * 0.05 }}
                onClick={() => onPickPrompt(SUGGESTIONS[template.id], template.id)}
                className="group text-left rounded-xl px-4 py-3 transition-all"
                style={{
                  background:
                    template.id === selectedTemplate ? 'rgba(180,200,255,0.08)' : 'rgba(255,255,255,0.02)',
                  border:
                    template.id === selectedTemplate
                      ? '1px solid rgba(180,200,255,0.22)'
                      : '1px solid rgba(255,255,255,0.06)',
                }}
              >
                <div className="flex items-center gap-2 mb-1.5 text-white/70 group-hover:text-white transition-colors">
                  {TEMPLATE_ICONS[template.id]}
                  <span className="font-mono text-[10.5px] tracking-[0.1em] uppercase">
                    {template.shortLabel}
                  </span>
                </div>
                <div className="mb-1 font-sans text-[13px] text-white/88">{template.label}</div>
                <span className="font-sans text-[12px] text-white/55 leading-relaxed">
                  {template.summary}
                </span>
              </motion.button>
            ))}
          </div>
        </div>

        <div
          className="rounded-2xl p-4"
          style={{
            background: 'rgba(255,255,255,0.025)',
            border: '1px solid rgba(255,255,255,0.06)',
          }}
        >
          <div className="mb-3 font-mono text-[10px] uppercase tracking-[0.12em] text-white/45">
            Review Depth
          </div>
          <div className="grid gap-2">
            {REVIEW_MODES.map((mode) => {
              const active = mode.id === selectedMode
              return (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => onSelectMode(mode.id)}
                  className="rounded-xl px-3 py-3 text-left"
                  style={{
                    background: active ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.02)',
                    border: active
                      ? '1px solid rgba(255,255,255,0.16)'
                      : '1px solid rgba(255,255,255,0.06)',
                  }}
                >
                  <div className="mb-1 flex items-center justify-between gap-3">
                    <span className="font-sans text-[13px] text-white/92">{mode.label}</span>
                    <span className="font-mono text-[10px] text-white/40">
                      {mode.reviewers} / {mode.rounds}
                    </span>
                  </div>
                  <div className="font-sans text-[11.5px] leading-relaxed text-white/55">
                    {mode.summary}
                  </div>
                </button>
              )
            })}
          </div>
          <button
            type="button"
            onClick={() => onSelectTemplate(selectedTemplate)}
            className="mt-3 w-full rounded-xl px-3 py-3 text-left"
            style={{
              background: 'rgba(255,255,255,0.03)',
              border: '1px dashed rgba(255,255,255,0.08)',
            }}
          >
            <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/42">
              Current Focus
            </div>
            <div className="mt-1 font-sans text-[12px] leading-relaxed text-white/62">
              {selectedTemplateDef.reportFocus.charAt(0).toUpperCase() + selectedTemplateDef.reportFocus.slice(1)}.
            </div>
          </button>
        </div>
      </div>
    </div>
  )
}
