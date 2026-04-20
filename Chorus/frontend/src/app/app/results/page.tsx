'use client'
import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { motion, useInView } from 'framer-motion'
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Cell, Tooltip } from 'recharts'
import { TopBar } from '@/components/top-bar'
import { BeamsBackground } from '@/components/ui/beams-background'
import { type Cluster, type AgentMessage } from '@/lib/mock-data'
import { useSharedJobRuntime } from '@/lib/runtime/job-runtime-provider'
import { buildClustersFromMessages, buildCostChartData, buildResults } from '@/lib/runtime/adapter'
import { buildAgentContributions } from '@/lib/runtime/agent-contributions'
import { AgentContributions } from '@/components/results/agent-contributions'
import Link from 'next/link'

const SANS = 'var(--font-geist-sans)'
const MONO = 'var(--font-geist-mono)'

// ─── Module-level Recharts formatters (avoid re-creating per render) ──────────

const costTooltipFormatter = (v: unknown): [string, string] => {
  const n = typeof v === 'number' ? v : Number(v ?? 0)
  return [`$${Number.isFinite(n) ? n.toFixed(2) : '0.00'}`, 'Cost']
}

const xAxisTick = { fill: 'rgba(255,255,255,0.38)', fontSize: 12, fontFamily: SANS }

const tooltipContentStyle = {
  background: '#0a0a0a',
  border: '1px solid rgba(255,255,255,0.09)',
  borderRadius: 2,
  fontFamily: MONO,
  fontSize: 11,
  color: 'rgba(255,255,255,0.65)',
}

const tooltipCursor = { fill: 'rgba(255,255,255,0.03)' }

// ─── Static confidence gauge tick marks (computed once) ───────────────────────

const GAUGE_TICKS = (() => {
  const r = 52
  const out: { x1: string; y1: string; x2: string; y2: string }[] = []
  for (let i = 0; i < 24; i++) {
    const angle = (i / 24) * 360 - 90
    const rad = (angle * Math.PI) / 180
    out.push({
      x1: (65 + (r - 8) * Math.cos(rad)).toFixed(2),
      y1: (65 + (r - 8) * Math.sin(rad)).toFixed(2),
      x2: (65 + r * Math.cos(rad)).toFixed(2),
      y2: (65 + r * Math.sin(rad)).toFixed(2),
    })
  }
  return out
})()

const GAUGE_TICKS_JSX = (
  <>
    {GAUGE_TICKS.map((t, i) => (
      <line
        key={i}
        x1={t.x1}
        y1={t.y1}
        x2={t.x2}
        y2={t.y2}
        stroke="rgba(255,255,255,0.08)"
        strokeWidth={1}
      />
    ))}
  </>
)

// ─── Count-up hook ────────────────────────────────────────────────────────────

function useCountUp(target: number, duration = 1400, delay = 0) {
  const [val, setVal] = useState(0)
  const raf = useRef<number>(0)
  const triggered = useRef(false)

  function start() {
    if (triggered.current) return
    triggered.current = true

    // Respect prefers-reduced-motion - jump straight to final value
    const prefersReduced =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReduced) {
      setVal(target)
      return
    }

    const startTime = performance.now() + delay
    function tick(now: number) {
      if (now < startTime) { raf.current = requestAnimationFrame(tick); return }
      const p = Math.min((now - startTime) / duration, 1)
      const eased = 1 - Math.pow(1 - p, 3)
      setVal(Math.round(target * eased))
      if (p < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
  }

  useEffect(() => () => cancelAnimationFrame(raf.current), [])
  return { val, start }
}

// ─── Word-by-word text reveal ─────────────────────────────────────────────────

function WordReveal({ text, delay = 0 }: { text: string; delay?: number }) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, margin: '-40px' })
  return (
    <span ref={ref}>
      <motion.span
        initial={{ opacity: 0, y: 8 }}
        animate={inView ? { opacity: 1, y: 0 } : {}}
        transition={{ type: 'spring', stiffness: 200, damping: 28, delay }}
        style={{ display: 'inline-block' }}
      >
        {text}
      </motion.span>
    </span>
  )
}

