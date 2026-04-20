'use client'

import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import type { AgentContribution, AgentContributionStatus } from '@/lib/runtime/agent-contributions'

const TOP_N = 12

const CLUSTER_DOT: Record<1 | 2 | 3 | 4, string> = {
  1: 'bg-sky-400',
  2: 'bg-violet-400',
  3: 'bg-emerald-400',
  4: 'bg-amber-400',
}

const STATUS_PILL: Record<AgentContributionStatus, string> = {
  strong: 'bg-emerald-500/15 text-emerald-300 border-emerald-400/20',
  mixed: 'bg-amber-500/15 text-amber-300 border-amber-400/20',
  weak: 'bg-rose-500/15 text-rose-300 border-rose-400/20',
}

const STATUS_BAR: Record<AgentContributionStatus, string> = {
  strong: 'bg-emerald-400',
  mixed: 'bg-amber-400',
  weak: 'bg-rose-400',
}

function formatPayout(n: number): string {
  if (!Number.isFinite(n) || n === 0) return '0'
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 })
  if (Math.abs(n) >= 1) return n.toFixed(2)
  return n.toFixed(4)
}

function ContributionCard({ c }: { c: AgentContribution }) {
  const pct = Math.round(Math.min(1, Math.max(0, c.usefulness)) * 100)
  return (
    <div className="rounded-sm border border-white/7 bg-white/[0.02] p-4 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span className={cn('w-2 h-2 rounded-full shrink-0', CLUSTER_DOT[c.clusterId])} />
        <span className="font-mono text-[13px] text-white/80 truncate">{c.agentId}</span>
        <span
          className={cn(
            'ml-auto text-[9px] uppercase tracking-[0.14em] px-1.5 py-0.5 rounded-sm border',
            STATUS_PILL[c.status],
          )}
        >
          {c.status}
        </span>
      </div>

      <div>
        <div className="flex justify-between mb-1.5">
          <span className="font-mono text-[9px] tracking-[0.12em] text-white/30">USEFULNESS</span>
          <span className="font-mono text-[10px] text-white/55">{pct}%</span>
        </div>
        <div className="h-1 bg-white/5 rounded-sm overflow-hidden">
          <div
            className={cn('h-full transition-all duration-300', STATUS_BAR[c.status])}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 pt-1 border-t border-white/5">
        <Stat label="PASSES" value={String(c.roundsActive)} />
        <Stat label="AVG MS" value={c.avgLatencyMs > 0 ? String(Math.round(c.avgLatencyMs)) : '-'} />
        <Stat
          label="V/S/P"
          value={`${c.validCount}/${c.suspectCount}/${c.prunedCount}`}
        />
      </div>

      <div>
        <div className="font-mono text-[9px] tracking-[0.12em] text-white/30 mb-1">WEIGHT</div>
        <div className="font-mono text-[14px] text-white/80">{formatPayout(c.payout)}</div>
        {c.payoutBreakdown ? (
          <div className="font-mono text-[9px] text-white/35 mt-1">
            {formatPayout(c.payoutBreakdown.floor)} baseline + {formatPayout(c.consensusBonus)} alignment +{' '}
            {formatPayout(c.dissentBonus)} dissent
          </div>
        ) : (
          <div className="font-mono text-[9px] text-white/25 mt-1">breakdown pending</div>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-mono text-[9px] tracking-[0.12em] text-white/30">{label}</div>
      <div className="font-mono text-[12px] text-white/70">{value}</div>
    </div>
  )
}

export function AgentContributions({ contributions }: { contributions: AgentContribution[] }) {
  const [showAll, setShowAll] = useState(false)

  const { visible, hiddenCount } = useMemo(() => {
    if (contributions.length <= TOP_N || showAll) {
      return { visible: contributions, hiddenCount: 0 }
    }
    return { visible: contributions.slice(0, TOP_N), hiddenCount: contributions.length - TOP_N }
  }, [contributions, showAll])

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="font-display text-xl text-white/85 tracking-tight">Reviewer Contributions</h2>
        <p className="font-mono text-[10px] tracking-[0.12em] text-white/30">
          PER-REVIEWER USEFULNESS, WEIGHT, AND RELIABILITY
        </p>
      </div>

      {contributions.length === 0 ? (
        <div className="font-mono text-[12px] text-white/35 py-8 text-center border border-dashed border-white/8 rounded-sm">
          Waiting for reviewer contributions...
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {visible.map((c) => (
              <ContributionCard key={c.slotId} c={c} />
            ))}
          </div>
          {(hiddenCount > 0 || showAll) && contributions.length > TOP_N ? (
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="self-start font-mono text-[10px] tracking-[0.14em] text-white/55 hover:text-white/85 transition-colors px-3 py-1.5 border border-white/10 rounded-sm"
            >
              {showAll ? 'SHOW TOP 12' : `SHOW ALL (${contributions.length})`}
            </button>
          ) : null}
        </>
      )}
    </section>
  )
}
