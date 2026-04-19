import type {
  JobStatus,
  OperatorView,
  PeerEntry,
  PruneStatus,
  SettlementPreview,
} from '@/lib/api/orchestrator'
import type { AgentMessage, Cluster, ClusterID, SimulationResults, SimulationJob } from '@/lib/mock-data'

export interface SimulationSession extends SimulationJob {
  jobId?: string
  mode: 'mock' | 'backend'
  createdAt: string
  launchedPeers?: PeerEntry[]
}

export interface RuntimeMessage extends AgentMessage {
  slotId: string
  /** Signaling job id - included in feed dedupe so the same peer/text on a new job is not dropped. */
  jobId?: string
  status?: PruneStatus
}

export interface JobRuntimeState {
  session: SimulationSession | null
  status: JobStatus
  currentRound: number
  totalRounds: number
  messages: RuntimeMessage[]
  clusters: Cluster[]
  results: SimulationResults
  settlement: SettlementPreview | null
  operator: OperatorView | null
  loading: boolean
  error: string | null
  /** Live job: from `GET /jobs/{id}`. Mock: from configured env bases. */
  agentCompletionOrigins: string[]
  /** Job create `embedding_model_version`; backend only. */
  embeddingModelVersion: string | null
  /** Live discovery view from signaling server. */
  connectedPeers: PeerEntry[]
  /** Moderator-merged final answer (set at job_done). */
  finalAnswer: string | null
  /** Slot IDs cited by the final answer. */
  citations: string[]
  /** Latest "edge" events for consensus/dissent visualization. */
  edges: RuntimeEdge[]
}

export interface RuntimeEdge {
  round: number
  from: string
  to: string
  kind: 'nearest' | 'furthest'
}

export interface FeedPreviewItem {
  agentId: string
  text: string
  clusterId: ClusterID
}