// ─── Circular confidence gauge ────────────────────────────────────────────────

function ConfidenceGauge({ value }: { value: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true })
  const r = 52
  const circ = 2 * Math.PI * r

  return (
    <div ref={ref} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
      <svg width={130} height={130} viewBox="0 0 130 130">
        {/* Track */}
        <circle cx={65} cy={65} r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={5} />
        {/* Tick marks */}
        {GAUGE_TICKS_JSX}
        {/* Arc - electric color with glow */}
        <motion.circle
          cx={65} cy={65} r={r}
          fill="none"
          stroke="var(--color-electric)"
          strokeWidth={5}
          strokeLinecap="round"
          strokeDasharray={circ}
          initial={{ strokeDashoffset: circ }}
          animate={inView ? { strokeDashoffset: circ - (value / 100) * circ } : {}}
          transition={{ duration: 1.6, ease: 'easeOut', delay: 0.3 }}
          transform="rotate(-90 65 65)"
          style={{ filter: 'drop-shadow(0 0 6px var(--color-electric))' }}
        />
        <text x={65} y={69} textAnchor="middle"
          fill="rgba(255,255,255,0.88)" fontSize={24}
          fontFamily={MONO} fontWeight={600} letterSpacing="-1">
          {value}%
        </text>
      </svg>
      <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.30)', letterSpacing: '0.14em' }}>
        REPORT CONFIDENCE
      </span>
    </div>
  )
}

// ─── Cluster card ─────────────────────────────────────────────────────────────

function ClusterCard({
  idx,
  delay,
  clusters,
  topMessages,
}: {
  idx: number
  delay: number
  clusters: Cluster[]
  topMessages: AgentMessage[]
}) {
  const cluster = clusters[idx]
  if (!cluster) return null
  const dotOpacity = 0.90 - idx * 0.18
  const topMsgs = topMessages

  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 200, damping: 28, delay }}
      style={{
        padding: '20px',
        borderRadius: 2,
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.07)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <div style={{
          width: 8 - idx * 0.8, height: 8 - idx * 0.8,
          borderRadius: '50%',
          background: `rgba(255,255,255,${dotOpacity})`,
          boxShadow: `0 0 8px 2px rgba(255,255,255,${dotOpacity * 0.3})`,
          flexShrink: 0,
        }} />
        <span style={{ fontFamily: SANS, fontSize: 13, fontWeight: 600, color: `rgba(255,255,255,${dotOpacity})` }}>
          {cluster.name}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.25)', marginLeft: 'auto' }}>
          {cluster.agentCount} reviewers
        </span>
      </div>

      <p style={{
        fontFamily: SANS, fontSize: 12, color: 'rgba(255,255,255,0.50)',
        lineHeight: 1.5, margin: '0 0 14px',
        paddingBottom: 12, borderBottom: '1px solid rgba(255,255,255,0.05)',
      }}>
        {cluster.stance}
      </p>

      {/* Confidence bar */}
      <div style={{ marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
          <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.10em' }}>CONFIDENCE</span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.50)' }}>{cluster.confidence}%</span>
        </div>
        <div style={{ height: 1, background: 'rgba(255,255,255,0.07)', borderRadius: 1 }}>
          <motion.div
            style={{ height: '100%', borderRadius: 1, background: `rgba(255,255,255,${dotOpacity})` }}
            initial={{ width: 0 }}
            animate={{ width: `${cluster.confidence}%` }}
            transition={{ duration: 1.1, ease: 'easeOut', delay: delay + 0.3 }}
          />
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {topMsgs.map(msg => (
          <div key={msg.id} style={{ display: 'flex', gap: 7 }}>
            <span style={{ fontFamily: MONO, fontSize: 9, color: `rgba(255,255,255,${dotOpacity * 0.7})`, flexShrink: 0, marginTop: 2 }}>
              {msg.agentId}
            </span>
            <p style={{
              fontFamily: SANS, fontSize: 11,
              color: 'rgba(255,255,255,0.35)',
              lineHeight: 1.45, margin: 0,
              overflow: 'hidden',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
            } as React.CSSProperties}>
              {msg.text}
            </p>
          </div>
        ))}
      </div>
    </motion.div>
  )
}

