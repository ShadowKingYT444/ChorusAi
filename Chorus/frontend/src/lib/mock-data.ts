// ─── Legacy type stubs (kept for unused legacy components) ────────────────────
/** Minimal node shape (avoids optional `@xyflow/react` dependency). */
export interface LegacyFlowNode<T> {
  id: string
  data: T
  position?: { x: number; y: number }
}
/** Minimal edge shape for legacy graph placeholders. */
export interface LegacyFlowEdge {
  id: string
  source: string
  target: string
  animated?: boolean
}

export type NodeColorType = 'teal' | 'risk' | 'amber' | 'purple' | 'neutral'
export interface CausalNodeData { label: string; value: string; delta: string; colorType: NodeColorType; [key: string]: unknown }
export type AgentType = 'Membrane' | 'Orchestrator' | 'Watchdog' | 'Auditor'
export const GRAPH_NODES: LegacyFlowNode<CausalNodeData>[] = []
export const GRAPH_EDGES: LegacyFlowEdge[] = []
export const INITIAL_QUERIES: string[] = []
export const CHART_DATA: { month: string; toolCalls: number; blocked: number; latencyP99: number }[] = []

// ─── Agent Info (for node panel) ─────────────────────────────────────────────

export interface AgentInfo {
  role: string
  strategy: string
  connectionReason: string
}

const C1_ROLES = ['Lead Analyst', 'Systems Architect', 'Research Synthesizer', 'Evidence Reviewer', 'Methodology Expert', 'Data Interpreter', 'Hypothesis Builder', 'Precision Auditor', 'Scope Specialist', 'Assumption Challenger', 'Pattern Recognizer', 'Integration Analyst', 'Depth Researcher', 'Context Mapper', 'Risk Evaluator', 'Priority Ranker', 'Solution Architect', 'Baseline Analyst', 'Verification Lead']
const C2_ROLES = ['Critical Reviewer', 'Devil\'s Advocate', 'Counter-Analyst', 'Limitation Finder', 'Bias Detector', 'Edge Case Hunter', 'Stress Tester', 'Regression Analyst', 'Assumption Auditor', 'Failure Mode Analyst', 'Gap Identifier', 'Quality Assessor', 'Risk Modeler', 'Scenario Planner']
const C3_ROLES = ['Neutral Observer', 'Meta-Analyst', 'Data Scientist', 'Cross-Domain Expert', 'Framework Builder', 'Synthesis Lead', 'Calibration Analyst', 'Uncertainty Modeler', 'Evidence Weigher', 'Consensus Tracker', 'Methodology Reviewer']
const C4_ROLES = ['Outlier Detector', 'Uncertainty Specialist', 'Tail-Risk Analyst', 'Edge Scenario Planner', 'Anomaly Hunter', 'Black Swan Analyst']

const C1_STRATEGY = 'Building constructive thesis — assembling supporting evidence and primary analysis to form initial conclusions'
const C2_STRATEGY = 'Adversarial review — identifying weaknesses, gaps, and counter-arguments to stress-test the primary analysis'
const C3_STRATEGY = 'Neutral synthesis — withholding directional commitment until all evidence streams are weighed'
const C4_STRATEGY = 'Uncertainty mapping — monitoring edge cases, tail risks, and scenarios others may underweight'

const C1_WHY = 'Driving the initial analysis forward — aggregating signals and evidence to build a coherent thesis'
const C2_WHY = 'Stress-testing the primary thesis — correlating counter-evidence to ensure robustness'
const C3_WHY = 'Maintaining neutral stance — acting as an impartial arbiter between competing perspectives'
const C4_WHY = 'Monitoring low-probability high-impact scenarios — preserving independent assessment of tail risks'

function makeAgentInfo(idx: number, clusterId: ClusterID): AgentInfo {
  if (clusterId === 1) return { role: C1_ROLES[idx % C1_ROLES.length], strategy: C1_STRATEGY, connectionReason: C1_WHY }
  if (clusterId === 2) return { role: C2_ROLES[idx % C2_ROLES.length], strategy: C2_STRATEGY, connectionReason: C2_WHY }
  if (clusterId === 3) return { role: C3_ROLES[idx % C3_ROLES.length], strategy: C3_STRATEGY, connectionReason: C3_WHY }
  return { role: C4_ROLES[idx % C4_ROLES.length], strategy: C4_STRATEGY, connectionReason: C4_WHY }
}

