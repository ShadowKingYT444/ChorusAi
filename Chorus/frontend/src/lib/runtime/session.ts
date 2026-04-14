import type { SimulationSession } from '@/lib/runtime/types'

const KEY = 'chorus_session'
const LEGACY_KEY = 'chorus_job'

export function readSimulationSession(): SimulationSession | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      return JSON.parse(raw) as SimulationSession
    }
    const legacyRaw = localStorage.getItem(LEGACY_KEY)
    if (!legacyRaw) return null
    const legacy = JSON.parse(legacyRaw) as {
      prompt: string
      agentCount: number
      rounds: number
      bounty: number
      jobId?: string
    }
    return {
      prompt: legacy.prompt,
      agentCount: legacy.agentCount,
      rounds: legacy.rounds,
      bounty: legacy.bounty,
      jobId: legacy.jobId,
      mode: legacy.jobId ? 'backend' : 'mock',
      createdAt: new Date().toISOString(),
    }
  } catch {
    return null
  }
}

export function writeSimulationSession(session: SimulationSession): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(KEY, JSON.stringify(session))
  localStorage.setItem(
    LEGACY_KEY,
    JSON.stringify({
      prompt: session.prompt,
      agentCount: session.agentCount,
      rounds: session.rounds,
      bounty: session.bounty,
      jobId: session.jobId,
    }),
  )
}
