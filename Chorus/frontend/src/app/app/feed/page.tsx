'use client'

import { TopBar } from '@/components/top-bar'
import { RightPanel } from '@/components/right-panel'
import { useSimulation } from '@/hooks/use-simulation'
import { Suspense, useEffect, useRef } from 'react'
import { Activity, Clock, Database, Zap, Server } from 'lucide-react'
import { useSharedJobRuntime } from '@/lib/runtime/job-runtime-provider'
import { useOrchestratorMetrics } from '@/lib/runtime/use-metrics'
import { getReviewMode, getReviewTemplate } from '@/lib/review-config'

function WaveformSparkline({ color, seed = 0, speed = 0.6 }: {
  color: string
  seed?: number
  speed?: number
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const offsetRef = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const context = ctx

    let rafId: number
    let dpr = window.devicePixelRatio || 1

    function resize() {
      if (!canvas) return
      dpr = window.devicePixelRatio || 1
      context.setTransform(1, 0, 0, 1, 0, 0)
      const w = canvas.offsetWidth
      const h = canvas.offsetHeight
      canvas.width = w * dpr
      canvas.height = h * dpr
      context.scale(dpr, dpr)
    }

    function draw() {
      if (!canvas) return
      const w = canvas.width / dpr
      const h = canvas.height / dpr

      context.clearRect(0, 0, w, h)
      context.beginPath()
      context.strokeStyle = color
      context.lineWidth = 1.5
      context.lineJoin = 'round'
      context.lineCap = 'round'

      const off = offsetRef.current
      for (let x = 0; x <= w; x++) {
        const t = (x + off) * 0.018
        const y = h / 2
          + Math.sin(t * 2.1 + seed) * h * 0.18
          + Math.sin(t * 4.7 + seed * 1.3) * h * 0.10
          + Math.sin(t * 1.3 + seed * 2.1) * h * 0.12
          + Math.sin(t * 8.9 + seed * 0.7) * h * 0.05

        if (x === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      }

      context.stroke()
      offsetRef.current += speed
      rafId = requestAnimationFrame(draw)
    }

    resize()
    rafId = requestAnimationFrame(draw)

    const ro = new ResizeObserver(() => { resize() })
    ro.observe(canvas)

    return () => {
      cancelAnimationFrame(rafId)
      ro.disconnect()
    }
  }, [color, seed, speed])

  return (
    <canvas
      ref={canvasRef}
      style={{ width: '100%', height: '100%', display: 'block' }}
    />
  )
}

function WaveformCard({ label, color, seed, speed }: {
  label: string
  color: string
  seed: number
  speed?: number
}) {
  return (
    <div
      style={{
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 12,
        padding: '14px 16px 12px',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.05)',
      }}
    >
      <span
        style={{
          fontFamily: 'var(--font-geist-mono)',
          fontSize: 9,
          letterSpacing: '0.18em',
          color: 'rgba(255,255,255,0.35)',
          textTransform: 'uppercase',
          display: 'block',
          marginBottom: 10,
        }}
      >
        {label}
      </span>
      <div style={{ height: 52, width: '100%' }}>
        <WaveformSparkline color={color} seed={seed} speed={speed} />
      </div>
    </div>
  )
}

function ClusterActivity() {
  return (
    <div>
      <div
        style={{
          fontFamily: 'var(--font-geist-mono)',
          fontSize: 10,
          letterSpacing: '0.20em',
          color: 'rgba(255,255,255,0.40)',
          textTransform: 'uppercase',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 12,
        }}
      >
        <Activity style={{ width: 12, height: 12 }} />
        Review Activity
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <WaveformCard label="Support Signals" color="rgba(255,255,255,0.65)" seed={0} speed={0.55} />
        <WaveformCard label="Counterpoints" color="rgba(100,160,255,0.85)" seed={3.7} speed={0.7} />
        <WaveformCard label="Risk Sweep" color="rgba(255,255,255,0.40)" seed={7.2} speed={0.45} />
      </div>
    </div>
  )
}