// ─── Simulation Job ───────────────────────────────────────────────────────────

export interface SimulationJob {
  prompt: string
  agentCount: number
  rounds: number
  bounty: number
}

export const SAMPLE_JOB: SimulationJob = {
  prompt: 'Analyze the pros and cons of migrating our monolith to microservices. Consider team size, current technical debt, and a 6-month timeline.',
  agentCount: 50,
  rounds: 3,
  bounty: 0.10,
}

// ─── Clusters ─────────────────────────────────────────────────────────────────

export type ClusterID = 1 | 2 | 3 | 4

export interface Cluster {
  id: ClusterID
  name: string
  color: string
  colorDim: string
  agentCount: number
  stance: string
  confidence: number
}

export const CLUSTERS: Cluster[] = [
  {
    id: 1,
    name: 'Cluster 1',
    color: 'rgba(255,255,255,0.88)',
    colorDim: 'rgba(255,255,255,0.40)',
    agentCount: 19,
    stance: 'Supportive — strong case for migration with phased approach',
    confidence: 71,
  },
  {
    id: 2,
    name: 'Cluster 2',
    color: 'rgba(255,255,255,0.65)',
    colorDim: 'rgba(255,255,255,0.28)',
    agentCount: 14,
    stance: 'Cautious — risks outweigh benefits given current constraints',
    confidence: 63,
  },
  {
    id: 3,
    name: 'Cluster 3',
    color: 'rgba(255,255,255,0.45)',
    colorDim: 'rgba(255,255,255,0.18)',
    agentCount: 11,
    stance: 'Neutral — insufficient data to commit to a direction',
    confidence: 58,
  },
  {
    id: 4,
    name: 'Cluster 4',
    color: 'rgba(255,255,255,0.30)',
    colorDim: 'rgba(255,255,255,0.12)',
    agentCount: 6,
    stance: 'Risk-focused — underestimated failure modes need attention',
    confidence: 44,
  },
]

// ─── Agent Network Nodes ───────────────────────────────────────────────────────

export interface AgentNode {
  id: string
  clusterId: ClusterID
  x: number
  y: number
  messageCount: number
}

