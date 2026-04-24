'use client'

import { AnimatePresence, motion } from 'framer-motion'
import Link from 'next/link'
import {
  ArrowLeft,
  ArrowRight,
  Cloud,
  Download,
  Globe2,
  Home,
  Laptop,
  Network,
  Package,
  Plug,
  Radio,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CodeBlock } from '@/components/chorus/setup-wizard/code-block'
import { ConnectionTest } from '@/components/chorus/setup-wizard/connection-test'
import { OsTabs, detectOs, type OsKey } from '@/components/chorus/setup-wizard/os-tabs'
import { StepShell } from '@/components/chorus/setup-wizard/step-shell'
import {
  MODEL_NAME_KEY,
  MODEL_PUBLIC_URL_KEY,
  getEffectiveOrchestratorBase,
  getOrchestratorBaseOverride,
  getSavedOllamaIp,
  normalizeOrchestratorBase,
  saveOllamaIp,
  setOrchestratorBaseOverride,
  suggestLocalOrchestratorBase,
} from '@/lib/api/orchestrator'
import { isLoopbackOllamaHost, isPrivateLanIpv4 } from '@/lib/lan/chat-proxy-allow'
import { normalizeOpenAIChatCompletionsUrl } from '@/lib/lan/normalize-openai-chat-url'
import {
  getOrCreateWorkspaceId,
  readWorkspaceToken,
  regenerateWorkspaceId,
  writeWorkspaceId,
  writeWorkspaceToken,
} from '@/lib/workspace-config'

type PathMode = 'local' | 'tunnel'
type TunnelProvider = 'ngrok' | 'cloudflared'

const SETUP_TUNNEL_URL_KEY = 'chorus_setup_tunnel_url'
const OLLAMA_PORT_KEY = 'chorus_ollama_port'
const DEFAULT_OLLAMA_PORT = '11434'

interface ModelChoice {
  id: string
  label: string
  size: string
  description: string
}

const MODEL_CHOICES: ModelChoice[] = [
  { id: 'qwen2.5:0.5b', label: 'Fast triage', size: '0.5B', description: 'Lightweight reviewer for quick reads and low-power machines.' },
  { id: 'llama3.2:3b', label: 'Balanced', size: '3B', description: 'Good default for day-to-day RFC and launch reviews.' },
  { id: 'qwen2.5:7b', label: 'Deep review', size: '7B', description: 'Best quality if you have the RAM or GPU headroom.' },
]

const TRAY_WARNING =
  "Don't rely on the system-tray Ollama for env vars. Start `ollama serve` in a terminal so you can verify the boot log contains the values you set."

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
  const trimmed = value.trim()
  if (trimmed) localStorage.setItem(key, trimmed)
  else localStorage.removeItem(key)
}