function FeedPageContent() {
  const job = useSimulation()
  const runtime = useSharedJobRuntime()
  const metrics = useOrchestratorMetrics(3000)
  const osName =
    typeof navigator === 'undefined'
      ? 'LOCAL HOST'
      : navigator.userAgent.includes('Mac')
      ? 'LOCAL (macOS)'
      : navigator.userAgent.includes('Win')
      ? 'LOCAL (Windows)'
      : navigator.userAgent.includes('Linux')
      ? 'LOCAL (Linux)'
      : 'LOCAL HOST'

  const connectedPeers = runtime.connectedPeers
  const hasSession = Boolean(runtime.session ?? job)
  const reviewMode = runtime.session?.reviewMode ? getReviewMode(runtime.session.reviewMode) : null
  const reviewTemplate = runtime.session?.reviewTemplate ? getReviewTemplate(runtime.session.reviewTemplate) : null
  const shadowCredits = hasSession ? (runtime.session ?? job)!.agentCount * (runtime.session ?? job)!.rounds : 0

  return (
    <div className="flex flex-col h-[100dvh] bg-black overflow-hidden font-sans text-foreground">
      <TopBar />

      <div className="flex-1 flex flex-col md:flex-row overflow-hidden w-full relative">
        <div className="w-full md:w-[480px] shrink-0 border-r border-white/5 flex flex-col bg-black overflow-y-auto">
          <div className="p-8 flex flex-col gap-8 h-full">
            <div>
              <h1 className="font-mono text-[10px] text-white/40 uppercase tracking-[0.2em] mb-4 flex items-center gap-2">
                <Activity className="w-3 h-3" />
                Review Operations
              </h1>

              <div className="bg-white/[0.02] border border-white/10 rounded-sm p-5 space-y-4 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-1 h-full bg-white/50" />
                <div className="font-mono text-[10px] text-white/40 uppercase tracking-widest border-b border-white/10 pb-3 mb-3">
                  Review Brief
                </div>
                {(runtime.session ?? job) ? (
                  <>
                    <p className="font-sans text-[15px] text-white/90 leading-relaxed font-light">
                      &quot;{(runtime.session ?? job)!.prompt}&quot;
                    </p>
                    {(reviewTemplate || reviewMode) && (
                      <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/35">
                        {[reviewTemplate?.label, reviewMode?.label].filter(Boolean).join(' · ')}
                      </div>
                    )}
                  </>
                ) : (
                  <p className="font-sans text-[14px] text-white/30 italic">
                    No active review found. Standing by...
                  </p>
                )}
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                <div className="font-mono text-[9px] text-white/30 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <Database className="w-3 h-3" />
                  Planned Reviewers
                </div>
                <div className="font-mono text-2xl text-white/85">
                  {hasSession
                    ? (runtime.session ?? job)!.agentCount.toLocaleString()
                    : connectedPeers.length > 0
                    ? connectedPeers.length.toLocaleString()
                    : <span className="text-white/20 text-sm tracking-widest">STANDBY</span>}
                </div>
              </div>
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                <div className="font-mono text-[9px] text-white/30 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <Clock className="w-3 h-3" />
                  Review Passes
                </div>
                <div className="font-mono text-2xl text-white/85">
                  {hasSession
                    ? (runtime.session ?? job)!.rounds
                    : <span className="text-white/20 text-sm tracking-widest">STANDBY</span>}
                </div>
              </div>
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                <div className="font-mono text-[9px] text-white/30 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                  <Zap className="w-3 h-3" />
                  Shadow Credits
                </div>
                <div className="font-mono text-2xl text-white/85">
                  {hasSession
                    ? shadowCredits.toLocaleString()
                    : <span className="text-white/20 text-sm tracking-widest">STANDBY</span>}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                <div className="font-mono text-[9px] text-white/30 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                  <Zap className="w-3 h-3" style={{ color: 'var(--color-warn)' }} />
                  Review Status
                </div>
                <div className="font-mono text-[11px]">
                  <div>
                    <span className="text-white/40">State: </span>
                    <span style={{ color: 'var(--color-secure)' }}>
                      {hasSession ? runtime.status.toUpperCase() : 'IDLE'}
                    </span>
                  </div>
                  {runtime.session?.workspaceId && (
                    <div className="mt-1.5">
                      <span className="text-white/40">Workspace: </span>
                      <span className="text-white/80">{runtime.session.workspaceId}</span>
                    </div>
                  )}
                </div>
              </div>
              <div className="bg-white/[0.02] border border-white/5 p-4 rounded-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
                <div className="font-mono text-[9px] text-white/30 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                  <Server className="w-3 h-3" style={{ color: 'var(--color-electric)' }} />
                  Reviewer Capacity
                </div>
                <div className="font-mono text-[10px] flex flex-col gap-1.5">
                  <div>
                    <span className="text-white/40">Console: </span>
                    <span style={{ color: 'var(--color-electric)' }}>{osName}</span>
                  </div>
                  <div>
                    <span className="text-white/40">Endpoints ready: </span>
                    <span className="text-white/85">{connectedPeers.length}</span>
                  </div>
                  {connectedPeers.length > 0 ? (
                    <ul className="list-none m-0 mt-1 p-0 max-h-[160px] overflow-auto pr-1 space-y-1.5">
                      {connectedPeers.map((peer) => (
                        <li key={peer.peer_id} className="text-white/75 break-all leading-tight flex items-center gap-1.5">
                          {peer.verified ? (
                            <span
                              title={`Ed25519 verified · ${peer.pubkey?.slice(0, 12)}…`}
                              className="inline-block"
                              style={{ color: 'var(--color-secure, #5ee29d)' }}
                            >
                              ✓
                            </span>
                          ) : (
                            <span
                              title="unverified peer (no signature)"
                              className="text-white/25"
                            >
                              ·
                            </span>
                          )}
                          <span className="font-mono">reviewer:{peer.peer_id.slice(0, 10)}…</span>
                          {peer.address ? (
                            <span className="text-white/50"> · {peer.address}</span>
                          ) : null}
                          <span className="text-white/30"> · {peer.model}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="text-white/35">
                      Waiting for reviewer endpoints…
                    </span>
                  )}
                </div>
              </div>
            </div>

            {metrics.fresh && (
              <div className="grid grid-cols-4 gap-2">
                {[
                  { label: 'READY', value: metrics.activePeers },
                  { label: 'IN-FLIGHT', value: metrics.jobsInFlight },
                  { label: 'AVG RT (ms)', value: Math.round(metrics.avgRoundLatencyMs) },
                  { label: 'PRUNE %', value: `${Math.round(metrics.pruneRate * 100)}` },
                ].map((t) => (
                  <div
                    key={t.label}
                    className="bg-white/[0.02] border border-white/5 p-3 rounded-lg"
                  >
                    <div className="font-mono text-[8px] text-white/35 uppercase tracking-widest mb-1">
                      {t.label}
                    </div>
                    <div className="font-mono text-base text-white/85">{t.value}</div>
                  </div>
                ))}
              </div>
            )}

            <ClusterActivity />

            <div className="mt-auto pt-6 border-t border-white/5 font-mono text-[10px] text-white/20 tracking-wider flex justify-between">
               <span>SYS.REVIEW.v2.4.1</span>
               <span>STATUS: ACTIVE TRACE</span>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-hidden h-full bg-[#030303]">
           <RightPanel
             messages={runtime.messages.length > 0 ? runtime.messages : undefined}
             totalSlots={runtime.session?.agentCount ?? job?.agentCount ?? connectedPeers.length}
             live={Boolean(runtime.session)}
             finalAnswer={runtime.finalAnswer}
             citations={runtime.citations}
             settlement={runtime.settlement}
           />
        </div>
      </div>
    </div>
  )
}

export default function FeedPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-[100dvh] items-center justify-center bg-black font-mono text-[10px] text-white/30 tracking-widest">
          LOADING REVIEW TRACE…
        </div>
      }
    >
      <FeedPageContent />
    </Suspense>
  )
}
