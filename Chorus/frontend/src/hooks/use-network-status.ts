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
  mode: 'live' | 'mock' | 'offline'
  lastUpdated: number | null
  refresh: () => void
}

const MOCK_PEERS: PeerEntry[] = Array.from({ length: 8 }).map((_, i) => ({
  peer_id: `mock-peer-${String(i + 1).padStart(3, '0')}`,
  address: `10.0.0.${10 + i}`,
  model: i % 2 === 0 ? 'qwen2.5:0.5b' : 'llama3.2:1b',
  joined_at: Date.now() / 1000 - i * 90,
  status: 'idle',
}))

export function useNetworkStatus(pollMs = 4000): NetworkStatus {
  const [peers, setPeers] = useState<PeerEntry[]>([])
  const [lastUpdated, setLastUpdated] = useState<number | null>(null)
  const [mode, setMode] = useState<'live' | 'mock' | 'offline'>(
    isOrchestratorConfigured() ? 'offline' : 'mock',
  )
  const mounted = useRef(false)

  const refresh = useCallback(async () => {
    if (!isOrchestratorConfigured()) {
      setPeers(MOCK_PEERS)
      setMode('mock')
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
