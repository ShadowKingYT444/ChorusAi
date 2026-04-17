'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getOrCreatePeerId,
  getPeers,
  getSavedOllamaIp,
  isOrchestratorConfigured,
  type JobStatus,
  type PeerEntry,
  type SettlementPreview,
  openSignalingSocket,
} from '@/lib/api/orchestrator'
import {
  buildClustersFromMessages,
  buildResults,
  slotToClusterId,
} from '@/lib/runtime/adapter'
import { configuredAgentOriginsFromEnv } from '@/lib/runtime/agent-origins'
import { readSimulationSession } from '@/lib/runtime/session'
import { useJobWebSocket, type JobLine } from '@/lib/runtime/use-job-websocket'
import type {
  JobRuntimeState,
  RuntimeEdge,
  RuntimeMessage,
  SimulationSession,
} from '@/lib/runtime/types'
import type { Cluster, SimulationResults } from '@/lib/mock-data'

const EMPTY_CLUSTERS: Cluster[] = []
const EMPTY_RESULTS: SimulationResults = {
  finalPrediction: '',
  confidenceScore: 0,
  costActual: 0,
  costCloud: 0,
  agentCount: 0,
  rounds: 0,
  totalMessages: 0,
  wallTimeSeconds: 0,
  nodesContributing: 0,
}
const EMPTY_EDGES: RuntimeEdge[] = []

function mergeLaunchedPeers(livePeers: PeerEntry[], launchedPeers: PeerEntry[] | undefined): PeerEntry[] {
  if (!launchedPeers || launchedPeers.length === 0) {
    return [...livePeers].sort((a, b) => a.peer_id.localeCompare(b.peer_id))
  }

  const liveById = new Map(livePeers.map((peer) => [peer.peer_id, peer]))
  const merged: PeerEntry[] = launchedPeers.map((peer) => liveById.get(peer.peer_id) ?? peer)
  for (const peer of livePeers) {
    if (!merged.some((item) => item.peer_id === peer.peer_id)) {
      merged.push(peer)
    }
  }
  return merged
}

function statusToType(status: JobLine['status']): RuntimeMessage['type'] {
  if (status === 'pruned') return 'cluster'
  if (status === 'suspect') return 'critique'
  return 'propose'
}

function linesToMessages(jobId: string, lines: JobLine[]): RuntimeMessage[] {
  return lines.map((line, i) => ({
    id: i + 1,
    agentId: line.slotId,
    slotId: line.slotId,
    jobId,
    clusterId: slotToClusterId(line.slotId),
    round: line.round,
    type: statusToType(line.status),
    text: line.snippet || '(no content)',
    timestamp: new Date().toLocaleTimeString(),
    status: line.status,
  }))
}

function defaultState(session: SimulationSession | null): JobRuntimeState {
  return {
    session,
    status: 'pending',
    currentRound: 1,
    totalRounds: session?.rounds ?? 0,
    messages: [],
    clusters: EMPTY_CLUSTERS,
    results: session ? buildResults(session, [], null) : EMPTY_RESULTS,
    settlement: null,
    operator: null,
    loading: true,
    error: null,
    agentCompletionOrigins: [],
    embeddingModelVersion: null,
    connectedPeers: [],
    finalAnswer: null,
    citations: [],
    edges: EMPTY_EDGES,
  }
}

