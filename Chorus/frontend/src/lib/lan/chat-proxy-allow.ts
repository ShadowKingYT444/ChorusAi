const METADATA_HOST = '169.254.169.254'

/** Known tunnel hostname patterns that are safe to proxy through. */
const TUNNEL_HOSTNAME_PATTERNS = [
  /\.ngrok-free\.app$/i,
  /\.ngrok-free\.dev$/i,
  /\.ngrok\.io$/i,
  /\.ngrok\.app$/i,
  /\.ngrok\.dev$/i,
  /\.trycloudflare\.com$/i,
  /\.loca\.lt$/i,
]

/** RFC1918 LAN (not 127.x — server-side proxy must not target loopback or it hits the wrong PC). */
export function isPrivateLanIpv4(host: string): boolean {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host)
  if (!m) return false
  const a = Number(m[1])
  const b = Number(m[2])
  const c = Number(m[3])
  const d = Number(m[4])
  if ([a, b, c, d].some((x) => x > 255 || Number.isNaN(x))) return false
  if (a === 10) return true
  if (a === 172 && b >= 16 && b <= 31) return true
  if (a === 192 && b === 168) return true
  return false
}

export function isLoopbackOllamaHost(hostname: string): boolean {
  const h = hostname.toLowerCase()
  return h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '[::1]'
}

/** Returns true if the hostname belongs to a known tunnel provider (ngrok, cloudflared). */
export function isTunnelHostname(hostname: string): boolean {
  const h = hostname.toLowerCase()
  return TUNNEL_HOSTNAME_PATTERNS.some((re) => re.test(h))
}

export function parseServerExtraHosts(): Set<string> {
  const raw = process.env.NEXT_CHAT_PROXY_EXTRA_HOSTS?.trim() ?? ''
  if (!raw) return new Set()
  return new Set(
    raw
      .split(',')
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean),
  )
}

/** Server-only: allowed forward target for /api/local-chat-completions. */
export function isAllowedChatProxyTarget(hostname: string): boolean {
  const h = hostname.toLowerCase()
  if (!h || h === METADATA_HOST) return false
  if (isLoopbackOllamaHost(h)) return false
  if (isPrivateLanIpv4(h)) return true
  if (isTunnelHostname(h)) return true
  return parseServerExtraHosts().has(h)
}
