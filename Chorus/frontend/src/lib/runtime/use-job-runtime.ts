'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getOrCreatePeerId,
  getJobResponses,
  getPeers,
  getSavedOllamaIp,
  isOrchestratorConfigured,
  type JobResponseRow,
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
import type { JobRuntimeState, RuntimeMessage, SimulationSession } from '@/lib/runtime/types'
import { CLUSTERS, SIMULATION_RESULTS } from '@/lib/mock-data'

function maxMessageId(prev: RuntimeMessage[]): number {
  if (prev.length === 0) return 0
  return Math.max(...prev.map((m) => m.id))
}

/** One mesh `peer_id` may host several workers; `instance_id` disambiguates them in the feed. */
function physicalAgentSlotId(peerId: string, instanceId?: string | null): string {
  const s = instanceId?.trim()
  if (!s) return peerId
  return `${peerId}#${s}`
}

function dedupeMessages(messages: RuntimeMessage[]): RuntimeMessage[] {
  const seen = new Set<string>()
  const out: RuntimeMessage[] = []
  for (const msg of messages) {
    const j = msg.jobId ?? ''
    const key = `${j}:${msg.round}:${msg.slotId}:${msg.type}:${msg.text}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(msg)
  }
  return out
}

function mergeBufferedResponses(activeJobId: string, prev: RuntimeMessage[], responses: JobResponseRow[]): RuntimeMessage[] {
  const seen = new Set(prev.map((m) => `${m.jobId ?? ''}:${m.slotId}:${m.text}`))
  let nid = maxMessageId(prev) + 1
  const adds: RuntimeMessage[] = []
  for (const r of responses) {
    const text = (r.text ?? r.error ?? '').trim()
    if (!text) continue
    const jid = r.job_id || activeJobId
    const slot = physicalAgentSlotId(r.peer_id, r.instance_id)
    const key = `${jid}:${slot}:${text}`
    if (seen.has(key)) continue
    seen.add(key)
    adds.push({
      id: nid++,
      agentId: slot,
      slotId: slot,
      jobId: jid,
      clusterId: slotToClusterId(slot),
      round: 1,
      type: 'propose',
      text,
      timestamp: new Date().toLocaleTimeString(),
    })
  }
  if (adds.length === 0) return prev
  return dedupeMessages([...prev, ...adds])
}

function defaultState(session: SimulationSession | null): JobRuntimeState {
  return {
    session,
    status: 'pending',
    currentRound: 1,
    totalRounds: session?.rounds ?? SIMULATION_RESULTS.rounds,
    messages: [],
    clusters: CLUSTERS,
    results: session ? buildResults(session, [], null) : SIMULATION_RESULTS,
    settlement: null,
    operator: null,
    loading: true,
    error: null,
    agentCompletionOrigins: [],
    embeddingModelVersion: null,
    connectedPeers: [],
  }
}

export function useJobRuntime(jobIdFromRoute?: string | null): JobRuntimeState {
  const transcriptJobRef = useRef<string | null>(null)
  const [session, setSession] = useState<SimulationSession | null>(null)
  const [status, setStatus] = useState<JobStatus>('pending')
  const [currentRound, setCurrentRound] = useState(1)
  const [settlement, setSettlement] = useState<SettlementPreview | null>(null)
  const [messages, setMessages] = useState<RuntimeMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [operator] = useState<JobRuntimeState['operator']>(null)
  const [error, setError] = useState<string | null>(null)
  const [agentCompletionOrigins, setAgentCompletionOrigins] = useState<string[]>([])
  const [embeddingModelVersion, setEmbeddingModelVersion] = useState<string | null>(null)
  const [connectedPeers, setConnectedPeers] = useState<PeerEntry[]>([])

  useEffect(() => {
    setSession(readSimulationSession())
  }, [])

  const effectiveSession = session
  const effectiveJobId = jobIdFromRoute ?? effectiveSession?.jobId
  const backendEnabled = isOrchestratorConfigured() && Boolean(effectiveJobId)

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
      setEmbeddingModelVersion(effectiveSession?.mode === 'mock' ? 'Phase 1-2 demo (no aggregation yet)' : null)
      setStatus('pending')
      return
    }

    if (transcriptJobRef.current !== effectiveJobId) {
      transcriptJobRef.current = effectiveJobId
      setMessages([])
    }

    let ws: WebSocket | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null
    let responsePollTimer: ReturnType<typeof setInterval> | null = null
    let cancelled = false
    setLoading(true)
    setError(null)
    setStatus('running')
    setCurrentRound(1)
    setSettlement(null)
    setEmbeddingModelVersion('Phase 1-2 demo (aggregation not enabled)')

    const refreshPeers = async () => {
      try {
        const snapshot = await getPeers()
        if (cancelled) return
        const sortedPeers = [...snapshot.peers].sort((a, b) => a.peer_id.localeCompare(b.peer_id))
        setConnectedPeers(sortedPeers)
        setAgentCompletionOrigins(sortedPeers.map((p) => p.peer_id))
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load peers')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void refreshPeers()

    const pullBufferedResponses = () => {
      void getJobResponses(effectiveJobId)
        .then(({ responses }) => {
          if (cancelled || responses.length === 0) return
          setStatus('running')
          setMessages((prev) => mergeBufferedResponses(effectiveJobId, prev, responses))
        })
        .catch(() => {
          /* best-effort */
        })
    }

    // HTTP invoke writes to the buffer after broadcast; poll until results land.
    pullBufferedResponses()
    responsePollTimer = setInterval(pullBufferedResponses, 2000)

    const savedIp = getSavedOllamaIp()
    ws = openSignalingSocket(getOrCreatePeerId(), 'web-prompter', {
      onEvent: (event) => {
        if (event.type === 'peer_count') {
          const peers = [...event.peers].sort((a, b) => a.peer_id.localeCompare(b.peer_id))
          setConnectedPeers(peers)
          setAgentCompletionOrigins(peers.map((p) => p.peer_id))
          return
        }
        if (event.type === 'job_response') {
          const text = event.text ?? event.error ?? ''
          if (!text) return
          setStatus('running')
          setMessages((prev) => {
            const nextId = maxMessageId(prev) + 1
            const jid = event.job_id
            const slot = physicalAgentSlotId(event.peer_id, event.instance_id)
            return dedupeMessages([
              ...prev,
              {
                id: nextId,
                agentId: slot,
                slotId: slot,
                jobId: jid,
                clusterId: slotToClusterId(slot),
                round: 1,
                type: 'propose' as const,
                text,
                timestamp: new Date().toLocaleTimeString(),
              },
            ])
          })
        }
      },
      onError: () => {
        setError('Signaling socket disconnected; using polling fallback')
      },
    }, savedIp || undefined)

    pollTimer = setInterval(() => {
      void refreshPeers()
    }, 3000)

    return () => {
      cancelled = true
      if (ws) ws.close()
      if (pollTimer) clearInterval(pollTimer)
      if (responsePollTimer) clearInterval(responsePollTimer)
    }
  }, [backendEnabled, effectiveJobId, effectiveSession])

  return useMemo(() => {
    if (!effectiveSession) {
      return defaultState(null)
    }
    const mergedMessages = dedupeMessages(messages)
    const clusters = buildClustersFromMessages(mergedMessages, effectiveSession.agentCount)
    const results = buildResults(effectiveSession, mergedMessages, settlement)
    return {
      session: effectiveSession,
      status,
      currentRound,
      totalRounds: effectiveSession.rounds,
      messages: mergedMessages,
      clusters,
      results,
      settlement,
      operator,
      loading,
      error,
      agentCompletionOrigins,
      embeddingModelVersion,
      connectedPeers,
    }
  }, [
    effectiveSession,
    status,
    currentRound,
    messages,
    settlement,
    operator,
    loading,
    error,
    agentCompletionOrigins,
    embeddingModelVersion,
    connectedPeers,
  ])
}
