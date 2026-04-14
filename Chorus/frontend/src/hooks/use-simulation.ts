'use client'

import { useState, useEffect } from 'react'
import { readSimulationSession } from '@/lib/runtime/session'

export interface SimulationJob {
  prompt: string
  agentCount: number
  rounds: number
  bounty: number
  jobId?: string
  mode?: 'mock' | 'backend'
}

export function useSimulation() {
  const [session, setSession] = useState<ReturnType<typeof readSimulationSession>>(null)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setSession(readSimulationSession())
    setMounted(true)
  }, [])

  if (!mounted || !session) return null

  return {
    prompt: session.prompt,
    agentCount: session.agentCount,
    rounds: session.rounds,
    bounty: session.bounty,
    jobId: session.jobId,
    mode: session.mode,
  }
}