// ─── Stat counter block ───────────────────────────────────────────────────────

function StatBlock({ label, target, suffix = '', delay = 0, decimals = 0 }: {
  label: string; target: number; suffix?: string; delay?: number; decimals?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true })
  const { val, start } = useCountUp(target * Math.pow(10, decimals), 1200, delay * 1000)

  useEffect(() => { if (inView) start() }, [inView, start])

  const display = decimals > 0
    ? (val / Math.pow(10, decimals)).toFixed(decimals)
    : val.toLocaleString()

  return (
    <div ref={ref}>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.12em', marginBottom: 5 }}>
        {label.toUpperCase()}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 20, color: 'rgba(255,255,255,0.78)', letterSpacing: '-0.02em' }}>
        {display}{suffix}
      </div>
    </div>
  )
}

// ─── Round Timeline ───────────────────────────────────────────────────────────

const ROUND_LABELS = ['Initial read', 'Cross-check', 'Verdict']
const TYPE_COLORS = {
  propose: 'rgba(255,255,255,0.55)',
  critique: 'rgba(255,100,100,0.65)',
  agree: 'rgba(100,220,160,0.65)',
  cluster: 'rgba(100,160,255,0.75)',
} as const

function RoundTimeline({
  messages,
  totalRounds,
}: {
  messages: AgentMessage[]
  totalRounds: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-40px' })

  const rounds = useMemo(() => {
    let maxRound = 0
    for (const m of messages) {
      if (m.round > maxRound) maxRound = m.round
    }
    const roundCount = Math.max(1, totalRounds, maxRound)
    // Bucket messages by round in a single pass
    const byRound: AgentMessage[][] = Array.from({ length: roundCount }, () => [])
    for (const m of messages) {
      const idx = m.round - 1
      if (idx >= 0 && idx < roundCount) byRound[idx].push(m)
    }
    return byRound.map((rMsgs, index) => {
      const r = index + 1
      const types = { propose: 0, critique: 0, agree: 0, cluster: 0 }
      let keyEvent: string | undefined
      for (const m of rMsgs) {
        if (m.type === 'propose') types.propose++
        else if (m.type === 'critique') types.critique++
        else if (m.type === 'agree') types.agree++
        else if (m.type === 'cluster') {
          types.cluster++
          if (keyEvent === undefined) keyEvent = m.text
        }
      }
      if (keyEvent === undefined) keyEvent = rMsgs[rMsgs.length - 1]?.text
      return {
        round: r,
        count: rMsgs.length,
        types,
        keyEvent,
        startTs: rMsgs[0]?.timestamp,
        endTs: rMsgs[rMsgs.length - 1]?.timestamp,
      }
    })
  }, [messages, totalRounds])

  return (
    <div ref={ref} style={{ marginBottom: '5rem' }}>
      <span style={{
        fontFamily: MONO, fontSize: 9, letterSpacing: '0.18em',
        color: 'rgba(255,255,255,0.50)', textTransform: 'uppercase',
        display: 'block', marginBottom: '1.4rem',
      } as React.CSSProperties}>
        Review Timeline
      </span>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem' }}>
        {rounds.map((round, i) => (
          <motion.div
            key={round.round}
            initial={{ opacity: 0, y: 16 }}
            animate={inView ? { opacity: 1, y: 0 } : {}}
            transition={{ type: 'spring', stiffness: 200, damping: 28, delay: 0.1 + i * 0.12 }}
            style={{
              padding: '20px',
              borderRadius: 2,
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.07)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
              <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.30)', letterSpacing: '0.12em' }}>
                PASS {round.round}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.20)', letterSpacing: '0.06em' }}>
                {round.startTs}-{round.endTs}
              </span>
            </div>

            <div style={{ fontFamily: SANS, fontSize: 13, fontWeight: 500, color: 'rgba(255,255,255,0.72)', marginBottom: 2 }}>
              {ROUND_LABELS[i] ?? `Pass ${round.round}`}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.08em', marginBottom: 16 }}>
              {round.count} review events
            </div>

            {/* Type breakdown bar */}
            <div style={{ display: 'flex', gap: 2, marginBottom: 8, height: 3, borderRadius: 2, overflow: 'hidden' }}>
              {(['propose', 'critique', 'agree', 'cluster'] as const).map(type => {
                const count = round.types[type]
                if (!count) return null
                const w = (count / round.count) * 100
                return (
                  <motion.div
                    key={type}
                    initial={{ width: 0 }}
                    animate={inView ? { width: `${w}%` } : {}}
                    transition={{ duration: 0.8, ease: 'easeOut', delay: 0.3 + i * 0.12 }}
                    style={{ height: '100%', background: TYPE_COLORS[type], flexShrink: 0 }}
                  />
                )
              })}
            </div>

            {/* Type legend */}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
              {(['propose', 'critique', 'agree', 'cluster'] as const).map(type => {
                const count = round.types[type]
                if (!count) return null
                return (
                  <span key={type} style={{ fontFamily: MONO, fontSize: 8, color: TYPE_COLORS[type], letterSpacing: '0.08em' }}>
                    {count} {type}
                  </span>
                )
              })}
            </div>

            {round.keyEvent && (
              <p style={{
                fontFamily: SANS, fontSize: 11, color: 'rgba(255,255,255,0.28)',
                lineHeight: 1.5, margin: 0,
                overflow: 'hidden',
                display: '-webkit-box',
                WebkitLineClamp: 3,
                WebkitBoxOrient: 'vertical',
              } as React.CSSProperties}>
                &ldquo;{round.keyEvent}&rdquo;
              </p>
            )}
          </motion.div>
        ))}
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

function ResultsPageContent() {
  const router = useRouter()
  const runtime = useSharedJobRuntime()
  const hasSession = Boolean(runtime.session)
  const hasMessages = runtime.messages.length > 0
  const messages = runtime.messages
  const r = useMemo(
    () => buildResults(runtime.session, messages, runtime.settlement, runtime.finalAnswer),
    [runtime.session, messages, runtime.settlement, runtime.finalAnswer],
  )
  const clusters = useMemo(
    () => buildClustersFromMessages(messages, runtime.session?.agentCount ?? 0),
    [messages, runtime.session?.agentCount],
  )
  const costChartData = useMemo(() => buildCostChartData(r), [r])
  const costRef = useRef<HTMLDivElement>(null)
  const costInView = useInView(costRef, { once: true })

  const contributions = useMemo(
    () => buildAgentContributions(messages, runtime.settlement),
    [messages, runtime.settlement],
  )
  const creditsUsed = r.agentCount * r.rounds

  const { verdictHeadline, verdictBody } = useMemo(() => {
    const full = (r.finalPrediction ?? '').trim()
    if (!full) return { verdictHeadline: '', verdictBody: '' }
    const [firstPara, ...restParas] = full.split(/\n\n+/)
    const match = firstPara.match(/^([\s\S]*?[.!?])(\s+)([\s\S]*)$/)
    if (match && match[1].length <= 220 && match[3].trim().length > 0) {
      const headline = match[1].trim()
      const rest = [match[3].trim(), ...restParas].join('\n\n').trim()
      return { verdictHeadline: headline, verdictBody: rest }
    }
    if (firstPara.length > 260 && restParas.length > 0) {
      const cut = firstPara.slice(0, 240).replace(/\s+\S*$/, '') + '…'
      return { verdictHeadline: cut, verdictBody: [firstPara, ...restParas].join('\n\n') }
    }
    return { verdictHeadline: firstPara, verdictBody: restParas.join('\n\n') }
  }, [r.finalPrediction])

  // Pre-bucket messages per cluster once, each bucket sliced to 3.
  const clusterTopMessages = useMemo(() => {
    const byCluster = new Map<number, AgentMessage[]>()
    for (const m of messages) {
      if (m.type === 'cluster') continue
      const bucket = byCluster.get(m.clusterId)
      if (bucket) {
        if (bucket.length < 3) bucket.push(m)
      } else {
        byCluster.set(m.clusterId, [m])
      }
    }
    return byCluster
  }, [messages])

  if (!hasSession || !hasMessages) {
    return (
      <div className="flex flex-col h-[100dvh] w-full bg-neutral-950 overflow-hidden relative">
        <BeamsBackground intensity="subtle" />
        <div className="relative z-10 shrink-0">
          <TopBar />
        </div>
        <div className="flex-1 grid place-items-center relative z-10">
          <div className="text-center max-w-md px-6">
            <span
              style={{
                fontFamily: MONO,
                fontSize: 9,
                letterSpacing: '0.18em',
                color: 'rgba(255,255,255,0.35)',
                textTransform: 'uppercase',
                display: 'block',
                marginBottom: '1.2rem',
              }}
            >
              No report yet
            </span>
            <h1
              style={{
                fontFamily: SANS,
                fontSize: 'clamp(1.3rem, 2.4vw, 1.8rem)',
                fontWeight: 600,
                color: 'rgba(255,255,255,0.88)',
                marginBottom: '1rem',
                lineHeight: 1.2,
              }}
            >
              {hasSession
                ? 'This review has no reviewer responses yet.'
                : 'Run a review first and the report will appear here.'}
            </h1>
            <p style={{ fontFamily: SANS, fontSize: 13, color: 'rgba(255,255,255,0.5)', marginBottom: '1.6rem', lineHeight: 1.5 }}>
              Start from the review workspace. Chorus will synthesize reviewer signals into perspectives, a verdict, and a usage summary.
            </p>
            <Link
              href="/"
              style={{
                display: 'inline-block',
                padding: '10px 22px',
                borderRadius: 2,
                background: 'rgba(255,255,255,0.92)',
                color: '#000',
                fontFamily: SANS,
                fontSize: 12,
                fontWeight: 500,
                textDecoration: 'none',
              }}
            >
              Open review workspace
            </Link>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-[100dvh] w-full bg-neutral-950 overflow-hidden relative">
      {/* Animated beams background */}
      <BeamsBackground intensity="subtle" />

      <div className="relative z-10 shrink-0">
        <TopBar />
      </div>

      <div className="flex-1 w-full min-h-0 overflow-y-auto relative z-10" style={{ scrollbarWidth: 'none' }}>
        <div style={{ padding: '4rem 5vw 6rem 8vw', maxWidth: 1100, margin: '0 auto' }}>

          {/* ── Section 1: Verdict ── */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr auto', gap: '3rem',
            alignItems: 'start', marginBottom: '5rem',
            paddingBottom: '4rem', borderBottom: '1px solid rgba(255,255,255,0.06)',
          }}>
            <div>
              <motion.span
                initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                transition={{ delay: 0.1, duration: 0.6 }}
                style={{
                  fontFamily: MONO, fontSize: 9, letterSpacing: '0.18em',
                  color: 'rgba(255,255,255,0.50)', textTransform: 'uppercase',
                  display: 'block', marginBottom: '1.4rem',
                } as React.CSSProperties}
              >
                Report ready · {r.agentCount} voices · {r.rounds} rounds · {r.totalMessages.toLocaleString()} events
              </motion.span>

              <h1 style={{
                fontFamily: SANS, fontWeight: 700,
                fontSize: 'clamp(1.5rem, 2.8vw, 2.25rem)',
                letterSpacing: '-0.03em', lineHeight: 1.15,
                color: 'rgba(255,255,255,0.88)',
                marginBottom: '1.5rem', maxWidth: '52ch',
              }}>
                <WordReveal text={verdictHeadline} delay={0.15} />
              </h1>

              {verdictBody && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.6, delay: 0.35 }}
                  style={{
                    fontFamily: SANS, fontSize: 'clamp(0.95rem, 1.15vw, 1.05rem)',
                    lineHeight: 1.65, color: 'rgba(255,255,255,0.62)',
                    marginBottom: '2rem', maxWidth: '68ch', whiteSpace: 'pre-wrap',
                  }}
                >
                  {verdictBody}
                </motion.div>
              )}

              {/* Stat row */}
              <div style={{ display: 'flex', gap: '2.5rem', flexWrap: 'wrap' }}>
                <StatBlock label="Wall time" target={r.wallTimeSeconds} suffix="s" delay={0.4} />
                <StatBlock label="Reviewers" target={r.nodesContributing} delay={0.5} />
                <StatBlock label="Messages" target={r.totalMessages} delay={0.6} />
                <StatBlock label="Credits" target={creditsUsed} delay={0.7} />
              </div>
            </div>

            <ConfidenceGauge value={r.confidenceScore} />
          </div>

          {/* ── Section 2: Clusters ── */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ type: 'spring', stiffness: 160, damping: 26, delay: 0.3 }}
            style={{ marginBottom: '5rem' }}
          >
            <span style={{
              fontFamily: MONO, fontSize: 9, letterSpacing: '0.18em',
              color: 'rgba(255,255,255,0.50)', textTransform: 'uppercase',
              display: 'block', marginBottom: '1.4rem',
            } as React.CSSProperties}>
              Review Perspectives
            </span>

            {/* Asymmetric grid */}
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: '1rem' }}>
              <ClusterCard idx={0} delay={0.35} clusters={clusters} topMessages={clusterTopMessages.get(clusters[0]?.id) ?? []} />
              <ClusterCard idx={1} delay={0.45} clusters={clusters} topMessages={clusterTopMessages.get(clusters[1]?.id) ?? []} />
              <ClusterCard idx={2} delay={0.55} clusters={clusters} topMessages={clusterTopMessages.get(clusters[2]?.id) ?? []} />
            </div>
            <div style={{ marginTop: '1rem', display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem' }}>
              <ClusterCard idx={3} delay={0.65} clusters={clusters} topMessages={clusterTopMessages.get(clusters[3]?.id) ?? []} />
            </div>
          </motion.div>

          {/* ── Section 2b: Round Timeline ── */}
          <RoundTimeline messages={messages} totalRounds={runtime.totalRounds} />

          {/* ── Section 2c: Agent Contributions ── */}
          <div style={{ marginBottom: '5rem' }}>
            <AgentContributions contributions={contributions} />
          </div>

          {/* ── Section 3: Cost ── */}
          <div
            ref={costRef}
            style={{ paddingTop: '3.5rem', borderTop: '1px solid rgba(255,255,255,0.06)' }}
          >
            {/* Cost statement - left-aligned, two-column split */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '3rem', alignItems: 'center', marginBottom: '3rem' }}>
              {/* Left: cost copy */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={costInView ? { opacity: 1 } : {}}
                transition={{ duration: 0.5, delay: 0.1 }}
              >
                <motion.p
                  initial={{ opacity: 0, y: 10 }}
                  animate={costInView ? { opacity: 1, y: 0 } : {}}
                  transition={{ delay: 0.2, type: 'spring', stiffness: 180, damping: 28 }}
                  style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '0.18em', color: 'rgba(255,255,255,0.45)', textTransform: 'uppercase', marginBottom: '1rem' }}
                >
                  Usage Summary
                </motion.p>

                <motion.p
                  initial={{ opacity: 0, y: 10 }}
                  animate={costInView ? { opacity: 1, y: 0 } : {}}
                  transition={{ delay: 0.3, type: 'spring', stiffness: 180, damping: 28 }}
                  style={{ fontFamily: SANS, fontSize: 'clamp(0.9rem, 1.6vw, 1.1rem)', color: 'rgba(255,255,255,0.38)', margin: '0 0 0.5rem' }}
                >
                  Benchmark cloud spend: ~${r.costCloud.toFixed(2)}
                </motion.p>

                <motion.p
                  initial={{ opacity: 0, y: 14 }}
                  animate={costInView ? { opacity: 1, y: 0 } : {}}
                  transition={{ delay: 0.5, type: 'spring', stiffness: 160, damping: 22 }}
                  style={{
                    fontFamily: MONO,
                    fontSize: 'clamp(2rem, 4vw, 3rem)',
                    color: 'rgba(255,255,255,0.92)',
                    letterSpacing: '-0.04em',
                    margin: '0 0 2rem',
                  }}
                >
                  Credits used: {creditsUsed}
                </motion.p>

                <p style={{ fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.22)', marginBottom: '1.8rem', letterSpacing: '0.06em' }}>
                  {r.agentCount} reviewers · {r.rounds} passes · {r.totalMessages.toLocaleString()} events · {r.wallTimeSeconds}s
                </p>

                {/* Primary CTA - magnetic, filled white */}
                <motion.button
                  onClick={() => router.push('/')}
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.97 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 22 }}
                  style={{
                    padding: '11px 28px', borderRadius: 2,
                    background: 'rgba(255,255,255,0.92)',
                    border: 'none',
                    fontFamily: SANS, fontSize: 12,
                    color: '#000',
                    cursor: 'pointer', letterSpacing: '0.03em',
                    fontWeight: 500,
                    transition: 'background-color 150ms ease-out, transform 150ms ease-out',
                  }}
                >
                  Run another review
                </motion.button>
              </motion.div>

              {/* Right: bar chart */}
              <motion.div
                initial={{ opacity: 0, y: 16 }}
                animate={costInView ? { opacity: 1, y: 0 } : {}}
                transition={{ delay: 0.7, type: 'spring', stiffness: 160, damping: 26 }}
                style={{ height: 160 }}
              >
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={costChartData} barSize={60}>
                    <XAxis dataKey="label"
                      tick={xAxisTick}
                      axisLine={false} tickLine={false} />
                    <YAxis hide />
                    <Tooltip
                      contentStyle={tooltipContentStyle}
                      cursor={tooltipCursor}
                      formatter={costTooltipFormatter}
                    />
                    <Bar dataKey="cost" radius={[2, 2, 0, 0]} animationDuration={600}>
                      {costChartData.map(entry => (
                        <Cell
                          key={entry.label}
                          fill={entry.label === 'Chorus' ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.10)'}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </motion.div>
            </div>

            <motion.p
              initial={{ opacity: 0 }}
              animate={costInView ? { opacity: 1 } : {}}
              transition={{ delay: 1.2, duration: 0.6 }}
              style={{
                fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.16)',
                marginTop: '1rem', letterSpacing: '0.12em',
              }}
            >
              PRIVATE SWARM REVIEW FOR DECISIONS THAT CANNOT RELY ON A SINGLE ANSWER.
            </motion.p>
          </div>

        </div>
      </div>
    </div>
  )
}

export default function ResultsPage() {
  return (
    <Suspense
      fallback={
        <div
          className="flex h-[100dvh] items-center justify-center bg-black font-mono text-[10px] text-white/30 tracking-widest"
        >
          LOADING REPORT…
        </div>
      }
    >
      <ResultsPageContent />
    </Suspense>
  )
}