function deriveModelPublicUrl(mode: PathMode, lanIp: string, tunnelUrl: string, ollamaPort: string): string {
  if (mode === 'tunnel') return tunnelUrl.trim()
  const raw = lanIp.trim()
  if (!raw) return ''
  if (/^https?:\/\//i.test(raw)) return raw
  const port = ollamaPort.trim() || DEFAULT_OLLAMA_PORT
  return `http://${raw}:${port}`
}

function isLocalModelBase(raw: string): boolean {
  const normalized = normalizeOpenAIChatCompletionsUrl(raw)
  if (!normalized) return false
  try {
    const host = new URL(normalized).hostname
    return isLoopbackOllamaHost(host) || isPrivateLanIpv4(host)
  } catch {
    return false
  }
}

export default function SetupPage() {
  const [mode, setMode] = useState<PathMode>('local')
  const [os, setOs] = useState<OsKey>('macos')
  const [stepIndex, setStepIndex] = useState(0)
  const [direction, setDirection] = useState<1 | -1>(1)
  const [model, setModel] = useState(MODEL_CHOICES[1].id)
  const [lanIp, setLanIp] = useState('')
  const [ollamaPort, setOllamaPort] = useState(DEFAULT_OLLAMA_PORT)
  const [tunnelProvider, setTunnelProvider] = useState<TunnelProvider>('ngrok')
  const [tunnelUrl, setTunnelUrl] = useState('')
  const [testOk, setTestOk] = useState(false)
  const [orchestratorBase, setOrchestratorBase] = useState('')
  const [orchestratorBaseFromEnv, setOrchestratorBaseFromEnv] = useState(false)
  const [origin, setOrigin] = useState('https://chorus.vercel.app')
  const [probePhase, setProbePhase] = useState<'idle' | 'probing' | 'ok' | 'error'>('idle')
  const [probeMessage, setProbeMessage] = useState('')
  const [workspaceId, setWorkspaceId] = useState('')
  const [workspaceToken, setWorkspaceToken] = useState('')

  useEffect(() => {
    setOs(detectOs())
    setMode(isDeployedHost() ? 'tunnel' : 'local')
    if (typeof window !== 'undefined') {
      setOrigin(window.location.origin)
    }

    const savedModel = readLocalStorage(MODEL_NAME_KEY)
    if (savedModel) setModel(savedModel)

    const savedTunnelDraft = readLocalStorage(SETUP_TUNNEL_URL_KEY)
    const savedPublicModelUrl = readLocalStorage(MODEL_PUBLIC_URL_KEY)
    const savedTunnel =
      savedTunnelDraft || (savedPublicModelUrl && !isLocalModelBase(savedPublicModelUrl) ? savedPublicModelUrl : '')
    if (savedTunnel) setTunnelUrl(savedTunnel)

    const savedIp = getSavedOllamaIp()
    if (savedIp) setLanIp(savedIp)
    const savedPort = readLocalStorage(OLLAMA_PORT_KEY)
    if (savedPort) setOllamaPort(savedPort)

    const override = getOrchestratorBaseOverride()
    const envBase = process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL?.trim() ?? ''
    const existingOrchestrator =
      override ?? getEffectiveOrchestratorBase() ?? suggestLocalOrchestratorBase() ?? ''
    setOrchestratorBase(existingOrchestrator)
    setOrchestratorBaseFromEnv(!override && envBase.length > 0)
    setWorkspaceId(getOrCreateWorkspaceId())
    setWorkspaceToken(readWorkspaceToken())
  }, [])

  useEffect(() => {
    writeLocalStorage(MODEL_NAME_KEY, model)
  }, [model])

  useEffect(() => {
    writeLocalStorage(SETUP_TUNNEL_URL_KEY, tunnelUrl)
  }, [tunnelUrl])

  useEffect(() => {
    if (lanIp.trim()) saveOllamaIp(lanIp.trim())
  }, [lanIp])

  useEffect(() => {
    writeLocalStorage(OLLAMA_PORT_KEY, ollamaPort.trim() || DEFAULT_OLLAMA_PORT)
  }, [ollamaPort])

  useEffect(() => {
    writeWorkspaceId(workspaceId)
  }, [workspaceId])

  useEffect(() => {
    writeWorkspaceToken(workspaceToken)
  }, [workspaceToken])

  const onRegenerateWorkspaceId = useCallback(() => {
    const next = regenerateWorkspaceId()
    setWorkspaceId(next)
  }, [])

  const hydratedRef = useRef(false)
  useEffect(() => {
    if (!hydratedRef.current) {
      hydratedRef.current = true
      return
    }
    setTestOk(false)
  }, [mode, lanIp, tunnelUrl, model, ollamaPort])

  const steps = useMemo(() => {
    const base = [
      { key: 'path', label: 'Capacity path' },
      { key: 'install', label: 'Install Ollama' },
      { key: 'model', label: 'Choose model' },
      { key: 'network', label: 'Allow access' },
    ]
    if (mode === 'tunnel') base.push({ key: 'tunnel', label: 'Expose endpoint' })
    base.push({ key: 'test', label: 'Test capacity' })
    base.push({ key: 'connect', label: 'Connect workspace' })
    return base
  }, [mode])

  const totalSteps = steps.length
  const clampedIndex = Math.min(stepIndex, totalSteps - 1)
  const currentKey = steps[clampedIndex].key
  const progress = ((clampedIndex + 1) / totalSteps) * 100

  const nextDisabled = currentKey === 'test' ? !testOk : false

  const goNext = useCallback(() => {
    setDirection(1)
    setStepIndex((current) => Math.min(current + 1, totalSteps - 1))
  }, [totalSteps])

  const goBack = useCallback(() => {
    setDirection(-1)
    setStepIndex((current) => Math.max(current - 1, 0))
  }, [])

  const onConnectOrchestrator = useCallback(() => {
    const trimmed = orchestratorBase.trim()
    const normalized = trimmed ? normalizeOrchestratorBase(trimmed) : null
    setOrchestratorBaseOverride(normalized)
    if (normalized) setOrchestratorBase(normalized)
    writeLocalStorage(MODEL_PUBLIC_URL_KEY, deriveModelPublicUrl(mode, lanIp, tunnelUrl, ollamaPort))
  }, [lanIp, mode, ollamaPort, orchestratorBase, tunnelUrl])

  useEffect(() => {
    setProbePhase('idle')
    setProbeMessage('')
  }, [orchestratorBase])

  const probeOrchestrator = useCallback(async (): Promise<boolean> => {
    const trimmed = orchestratorBase.trim()
    if (!trimmed) {
      setProbePhase('error')
      setProbeMessage('No control plane URL set. Paste the Railway or private deployment URL first.')
      return false
    }

    setProbePhase('probing')
    setProbeMessage('')

    const base = normalizeOrchestratorBase(trimmed)
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
            `Health check returned 404. ${base}/health is missing. Most often this means the frontend URL was pasted instead of the control plane URL.`,
          )
        } else {
          setProbeMessage(`Health check failed with HTTP ${res.status}.`)
        }
        return false
      }

      setProbePhase('ok')
      setProbeMessage('Control plane reachable.')
      return true
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setProbePhase('error')
      if (/abort/i.test(msg)) {
        setProbeMessage(`Health check timed out after 8s. Confirm ${base} is online and reachable from ${origin}.`)
      } else if (/cors|network|failed to fetch/i.test(msg)) {
        setProbeMessage(`Browser request failed. Check CORS on the control plane and confirm ${origin} is allowed.`)
      } else {
        setProbeMessage(`Could not reach ${base}/health: ${msg}`)
      }
      return false
    } finally {
      clearTimeout(timeout)
    }
  }, [orchestratorBase, origin])

  const onFinishSetup = useCallback(async () => {
    if (!workspaceId.trim()) {
      setProbePhase('error')
      setProbeMessage('Generate or enter a workspace id before opening the workspace.')
      return
    }
    if (!workspaceToken.trim()) {
      setProbePhase('error')
      setProbeMessage('Enter your workspace token for this browser session before continuing.')
      return
    }
    onConnectOrchestrator()
    const ok = await probeOrchestrator()
    if (ok && typeof window !== 'undefined') {
      window.location.href = '/'
    }
  }, [onConnectOrchestrator, probeOrchestrator, workspaceId, workspaceToken])

  const installCommands: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: '# Download the app\nopen https://ollama.com/download/Ollama.dmg\n\n# Or via Homebrew\nbrew install --cask ollama',
      note: 'Launch Ollama once after install so the service is ready.',
    },
    windows: {
      code: '# Download the installer, then run it\nstart https://ollama.com/download/OllamaSetup.exe',
      note: 'Launch Ollama once after install so the service is ready.',
    },
    linux: {
      code: 'curl -fsSL https://ollama.com/install.sh | sh',
      note: 'The installer sets up a systemd service. Confirm with `systemctl status ollama`.',
    },
  }

  const ollamaPortValue = ollamaPort.trim() || DEFAULT_OLLAMA_PORT
  const localOllamaBase = `http://localhost:${ollamaPortValue}`

  const networkCommandsLan: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: `# Stop any running Ollama instance first\npkill -x Ollama || true\npkill -x ollama || true\nOLLAMA_HOST=0.0.0.0:${ollamaPortValue} ollama serve`,
      note: TRAY_WARNING,
    },
    windows: {
      code: `Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force\nwsl -e sh -lc "pkill -f ollama || true" 2>$null\nwsl --shutdown 2>$null\n$env:OLLAMA_HOST = "0.0.0.0:${ollamaPortValue}"\nollama serve`,
      note: `${TRAY_WARNING} If port ${ollamaPortValue} is forbidden, run \`netsh interface ipv4 show excludedportrange protocol=tcp\`. If the port is inside an excluded range, pick a free port such as 11500 and use the full URL in Chorus setup.`,
    },
    linux: {
      code: `sudo systemctl stop ollama || true\nsudo systemctl edit ollama.service\n# add under [Service]:\n#   Environment="OLLAMA_HOST=0.0.0.0:${ollamaPortValue}"\nsudo systemctl daemon-reload\nsudo systemctl restart ollama`,
      note: `Or for a one-off: \`OLLAMA_HOST=0.0.0.0:${ollamaPortValue} ollama serve\`.`,
    },
  }

  const networkCommandsTunnel: Record<OsKey, { code: string; note?: string }> = {
    macos: {
      code: `# Terminal #1\npkill -x Ollama || true\npkill -x ollama || true\nOLLAMA_ORIGINS="${origin}" OLLAMA_HOST=127.0.0.1:${ollamaPortValue} ollama serve`,
      note: TRAY_WARNING,
    },
    windows: {
      code: `Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force\nwsl -e sh -lc "pkill -f ollama || true" 2>$null\nwsl --shutdown 2>$null\n$env:OLLAMA_ORIGINS = "${origin}"\n$env:OLLAMA_HOST = "127.0.0.1:${ollamaPortValue}"\nollama serve`,
      note: `${TRAY_WARNING} If you still see a bind error, run \`netsh interface ipv4 show excludedportrange protocol=tcp\` and check whether ${ollamaPortValue} is reserved. If it is, change the port here and in your tunnel command.`,
    },
    linux: {
      code: `sudo systemctl stop ollama || true\nsudo systemctl edit ollama.service\n# add under [Service]:\n#   Environment="OLLAMA_ORIGINS=${origin}"\n#   Environment="OLLAMA_HOST=127.0.0.1:${ollamaPortValue}"\nsudo systemctl daemon-reload\nsudo systemctl restart ollama`,
    },
  }

  const stepContent: Record<string, React.ReactNode> = {
    path: (
      <StepShell
        icon={<Laptop size={18} />}
        eyebrow={`Step 1 of ${totalSteps}`}
        title="Choose your capacity path"
        subtitle="Decide how Chorus will reach the reviewer endpoint behind Ollama."
      >
        {isDeployedHost() ? (
          <Notice kind="warn" title="Hosted workspace detected">
            This app is running remotely, so it cannot reach <code>127.0.0.1</code> on your laptop. Use a tunnel to expose a public reviewer endpoint.
          </Notice>
        ) : (
          <Notice kind="info" title="Fastest path">
            If Chorus and Ollama are on the same machine, use the local path and test with <code>127.0.0.1</code>.
          </Notice>
        )}
        <div style={{ display: 'grid', gap: '0.7rem' }}>
          {!isDeployedHost() && (
            <PathCard
              selected={mode === 'local'}
              onClick={() => setMode('local')}
              icon={<Home size={16} />}
              title="Same machine or LAN"
              description="Use localhost or a LAN IP when the reviewer endpoint is reachable inside your local network."
            />
          )}
          <PathCard
            selected={mode === 'tunnel' || isDeployedHost()}
            onClick={() => setMode('tunnel')}
            icon={<Cloud size={16} />}
            title="Tunnel for hosted access"
            description="Expose the reviewer endpoint with ngrok or cloudflared when Chorus is hosted elsewhere."
            hint={isDeployedHost() ? 'Required for hosted workspaces.' : undefined}
          />
        </div>
      </StepShell>
    ),

    install: (
      <StepShell
        icon={<Download size={18} />}
        eyebrow={`Step 2 of ${totalSteps}`}
        title="Install Ollama"
        subtitle="Ollama powers the private review endpoint and exposes an OpenAI-compatible chat-completions API."
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
      </StepShell>
    ),

    model: (
      <StepShell
        icon={<Package size={18} />}
        eyebrow={`Step 3 of ${totalSteps}`}
        title="Choose a review model"
        subtitle="Pick a size that matches your machine and the depth of reviews you want to run."
      >
        <div style={{ display: 'grid', gap: '0.55rem' }}>
          {MODEL_CHOICES.map((choice) => {
            const active = choice.id === model
            return (
              <button
                key={choice.id}
                type="button"
                onClick={() => setModel(choice.id)}
                style={{
                  textAlign: 'left',
                  padding: '0.8rem 0.9rem',
                  borderRadius: 6,
                  border: active ? '1px solid rgba(180,200,255,0.45)' : '1px solid rgba(255,255,255,0.08)',
                  background: active ? 'rgba(180,200,255,0.08)' : 'rgba(255,255,255,0.025)',
                  color: 'rgba(255,255,255,0.9)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: 4 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{choice.label}</span>
                  <span style={{ fontFamily: 'var(--font-geist-mono), monospace', fontSize: 10, color: 'rgba(255,255,255,0.45)' }}>
                    {choice.size}
                  </span>
                </div>
                <div style={{ fontFamily: 'var(--font-geist-mono), monospace', fontSize: 11, color: 'rgba(255,255,255,0.6)', marginBottom: 4 }}>
                  {choice.id}
                </div>
                <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.52)', lineHeight: 1.45 }}>
                  {choice.description}
                </div>
              </button>
            )
          })}
        </div>
        <CodeBlock code={`ollama pull ${model}`} label="shell" />
      </StepShell>
    ),

    network: (
      <StepShell
        icon={<Network size={18} />}
        eyebrow={`Step 4 of ${totalSteps}`}
        title={mode === 'local' ? 'Allow local workspace access' : 'Allow the hosted workspace'}
        subtitle={
          mode === 'local'
            ? 'Only do this when Ollama runs on another machine on your LAN.'
            : `Whitelist ${origin} so the browser can reach the tunneled endpoint.`
        }
      >
        <label style={fieldLabelStyle}>Ollama port</label>
        <input
          value={ollamaPort}
          onChange={(e) => setOllamaPort(e.target.value.replace(/[^0-9]/g, '').slice(0, 5) || DEFAULT_OLLAMA_PORT)}
          placeholder="11434"
          inputMode="numeric"
          style={inputStyle}
        />
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.52)', lineHeight: 1.45, marginBottom: '0.75rem' }}>
          If Windows blocks <code>11434</code> with “forbidden by access permissions,” try <code>11500</code> and paste the full URL such as <code>http://127.0.0.1:11500</code> when testing.
        </div>
        <OsTabs
          value={os}
          onChange={setOs}
          commands={{
            macos: { code: (mode === 'local' ? networkCommandsLan : networkCommandsTunnel).macos.code, note: (mode === 'local' ? networkCommandsLan : networkCommandsTunnel).macos.note, label: 'macOS' },
            windows: { code: (mode === 'local' ? networkCommandsLan : networkCommandsTunnel).windows.code, note: (mode === 'local' ? networkCommandsLan : networkCommandsTunnel).windows.note, label: 'Windows' },
            linux: { code: (mode === 'local' ? networkCommandsLan : networkCommandsTunnel).linux.code, note: (mode === 'local' ? networkCommandsLan : networkCommandsTunnel).linux.note, label: 'Linux' },
          }}
        />
        {os === 'windows' && (
          <Notice kind="info" title="Windows port troubleshooting">
            If <code>ollama serve</code> says port <code>{ollamaPortValue}</code> is forbidden even when nothing is listening,
            Windows may have reserved that port. Run <code>netsh interface ipv4 show excludedportrange protocol=tcp</code>;
            if the port is inside an excluded range, switch to a free port like <code>11500</code>. If it is merely in use,
            <code>wslrelay.exe</code> may be holding it; the setup command clears both Windows Ollama and WSL before restarting.
          </Notice>
        )}
      </StepShell>
    ),

    tunnel: (
      <StepShell
        icon={<Globe2 size={18} />}
        eyebrow={`Step 5 of ${totalSteps}`}
        title="Expose a secure review endpoint"
        subtitle="Open a second terminal, leave Ollama running, and create a public https URL that Chorus can reach."
      >
        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
          {(['ngrok', 'cloudflared'] as TunnelProvider[]).map((provider) => {
            const active = provider === tunnelProvider
            return (
              <button
                key={provider}
                type="button"
                onClick={() => setTunnelProvider(provider)}
                style={{
                  padding: '0.4rem 0.85rem',
                  borderRadius: 4,
                  border: active ? '1px solid rgba(255,255,255,0.22)' : '1px solid rgba(255,255,255,0.08)',
                  background: active ? 'rgba(255,255,255,0.1)' : 'transparent',
                  color: active ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.55)',
                  fontSize: 12,
                }}
              >
                {provider}
              </button>
            )
          })}
        </div>
        <CodeBlock
          code={
            tunnelProvider === 'ngrok'
              ? `ngrok http ${ollamaPortValue} --host-header=localhost:${ollamaPortValue}`
              : `cloudflared tunnel --url ${localOllamaBase}`
          }
          label="shell"
        />
        <label style={fieldLabelStyle}>Tunnel URL</label>
        <input
          value={tunnelUrl}
          onChange={(e) => setTunnelUrl(e.target.value)}
          placeholder="https://abc-123.ngrok-free.app"
          autoComplete="off"
          spellCheck={false}
          style={inputStyle}
        />
      </StepShell>
    ),

    test: (
      <StepShell
        icon={<Plug size={18} />}
        eyebrow={`Step ${mode === 'tunnel' ? 6 : 5} of ${totalSteps}`}
        title="Test your capacity"
        subtitle="Verify the endpoint responds before you open the review workspace."
      >
        {mode === 'local' ? (
          <>
            <label style={fieldLabelStyle}>Ollama address</label>
            <input
              value={lanIp}
              onChange={(e) => setLanIp(e.target.value)}
              placeholder="127.0.0.1 or 192.168.1.10"
              autoComplete="off"
              spellCheck={false}
              style={inputStyle}
            />
            <ConnectionTest mode="lan" target={lanIp} model={model} ollamaPort={ollamaPortValue} onResult={setTestOk} />
          </>
        ) : (
          <>
            <label style={fieldLabelStyle}>Tunnel URL</label>
            <input
              value={tunnelUrl}
              onChange={(e) => setTunnelUrl(e.target.value)}
              placeholder="https://abc-123.ngrok-free.app"
              autoComplete="off"
              spellCheck={false}
              style={inputStyle}
            />
            <ConnectionTest mode="tunnel" target={tunnelUrl} model={model} ollamaPort={ollamaPortValue} onResult={setTestOk} />
          </>
        )}
      </StepShell>
    ),

    connect: (
      <StepShell
        icon={<Radio size={18} />}
        eyebrow={`Step ${totalSteps} of ${totalSteps}`}
        title="Connect your workspace"
        subtitle="Save the control plane URL, keep the generated workspace id, and enter the token for this browser session."
      >
        {!orchestratorBase.trim() && !orchestratorBaseFromEnv && (
          <Notice kind="info" title="No control plane configured">
            Deploy the backend to Railway or your private environment, then paste the <code>https://*.up.railway.app</code> URL below.
          </Notice>
        )}

        <label style={fieldLabelStyle}>Control plane URL</label>
        <input
          value={orchestratorBase}
          onChange={(e) => setOrchestratorBase(e.target.value)}
          onBlur={onConnectOrchestrator}
          placeholder="https://your-app.up.railway.app"
          autoComplete="off"
          spellCheck={false}
          style={inputStyle}
        />

        <div style={{ display: 'grid', gap: '0.6rem', gridTemplateColumns: '1.15fr 1fr' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem' }}>
              <label style={fieldLabelStyle}>Workspace id</label>
              <button
                type="button"
                onClick={onRegenerateWorkspaceId}
                style={{
                  border: 'none',
                  background: 'transparent',
                  color: 'rgba(180, 210, 255, 0.9)',
                  cursor: 'pointer',
                  fontSize: 11,
                  padding: 0,
                }}
              >
                Generate new id
              </button>
            </div>
            <input
              value={workspaceId}
              onChange={(e) => setWorkspaceId(e.target.value)}
              placeholder="workspace-xxxxxxx"
              autoComplete="off"
              spellCheck={false}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={fieldLabelStyle}>Workspace token</label>
            <input
              value={workspaceToken}
              onChange={(e) => setWorkspaceToken(e.target.value)}
              placeholder="enter token for this session"
              type="password"
              autoComplete="off"
              spellCheck={false}
              style={inputStyle}
            />
          </div>
        </div>

        <p style={{ fontSize: 11.5, color: 'rgba(255,255,255,0.42)', lineHeight: 1.5 }}>
          The workspace id stays in local storage so the same browser keeps its routing identity. The workspace token stays in session storage, so users need to enter it again when they come back in a new session.
        </p>

        <button
          type="button"
          onClick={onFinishSetup}
          disabled={probePhase === 'probing' || !orchestratorBase.trim()}
          style={{
            alignSelf: 'flex-start',
            padding: '0.6rem 1.15rem',
            borderRadius: 4,
            border: 'none',
            background:
              probePhase === 'probing' || !orchestratorBase.trim()
                ? 'rgba(255,255,255,0.35)'
                : 'rgba(255,255,255,0.92)',
            color: '#050508',
            fontWeight: 600,
            fontSize: 13.5,
            cursor:
              probePhase === 'probing'
                ? 'wait'
                : !orchestratorBase.trim()
                  ? 'not-allowed'
                  : 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.45rem',
          }}
        >
          {probePhase === 'probing' ? 'Testing connection…' : 'Test & open workspace'}
          <ArrowRight size={14} />
        </button>

        {probePhase === 'error' && (
          <Notice kind="error" title="Control plane unreachable">
            {probeMessage}
          </Notice>
        )}

        {probePhase === 'ok' && (
          <Notice kind="success" title="Verified">
            Opening the review workspace…
          </Notice>
        )}
      </StepShell>
    ),
  }

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

        <div style={{ marginBottom: '2rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.4rem' }}>
            <span style={{ fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.4)', fontFamily: 'var(--font-geist-mono), monospace' }}>
              Step {clampedIndex + 1} of {totalSteps}
            </span>
            <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.52)' }}>
              {steps[clampedIndex].label}
            </span>
          </div>
          <div role="progressbar" aria-valuenow={clampedIndex + 1} aria-valuemin={1} aria-valuemax={totalSteps} style={{ height: 3, borderRadius: 3, background: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
            <motion.div
              animate={{ width: `${progress}%` }}
              transition={{ duration: 0.35, ease: 'easeOut' }}
              style={{
                height: '100%',
                background: 'linear-gradient(90deg, rgba(180,200,255,0.95), rgba(200,180,255,0.75))',
                boxShadow: '0 0 14px rgba(180,200,255,0.35)',
              }}
            />
          </div>
        </div>

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

        <div
          style={{
            position: 'sticky',
            bottom: 0,
            marginTop: '2rem',
            paddingTop: '1rem',
            display: 'flex',
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

          {clampedIndex < totalSteps - 1 && (
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
          )}
        </div>
      </div>
    </div>
  )
}

const fieldLabelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 11.5,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: 'rgba(255,255,255,0.5)',
  marginBottom: '0.3rem',
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '0.6rem 0.75rem',
  borderRadius: 4,
  border: '1px solid rgba(255,255,255,0.14)',
  background: 'rgba(0,0,0,0.35)',
  color: '#fff',
  fontSize: 13.5,
  fontFamily: 'var(--font-geist-mono), monospace',
}

function Notice({
  kind,
  title,
  children,
}: {
  kind: 'info' | 'warn' | 'success' | 'error'
  title: string
  children: React.ReactNode
}) {
  const palette = {
    info: {
      border: '1px solid rgba(180,200,255,0.22)',
      background: 'rgba(30,40,70,0.3)',
      color: 'rgba(210,225,255,0.9)',
    },
    warn: {
      border: '1px solid rgba(255,200,120,0.3)',
      background: 'rgba(60,45,20,0.35)',
      color: 'rgba(255,230,180,0.88)',
    },
    success: {
      border: '1px solid rgba(143,212,168,0.35)',
      background: 'rgba(30,60,40,0.32)',
      color: 'rgba(200,240,210,0.92)',
    },
    error: {
      border: '1px solid rgba(246,168,154,0.35)',
      background: 'rgba(60,30,30,0.3)',
      color: '#f6a89a',
    },
  }[kind]

  return (
    <div
      style={{
        padding: '0.75rem 0.9rem',
        borderRadius: 5,
        border: palette.border,
        background: palette.background,
        color: palette.color,
        fontSize: 12.5,
        lineHeight: 1.55,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
      <div>{children}</div>
    </div>
  )
}

function PathCard({
  selected,
  onClick,
  icon,
  title,
  description,
  hint,
}: {
  selected: boolean
  onClick: () => void
  icon: React.ReactNode
  title: string
  description: string
  hint?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      style={{
        textAlign: 'left',
        padding: '1rem 1.1rem',
        borderRadius: 7,
        border: selected ? '1px solid rgba(180,200,255,0.5)' : '1px solid rgba(255,255,255,0.08)',
        background: selected ? 'rgba(180,200,255,0.07)' : 'rgba(255,255,255,0.025)',
        color: 'rgba(255,255,255,0.92)',
        cursor: 'pointer',
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
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4, color: 'rgba(255,255,255,0.96)' }}>
          {title}
        </div>
        <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'rgba(255,255,255,0.6)' }}>
          {description}
        </div>
        {hint && (
          <div style={{ marginTop: 6, fontSize: 11, color: 'rgba(160,200,255,0.8)', fontFamily: 'var(--font-geist-mono), monospace' }}>
            {hint}
          </div>
        )}
      </div>
    </button>
  )
}