export function useJobRuntime(jobIdFromRoute?: string | null): JobRuntimeState {
  const [session, setSession] = useState<SimulationSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [agentCompletionOrigins, setAgentCompletionOrigins] = useState<string[]>([])
  const [embeddingModelVersion, setEmbeddingModelVersion] = useState<string | null>(null)
  const [connectedPeers, setConnectedPeers] = useState<PeerEntry[]>([])
  const transcriptJobRef = useRef<string | null>(null)

  useEffect(() => {
    setSession(readSimulationSession())
  }, [])

  const effectiveSession = session
  const effectiveJobId = jobIdFromRoute ?? effectiveSession?.jobId ?? null
  const backendEnabled = isOrchestratorConfigured() && Boolean(effectiveJobId)

  const jobWs = useJobWebSocket(backendEnabled ? effectiveJobId : null)

  // Peers: prefer signaling WS push; keep a light HTTP GET on mount as a warm-up.
  useEffect(() => {
    if (!effectiveSession) {
      setLoading(false)
      setAgentCompletionOrigins([])
      setEmbeddingModelVersion(null)
      return
    }
    if (!backendEnabled || !effectiveJobId) {
      transcriptJobRef.current = null
      setLoading(false)
      setAgentCompletionOrigins(
        effectiveSession?.mode === 'mock' ? configuredAgentOriginsFromEnv() : [],
      )
      setConnectedPeers([])
      setEmbeddingModelVersion(effectiveSession?.mode === 'mock' ? 'Local preview (no aggregation)' : null)
      return
    }

    if (transcriptJobRef.current !== effectiveJobId) {
      transcriptJobRef.current = effectiveJobId
    }

    let cancelled = false
    setLoading(true)
    setError(null)
    setEmbeddingModelVersion('Live consensus (ws /ws/jobs)')

    void (async () => {
      try {
        const snapshot = await getPeers()
        if (cancelled) return
        const mergedPeers = mergeLaunchedPeers(snapshot.peers, effectiveSession.launchedPeers)
        setConnectedPeers(mergedPeers)
        setAgentCompletionOrigins(mergedPeers.map((p) => p.peer_id))
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load peers')
          const fallbackPeers = mergeLaunchedPeers([], effectiveSession.launchedPeers)
          setConnectedPeers(fallbackPeers)
          setAgentCompletionOrigins(fallbackPeers.map((p) => p.peer_id))
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    const savedIp = getSavedOllamaIp()
    const ws = openSignalingSocket(
      getOrCreatePeerId(),
      'web-prompter',
      {
        onEvent: (event) => {
          if (event.type === 'peer_count') {
            const peers = mergeLaunchedPeers(event.peers, effectiveSession.launchedPeers)
            setConnectedPeers(peers)
            setAgentCompletionOrigins(peers.map((p) => p.peer_id))
          }
        },
        onError: () => {
          setError('Signaling socket disconnected')
        },
      },
      savedIp || undefined,
    )

    return () => {
      cancelled = true
      ws.close()
    }
  }, [backendEnabled, effectiveJobId, effectiveSession])

  const jobId = effectiveJobId ?? ''
  const messages = useMemo(
    () => linesToMessages(jobId, jobWs.lines),
    [jobId, jobWs.lines],
  )

  return useMemo<JobRuntimeState>(() => {
    if (!effectiveSession) return defaultState(null)

    const clusters = buildClustersFromMessages(messages, effectiveSession.agentCount)
    const results = buildResults(effectiveSession, messages, jobWs.settlement)
    const status: JobStatus = backendEnabled && jobWs.status !== 'pending' ? jobWs.status : 'pending'

    return {
      session: effectiveSession,
      status,
      currentRound: jobWs.currentRound || 1,
      totalRounds: effectiveSession.rounds,
      messages,
      clusters,
      results,
      settlement: jobWs.settlement,
      operator: null,
      loading,
      error: error ?? jobWs.error,
      agentCompletionOrigins,
      embeddingModelVersion,
      connectedPeers,
      finalAnswer: jobWs.finalAnswer,
      citations: jobWs.citations,
      edges: jobWs.edges,
    }
  }, [
    effectiveSession,
    messages,
    jobWs.settlement,
    jobWs.currentRound,
    jobWs.status,
    jobWs.error,
    jobWs.finalAnswer,
    jobWs.citations,
    jobWs.edges,
    loading,
    error,
    agentCompletionOrigins,
    embeddingModelVersion,
    connectedPeers,
    backendEnabled,
  ])
}
