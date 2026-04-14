import type {
  OperatorView,
  PruneStatus,
  SettlementPreview,
  SlotRoundAudit,
  WsAgentLineEvent,
  WsEdgeEvent,
} from '@/lib/api/orchestrator'
import {
  CLUSTERS,
  COST_CHART_DATA,
  type AgentMessage,
  type Cluster,
  type ClusterID,
  type SimulationResults,
} from '@/lib/mock-data'
import type { RuntimeMessage, SimulationSession } from '@/lib/runtime/types'

export function slotToClusterId(slotId: string): ClusterID {
  const raw = Array.from(slotId).reduce((acc, ch) => acc + ch.charCodeAt(0), 0)
  const index = (raw % 4) + 1
  return index as ClusterID
}

function statusToType(status: PruneStatus): AgentMessage['type'] {
  if (status === 'pruned') return 'cluster'
  if (status === 'suspect') return 'critique'
  return 'propose'
}

function nowClock(): string {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ─── Text cleaning ────────────────────────────────────────────────────────────

const CHATBOT_OPENER = /^(sure[!,. ]|here (is|are|'s)|of course[!,. ]|certainly[!,. ]|absolutely[!,. ]|i (will|would|can |should)|as an ai|based on (your|the)|my (response|answer|analysis)|let me |please (note|let|see)|this (is|would|simulation)|the (context|prompt|scenario|embedding|following))[^\n]*/i

function cleanPredictionText(text: string): string {
  let s = text
    .replace(/\*\*/g, '')
    .replace(/\*/g, '')
    .replace(/`/g, '')
    .replace(/#+\s*/g, '')
    .replace(/\n+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  // Strip chatbot/meta openers
  s = s.replace(CHATBOT_OPENER, '').replace(/^[:\-–,. ]+/, '').trim()
  if (s.length > 0) s = s.charAt(0).toUpperCase() + s.slice(1)

  // Extract first complete sentence
  const end = s.search(/[.!?](\s|$)/)
  if (end !== -1 && end > 20) return s.slice(0, end + 1).trim()

  // Cap at 160 chars on a word boundary
  if (s.length > 160) {
    const cut = s.lastIndexOf(' ', 160)
    return s.slice(0, cut > 0 ? cut : 160) + '…'
  }
  return s
}

// ─── GPT-4o API equivalent cost per agent-round ───────────────────────────────
// ~500 input tokens + ~200 output tokens at GPT-4o pricing ($2.50/1M in, $10/1M out)
// = $0.00125 + $0.002 = ~$0.004 per call
const CLOUD_COST_PER_AGENT_ROUND = 0.004

// ─── Message adapters ─────────────────────────────────────────────────────────

export function makeMessageFromAgentLine(
  event: WsAgentLineEvent,
  nextId: number,
): RuntimeMessage {
  return {
    id: nextId,
    agentId: event.slot_id,
    slotId: event.slot_id,
    clusterId: slotToClusterId(event.slot_id),
    round: event.round,
    type: statusToType(event.payload.status),
    text: event.payload.snippet || '(no content)',
    timestamp: nowClock(),
    status: event.payload.status,
  }
}

export function makeMessageFromEdge(event: WsEdgeEvent, nextId: number): RuntimeMessage {
  const sourceCluster = slotToClusterId(event.payload.from)
  return {
    id: nextId,
    agentId: event.payload.from,
    slotId: event.payload.from,
    clusterId: sourceCluster,
    round: event.round,
    type: 'cluster',
    text: `${event.payload.from} ${event.payload.kind} -> ${event.payload.to}`,
    timestamp: nowClock(),
  }
}

function completionToType(audit: SlotRoundAudit): AgentMessage['type'] {
  if (audit.prune_status === 'pruned') return 'cluster'
  if (audit.prune_status === 'suspect') return 'critique'
  if ((audit.completion ?? '').toLowerCase().includes('agree')) return 'agree'
  return 'propose'
}

export function operatorToMessages(operator: OperatorView): RuntimeMessage[] {
  const out: RuntimeMessage[] = []
  let idx = 1
  for (const round of operator.rounds) {
    for (const [slotId, audit] of Object.entries(round.slots)) {
      if (!audit.completion) continue
      out.push({
        id: idx++,
        agentId: slotId,
        slotId,
        clusterId: slotToClusterId(slotId),
        round: round.round,
        type: completionToType(audit),
        text: audit.completion,
        timestamp: nowClock(),
        status: audit.prune_status,
      })
    }
    for (const [from, to] of round.nearest_edges) {
      out.push({
        id: idx++,
        agentId: from,
        slotId: from,
        clusterId: slotToClusterId(from),
        round: round.round,
        type: 'cluster',
        text: `${from} nearest -> ${to}`,
        timestamp: nowClock(),
      })
    }
  }
  return out
}

export function buildClustersFromMessages(messages: RuntimeMessage[], agentCount: number): Cluster[] {
  const total = Math.max(1, messages.length)
  return CLUSTERS.map((cluster, i) => {
    const clusterMessages = messages.filter((m) => m.clusterId === cluster.id)
    const confidence = Math.min(95, Math.max(5, Math.round((clusterMessages.length / total) * 100)))
    const estimatedAgents = Math.max(
      1,
      Math.round((agentCount * clusterMessages.length) / Math.max(1, messages.length)),
    )
    const stanceMsg = clusterMessages.find(
      (m) => m.type !== 'cluster' && m.text && m.text !== '(no content)',
    )
    const stance = stanceMsg
      ? cleanPredictionText(stanceMsg.text) || `Cluster ${i + 1} collecting consensus signals`
      : `Cluster ${i + 1} collecting consensus signals`
    return { ...cluster, confidence, agentCount: estimatedAgents, stance }
  })
}

export function buildResults(
  session: SimulationSession | null,
  messages: RuntimeMessage[],
  settlement: SettlementPreview | null,
): SimulationResults {
  if (!session) {
    return {
      finalPrediction: 'No simulation session found.',
      confidenceScore: 0,
      costActual: 0,
      costCloud: 0,
      agentCount: 0,
      rounds: 0,
      totalMessages: 0,
      wallTimeSeconds: 0,
      nodesContributing: 0,
    }
  }

  let finalPrediction = 'Consensus still forming. Awaiting more agent outputs.'
  const realMessages = messages.filter(
    m => m.type !== 'cluster' && m.text && m.text !== '(no content)',
  )
  if (realMessages.length > 0) {
    const lastRound = Math.max(...realMessages.map(m => m.round))
    const lastRoundMsgs = realMessages.filter(m => m.round === lastRound)
    // Prefer shortest message ≥ 40 chars — small models give more focused short answers
    const candidates = lastRoundMsgs.filter(m => m.text.length >= 40)
    const best = candidates.length > 0
      ? candidates.reduce((a, b) => a.text.length <= b.text.length ? a : b)
      : lastRoundMsgs.reduce((a, b) => b.text.length > a.text.length ? b : a)
    finalPrediction = cleanPredictionText(best.text)
  }

  const nodesContributing = new Set(messages.map((m) => m.slotId)).size
  const confidenceScore = settlement
    ? Math.min(99, Math.max(1, Math.round(
        (Object.keys(settlement.payouts).length / Math.max(1, session.agentCount)) * 100,
      )))
    : Math.min(95, Math.max(10, Math.round(
        (nodesContributing / Math.max(1, session.agentCount)) * 100,
      )))

  return {
    finalPrediction,
    confidenceScore,
    costActual: session.bounty,
    costCloud: Number((session.agentCount * session.rounds * CLOUD_COST_PER_AGENT_ROUND).toFixed(2)),
    agentCount: session.agentCount,
    rounds: session.rounds,
    totalMessages: messages.length,
    wallTimeSeconds: Math.max(1, Math.round(messages.length * 0.8)),
    nodesContributing,
  }
}

export function buildCostChartData(results: SimulationResults) {
  return [
    { label: COST_CHART_DATA[0].label, cost: results.costCloud },
    { label: COST_CHART_DATA[1].label, cost: results.costActual },
  ]
}
