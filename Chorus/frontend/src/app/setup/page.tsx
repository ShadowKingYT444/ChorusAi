'use client'

import { AnimatePresence, motion } from 'framer-motion'
import Link from 'next/link'
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Cloud,
  Download,
  Globe2,
  Home,
  Laptop,
  Network,
  Package,
  Plug,
  Radio,
  Shield,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { CodeBlock } from '@/components/chorus/setup-wizard/code-block'
import { ConnectionTest } from '@/components/chorus/setup-wizard/connection-test'
import { OsTabs, detectOs, type OsKey } from '@/components/chorus/setup-wizard/os-tabs'
import { StepShell } from '@/components/chorus/setup-wizard/step-shell'
import {
  MODEL_NAME_KEY,
  MODEL_PUBLIC_URL_KEY,
  getOrchestratorBaseOverride,
  getSavedOllamaIp,
  isSavedModelVerified,
  saveOllamaIp,
  setSavedModelVerified,
  setOrchestratorBaseOverride,
} from '@/lib/api/orchestrator'

type PathMode = 'local' | 'tunnel'
type TunnelProvider = 'ngrok' | 'cloudflared'

interface ModelChoice {
  id: string
  label: string
  size: string
  description: string
}

const MODEL_CHOICES: ModelChoice[] = [
  { id: 'qwen2.5:0.5b', label: 'Fast', size: '0.5B', description: 'Smallest, runs on almost anything.' },
  { id: 'llama3.2:3b', label: 'Balanced', size: '3B', description: 'Solid replies on a modern laptop.' },
  { id: 'qwen2.5:7b', label: 'Quality', size: '7B', description: 'Needs ~8 GB RAM / decent GPU.' },
]

function isDeployedHost(): boolean {
  if (typeof window === 'undefined') return false
  const h = window.location.hostname
  return h !== 'localhost' && h !== '127.0.0.1' && h !== ''
}

function readLocalStorage(key: string): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem(key)?.trim() ?? ''
}

function writeLocalStorage(key: string, value: string): void {
  if (typeof window === 'undefined') return
  if (value.trim()) localStorage.setItem(key, value.trim())
  else localStorage.removeItem(key)
}

function deriveModelPublicUrl(mode: PathMode, lanIp: string, tunnelUrl: string): string {
  if (mode === 'tunnel') return tunnelUrl.trim()
  const raw = lanIp.trim()
  if (!raw) return ''
  return /^https?:\/\//i.test(raw) ? raw : `http://${raw}:11434`
}

