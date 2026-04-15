'use client'

import { useEffect, useRef, useState } from 'react'
import {
  openJobEventsSocket,
  type JobStatus,
  type JobStreamEvent,
  type SettlementPreview,
} from '@/lib/api/orchestrator'
import type { RuntimeEdge } from '@/lib/runtime/types'

export interface JobLine {
  round: number
  slotId: string
  status: 'valid' | 'suspect' | 'pruned'
  latencyMs: number
  snippet: string
}

export interface JobWebSocketState {
  status: JobStatus
  currentRound: number
  lines: JobLine[]
  edges: RuntimeEdge[]
  settlement: SettlementPreview | null
  finalAnswer: string | null
  citations: string[]
  error: string | null
  connected: boolean
}

const EMPTY: JobWebSocketState = {
  status: 'pending',
  currentRound: 0,
  lines: [],
  edges: [],
  settlement: null,
  finalAnswer: null,
  citations: [],
  error: null,
  connected: false,
}

/**
 * Subscribe to orchestrator `/ws/jobs/{job_id}` event stream.
 * Replays history on (re)connect — deduped by `(round, slot_id, type, snippet)` /
 * `(round, from, to, kind)`.
 */
export function useJobWebSocket(jobId: string | null | undefined): JobWebSocketState {
  const [state, setState] = useState<JobWebSocketState>(EMPTY)
  const mountedRef = useRef(false)

  useEffect(() => {
    if (!jobId) {
      setState(EMPTY)
      return
    }

    mountedRef.current = true
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let cancelled = false
    const seenLines = new Set<string>()
    const seenEdges = new Set<string>()

    const handleEvent = (event: JobStreamEvent) => {
      setState((prev) => {
        switch (event.type) {
          case 'round_started':
            return {
              ...prev,
              status: 'running',
              currentRound: Math.max(prev.currentRound, event.round),
            }
          case 'agent_line': {
            const key = `${event.round}:${event.slot_id}:${event.payload.snippet}`
            if (seenLines.has(key)) return prev
            seenLines.add(key)
            return {
              ...prev,
              status: 'running',
              lines: [
                ...prev.lines,
                {
                  round: event.round,
                  slotId: event.slot_id,
                  status: event.payload.status,
                  latencyMs: event.payload.latency_ms,
                  snippet: event.payload.snippet,
                },
              ],
            }
          }
          case 'edge': {
            const key = `${event.round}:${event.payload.from}:${event.payload.to}:${event.payload.kind}`
            if (seenEdges.has(key)) return prev
            seenEdges.add(key)
            return {
              ...prev,
              edges: [
                ...prev.edges,
                {
                  round: event.round,
                  from: event.payload.from,
                  to: event.payload.to,
                  kind: event.payload.kind,
                },
              ],
            }
          }
          case 'final_answer':
            return {
              ...prev,
              finalAnswer: event.payload.text,
              citations: event.payload.citations ?? [],
            }
          case 'job_done':
            return {
              ...prev,
              status: 'completed',
              settlement: event.payload.settlement_preview ?? null,
              finalAnswer: event.payload.final_answer ?? prev.finalAnswer,
              citations: event.payload.citations ?? prev.citations,
            }
          case 'job_failed':
            return {
              ...prev,
              status: 'failed',
              error: event.payload.error,
            }
          default:
            return prev
        }
      })
    }

    const connect = () => {
      if (cancelled) return
      try {
        ws = openJobEventsSocket(jobId, {
          onOpen: () => {
            attempt = 0
            if (mountedRef.current) setState((p) => ({ ...p, connected: true, error: null }))
          },
          onEvent: handleEvent,
          onError: () => {
            if (mountedRef.current) setState((p) => ({ ...p, error: 'job ws error' }))
          },
          onClose: () => {
            if (cancelled) return
            if (mountedRef.current) setState((p) => ({ ...p, connected: false }))
            const delay = Math.min(5000, 500 * Math.pow(2, attempt++))
            reconnectTimer = setTimeout(connect, delay)
          },
        })
      } catch (err) {
        if (mountedRef.current) {
          setState((p) => ({ ...p, error: err instanceof Error ? err.message : 'ws open failed' }))
        }
      }
    }

    setState(EMPTY)
    connect()

    return () => {
      cancelled = true
      mountedRef.current = false
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [jobId])

  return state
}