// 50 agents distributed across 4 clusters, laid out in rough cluster groups
export const AGENT_NODES: AgentNode[] = [
  // Cluster 1 — top-left region
  { id: 'a01', clusterId: 1, x: 120, y: 130, messageCount: 7 },
  { id: 'a02', clusterId: 1, x: 180, y: 95,  messageCount: 4 },
  { id: 'a03', clusterId: 1, x: 210, y: 165, messageCount: 9 },
  { id: 'a04', clusterId: 1, x: 145, y: 200, messageCount: 3 },
  { id: 'a05', clusterId: 1, x: 260, y: 125, messageCount: 6 },
  { id: 'a06', clusterId: 1, x: 90,  y: 175, messageCount: 5 },
  { id: 'a07', clusterId: 1, x: 195, y: 230, messageCount: 8 },
  { id: 'a08', clusterId: 1, x: 155, y: 70,  messageCount: 2 },
  { id: 'a09', clusterId: 1, x: 235, y: 85,  messageCount: 11 },
  { id: 'a10', clusterId: 1, x: 100, y: 235, messageCount: 4 },
  { id: 'a11', clusterId: 1, x: 275, y: 185, messageCount: 6 },
  { id: 'a12', clusterId: 1, x: 165, y: 145, messageCount: 13 },
  { id: 'a13', clusterId: 1, x: 220, y: 215, messageCount: 5 },
  { id: 'a14', clusterId: 1, x: 130, y: 160, messageCount: 7 },
  { id: 'a15', clusterId: 1, x: 70,  y: 140, messageCount: 3 },
  { id: 'a16', clusterId: 1, x: 250, y: 155, messageCount: 9 },
  { id: 'a17', clusterId: 1, x: 185, y: 110, messageCount: 4 },
  { id: 'a18', clusterId: 1, x: 115, y: 115, messageCount: 6 },
  { id: 'a19', clusterId: 1, x: 245, y: 220, messageCount: 8 },
  // Cluster 2 — top-right region
  { id: 'a20', clusterId: 2, x: 520, y: 110, messageCount: 6 },
  { id: 'a21', clusterId: 2, x: 570, y: 155, messageCount: 4 },
  { id: 'a22', clusterId: 2, x: 600, y: 90,  messageCount: 9 },
  { id: 'a23', clusterId: 2, x: 545, y: 200, messageCount: 3 },
  { id: 'a24', clusterId: 2, x: 620, y: 145, messageCount: 7 },
  { id: 'a25', clusterId: 2, x: 490, y: 160, messageCount: 5 },
  { id: 'a26', clusterId: 2, x: 555, y: 75,  messageCount: 11 },
  { id: 'a27', clusterId: 2, x: 640, y: 200, messageCount: 2 },
  { id: 'a28', clusterId: 2, x: 585, y: 220, messageCount: 8 },
  { id: 'a29', clusterId: 2, x: 510, y: 230, messageCount: 4 },
  { id: 'a30', clusterId: 2, x: 530, y: 135, messageCount: 6 },
  { id: 'a31', clusterId: 2, x: 660, y: 135, messageCount: 12 },
  { id: 'a32', clusterId: 2, x: 605, y: 175, messageCount: 5 },
  { id: 'a33', clusterId: 2, x: 475, y: 115, messageCount: 7 },
  // Cluster 3 — bottom-left region
  { id: 'a34', clusterId: 3, x: 130, y: 390, messageCount: 5 },
  { id: 'a35', clusterId: 3, x: 185, y: 420, messageCount: 3 },
  { id: 'a36', clusterId: 3, x: 220, y: 370, messageCount: 8 },
  { id: 'a37', clusterId: 3, x: 165, y: 450, messageCount: 4 },
  { id: 'a38', clusterId: 3, x: 250, y: 410, messageCount: 6 },
  { id: 'a39', clusterId: 3, x: 100, y: 435, messageCount: 7 },
  { id: 'a40', clusterId: 3, x: 200, y: 395, messageCount: 9 },
  { id: 'a41', clusterId: 3, x: 145, y: 360, messageCount: 3 },
  { id: 'a42', clusterId: 3, x: 270, y: 445, messageCount: 5 },
  { id: 'a43', clusterId: 3, x: 115, y: 400, messageCount: 2 },
  { id: 'a44', clusterId: 3, x: 235, y: 465, messageCount: 7 },
  // Cluster 4 — bottom-right region
  { id: 'a45', clusterId: 4, x: 530, y: 390, messageCount: 8 },
  { id: 'a46', clusterId: 4, x: 580, y: 420, messageCount: 5 },
  { id: 'a47', clusterId: 4, x: 610, y: 370, messageCount: 11 },
  { id: 'a48', clusterId: 4, x: 555, y: 450, messageCount: 4 },
  { id: 'a49', clusterId: 4, x: 640, y: 415, messageCount: 7 },
  { id: 'a50', clusterId: 4, x: 510, y: 440, messageCount: 6 },
]

// ─── Agent Info Map ────────────────────────────────────────────────────────────

export const AGENT_INFO_MAP: Record<string, AgentInfo> = Object.fromEntries(
  AGENT_NODES.map((n, i) => [n.id, makeAgentInfo(i, n.clusterId)])
)

// ─── Agent Network Edges ───────────────────────────────────────────────────────