export default function SetupPage() {
  const [mode, setMode] = useState<PathMode>('local')
  const [os, setOs] = useState<OsKey>('macos')
  const [stepIndex, setStepIndex] = useState(0)
  const [direction, setDirection] = useState<1 | -1>(1)

  const [model, setModel] = useState<string>(MODEL_CHOICES[0].id)
  const [lanIp, setLanIp] = useState('')
  const [tunnelProvider, setTunnelProvider] = useState<TunnelProvider>('ngrok')
  const [tunnelUrl, setTunnelUrl] = useState('')
  const [testOk, setTestOk] = useState(false)
  const [orchestratorBase, setOrchestratorBase] = useState('')
  const [orchestratorBaseFromEnv, setOrchestratorBaseFromEnv] = useState(false)
  const [origin, setOrigin] = useState('https://chorus.vercel.app')
  const [probePhase, setProbePhase] = useState<'idle' | 'probing' | 'ok' | 'error'>('idle')
  const [probeMessage, setProbeMessage] = useState('')

  // Hydrate from environment on mount.
  useEffect(() => {
    setOs(detectOs())
    setMode(isDeployedHost() ? 'tunnel' : 'local')
    if (typeof window !== 'undefined') {
      setOrigin(window.location.origin)
    }
    const savedModel = readLocalStorage(MODEL_NAME_KEY)
    if (savedModel) setModel(savedModel)
    const savedTunnel = readLocalStorage(MODEL_PUBLIC_URL_KEY)
    if (savedTunnel) setTunnelUrl(savedTunnel)
    const savedIp = getSavedOllamaIp()
    if (savedIp) setLanIp(savedIp)
    const override = getOrchestratorBaseOverride()
    const envBase = process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL?.trim() ?? ''
    const existingOrchestrator = override ?? envBase
    setOrchestratorBase(existingOrchestrator)
    // Track whether the value came from a baked-in env var (no override yet).
    setOrchestratorBaseFromEnv(!override && envBase.length > 0)
  }, [])

  // Persist model name whenever it changes.
  useEffect(() => {
    writeLocalStorage(MODEL_NAME_KEY, model)
  }, [model])

  // Persist tunnel URL whenever it changes.
  useEffect(() => {
    writeLocalStorage(MODEL_PUBLIC_URL_KEY, tunnelUrl)
  }, [tunnelUrl])

  // Persist LAN IP whenever it changes.
  useEffect(() => {
    if (lanIp.trim()) saveOllamaIp(lanIp.trim())
  }, [lanIp])

  // Reset test state when key inputs change.
  useEffect(() => {
    setTestOk(false)
    setSavedModelVerified(false)
  }, [mode, lanIp, tunnelUrl, model])

  // Build step list conditional on mode.
  const steps = useMemo(() => {
    const base = [
      { key: 'path', label: 'Path' },
      { key: 'install', label: 'Install Ollama' },
      { key: 'model', label: 'Pull a model' },
      { key: 'network', label: 'Enable network' },
    ]
    if (mode === 'tunnel') base.push({ key: 'tunnel', label: 'Expose tunnel' })
    base.push({ key: 'test', label: 'Test setup' })
    base.push({ key: 'connect', label: 'Connect to Chorus' })
    return base
  }, [mode])

  const totalSteps = steps.length
  const clampedIndex = Math.min(stepIndex, totalSteps - 1)
  const currentKey = steps[clampedIndex].key

  const goNext = useCallback(() => {
    setDirection(1)
    setStepIndex((i) => Math.min(i + 1, totalSteps - 1))
  }, [totalSteps])

  const goBack = useCallback(() => {
    setDirection(-1)
    setStepIndex((i) => Math.max(i - 1, 0))
  }, [])

  // Gate "Next" where user input is required.
  const nextDisabled = useMemo(() => {
    if (currentKey === 'test') return !testOk
    return false
  }, [currentKey, testOk])

  const onConnectOrchestrator = useCallback(() => {
    const v = orchestratorBase.trim()
    setOrchestratorBaseOverride(v || null)
    writeLocalStorage(MODEL_PUBLIC_URL_KEY, deriveModelPublicUrl(mode, lanIp, tunnelUrl))
  }, [lanIp, mode, orchestratorBase, tunnelUrl])

  // Reset probe state when the URL changes.
  useEffect(() => {
    setProbePhase('idle')
    setProbeMessage('')
  }, [orchestratorBase])

  const probeOrchestrator = useCallback(async (): Promise<boolean> => {
    const v = orchestratorBase.trim()
    if (!v) {
      setProbePhase('error')
      setProbeMessage(
        'No orchestrator URL set. Paste a Chorus signaling URL above (or set NEXT_PUBLIC_ORCHESTRATOR_BASE_URL on Vercel).',
      )
      return false
    }
    setProbePhase('probing')
    setProbeMessage('')
    const base = v.replace(/\/+$/, '')
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 8000)
    try {
      const res = await fetch(`${base}/health`, {
        method: 'GET',
        signal: controller.signal,
        headers: { Accept: 'application/json' },
      })
      if (!res.ok) {
        setProbePhase('error')
        if (res.status === 404) {
          setProbeMessage(
            `Health check returned 404. The URL is reachable but ${base}/health doesn't exist on it. Most common cause: you pasted the Vercel frontend URL by mistake. The orchestrator URL is the Railway one (e.g. https://your-app.up.railway.app), NOT the Vercel one. Open ${base}/health directly in a browser tab — if you don't see {"status":"ok"}, this isn't a Chorus orchestrator.`,
          )
        } else if (res.status === 503) {
          setProbeMessage(
            `Health check returned 503. The orchestrator is up but reports unavailable. Check the Railway deploy logs.`,
          )
        } else {
          setProbeMessage(
            `Health check failed: HTTP ${res.status}. The URL is reachable but the server isn't responding like a Chorus orchestrator.`,
          )
        }
        return false
      }
      setProbePhase('ok')
      setProbeMessage('Orchestrator reachable.')
      return true
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setProbePhase('error')
      if (/abort/i.test(msg)) {
        setProbeMessage(`Health check timed out after 8s. Confirm ${base} is online and CORS allows ${origin}.`)
      } else if (/cors|network|failed to fetch/i.test(msg)) {
        setProbeMessage(
          `Browser blocked the request. Most likely the orchestrator's CORS allowlist is missing this origin. On the orchestrator host (e.g. Railway), set ORC_CORS_ORIGINS=${origin} and redeploy.`,
        )
      } else {
        setProbeMessage(`Could not reach ${base}/health: ${msg}`)
      }
      return false
    } finally {
      clearTimeout(timeout)
    }
  }, [orchestratorBase, origin])

  const onFinishSetup = useCallback(async () => {
    onConnectOrchestrator()
    const ok = await probeOrchestrator()
    if (ok && typeof window !== 'undefined') {
      window.location.href = '/'
    }
  }, [onConnectOrchestrator, probeOrchestrator])

  const installCommands: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: '# Download the app\nopen https://ollama.com/download/Ollama.dmg\n\n# Or via Homebrew\nbrew install --cask ollama',
      note: 'After installing, launch Ollama once so the tray icon shows up.',
    },
    windows: {
      code: '# Download the installer, then run it\nstart https://ollama.com/download/OllamaSetup.exe',
      note: 'After installing, launch Ollama — you should see the llama icon in your system tray.',
    },
    linux: {
      code: 'curl -fsSL https://ollama.com/install.sh | sh',
      note: 'The installer sets up a systemd service. Confirm with `systemctl status ollama`.',
    },
  }

  const TRAY_WARNING =
    "Don't rely on the system-tray Ollama for env vars — on Windows and macOS the tray app frequently keeps stale environment values from the user's login session. Use `ollama serve` in a terminal instead. The terminal will show OLLAMA_ORIGINS in its boot log so you can confirm the value is live."

  const networkCommandsLan: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: '# Quit any tray Ollama first (menu bar → Quit), then in Terminal:\nOLLAMA_HOST=0.0.0.0 ollama serve\n# Leave this terminal open — Ollama runs here. Boot log should show:\n#   "OLLAMA_HOST: 0.0.0.0"',
      note: TRAY_WARNING,
    },
    windows: {
      code: '# In PowerShell. The tray Ollama often misses env vars set after login,\n# so kill it and run `ollama serve` directly:\nGet-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force\n$env:OLLAMA_HOST = "0.0.0.0"\nollama serve\n# Leave this PowerShell window open — Ollama runs here.\n# Boot log should show: "OLLAMA_HOST: 0.0.0.0"',
      note: TRAY_WARNING,
    },
    linux: {
      code: 'sudo systemctl edit ollama.service\n# add under [Service]:\n#   Environment="OLLAMA_HOST=0.0.0.0"\nsudo systemctl daemon-reload\nsudo systemctl restart ollama',
      note: 'Or for a one-off: `OLLAMA_HOST=0.0.0.0 ollama serve`.',
    },
  }

  const networkCommandsTunnel: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: `# Quit any tray Ollama first (menu bar → Quit), then in Terminal:\nOLLAMA_ORIGINS="${origin}" OLLAMA_HOST=127.0.0.1 ollama serve\n# Leave this terminal open — Ollama runs here. Boot log should print:\n#   "OLLAMA_ORIGINS: ${origin}"`,
      note: TRAY_WARNING,
    },
    windows: {
      code: `# In PowerShell. The tray Ollama often ignores env vars set after login,\n# so kill it and run \`ollama serve\` directly with the env var inline:\nGet-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force\n$env:OLLAMA_ORIGINS = "${origin}"\n$env:OLLAMA_HOST = "127.0.0.1"\nollama serve\n# Leave this PowerShell window open — Ollama runs here.\n# Boot log should print: "OLLAMA_ORIGINS: ${origin}"\n#\n# (Optional) Also persist for future shells:\n# [System.Environment]::SetEnvironmentVariable('OLLAMA_ORIGINS','${origin}','User')`,
      note: TRAY_WARNING,
    },
    linux: {
      code: `sudo systemctl edit ollama.service\n# add under [Service]:\n#   Environment="OLLAMA_ORIGINS=${origin}"\n#   Environment="OLLAMA_HOST=127.0.0.1"\nsudo systemctl daemon-reload\nsudo systemctl restart ollama`,
    },
  }

  const stepContent: Record<string, React.ReactNode> = {
    path: (
      <StepShell
        icon={<Laptop size={18} />}
        eyebrow={`Step 1 of ${totalSteps}`}
        title="Pick your path"
        subtitle="First decide where Ollama is running. If Chorus and Ollama are on the same computer, use the local path and `127.0.0.1`. Only use a LAN IP if Ollama is on a different machine. Only use ngrok/cloudflared if you are opening a deployed Chorus site over the internet."
      >
        <div
          style={{
            padding: '0.8rem 0.95rem',
            borderRadius: 6,
            border: '1px solid rgba(255,255,255,0.08)',
            background: 'rgba(255,255,255,0.025)',
            fontSize: 12.5,
            color: 'rgba(255,255,255,0.72)',
            lineHeight: 1.6,
            marginBottom: '0.85rem',
          }}
        >
          <div style={{ fontWeight: 600, color: 'rgba(255,255,255,0.92)', marginBottom: 6 }}>
            The simplest demo setup
          </div>
          <div>1. Run Ollama on the same laptop as Chorus.</div>
          <div>2. In the test step, enter <code>127.0.0.1</code>.</div>
          <div>3. You do not need ngrok for that path.</div>
        </div>
        <div style={{ display: 'grid', gap: '0.7rem' }}>
          <PathCard
            selected={mode === 'local'}
            onClick={() => setMode('local')}
            icon={<Home size={16} />}
            title="Run locally (dev mode)"
            description="Best option for a demo. If Ollama is on this same machine, use `127.0.0.1`. If Ollama is on another machine on your Wi-Fi, use its `192.168.x.x` address so this Next.js server can reach it."
            hint={isDeployedHost() ? undefined : 'Default — you seem to be running on localhost.'}
          />
          <PathCard
            selected={mode === 'tunnel'}
            onClick={() => setMode('tunnel')}
            icon={<Cloud size={16} />}
            title="Deploy & join the public network"
            description="Use this only if Chorus is running on a deployed site. `ngrok` or `cloudflared` gives your local Ollama a temporary public https URL."
            hint={isDeployedHost() ? 'Recommended — you are on a deployed instance.' : undefined}
          />
        </div>
      </StepShell>
    ),

    install: (
      <StepShell
        icon={<Download size={18} />}
        eyebrow={`Step 2 of ${totalSteps}`}
        title="Install Ollama"
        subtitle="Ollama runs the LLM locally and speaks the OpenAI chat-completions API."
      >
        <OsTabs
          value={os}
          onChange={setOs}
          commands={{
            macos: { code: installCommands.macos.code, note: installCommands.macos.note, label: 'macOS' },
            windows: { code: installCommands.windows.code, note: installCommands.windows.note, label: 'Windows' },
            linux: { code: installCommands.linux.code, note: installCommands.linux.note, label: 'Linux' },
          }}
        />
        <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)' }}>
          Already installed?{' '}
          <button
            type="button"
            onClick={goNext}
            style={{
              background: 'none',
              border: 'none',
              color: 'rgba(160,200,255,0.95)',
              cursor: 'pointer',
              fontSize: 12,
              textDecoration: 'underline',
              padding: 0,
            }}
          >
            Skip →
          </button>
        </p>
      </StepShell>
    ),

    model: (
      <StepShell
        icon={<Package size={18} />}
        eyebrow={`Step 3 of ${totalSteps}`}
        title="Pull a model"
        subtitle="Pick a size based on your machine. You can swap later."
      >
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.45rem' }}>
          {MODEL_CHOICES.map((m) => {
            const active = m.id === model
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => setModel(m.id)}
                style={{
                  flex: '1 1 10rem',
                  textAlign: 'left',
                  padding: '0.7rem 0.85rem',
                  borderRadius: 6,
                  border: active
                    ? '1px solid rgba(180,200,255,0.5)'
                    : '1px solid rgba(255,255,255,0.08)',
                  background: active ? 'rgba(180,200,255,0.08)' : 'rgba(255,255,255,0.025)',
                  color: 'rgba(255,255,255,0.9)',
                  cursor: 'pointer',
                  transition: 'all 140ms ease',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    marginBottom: 4,
                  }}
                >
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>{m.label}</span>
                  <span
                    style={{
                      fontFamily: 'var(--font-geist-mono), monospace',
                      fontSize: 10,
                      color: 'rgba(255,255,255,0.45)',
                      background: 'rgba(255,255,255,0.05)',
                      padding: '1px 6px',
                      borderRadius: 3,
                    }}
                  >
                    {m.size}
                  </span>
                </div>
                <div
                  style={{
                    fontFamily: 'var(--font-geist-mono), monospace',
                    fontSize: 11,
                    color: active ? 'rgba(200,220,255,0.92)' : 'rgba(255,255,255,0.6)',
                    marginBottom: 4,
                  }}
                >
                  {m.id}
                </div>
                <div style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.5)', lineHeight: 1.4 }}>
                  {m.description}
                </div>
              </button>
            )
          })}
        </div>
        <CodeBlock code={`ollama pull ${model}`} label="shell" />
        <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.45)', lineHeight: 1.5 }}>
          This downloads the weights. First pull can take a few minutes — subsequent runs are instant.
        </p>
      </StepShell>
    ),

    network: (
      <StepShell
        icon={<Network size={18} />}
        eyebrow={`Step 4 of ${totalSteps}`}
        title={mode === 'local' ? 'Enable LAN access' : 'Allow the deployed site'}
        subtitle={
          mode === 'local'
            ? 'Only do this if Ollama is on a different machine from Chorus. If Ollama and Chorus are on the same computer, you can skip straight to the test step and use 127.0.0.1.'
            : `Ollama blocks unknown origins by default. Whitelist ${origin} so the browser request is accepted through the tunnel.`
        }
      >
        {mode === 'local' && (
          <div
            style={{
              padding: '0.7rem 0.85rem',
              borderRadius: 5,
              border: '1px solid rgba(180,200,255,0.22)',
              background: 'rgba(30,40,70,0.26)',
              fontSize: 12.5,
              color: 'rgba(220,230,255,0.88)',
              lineHeight: 1.55,
              marginBottom: '0.8rem',
            }}
          >
            Plain English: if Ollama is on the <strong>same PC</strong> as Chorus, leave it local and test with{' '}
            <code>127.0.0.1</code>. If Ollama is on a <strong>different PC</strong>, then you must expose it on your LAN with{' '}
            <code>OLLAMA_HOST=0.0.0.0</code>.
          </div>
        )}
        <OsTabs
          value={os}
          onChange={setOs}
          commands={mode === 'local' ? networkCommandsLan : networkCommandsTunnel}
        />
        <div
          style={{
            padding: '0.7rem 0.85rem',
            borderRadius: 5,
            border: '1px solid rgba(255,210,160,0.2)',
            background: 'rgba(60,45,30,0.3)',
            fontSize: 12.5,
            color: 'rgba(255,220,180,0.85)',
            lineHeight: 1.5,
          }}
        >
          <strong style={{ color: 'rgba(255,230,200,0.95)' }}>After setting the env var:</strong> quit
          Ollama from the tray/menu bar icon, then relaunch it. Env vars set before the app was running
          are not picked up.
        </div>
      </StepShell>
    ),

    tunnel: (
      <StepShell
        icon={<Globe2 size={18} />}
        eyebrow={`Step 5 of ${totalSteps}`}
        title="Expose Ollama to the internet"
        subtitle="A tunnel is a small program that gives your computer a temporary public URL. Chorus uses that URL to reach your local Ollama when the site itself is not running on your laptop."
      >
        <div
          style={{
            padding: '0.75rem 0.9rem',
            borderRadius: 5,
            border: '1px solid rgba(255,255,255,0.08)',
            background: 'rgba(255,255,255,0.025)',
            fontSize: 12.5,
            color: 'rgba(255,255,255,0.72)',
            lineHeight: 1.6,
            marginBottom: '0.8rem',
          }}
        >
          <div style={{ fontWeight: 600, color: 'rgba(255,255,255,0.92)', marginBottom: 6 }}>
            What is ngrok?
          </div>
          <div>
            `ngrok` is an app you run on your computer. It creates an <strong>https URL on the public internet</strong> and forwards requests from that URL to{' '}
            <code>http://localhost:11434</code>, where Ollama is running.
          </div>
        </div>
        <div
          role="tablist"
          aria-label="Tunnel provider"
          style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}
        >
          {(['ngrok', 'cloudflared'] as TunnelProvider[]).map((p) => {
            const active = p === tunnelProvider
            return (
              <button
                key={p}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setTunnelProvider(p)}
                style={{
                  padding: '0.4rem 0.85rem',
                  borderRadius: 4,
                  border: active
                    ? '1px solid rgba(255,255,255,0.22)'
                    : '1px solid rgba(255,255,255,0.08)',
                  background: active ? 'rgba(255,255,255,0.1)' : 'transparent',
                  color: active ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.55)',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                  letterSpacing: '0.04em',
                }}
              >
                {p}
              </button>
            )
          })}
        </div>
        {tunnelProvider === 'ngrok' ? (
          <>
            <CodeBlock code="ngrok http 11434" label="shell" />
            <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', lineHeight: 1.5 }}>
              Don&apos;t have ngrok?{' '}
              <a
                href="https://ngrok.com/download"
                target="_blank"
                rel="noreferrer"
                style={{ color: 'rgba(160,200,255,0.95)' }}
              >
                Install it →
              </a>{' '}
              Look for the line like{' '}
              <span style={{ fontFamily: 'var(--font-geist-mono), monospace' }}>
                Forwarding https://abc-123.ngrok-free.app → http://localhost:11434
              </span>
              . Copy that https URL into the field below.
            </p>
          </>
        ) : (
          <>
            <CodeBlock code="cloudflared tunnel --url http://localhost:11434" label="shell" />
            <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', lineHeight: 1.5 }}>
              Don&apos;t have cloudflared?{' '}
              <a
                href="https://developers.cloudflare.com/cloudflared/"
                target="_blank"
                rel="noreferrer"
                style={{ color: 'rgba(160,200,255,0.95)' }}
              >
                Install it →
              </a>{' '}
              Look for a line like{' '}
              <span style={{ fontFamily: 'var(--font-geist-mono), monospace' }}>
                https://random-words.trycloudflare.com
              </span>
              . Copy that https URL into the field below.
            </p>
          </>
        )}

        <label
          style={{
            display: 'block',
            fontSize: 11.5,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.5)',
            marginTop: '0.25rem',
          }}
        >
          Paste the https URL it gave you
        </label>
        <input
          type="url"
          inputMode="url"
          value={tunnelUrl}
          onChange={(e) => setTunnelUrl(e.target.value)}
          placeholder="https://abc-123.ngrok-free.app"
          autoComplete="off"
          spellCheck={false}
          style={{
            width: '100%',
            padding: '0.6rem 0.75rem',
            borderRadius: 4,
            border: '1px solid rgba(255,255,255,0.14)',
            background: 'rgba(0,0,0,0.35)',
            color: '#fff',
            fontSize: 13.5,
            fontFamily: 'var(--font-geist-mono), monospace',
          }}
        />
        <p style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.4)', lineHeight: 1.5 }}>
          Saved locally in this browser so the main app can reuse it immediately.
        </p>
      </StepShell>
    ),

    test: (
      <StepShell
        icon={<Plug size={18} />}
        eyebrow={`Step ${mode === 'tunnel' ? 6 : 5} of ${totalSteps}`}
        title="Test your setup"
        subtitle={
          mode === 'local'
            ? 'Run this before you continue. If Ollama is on this same computer, use 127.0.0.1. If it is on another computer, use its 192.168.x.x address.'
            : 'Run this before you continue. We will call the public tunnel URL directly from your browser.'
        }
      >
        {mode === 'local' ? (
          <>
            <div style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap' }}>
              <button
                type="button"
                onClick={() => setLanIp('127.0.0.1')}
                style={{
                  padding: '0.45rem 0.75rem',
                  borderRadius: 4,
                  border: '1px solid rgba(180,200,255,0.22)',
                  background: 'rgba(30,40,70,0.32)',
                  color: 'rgba(220,230,255,0.92)',
                  fontSize: 12.5,
                  cursor: 'pointer',
                }}
              >
                Ollama is on this computer
              </button>
              <button
                type="button"
                onClick={() => setLanIp('')}
                style={{
                  padding: '0.45rem 0.75rem',
                  borderRadius: 4,
                  border: '1px solid rgba(255,255,255,0.12)',
                  background: 'rgba(255,255,255,0.02)',
                  color: 'rgba(255,255,255,0.72)',
                  fontSize: 12.5,
                  cursor: 'pointer',
                }}
              >
                Ollama is on another computer
              </button>
            </div>
            <label
              style={{
                display: 'block',
                fontSize: 11.5,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.5)',
              }}
            >
              Ollama address
            </label>
            <input
              value={lanIp}
              onChange={(e) => setLanIp(e.target.value)}
              placeholder="127.0.0.1 or 192.168.1.10"
              autoComplete="off"
              spellCheck={false}
              style={{
                width: '100%',
                padding: '0.6rem 0.75rem',
                borderRadius: 4,
                border: '1px solid rgba(255,255,255,0.14)',
                background: 'rgba(0,0,0,0.35)',
                color: '#fff',
                fontSize: 13.5,
                fontFamily: 'var(--font-geist-mono), monospace',
              }}
            />
            <div
              style={{
                padding: '0.65rem 0.8rem',
                borderRadius: 5,
                border: '1px solid rgba(255,255,255,0.08)',
                background: 'rgba(255,255,255,0.025)',
                fontSize: 12,
                color: 'rgba(255,255,255,0.62)',
                lineHeight: 1.55,
              }}
            >
              <div style={{ marginBottom: 4, fontWeight: 600, color: 'rgba(255,255,255,0.82)' }}>
                Which address should I use?
              </div>
              <div style={{ fontSize: 12, lineHeight: 1.55, color: 'rgba(255,255,255,0.68)' }}>
                Same computer as Chorus: use <code>127.0.0.1</code>.
              </div>
              <div style={{ fontSize: 12, lineHeight: 1.55, color: 'rgba(255,255,255,0.68)' }}>
                Different computer on your Wi-Fi/LAN: use that machine&apos;s <code>192.168.x.x</code> address.
              </div>
              <div style={{ fontFamily: 'var(--font-geist-mono), monospace', fontSize: 11.5, marginTop: 6 }}>
                Windows: <span style={{ color: 'rgba(200,220,255,0.9)' }}>ipconfig</span>
                {'  ·  '}
                macOS: <span style={{ color: 'rgba(200,220,255,0.9)' }}>ipconfig getifaddr en0</span>
                {'  ·  '}
                Linux: <span style={{ color: 'rgba(200,220,255,0.9)' }}>hostname -I</span>
              </div>
            </div>
            <ConnectionTest mode="lan" target={lanIp} model={model} onResult={setTestOk} />
          </>
        ) : (
          <>
            <label
              style={{
                display: 'block',
                fontSize: 11.5,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.5)',
              }}
            >
              Tunnel URL
            </label>
            <input
              value={tunnelUrl}
              onChange={(e) => setTunnelUrl(e.target.value)}
              placeholder="https://abc-123.ngrok-free.app"
              autoComplete="off"
              spellCheck={false}
              style={{
                width: '100%',
                padding: '0.6rem 0.75rem',
                borderRadius: 4,
                border: '1px solid rgba(255,255,255,0.14)',
                background: 'rgba(0,0,0,0.35)',
                color: '#fff',
                fontSize: 13.5,
                fontFamily: 'var(--font-geist-mono), monospace',
              }}
            />
            <ConnectionTest mode="tunnel" target={tunnelUrl} model={model} onResult={setTestOk} />
          </>
        )}
      </StepShell>
    ),

    connect: (
      <StepShell
        icon={<Radio size={18} />}
        eyebrow={`Step ${totalSteps} of ${totalSteps}`}
        title="Connect to the Chorus network"
        subtitle={
          orchestratorBase.trim()
            ? "Orchestrator URL is set. Click Finish setup — we'll verify it's reachable before continuing."
            : "Point this browser at a signaling / orchestrator server, then click Finish setup so we can verify it's reachable."
        }
      >
        {orchestratorBase.trim() ? (
          <div
            style={{
              padding: '0.75rem 0.9rem',
              borderRadius: 5,
              border: '1px solid rgba(143,212,168,0.3)',
              background: 'rgba(30,60,40,0.3)',
              color: 'rgba(210,240,220,0.92)',
              fontSize: 12.5,
              lineHeight: 1.55,
              marginBottom: '0.8rem',
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 4, color: 'rgba(220,245,225,0.98)' }}>
              {orchestratorBaseFromEnv
                ? 'Public Chorus signaling server pre-configured'
                : 'Orchestrator URL set'}
            </div>
            <div>
              {orchestratorBaseFromEnv
                ? "The orchestrator URL below was provided by this deployment. You don't need to host anything — just click "
                : 'Using the URL below. Click '}
              <strong>Finish setup</strong>. The wizard will GET <code>{`${orchestratorBase.trim().replace(/\/+$/, '')}/health`}</code>{' '}
              and surface the exact problem if it fails.
            </div>
          </div>
        ) : (
          <div
            style={{
              padding: '0.75rem 0.9rem',
              borderRadius: 5,
              border: '1px solid rgba(180,200,255,0.22)',
              background: 'rgba(30,40,70,0.32)',
              color: 'rgba(210,225,255,0.9)',
              fontSize: 12.5,
              lineHeight: 1.55,
              marginBottom: '0.8rem',
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 6, color: 'rgba(230,238,255,0.98)' }}>
              No orchestrator URL configured for this deployment
            </div>
            <div style={{ marginBottom: 8 }}>
              Easiest path: deploy your own backend to Railway with one click —{' '}
              <a
                href="https://github.com/ShadowKingYT444/ChorusAi#hosting-the-backend-railway"
                target="_blank"
                rel="noreferrer"
                style={{ color: 'rgba(180,210,255,0.95)' }}
              >
                follow the README
              </a>
              , then paste the resulting <code>https://*.up.railway.app</code> URL below.
            </div>
            <div style={{ marginBottom: 8 }}>
              Self-hosting locally is also possible (Python 3.11+ from a clone of{' '}
              <a
                href="https://github.com/ShadowKingYT444/ChorusAi"
                target="_blank"
                rel="noreferrer"
                style={{ color: 'rgba(180,210,255,0.95)' }}
              >
                the repo
              </a>
              ), but Railway is the recommended path for hosted users.
            </div>
          </div>
        )}

        <label
          style={{
            display: 'block',
            fontSize: 11.5,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.5)',
          }}
        >
          Orchestrator base URL
        </label>
        <input
          value={orchestratorBase}
          onChange={(e) => setOrchestratorBase(e.target.value)}
          onBlur={onConnectOrchestrator}
          placeholder="http://192.168.1.10:8000"
          autoComplete="off"
          spellCheck={false}
          style={{
            width: '100%',
            padding: '0.6rem 0.75rem',
            borderRadius: 4,
            border: '1px solid rgba(255,255,255,0.14)',
            background: 'rgba(0,0,0,0.35)',
            color: '#fff',
            fontSize: 13.5,
            fontFamily: 'var(--font-geist-mono), monospace',
          }}
        />
        <p style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.45)', lineHeight: 1.5 }}>
          Saved in this browser and reused across tabs. Equivalent to{' '}
          <code style={{ fontFamily: 'var(--font-geist-mono), monospace' }}>
            NEXT_PUBLIC_ORCHESTRATOR_BASE_URL
          </code>
          .{' '}
          {orchestratorBase.trim() && (
            <>
              Currently using{' '}
              <span style={{ color: 'rgba(255,255,255,0.82)' }}>{orchestratorBase.trim()}</span>.
            </>
          )}
        </p>

        <div
          style={{
            display: 'flex',
            gap: '0.6rem',
            flexWrap: 'wrap',
            marginTop: '0.3rem',
          }}
        >
          <button
            type="button"
            onClick={onFinishSetup}
            disabled={probePhase === 'probing'}
            style={{
              padding: '0.6rem 1.15rem',
              borderRadius: 4,
              border: 'none',
              background: probePhase === 'probing' ? 'rgba(255,255,255,0.5)' : 'rgba(255,255,255,0.92)',
              color: '#050508',
              fontWeight: 600,
              fontSize: 13.5,
              cursor: probePhase === 'probing' ? 'wait' : 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.45rem',
            }}
          >
            {probePhase === 'probing' ? 'Verifying…' : 'Verify & open Chorus'}
            <ArrowRight size={14} />
          </button>
          <Link
            href="/join"
            style={{
              padding: '0.6rem 1rem',
              borderRadius: 4,
              border: '1px solid rgba(255,255,255,0.18)',
              background: 'transparent',
              color: 'rgba(255,255,255,0.85)',
              fontWeight: 500,
              fontSize: 13,
              textDecoration: 'none',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.4rem',
            }}
          >
            <Radio size={13} />
            Advanced join
          </Link>
        </div>

        {probePhase === 'error' && (
          <div
            role="alert"
            style={{
              marginTop: '0.6rem',
              padding: '0.7rem 0.85rem',
              borderRadius: 5,
              border: '1px solid rgba(246,168,154,0.35)',
              background: 'rgba(60,30,30,0.3)',
              color: '#f6a89a',
              fontSize: 12.5,
              lineHeight: 1.5,
              wordBreak: 'break-word',
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 3 }}>Couldn&apos;t reach the orchestrator</div>
            <div style={{ color: 'rgba(246,200,190,0.88)' }}>{probeMessage}</div>
          </div>
        )}
        {probePhase === 'ok' && (
          <div
            role="status"
            style={{
              marginTop: '0.6rem',
              padding: '0.6rem 0.85rem',
              borderRadius: 5,
              border: '1px solid rgba(143,212,168,0.35)',
              background: 'rgba(30,60,40,0.32)',
              color: 'rgba(200,240,210,0.92)',
              fontSize: 12.5,
            }}
          >
            Verified. Opening Chorus…
          </div>
        )}

        <div
          style={{
            marginTop: '0.5rem',
            padding: '0.75rem 0.9rem',
            borderRadius: 5,
            border: '1px solid rgba(143,212,168,0.25)',
            background: 'rgba(30,60,40,0.28)',
            color: 'rgba(200,240,210,0.9)',
            fontSize: 12.5,
            lineHeight: 1.55,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              fontWeight: 600,
              marginBottom: 4,
            }}
          >
            <CheckCircle2 size={14} style={{ color: '#8fd4a8' }} />
            You&apos;re set up
          </div>
          <div>
            Model <code>{model}</code> · {mode === 'local' ? 'LAN mode' : 'Tunnel mode'} ·{' '}
            {mode === 'local' ? lanIp || '(no IP yet)' : tunnelUrl || '(no tunnel yet)'}
          </div>
          <div style={{ marginTop: 4 }}>
            Peer endpoint <code>{deriveModelPublicUrl(mode, lanIp, tunnelUrl) || '(not set yet)'}</code>
          </div>
          <div style={{ marginTop: 4 }}>
            Status: {isSavedModelVerified() ? <strong>verified</strong> : <strong>not verified yet</strong>}
          </div>
        </div>

        <div
          style={{
            marginTop: '0.9rem',
            padding: '0.85rem 0.95rem',
            borderRadius: 5,
            border: '1px solid rgba(255,255,255,0.10)',
            background: 'rgba(255,255,255,0.025)',
          }}
        >
          <div
            style={{
              fontSize: 10.5,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              color: 'rgba(255,255,255,0.45)',
              fontFamily: 'var(--font-geist-mono), monospace',
              marginBottom: 8,
            }}
          >
            What happens when you prompt Chorus
          </div>
          <ul
            style={{
              listStyle: 'none',
              margin: 0,
              padding: 0,
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              fontSize: 12.5,
              color: 'rgba(255,255,255,0.80)',
              lineHeight: 1.55,
            }}
          >
            <li>
              <span style={{ color: '#8fd4a8', marginRight: 6 }}>▸</span>
              Orchestrator fans the prompt across every online peer, each with a distinct persona.
            </li>
            <li>
              <span style={{ color: '#8fd4a8', marginRight: 6 }}>▸</span>
              Round-by-round answers stream into the feed live — watch the swarm think in real time.
            </li>
            <li>
              <span style={{ color: '#8fd4a8', marginRight: 6 }}>▸</span>
              A consensus/dissent graph scores each peer; watchdogs prune refusals and duplicates.
            </li>
            <li>
              <span style={{ color: '#8fd4a8', marginRight: 6 }}>▸</span>
              A moderator agent merges the best answers into a <strong>single final response</strong> with citations.
            </li>
            <li>
              <span style={{ color: '#8fd4a8', marginRight: 6 }}>▸</span>
              Payout splits (floor + consensus bonus + dissent bonus) ship with an <strong>Ed25519-signed receipt</strong>.
            </li>
            <li>
              <span style={{ color: '#8fd4a8', marginRight: 6 }}>▸</span>
              Every job is saved — your history is on the sidebar, intact across restarts.
            </li>
          </ul>
        </div>
      </StepShell>
    ),
  }

  const progress = ((clampedIndex + 1) / totalSteps) * 100

  return (
    <div
      style={{
        minHeight: '100dvh',
        background: '#050508',
        color: 'rgba(255,255,255,0.92)',
        fontFamily: 'var(--font-geist-sans), system-ui, sans-serif',
        padding: 'clamp(1.25rem, 4vw, 2.5rem)',
        paddingBottom: '5rem',
      }}
    >
      <div style={{ maxWidth: '40rem', margin: '0 auto' }}>
        {/* Top nav */}
        <nav
          aria-label="Breadcrumb"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '1.25rem',
          }}
        >
          <Link
            href="/"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.3rem',
              fontSize: 11.5,
              fontWeight: 600,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '0.4rem 0.75rem',
              borderRadius: 3,
              color: 'rgba(255,255,255,0.55)',
              border: '1px solid rgba(255,255,255,0.1)',
              textDecoration: 'none',
            }}
          >
            <ArrowLeft size={12} />
            Home
          </Link>
          <span
            style={{
              fontSize: 10.5,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              color: 'rgba(255,255,255,0.35)',
              fontFamily: 'var(--font-geist-mono), monospace',
            }}
          >
            Chorus · Setup
          </span>
        </nav>

        {/* Progress */}
        <div style={{ marginBottom: '2rem' }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginBottom: '0.4rem',
            }}
          >
            <span
              style={{
                fontSize: 11,
                letterSpacing: '0.14em',
                textTransform: 'uppercase',
                color: 'rgba(255,255,255,0.4)',
                fontFamily: 'var(--font-geist-mono), monospace',
              }}
            >
              Step {clampedIndex + 1} of {totalSteps}
            </span>
            <span
              style={{
                fontSize: 12,
                color: 'rgba(255,255,255,0.52)',
              }}
            >
              {steps[clampedIndex].label}
            </span>
          </div>
          <div
            role="progressbar"
            aria-valuenow={clampedIndex + 1}
            aria-valuemin={1}
            aria-valuemax={totalSteps}
            style={{
              height: 3,
              borderRadius: 3,
              background: 'rgba(255,255,255,0.06)',
              overflow: 'hidden',
            }}
          >
            <motion.div
              animate={{ width: `${progress}%` }}
              transition={{ duration: 0.35, ease: 'easeOut' }}
              style={{
                height: '100%',
                background:
                  'linear-gradient(90deg, rgba(180,200,255,0.95), rgba(200,180,255,0.75))',
                boxShadow: '0 0 14px rgba(180,200,255,0.35)',
              }}
            />
          </div>
        </div>

        {/* Animated step */}
        <div style={{ position: 'relative', minHeight: '24rem' }}>
          <AnimatePresence mode="wait" custom={direction}>
            <motion.div
              key={currentKey}
              initial={{ opacity: 0, x: direction * 16 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: direction * -16 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
            >
              {stepContent[currentKey]}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Sticky nav */}
        <div
          style={{
            position: 'sticky',
            bottom: 0,
            marginTop: '2rem',
            paddingTop: '1rem',
            display: 'flex',
            gap: '0.6rem',
            justifyContent: 'space-between',
            alignItems: 'center',
            background: 'linear-gradient(to top, rgba(5,5,8,0.96), rgba(5,5,8,0.0))',
          }}
        >
          <button
            type="button"
            onClick={goBack}
            disabled={clampedIndex === 0}
            style={{
              padding: '0.55rem 1rem',
              borderRadius: 4,
              border: '1px solid rgba(255,255,255,0.14)',
              background: 'transparent',
              color: clampedIndex === 0 ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.8)',
              fontWeight: 500,
              fontSize: 13,
              cursor: clampedIndex === 0 ? 'not-allowed' : 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.35rem',
            }}
          >
            <ArrowLeft size={13} />
            Back
          </button>

          {clampedIndex < totalSteps - 1 ? (
            <button
              type="button"
              onClick={goNext}
              disabled={nextDisabled}
              style={{
                padding: '0.6rem 1.25rem',
                borderRadius: 4,
                border: 'none',
                background: nextDisabled ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.92)',
                color: nextDisabled ? 'rgba(255,255,255,0.55)' : '#050508',
                fontWeight: 600,
                fontSize: 13.5,
                cursor: nextDisabled ? 'not-allowed' : 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.4rem',
              }}
            >
              Next
              <ArrowRight size={14} />
            </button>
          ) : (
            <button
              type="button"
              onClick={onFinishSetup}
              disabled={probePhase === 'probing'}
              style={{
                padding: '0.6rem 1.25rem',
                borderRadius: 4,
                border: 'none',
                background:
                  probePhase === 'probing' ? 'rgba(255,255,255,0.5)' : 'rgba(255,255,255,0.92)',
                color: '#050508',
                fontWeight: 600,
                fontSize: 13.5,
                cursor: probePhase === 'probing' ? 'wait' : 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.4rem',
              }}
            >
              <Shield size={13} />
              {probePhase === 'probing' ? 'Verifying…' : 'Finish setup'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

interface PathCardProps {
  selected: boolean
  onClick: () => void
  icon: React.ReactNode
  title: string
  description: string
  hint?: string
}

function PathCard({ selected, onClick, icon, title, description, hint }: PathCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      style={{
        textAlign: 'left',
        padding: '1rem 1.1rem',
        borderRadius: 7,
        border: selected
          ? '1px solid rgba(180,200,255,0.5)'
          : '1px solid rgba(255,255,255,0.08)',
        background: selected ? 'rgba(180,200,255,0.07)' : 'rgba(255,255,255,0.025)',
        color: 'rgba(255,255,255,0.92)',
        cursor: 'pointer',
        transition: 'all 160ms ease',
        display: 'flex',
        gap: '0.85rem',
        alignItems: 'flex-start',
      }}
    >
      <span
        aria-hidden
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 30,
          height: 30,
          borderRadius: 6,
          background: selected ? 'rgba(180,200,255,0.18)' : 'rgba(255,255,255,0.05)',
          color: selected ? 'rgba(220,230,255,0.98)' : 'rgba(255,255,255,0.75)',
          flexShrink: 0,
        }}
      >
        {icon}
      </span>
      <div style={{ flex: 1 }}>
        <div
          style={{
            fontSize: 14,
            fontWeight: 600,
            marginBottom: 4,
            color: 'rgba(255,255,255,0.96)',
          }}
        >
          {title}
        </div>
        <div
          style={{
            fontSize: 12.5,
            lineHeight: 1.5,
            color: 'rgba(255,255,255,0.6)',
          }}
        >
          {description}
        </div>
        {hint && (
          <div
            style={{
              marginTop: 6,
              fontSize: 11,
              color: 'rgba(160,200,255,0.8)',
              fontFamily: 'var(--font-geist-mono), monospace',
              letterSpacing: '0.02em',
            }}
          >
            {hint}
          </div>
        )}
      </div>
    </button>
  )
}
