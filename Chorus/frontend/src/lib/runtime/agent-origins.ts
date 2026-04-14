/** Normalize a completion base URL to `scheme://host[:port]` for display. */
export function normalizeCompletionOrigin(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed) return trimmed
  try {
    const u = new URL(trimmed.includes('://') ? trimmed : `http://${trimmed}`)
    return `${u.protocol}//${u.host}`.replace(/\/$/, '')
  } catch {
    return trimmed.replace(/\/$/, '')
  }
}

/** Distinct agent bases from Next public env (used in mock / before slots register). */
export function configuredAgentOriginsFromEnv(): string[] {
  const rawList = process.env.NEXT_PUBLIC_AGENT_BASE_URLS?.trim()
  const single = process.env.NEXT_PUBLIC_AGENT_BASE_URL?.trim()
  const parts = rawList ? rawList.split(',') : [single ?? '']
  const set = new Set<string>()
  for (const p of parts) {
    const t = p.trim()
    if (!t) continue
    set.add(normalizeCompletionOrigin(t))
  }
  return [...set].sort()
}
