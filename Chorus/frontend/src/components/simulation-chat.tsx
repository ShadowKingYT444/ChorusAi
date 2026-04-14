'use client'

import { useRef, useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { motion, AnimatePresence, useMotionValue, useSpring } from 'framer-motion'
import { Textarea } from '@/components/ui/textarea'
import { Typewriter } from '@/components/ui/typewriter'
import { cn } from '@/lib/utils'
import { ArrowUpIcon, Users, RefreshCw, Coins, Server } from 'lucide-react'
import { useReducedMotion } from '@/hooks/use-reduced-motion'
import { PROMPT_CHIPS } from '@/lib/mock-data'
import {
  createBroadcastPlan,
  getOrCreatePeerId,
  getSavedOllamaIp,
  invokeBroadcastCompletions,
  isOrchestratorConfigured,
  openSignalingSocket,
  saveOllamaIp,
} from '@/lib/api/orchestrator'
import { writeSimulationSession } from '@/lib/runtime/session'

// ─── Auto-resize ──────────────────────────────────────────────────────────────

function useAutoResize({ minHeight, maxHeight }: { minHeight: number; maxHeight?: number }) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const adjust = useCallback((reset?: boolean) => {
    const el = ref.current
    if (!el) return
    el.style.height = `${minHeight}px`
    if (!reset) el.style.height = `${Math.max(minHeight, Math.min(el.scrollHeight, maxHeight ?? Infinity))}px`
  }, [minHeight, maxHeight])
  useEffect(() => { if (ref.current) ref.current.style.height = `${minHeight}px` }, [minHeight])
  useEffect(() => {
    const h = () => adjust()
    window.addEventListener('resize', h)
    return () => window.removeEventListener('resize', h)
  }, [adjust])
  return { ref, adjust }
}

// ─── Magnetic button ──────────────────────────────────────────────────────────

function MagneticButton({ children, onClick, disabled, style, className }: {
  children: React.ReactNode
  onClick?: () => void
  disabled?: boolean
  style?: React.CSSProperties
  className?: string
}) {
  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const springX = useSpring(x, { stiffness: 180, damping: 18 })
  const springY = useSpring(y, { stiffness: 180, damping: 18 })

  function handleMove(e: React.MouseEvent<HTMLButtonElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const cx = rect.left + rect.width / 2
    const cy = rect.top + rect.height / 2
    x.set((e.clientX - cx) * 0.35)
    y.set((e.clientY - cy) * 0.35)
  }

  function handleLeave() {
    x.set(0)
    y.set(0)
  }

  return (
    <motion.button
      style={{ x: springX, y: springY, ...style }}
      className={className}
      onClick={onClick}
      disabled={disabled}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      whileTap={{ scale: 0.94 }}
      transition={{ type: 'spring', stiffness: 400, damping: 20 }}
    >
      {children}
    </motion.button>
  )
}

// ─── Slider ───────────────────────────────────────────────────────────────────

function SimSlider({
  label, icon, min, max, step, value, onChange, format,
}: {
  label: string; icon: React.ReactNode; min: number; max: number
  step: number; value: number; onChange: (v: number) => void; format: (v: number) => string
}) {
  const pct = ((value - min) / (max - min)) * 100

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-white/80">{icon}</span>
          <span className="font-sans text-[10px] text-white/80 uppercase tracking-widest">{label}</span>
        </div>
        <motion.span
          key={value}
          initial={{ opacity: 0.5, y: -3 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 500, damping: 30 }}
          className="font-mono text-[13px] text-white/95 tabular-nums"
        >
          {format(value)}
        </motion.span>
      </div>

      {/* Custom track */}
      <div className="relative h-px rounded-full" style={{ background: 'rgba(255,255,255,0.20)' }}>
        <motion.div
          className="absolute left-0 top-0 h-full rounded-full"
          style={{ width: `${pct}%`, background: 'rgba(255,255,255,0.75)' }}
        />
        {/* Thumb glow */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full"
          style={{
            left: `calc(${pct}% - 5px)`,
            background: '#ffffff',
            boxShadow: '0 0 8px 2px rgba(255,255,255,0.45)',
          }}
        />
        <input
          type="range" min={min} max={max} step={step} value={value}
          onChange={e => onChange(Number(e.target.value))}
          className="absolute inset-0 w-full opacity-0 cursor-pointer"
          style={{ height: '16px', top: '-7px' }}
        />
      </div>
    </div>
  )
}

// ─── Launching view ───────────────────────────────────────────────────────────

