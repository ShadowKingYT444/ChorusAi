import { NextResponse, type NextRequest } from 'next/server'
import { isAllowedChatProxyTarget, isTunnelHostname } from '@/lib/lan/chat-proxy-allow'
import { normalizeOpenAIChatCompletionsUrl } from '@/lib/lan/normalize-openai-chat-url'

export const runtime = 'nodejs'

function proxyDisabled(): boolean {
  return process.env.NEXT_LAN_CHAT_PROXY === '0'
}

function collectFetchErrorChain(err: unknown): string[] {
  const parts: string[] = []
  let cur: unknown = err
  for (let i = 0; i < 6 && cur; i++) {
    if (cur instanceof Error) {
      parts.push(cur.message)
      const ne = cur as NodeJS.ErrnoException
      if (ne.code) parts.push(`code=${ne.code}`)
      cur = (cur as { cause?: unknown }).cause
      continue
    }
    if (typeof cur === 'object' && cur !== null && 'code' in cur) {
      parts.push(`code=${String((cur as { code: unknown }).code)}`)
      break
    }
    parts.push(String(cur))
    break
  }
  return parts.filter(Boolean)
}

function upstreamConnectFailureMessage(err: unknown, completionUrl: string): string {
  const chain = collectFetchErrorChain(err).join(' · ')
  return [
    `Could not reach Ollama at ${completionUrl}.`,
    chain ? `Node: ${chain}.` : '',
    'This request runs on the PC where Next.js runs (npm run dev), not in your browser — that machine must reach Ollama on the network.',
    'On the Ollama PC: set OLLAMA_HOST=0.0.0.0 (Windows: system environment variable, then restart Ollama) so it listens on your LAN IP, not only 127.0.0.1.',
    'From the Next PC, test: curl or Invoke-WebRequest that same URL (GET /api/tags on the Ollama root is fine).',
    'If Next and Ollama are on the same PC but you used a LAN IP, Windows may still refuse the port until Ollama binds 0.0.0.0; allow TCP 11434 in Windows Firewall if prompted.',
  ]
    .filter(Boolean)
    .join(' ')
}

export async function POST(req: NextRequest) {
  if (proxyDisabled()) {
    return NextResponse.json({ error: 'LAN chat proxy disabled (NEXT_LAN_CHAT_PROXY=0).' }, { status: 503 })
  }

  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body.' }, { status: 400 })
  }

  if (!body || typeof body !== 'object') {
    return NextResponse.json({ error: 'Expected JSON object.' }, { status: 400 })
  }

  const rec = body as Record<string, unknown>
  const targetBase = typeof rec.targetBase === 'string' ? rec.targetBase : ''
  const allowLoopback = rec.allowLoopback === true
  if (!targetBase.trim()) {
    return NextResponse.json({ error: 'Missing targetBase.' }, { status: 400 })
  }

  const completionUrl = normalizeOpenAIChatCompletionsUrl(targetBase)
  if (!completionUrl) {
    return NextResponse.json({ error: 'Invalid targetBase URL.' }, { status: 400 })
  }

  let host: string
  try {
    host = new URL(completionUrl).hostname
  } catch {
    return NextResponse.json({ error: 'Could not parse target URL.' }, { status: 400 })
  }

  if (!(allowLoopback && (host === '127.0.0.1' || host === 'localhost' || host === '::1')) && !isAllowedChatProxyTarget(host)) {
    return NextResponse.json(
      {
        error:
          'Proxy target not allowed. Allowed: RFC1918 LAN IPs (10.x / 172.16–31.x / 192.168.x), or tunnel hosts on *.ngrok-free.app/.dev, *.ngrok.app/.io/.dev, *.trycloudflare.com, *.loca.lt. Override with NEXT_CHAT_PROXY_EXTRA_HOSTS. For 127.0.0.1, the browser calls Ollama directly — make sure OLLAMA_ORIGINS includes this UI origin.',
      },
      { status: 400 },
    )
  }

  const { targetBase: _t, allowLoopback: _a, ...openaiBody } = rec
  if (!openaiBody || typeof openaiBody !== 'object') {
    return NextResponse.json({ error: 'Invalid OpenAI body.' }, { status: 400 })
  }

  // Build headers — forward browser Origin so Ollama's CORS check passes,
  // and add ngrok bypass header for tunnel URLs.
  const upstreamHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
  const incomingOrigin = req.headers.get('origin')
  if (incomingOrigin) {
    upstreamHeaders['Origin'] = incomingOrigin
  }
  if (isTunnelHostname(host)) {
    upstreamHeaders['ngrok-skip-browser-warning'] = 'true'
  }

  try {
    const upstream = await fetch(completionUrl, {
      method: 'POST',
      headers: upstreamHeaders,
      body: JSON.stringify(openaiBody),
    })
    const contentType = upstream.headers.get('Content-Type') ?? 'application/json'
    const text = await upstream.text()
    // Detect ngrok interstitial HTML returned instead of JSON.
    if (contentType.includes('text/html') && text.includes('ngrok')) {
      return NextResponse.json(
        { error: 'ngrok returned its browser warning page instead of proxying to Ollama. Make sure Ollama is running and the ngrok tunnel is forwarding to port 11434.' },
        { status: 502 },
      )
    }
    return new NextResponse(text, {
      status: upstream.status,
      headers: { 'Content-Type': contentType },
    })
  } catch (e) {
    return NextResponse.json({ error: upstreamConnectFailureMessage(e, completionUrl) }, { status: 502 })
  }
}
