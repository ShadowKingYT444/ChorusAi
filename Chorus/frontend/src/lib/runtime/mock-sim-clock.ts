const KEY = 'chorus_mock_clock'

interface MockClockEntry {
  createdAt: string
  t0: number
}

/** Wall-clock anchor for mock UI so leaving /app and returning does not restart the timeline. */
export function ensureMockSimClock(createdAt: string): number {
  if (typeof window === 'undefined') return -1
  try {
    const raw = sessionStorage.getItem(KEY)
    const parsed = raw ? (JSON.parse(raw) as MockClockEntry) : null
    if (parsed?.createdAt === createdAt && typeof parsed.t0 === 'number') {
      return parsed.t0
    }
  } catch {
    /* ignore */
  }
  const t0 = Date.now()
  sessionStorage.setItem(KEY, JSON.stringify({ createdAt, t0 } satisfies MockClockEntry))
  return t0
}

export function mockSimElapsedMs(createdAt: string): number {
  if (typeof window === 'undefined') return 0
  const t0 = ensureMockSimClock(createdAt)
  if (t0 < 0) return 0
  return Math.max(0, Date.now() - t0)
}
