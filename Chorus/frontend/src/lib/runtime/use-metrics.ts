'use client'

import { useEffect, useState } from 'react'
import { getEffectiveOrchestratorBase } from '@/lib/api/orchestrator'

export interface MetricsSnapshot {
  activePeers: number
  jobsInFlight: number
  jobsCompleted: number
  avgRoundLatencyMs: number
  pruneRate: number
  uptimeSeconds: number
  fresh: boolean
}

const EMPTY: MetricsSnapshot = {
  activePeers: 0,
  jobsInFlight: 0,
  jobsCompleted: 0,
  avgRoundLatencyMs: 0,
  pruneRate: 0,
  uptimeSeconds: 0,
  fresh: false,
}

export function useOrchestratorMetrics(pollMs = 3000): MetricsSnapshot {
  const [snap, setSnap] = useState<MetricsSnapshot>(EMPTY)

  useEffect(() => {
    const base = getEffectiveOrchestratorBase()
    if (!base) return
    let cancelled = false

    const tick = async () => {
      try {
        const res = await fetch(`${base}/metrics.json`, { cache: 'no-store' })
        if (!res.ok) return
        const data = await res.json()
        if (cancelled) return
        const g = data.gauges ?? {}
        const c = data.counters ?? {}
        const h = data.histograms ?? {}
        const derived = data.derived ?? {}
        setSnap({
          activePeers: Number(g.active_peers ?? 0),
          jobsInFlight: Number(g.jobs_in_flight ?? 0),
          jobsCompleted: Number(c.jobs_completed_total ?? 0),
          avgRoundLatencyMs: Number(h.round_latency_ms?.avg_ms ?? 0),
          pruneRate: Number(derived.prune_rate ?? 0),
          uptimeSeconds: Number(data.uptime_seconds ?? 0),
          fresh: true,
        })
      } catch {
        /* ignore transient */
      }
    }
    void tick()
    const t = setInterval(tick, pollMs)
    return () => {
      cancelled = true
      clearInterval(t)
    }
  }, [pollMs])

  return snap
}
