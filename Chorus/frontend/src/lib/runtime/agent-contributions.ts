import type { PayoutBreakdown, SettlementPreview } from '@/lib/api/orchestrator'
import { slotToClusterId } from '@/lib/runtime/adapter'
import type { RuntimeMessage } from '@/lib/runtime/types'

export type AgentContributionStatus = 'strong' | 'mixed' | 'weak'

export interface AgentContribution {
  slotId: string
  agentId: string
  clusterId: 1 | 2 | 3 | 4
  roundsActive: number
  validCount: number
  suspectCount: number
  prunedCount: number
  avgLatencyMs: number
  impactWeight: number
  payout: number
  payoutBreakdown: PayoutBreakdown | null
  consensusBonus: number
  dissentBonus: number
  usefulness: number
  status: AgentContributionStatus
}

interface Aggregate {
  slotId: string
  rounds: Set<number>
  validCount: number
  suspectCount: number
  prunedCount: number
}

function statusFromUsefulness(u: number): AgentContributionStatus {
  if (u > 0.66) return 'strong'
  if (u < 0.33) return 'weak'
  return 'mixed'
}

export function buildAgentContributions(
  messages: RuntimeMessage[],
  settlement: SettlementPreview | null,
): AgentContribution[] {
  const bySlot = new Map<string, Aggregate>()

  for (const m of messages) {
    let agg = bySlot.get(m.slotId)
    if (!agg) {
      agg = {
        slotId: m.slotId,
        rounds: new Set<number>(),
        validCount: 0,
        suspectCount: 0,
        prunedCount: 0,
      }
      bySlot.set(m.slotId, agg)
    }
    agg.rounds.add(m.round)
    const status = m.status ?? 'valid'
    if (status === 'pruned') agg.prunedCount += 1
    else if (status === 'suspect') agg.suspectCount += 1
    else agg.validCount += 1
  }

  // Also surface slots present in settlement but with no messages yet.
  if (settlement) {
    for (const slotId of Object.keys(settlement.payouts)) {
      if (!bySlot.has(slotId)) {
        bySlot.set(slotId, {
          slotId,
          rounds: new Set<number>(),
          validCount: 0,
          suspectCount: 0,
          prunedCount: 0,
        })
      }
    }
  }

  const impactWeights = settlement?.impact_weights ?? {}
  const maxImpact = Object.values(impactWeights).reduce((a, b) => (b > a ? b : a), 0)

  const out: AgentContribution[] = []
  for (const agg of bySlot.values()) {
    const impactWeight = impactWeights[agg.slotId] ?? 0
    const payout = settlement?.payouts[agg.slotId] ?? 0
    const breakdown = settlement?.payout_breakdown?.[agg.slotId] ?? null

    const totalObserved = agg.validCount + agg.suspectCount + agg.prunedCount
    const validRatio = totalObserved > 0 ? agg.validCount / totalObserved : 0
    const normalizedImpact = maxImpact > 0 ? impactWeight / maxImpact : 0

    const usefulness = settlement
      ? 0.6 * normalizedImpact + 0.4 * validRatio
      : validRatio

    out.push({
      slotId: agg.slotId,
      agentId: agg.slotId.split('#')[0],
      clusterId: slotToClusterId(agg.slotId),
      roundsActive: agg.rounds.size,
      validCount: agg.validCount,
      suspectCount: agg.suspectCount,
      prunedCount: agg.prunedCount,
      avgLatencyMs: 0,
      impactWeight,
      payout,
      payoutBreakdown: breakdown,
      consensusBonus: breakdown?.consensus_bonus ?? 0,
      dissentBonus: breakdown?.dissent_bonus ?? 0,
      usefulness,
      status: statusFromUsefulness(usefulness),
    })
  }

  out.sort((a, b) => {
    if (b.usefulness !== a.usefulness) return b.usefulness - a.usefulness
    return b.payout - a.payout
  })
  return out
}