// Intra-cluster edges (dense) + a few inter-cluster bridges
export const AGENT_EDGES: LegacyFlowEdge[] = [
  // Cluster 1 intra
  { id: 'e-a01-a03', source: 'a01', target: 'a03', animated: true },
  { id: 'e-a01-a06', source: 'a01', target: 'a06', animated: false },
  { id: 'e-a02-a05', source: 'a02', target: 'a05', animated: true },
  { id: 'e-a03-a07', source: 'a03', target: 'a07', animated: true },
  { id: 'e-a04-a14', source: 'a04', target: 'a14', animated: false },
  { id: 'e-a05-a11', source: 'a05', target: 'a11', animated: true },
  { id: 'e-a09-a12', source: 'a09', target: 'a12', animated: true },
  { id: 'e-a12-a16', source: 'a12', target: 'a16', animated: false },
  { id: 'e-a13-a19', source: 'a13', target: 'a19', animated: true },
  { id: 'e-a15-a18', source: 'a15', target: 'a18', animated: false },
  { id: 'e-a17-a02', source: 'a17', target: 'a02', animated: true },
  // Cluster 2 intra
  { id: 'e-a20-a24', source: 'a20', target: 'a24', animated: true },
  { id: 'e-a21-a23', source: 'a21', target: 'a23', animated: false },
  { id: 'e-a22-a26', source: 'a22', target: 'a26', animated: true },
  { id: 'e-a25-a30', source: 'a25', target: 'a30', animated: false },
  { id: 'e-a27-a31', source: 'a27', target: 'a31', animated: true },
  { id: 'e-a28-a32', source: 'a28', target: 'a32', animated: true },
  { id: 'e-a29-a33', source: 'a29', target: 'a33', animated: false },
  { id: 'e-a31-a22', source: 'a31', target: 'a22', animated: true },
  // Cluster 3 intra
  { id: 'e-a34-a36', source: 'a34', target: 'a36', animated: false },
  { id: 'e-a35-a37', source: 'a35', target: 'a37', animated: true },
  { id: 'e-a38-a40', source: 'a38', target: 'a40', animated: false },
  { id: 'e-a39-a43', source: 'a39', target: 'a43', animated: true },
  { id: 'e-a41-a34', source: 'a41', target: 'a34', animated: false },
  { id: 'e-a42-a44', source: 'a42', target: 'a44', animated: true },
  // Cluster 4 intra
  { id: 'e-a45-a47', source: 'a45', target: 'a47', animated: true },
  { id: 'e-a46-a48', source: 'a46', target: 'a48', animated: false },
  { id: 'e-a47-a49', source: 'a47', target: 'a49', animated: true },
  { id: 'e-a50-a45', source: 'a50', target: 'a45', animated: false },
  // Inter-cluster bridges (sparse — show cross-perspective debate)
  { id: 'e-a12-a31', source: 'a12', target: 'a31', animated: false },
  { id: 'e-a16-a20', source: 'a16', target: 'a20', animated: false },
  { id: 'e-a19-a34', source: 'a19', target: 'a34', animated: false },
  { id: 'e-a33-a42', source: 'a33', target: 'a42', animated: false },
  { id: 'e-a44-a50', source: 'a44', target: 'a50', animated: false },
]

// ─── Agent Messages (Feed) ────────────────────────────────────────────────────

export type MessageType = 'propose' | 'critique' | 'agree' | 'cluster'

export interface AgentMessage {
  id: number
  agentId: string
  /** @deprecated use agentId */
  agent?: string
  clusterId: ClusterID
  type: MessageType
  text: string
  replyTo?: string
  timestamp: string
  round: number
}

