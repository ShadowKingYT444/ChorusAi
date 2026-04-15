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
  getOrchestratorBaseOverride,
  getSavedOllamaIp,
  saveOllamaIp,
  setOrchestratorBaseOverride,
} from '@/lib/api/orchestrator'

type PathMode = 'local' | 'tunnel'
type TunnelProvider = 'ngrok' | 'cloudflared'

const MODEL_PUBLIC_URL_KEY = 'chorus_model_public_url'
const MODEL_NAME_KEY = 'chorus_model_name'

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
  const [origin, setOrigin] = useState('https://chorus.vercel.app')

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
    const existingOrchestrator =
      getOrchestratorBaseOverride() ?? process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL?.trim() ?? ''
    setOrchestratorBase(existingOrchestrator)
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

  const installCommands: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: '# Download the app\nopen https://ollama.com/download/Ollama-darwin.zip\n\n# Or via Homebrew\nbrew install ollama',
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

  const networkCommandsLan: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: 'launchctl setenv OLLAMA_HOST "0.0.0.0"\n# then quit Ollama from the menu bar and relaunch it',
      note: 'Alternatively, run `OLLAMA_HOST=0.0.0.0 ollama serve` in a terminal instead of the app.',
    },
    windows: {
      code: "# In PowerShell (regular user, no admin needed):\n[System.Environment]::SetEnvironmentVariable('OLLAMA_HOST','0.0.0.0','User')\n# then right-click the Ollama tray icon → Quit, and relaunch it",
    },
    linux: {
      code: 'sudo systemctl edit ollama.service\n# add under [Service]:\n#   Environment="OLLAMA_HOST=0.0.0.0"\nsudo systemctl daemon-reload\nsudo systemctl restart ollama',
      note: 'Or for a one-off: `OLLAMA_HOST=0.0.0.0 ollama serve`.',
    },
  }

  const networkCommandsTunnel: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: `launchctl setenv OLLAMA_ORIGINS "${origin}"\nlaunchctl setenv OLLAMA_HOST "127.0.0.1"\n# then quit Ollama from the menu bar and relaunch it`,
      note: 'This allows the deployed Chorus site to call your local Ollama via the tunnel.',
    },
    windows: {
      code: `[System.Environment]::SetEnvironmentVariable('OLLAMA_ORIGINS','${origin}','User')\n[System.Environment]::SetEnvironmentVariable('OLLAMA_HOST','127.0.0.1','User')\n# then right-click the Ollama tray icon → Quit, and relaunch it`,
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
        subtitle="Chorus is a swarm of peer LLMs. You host one — Chorus fans every prompt across the network, streams answers back live, merges them into a signed consensus response, and pays out by impact. You need an OpenAI-compatible endpoint on your machine; choose how it should be reached."
      >
        <div style={{ display: 'grid', gap: '0.7rem' }}>
          <PathCard
            selected={mode === 'local'}
            onClick={() => setMode('local')}
            icon={<Home size={16} />}
            title="Run locally (dev mode)"
            description="You're running `npm run dev` on this machine. Peers on your LAN can reach you directly."
            hint={isDeployedHost() ? undefined : 'Default — you seem to be running on localhost.'}
          />
          <PathCard
            selected={mode === 'tunnel'}
            onClick={() => setMode('tunnel')}
            icon={<Cloud size={16} />}
            title="Deploy & join the public network"
            description="You're using the deployed Chorus site. Expose your local Ollama via an https tunnel so the network can call you."
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
            ? 'By default Ollama only listens on localhost. Bind it to 0.0.0.0 so LAN peers can reach it.'
            : `Ollama blocks unknown origins by default. Whitelist ${origin} so the browser request isn't rejected.`
        }
      >
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
        subtitle="Pick a tunnel provider, run the command, paste the https URL it prints."
      >
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
              .
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
              .
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
          Saved locally in this browser. Shared with <code>/join</code> so you don&apos;t retype it.
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
            ? 'We will send a tiny chat request through the Next proxy to your LAN IP.'
            : 'We will send a tiny chat request directly from your browser to the tunnel URL.'
        }
      >
        {mode === 'local' ? (
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
              Your LAN IP (running Ollama)
            </label>
            <input
              value={lanIp}
              onChange={(e) => setLanIp(e.target.value)}
              placeholder="192.168.1.10"
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
                Find your LAN IP
              </div>
              <div style={{ fontFamily: 'var(--font-geist-mono), monospace', fontSize: 11.5 }}>
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
        subtitle="Point this browser at a signaling / orchestrator server. Once set, you'll hand off to /join to register."
      >
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
          <Link
            href="/join"
            onClick={onConnectOrchestrator}
            style={{
              padding: '0.6rem 1.15rem',
              borderRadius: 4,
              background: 'rgba(255,255,255,0.92)',
              color: '#050508',
              fontWeight: 600,
              fontSize: 13.5,
              textDecoration: 'none',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.45rem',
            }}
          >
            Join the network
            <ArrowRight size={14} />
          </Link>
          <Link
            href="/"
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
            <Home size={13} />
            Back to chat
          </Link>
        </div>

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
            <Link
              href="/join"
              onClick={onConnectOrchestrator}
              style={{
                padding: '0.6rem 1.25rem',
                borderRadius: 4,
                background: 'rgba(255,255,255,0.92)',
                color: '#050508',
                fontWeight: 600,
                fontSize: 13.5,
                textDecoration: 'none',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.4rem',
              }}
            >
              <Shield size={13} />
              Finish & join
            </Link>
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
