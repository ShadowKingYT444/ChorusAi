'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getPeers,
  isOrchestratorConfigured,
  type PeerEntry,
} from '@/lib/api/orchestrator'

export interface NetworkStatus {
  online: number
  peers: PeerEntry[]
  /**
   * `live`     - orchestrator reachable, peers returned (may be 0)
   * `offline`  - orchestrator configured but unreachable
   * `unconfigured` - no orchestrator URL set yet (user needs /setup or /join)
   */
  mode: 'live' | 'offline' | 'unconfigured'
  lastUpdated: number | null
  refresh: () => void
}

export function useNetworkStatus(pollMs = 4000): NetworkStatus {
  const [peers, setPeers] = useState<PeerEntry[]>([])
  const [lastUpdated, setLastUpdated] = useState<number | null>(null)
  const [mode, setMode] = useState<'live' | 'offline' | 'unconfigured'>(
    isOrchestratorConfigured() ? 'offline' : 'unconfigured',
  )
  const mounted = useRef(false)

  const refresh = useCallback(async () => {
    if (!isOrchestratorConfigured()) {
      setPeers([])
      setMode('unconfigured')
      setLastUpdated(Date.now())
      return
    }
    try {
      const res = await getPeers()
      if (!mounted.current) return
      setPeers(res.peers ?? [])
      setMode('live')
      setLastUpdated(Date.now())
    } catch {
      if (!mounted.current) return
      setMode('offline')
      setPeers([])
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
    online: peers.length,
    peers,
    mode,
    lastUpdated,
    refresh,
  }
}