export const AGENT_MESSAGES: AgentMessage[] = [
  { id: 1,  agentId: 'a09', clusterId: 1, type: 'propose',  text: 'Service boundaries are well-defined in the current codebase. A phased migration starting with the auth module would limit risk while proving the pattern.',                round: 1, timestamp: '0:04' },
  { id: 2,  agentId: 'a31', clusterId: 2, type: 'critique', text: 'Disagree — the team has 6 engineers. Operating a distributed system adds significant overhead that a small team cannot absorb.',                      round: 1, timestamp: '0:07', replyTo: 'a09' },
  { id: 3,  agentId: 'a12', clusterId: 1, type: 'agree',    text: 'Supporting a09\'s phased approach. The existing API gateway already handles routing, reducing migration friction.',                       round: 1, timestamp: '0:11' },
  { id: 4,  agentId: 'a40', clusterId: 3, type: 'propose',  text: 'Holding neutral. We lack production latency data under load — decisions should wait until the profiling sprint completes.',                        round: 1, timestamp: '0:14' },
  { id: 5,  agentId: 'a47', clusterId: 4, type: 'propose',  text: 'Distributed tracing and observability gaps are underestimated. Incident response time could double without proper tooling.',                               round: 1, timestamp: '0:18' },
  { id: 6,  agentId: 'a22', clusterId: 2, type: 'agree',    text: 'Confirming a31. The current CI/CD pipeline has no multi-service deployment support — that alone is a 2-month prerequisite.',                                    round: 1, timestamp: '0:21', replyTo: 'a31' },
  { id: 7,  agentId: 'a16', clusterId: 1, type: 'critique', text: 'a47\'s tooling concern is valid but solvable. OpenTelemetry adoption is a weekend spike, not a blocker.',                              round: 1, timestamp: '0:25', replyTo: 'a47' },
  { id: 8,  agentId: 'a36', clusterId: 3, type: 'agree',    text: 'Neutral position holds. Need to see the dependency graph analysis before committing to a migration sequence.',                                  round: 1, timestamp: '0:29' },
  { id: 9,  agentId: 'a03', clusterId: 1, type: 'cluster',  text: 'Cluster 1 forming consensus — phased migration is viable with existing infrastructure as a foundation.',                        round: 2, timestamp: '0:34' },
  { id: 10, agentId: 'a31', clusterId: 2, type: 'critique', text: 'Cluster 1 underestimates operational complexity. Historical data: 7 of 10 similar migrations at this team size exceeded timeline by 3x.', round: 2, timestamp: '0:38' },
  { id: 11, agentId: 'a09', clusterId: 1, type: 'critique', text: 'Those comparisons lack context — most did not have an existing API gateway. Our starting position is materially different.',                    round: 2, timestamp: '0:42', replyTo: 'a31' },
  { id: 12, agentId: 'a45', clusterId: 4, type: 'propose',  text: 'Cluster 2 raises valid concerns. Monitoring for scope creep: if the auth service extraction exceeds 4 weeks, the entire timeline fails.',  round: 2, timestamp: '0:46' },
  { id: 13, agentId: 'a40', clusterId: 3, type: 'agree',    text: 'Cluster 3 remaining neutral. Recommend a time-boxed proof of concept before full commitment.',                     round: 2, timestamp: '0:51' },
  { id: 14, agentId: 'a12', clusterId: 1, type: 'propose',  text: 'Developer velocity data shows 40% of PRs touch 3+ modules. Service isolation would reduce blast radius and review complexity.',                            round: 2, timestamp: '0:55' },
  { id: 15, agentId: 'a22', clusterId: 2, type: 'cluster',  text: 'Cluster 2 internal consensus reached — migration is premature given current constraints. Confidence 63%.',             round: 2, timestamp: '1:00' },
  { id: 16, agentId: 'a47', clusterId: 4, type: 'critique', text: 'Both clusters are overconfident. The unknown unknowns in data consistency across services are underpriced.',               round: 2, timestamp: '1:04' },
  { id: 17, agentId: 'a16', clusterId: 1, type: 'agree',    text: 'Cluster 1 final position: proceed with phased migration. Start with auth, validate in 4 weeks. Confidence 71%.',                               round: 3, timestamp: '1:09' },
  { id: 18, agentId: 'a31', clusterId: 2, type: 'critique', text: 'Cluster 1 underweights team capacity risk. Our recommendation: defer migration until headcount reaches 10. Confidence 63%.',                        round: 3, timestamp: '1:13', replyTo: 'a16' },
  { id: 19, agentId: 'a47', clusterId: 4, type: 'propose',  text: 'Neither cluster accounts for the database coupling. Shared state across services requires an event-driven rewrite first.',                 round: 3, timestamp: '1:17' },
  { id: 20, agentId: 'a40', clusterId: 3, type: 'agree',    text: 'Cluster 3 final: neutral. Recommend a 2-week spike to validate assumptions before deciding.',                                        round: 3, timestamp: '1:22' },
  { id: 21, agentId: 'a09', clusterId: 1, type: 'cluster',  text: 'Analysis complete. Weighted consensus: phased migration is feasible with risk mitigation. Confidence: 68%.',   round: 3, timestamp: '1:26' },
]

// ─── Simulation Results ───────────────────────────────────────────────────────

export interface SimulationResults {
  finalPrediction: string
  confidenceScore: number
  costActual: number
  costCloud: number
  agentCount: number
  rounds: number
  totalMessages: number
  wallTimeSeconds: number
  nodesContributing: number
}

export const SIMULATION_RESULTS: SimulationResults = {
  finalPrediction: 'Phased microservices migration is viable starting with the auth module, contingent on a 2-week proof-of-concept and headcount growth to 10 within 6 months. Key risk: database coupling requires event-driven architecture first.',
  confidenceScore: 68,
  costActual: 0.10,
  costCloud: 0.60,
  agentCount: 50,
  rounds: 3,
  totalMessages: 847,
  wallTimeSeconds: 86,
  nodesContributing: 12,
}

// ─── Prompt Chips ─────────────────────────────────────────────────────────────

export const PROMPT_CHIPS: string[] = [
  'Should we migrate our monolith to microservices?',
  'Debate the best approach to reduce API latency by 50%',
  'Evaluate three competing database architectures for our use case',
  'Design a disaster recovery plan for a multi-region deployment',
]

// ─── Cost Comparison Chart ────────────────────────────────────────────────────

export const COST_CHART_DATA = [
  { label: 'Cloud API', cost: 0.60 },
  { label: 'Chorus', cost: 0.10 },
]