function LaunchingView({ agentCount }: { agentCount: number }) {
  return (
    <div className="w-full flex flex-col items-center justify-center py-10 gap-5">
      <div className="flex items-center gap-2.5">
        {[0, 1, 2, 3, 4].map(i => (
          <motion.div
            key={i}
            className="rounded-full"
            style={{ background: '#ffffff', height: '4px' }}
            animate={{
              width: ['4px', '18px', '4px'],
              opacity: [0.2, 1, 0.2],
            }}
            transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.12, ease: 'easeInOut' }}
          />
        ))}
      </div>
      <p className="font-mono text-[10px] text-white/40 tracking-[0.20em] uppercase">
        Spinning up {agentCount.toLocaleString()} agents
      </p>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function SimulationChat() {
  const router = useRouter()
  const reducedMotion = useReducedMotion()
  const [value, setValue] = useState('')
  const [agentCount, setAgentCount] = useState(50)
  const [rounds, setRounds] = useState(3)
  const [bounty, setBounty] = useState(0.10)
  const [launching, setLaunching] = useState(false)
  const [launchError, setLaunchError] = useState<string | null>(null)
  const [ollamaIp, setOllamaIp] = useState('')
  const { ref: textareaRef, adjust } = useAutoResize({ minHeight: 100, maxHeight: 200 })

  useEffect(() => { setOllamaIp(getSavedOllamaIp()) }, [])

  const estimatedCost = (agentCount * rounds * 0.0012).toFixed(2)
  const gpt4oCost = (agentCount * rounds * 0.0045).toFixed(2)
  const claudeCost = (agentCount * rounds * 0.0038).toFixed(2)
  const o1Cost = (agentCount * rounds * 0.015).toFixed(2)
  const hasValue = value.trim().length > 0

  async function launch() {
    if (!hasValue || launching) return
    setLaunchError(null)
    setLaunching(true)
    const prompt = value.trim()
    try {
      if (!isOrchestratorConfigured()) {
        writeSimulationSession({
          prompt,
          agentCount,
          rounds,
          bounty,
          mode: 'mock',
          createdAt: new Date().toISOString(),
        })
        setTimeout(() => router.push('/app'), 350)
        return
      }

      const plan = await createBroadcastPlan({
        prompt,
        timeout_ms: Math.max(2000, rounds * 2500),
      })

      saveOllamaIp(ollamaIp)
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => {
          ws?.close()
          reject(new Error('Broadcast handshake timed out'))
        }, 10_000)
        let settled = false
        const ws = openSignalingSocket(getOrCreatePeerId(), 'web-prompter', {
          onEvent: (event) => {
            console.log('[SimulationChat] WS Event:', event.type)
            if (event.type === 'registered') {
              ws.send(
                JSON.stringify({
                  type: 'broadcast_job',
                  job_id: plan.job_id,
                  prompt,
                  timeout_ms: plan.timeout_ms,
                  target_peer_ids: plan.target_peer_ids,
                }),
              )
              return
            }
            if (event.type === 'broadcast_started') {
              if (settled) return
              settled = true
              clearTimeout(timeout)
              ws.close()
              if (!event.ok) {
                reject(new Error(event.error ?? 'Broadcast failed'))
                return
              }
              void invokeBroadcastCompletions({
                job_id: plan.job_id,
                prompt,
                timeout_ms: plan.timeout_ms,
                target_peer_ids: plan.target_peer_ids,
              })
                .then(() => resolve())
                .catch(() => {
                  /* no HTTP bases / network — still continue to app */
                  resolve()
                })
            }
            if (event.type === 'error' && !settled) {
              settled = true
              clearTimeout(timeout)
              ws.close()
              reject(new Error(event.detail ?? event.error))
            }
          },
          onError: (err) => {
            if (settled) return
            settled = true
            clearTimeout(timeout)
            console.error('[SimulationChat] WS Error:', err)
            reject(new Error('Failed to connect signaling socket to ' + (process.env.NEXT_PUBLIC_ORCHESTRATOR_BASE_URL || 'backend')))
          },
        }, ollamaIp || undefined)
      })

      writeSimulationSession({
        prompt,
        agentCount: Math.max(1, plan.expected_peers),
        rounds,
        bounty,
        jobId: plan.job_id,
        mode: 'backend',
        createdAt: new Date().toISOString(),
      })
      router.push(`/app?job_id=${encodeURIComponent(plan.job_id)}`)
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'Failed to launch simulation')
      setLaunching(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); launch() }
  }

  return (
    <div className="w-full grid grid-cols-1 md:grid-cols-[1fr_auto] gap-6 items-start">

      {/* LEFT: Prompt + chips */}
      <div className="flex flex-col gap-4">
        <AnimatePresence mode="wait">
          {launching ? (
            <motion.div
              key="launching"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="rounded-sm overflow-hidden"
              style={{ background: 'rgba(255,255,255,0.03)', border: '1.5px solid rgba(200,220,255,0.62)' }}
            >
              <LaunchingView agentCount={agentCount} />
            </motion.div>
          ) : (
            <motion.div
              key="input"
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ type: 'spring', stiffness: 300, damping: 30 }}
              className="relative overflow-hidden rounded-sm"
              style={{
                background: 'rgba(255,255,255,0.03)',
                border: '1.5px solid rgba(200,220,255,0.72)',
                boxShadow: 'inset 0 1px 0 rgba(200,220,255,0.14), 0 0 0 1px rgba(200,220,255,0.06)',
              }}
            >
              <div className="relative w-full min-h-[100px] flex">
                {!hasValue && (
                  <div className="absolute inset-0 pointer-events-none px-4 py-4 font-sans text-sm text-white/45">
                    <Typewriter
                      words={[
                        'Describe your simulation...',
                        '50 traders in a volatile crypto market...',
                        'Simulate a city council negotiation...',
                        '100 scientists racing to solve a problem...',
                      ]}
                      speed={46}
                      delayBetweenWords={3000}
                    />
                  </div>
                )}
                <Textarea
                  ref={textareaRef}
                  value={value}
                  onChange={e => { setValue(e.target.value); adjust() }}
                  onKeyDown={handleKeyDown}
                  placeholder=""
                  className={cn(
                    'w-full px-4 py-4 resize-none bg-transparent border-none',
                    'font-sans text-[14px] text-white/85 leading-relaxed',
                    'focus:outline-none focus-visible:ring-0 focus-visible:ring-offset-0',
                    'min-h-[100px]'
                  )}
                  style={{ overflow: 'hidden' }}
                />
              </div>

              <div
                className="flex items-center justify-between px-3 py-2.5"
                style={{ borderTop: '1.5px solid rgba(200,220,255,0.38)' }}
              >
                <span className="font-mono text-[9px] text-white/70 tracking-[0.18em] uppercase">
                  Simulation Engine
                </span>
                <div className="flex items-center gap-2.5">
                  <motion.span className="font-mono text-[10px] text-white/80" animate={{ opacity: hasValue ? 1 : 0 }}>
                    ↵ launch
                  </motion.span>
                  <MagneticButton
                    onClick={launch}
                    disabled={!hasValue}
                    className={cn(
                      'w-7 h-7 rounded-sm flex items-center justify-center transition-all duration-150',
                      hasValue ? 'text-black' : 'text-white/80 border border-white/20'
                    )}
                    style={hasValue ? { background: 'rgba(255,255,255,0.92)' } : {}}
                  >
                    <ArrowUpIcon className="w-3.5 h-3.5" />
                  </MagneticButton>
                </div>
              </div>
              {launchError && (
                <div
                  className="px-3 py-2 font-mono text-[10px] text-red-200/90"
                  style={{ borderTop: '1px solid rgba(255,255,255,0.12)', background: 'rgba(120,0,0,0.18)' }}
                >
                  {launchError}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Prompt chips */}
        <motion.div
          animate={{ opacity: launching ? 0 : 1 }}
          transition={{ duration: 0.2 }}
          className="flex flex-wrap gap-2"
        >
          {PROMPT_CHIPS.map((chip, i) => (
            <motion.button
              key={chip}
              type="button"
              onClick={() => { setValue(chip); adjust() }}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ type: 'spring', stiffness: 280, damping: 24, delay: 0.28 + i * 0.06 }}
              whileHover={reducedMotion ? undefined : { scale: 1.02 }}
              whileTap={{ scale: 0.97 }}
              className="px-3 py-1.5 rounded-sm font-sans text-[11px] text-white/85
                hover:text-white/100"
              style={{
                background: 'rgba(255,255,255,0.025)',
                border: '1.5px solid rgba(200,220,255,0.30)',
                transition: 'color 150ms ease-out, border-color 150ms ease-out',
              }}
            >
              {chip}
            </motion.button>
          ))}
        </motion.div>
      </div>

      {/* RIGHT: Sliders panel */}
      <motion.div
        initial={{ opacity: 0, x: 16 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ type: 'spring', stiffness: 240, damping: 28, delay: 0.12 }}
        className="flex flex-col gap-6 w-full md:w-[220px] rounded-sm p-5"
        style={{
          background: 'rgba(255,255,255,0.02)',
          border: '1.5px solid rgba(200,220,255,0.72)',
          boxShadow: 'inset 0 1px 0 rgba(200,220,255,0.14), 0 0 0 1px rgba(200,220,255,0.06)',
        }}
      >
        <SimSlider label="Agents" icon={<Users className="w-3 h-3" />}
          min={10} max={1000} step={10} value={agentCount}
          onChange={setAgentCount} format={v => v.toLocaleString()} />
        <SimSlider label="Rounds" icon={<RefreshCw className="w-3 h-3" />}
          min={1} max={10} step={1} value={rounds}
          onChange={setRounds} format={v => String(v)} />
        <SimSlider label="Bounty" icon={<Coins className="w-3 h-3" />}
          min={0.01} max={1.00} step={0.01} value={bounty}
          onChange={setBounty} format={v => `$${v.toFixed(2)}`} />

        {/* Ollama IP */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-white/80"><Server className="w-3 h-3" /></span>
            <span className="font-sans text-[10px] text-white/80 uppercase tracking-widest">Your Ollama IP</span>
          </div>
          <input
            type="text"
            value={ollamaIp}
            onChange={e => { setOllamaIp(e.target.value); saveOllamaIp(e.target.value) }}
            placeholder="e.g. 192.168.1.42"
            className="w-full px-3 py-2 rounded-sm bg-white/[0.04] border border-white/15
              font-mono text-[12px] text-white/85 placeholder:text-white/25
              focus:outline-none focus:border-white/60 transition-colors"
          />
          <span className="font-sans text-[9px] text-white/80 leading-relaxed">
            Your LAN IP where Ollama is running (:11434)
          </span>
        </div>

        {/* Cost estimate */}
        <div className="pt-4 flex flex-col gap-1" style={{ borderTop: '1.5px solid rgba(200,220,255,0.38)' }}>
          <span className="font-sans text-[9px] text-white/80 uppercase tracking-[0.16em]">Est. cost</span>
          <motion.span
            key={estimatedCost}
            initial={{ opacity: 0.5, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: 'spring', stiffness: 500, damping: 30 }}
            className="font-mono text-[20px] text-white/80 tabular-nums"
            style={{ letterSpacing: '-0.02em' }}
          >
            ~${estimatedCost}
          </motion.span>
          <div className="flex flex-col gap-1.5 opacity-90">
            <span className="font-sans text-[9px] text-white/75 leading-relaxed flex justify-between">
              <span>via GPT-4o API</span>
              <span className="font-mono">${gpt4oCost}</span>
            </span>
            <span className="font-sans text-[9px] text-white/75 leading-relaxed flex justify-between">
              <span>via Claude 3.5 Sonnet</span>
              <span className="font-mono">${claudeCost}</span>
            </span>
            <span className="font-sans text-[9px] text-white/80 leading-relaxed flex justify-between font-medium">
              <span>via OpenAI o1-preview</span>
              <span className="font-mono text-red-300/60">${o1Cost}</span>
            </span>
          </div>
        </div>
        {isOrchestratorConfigured() && (
          <p className="font-sans text-[10px] text-white/80 leading-relaxed">
            Live mode uses the signaling server&apos;s current peer registry at launch time.
          </p>
        )}

        {/* Launch */}
        <motion.div
          animate={{ opacity: launching ? 0.45 : 1 }}
          transition={{ duration: 0.15 }}
          style={{ pointerEvents: launching ? 'none' : 'auto' }}
        >
          <MagneticButton
            onClick={launch}
            disabled={!hasValue || launching}
            className="w-full py-2.5 rounded-sm font-sans text-[12px] tracking-[0.04em]"
            style={{
              background: hasValue ? 'rgba(255,255,255,0.92)' : 'rgba(255,255,255,0.05)',
              color: hasValue ? '#000000' : 'rgba(255,255,255,0.55)',
              border: hasValue ? 'none' : '1px solid rgba(200,220,255,0.62)',
              cursor: hasValue && !launching ? 'pointer' : 'default',
              transition: 'background-color 150ms ease-out, color 150ms ease-out, border-color 150ms ease-out',
            }}
          >
            {launching ? 'Launching…' : 'Launch Simulation'}
          </MagneticButton>
        </motion.div>
      </motion.div>
    </div>
  )
}
