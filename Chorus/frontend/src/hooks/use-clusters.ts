'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getClusters,
  isOrchestratorConfigured,
  type ClusterEdge,
  type ClusterEntry,
  type ClusterStats,
} from '@/lib/api/orchestrator'

export interface ClustersState {
  clusters: ClusterEntry[]
  edges: ClusterEdge[]
  stats: ClusterStats | null
  lastUpdated: number | null
  /**
   * `live`         - orchestrator reachable, clusters returned (may be 0)
   * `offline`      - orchestrator configured but unreachable
   * `unconfigured` - no orchestrator URL set yet (user needs /setup or /join)
   */
  mode: 'live' | 'offline' | 'unconfigured'
  refresh: () => void
}

export function useClusters(pollMs = 4000): ClustersState {
  const [clusters, setClusters] = useState<ClusterEntry[]>([])
  const [edges, setEdges] = useState<ClusterEdge[]>([])
  const [stats, setStats] = useState<ClusterStats | null>(null)
  const [lastUpdated, setLastUpdated] = useState<number | null>(null)
  const [mode, setMode] = useState<'live' | 'offline' | 'unconfigured'>(
    isOrchestratorConfigured() ? 'offline' : 'unconfigured',
  )
  const mounted = useRef(false)

  const refresh = useCallback(async () => {
    if (!isOrchestratorConfigured()) {
      setClusters([])
      setEdges([])
      setStats(null)
      setMode('unconfigured')
      setLastUpdated(Date.now())
      return
    }
    try {
      const res = await getClusters()
      if (!mounted.current) return
      setClusters(res.clusters ?? [])
      setEdges(res.edges ?? [])
      setStats(res.stats ?? null)
      setMode('live')
      setLastUpdated(Date.now())
    } catch {
      if (!mounted.current) return
      setMode('offline')
      setClusters([])
      setEdges([])
      setStats(null)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    const run = () => {
      void refresh()
    }
    const handle = setTimeout(run, 0)
    const id = setInterval(run, pollMs)
    return () => {
      mounted.current = false
      clearTimeout(handle)
      clearInterval(id)
    }
  }, [refresh, pollMs])

  return {
    clusters,
    edges,
    stats,
    lastUpdated,
    mode,
    refresh,
  }
}
