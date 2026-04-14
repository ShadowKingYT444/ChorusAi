/** Match server-side `broadcast_completions.normalize_completion_url` for chat/completions POST. */
export function normalizeOpenAIChatCompletionsUrl(raw: string): string {
  const s = raw.trim()
  if (!s) return ''
  const u = /^https?:\/\//i.test(s) ? s : `http://${s}`
  const trimmed = u.replace(/\/+$/, '')
  if (trimmed.toLowerCase().endsWith('/v1/chat/completions')) return trimmed
  let parsed: URL
  try {
    parsed = new URL(u)
  } catch {
    return ''
  }
  const path = parsed.pathname.replace(/\/+$/, '') || ''
  if (path.endsWith('/v1/chat/completions')) return `${parsed.origin}${path}`
  if (path.endsWith('/v1/chat')) return `${parsed.origin}${path}/completions`
  if (path.endsWith('/v1')) return `${parsed.origin}${path}/chat/completions`
  if (path.length > 0) return `${parsed.origin}${path}/v1/chat/completions`
  if (parsed.protocol === 'http:' && !parsed.port) {
    return `http://${parsed.hostname}:11434/v1/chat/completions`
  }
  return `${parsed.origin}/v1/chat/completions`
}
