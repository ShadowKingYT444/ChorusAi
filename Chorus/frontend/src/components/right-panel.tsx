'use client'
import { useState, useEffect, useRef, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CLUSTERS, type AgentMessage, type ClusterID } from '@/lib/mock-data'

const SANS = 'var(--font-geist-sans)'
const MONO = 'var(--font-geist-mono)'

// ─── Message type config ──────────────────────────────────────────────────────

const TYPE_CONFIG: Record<AgentMessage['type'], {
  label: string
  alpha: number
  barAlpha: number
  description: string
}> = {
  propose:  { label: 'PROPOSE',  alpha: 0.82, barAlpha: 0.70, description: 'New thesis introduced' },
  critique: { label: 'CRITIQUE', alpha: 0.60, barAlpha: 0.45, description: 'Counter-argument' },
  agree:    { label: 'AGREE',    alpha: 0.40, barAlpha: 0.28, description: 'Consensus signal' },
  cluster:  { label: 'EVENT',    alpha: 0.95, barAlpha: 0.90, description: 'Network event' },
}

type FilterType = 'all' | AgentMessage['type']

// ─── Sub-components ───────────────────────────────────────────────────────────

function TypeBadge({ type }: { type: AgentMessage['type'] }) {
  const cfg = TYPE_CONFIG[type]
  return (
    <span style={{
      fontFamily: MONO,
      fontSize: 8,
      letterSpacing: '0.10em',
      color: `rgba(255,255,255,${cfg.alpha})`,
      border: `1px solid rgba(255,255,255,${cfg.alpha * 0.45})`,
      borderRadius: 1,
      padding: '2px 6px',
      flexShrink: 0,
      lineHeight: 1,
    }}>
      {cfg.label}
    </span>
  )
}

function ClusterDot({ clusterId, size = 6 }: { clusterId: ClusterID; size?: number }) {
  const alpha = 0.90 - (clusterId - 1) * 0.18
  return (
    <div style={{
      width: size,
      height: size,
      borderRadius: '50%',
      background: `rgba(255,255,255,${alpha})`,
      boxShadow: `0 0 ${size}px ${size / 2}px rgba(255,255,255,${alpha * 0.2})`,
      flexShrink: 0,
    }} />
  )
}

function ClusterBanner({ text }: { text: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, scaleX: 0.92 }}
      animate={{ opacity: 1, scaleX: 1 }}
      transition={{ type: 'spring', stiffness: 280, damping: 28 }}
      style={{
        margin: '10px 0',
        padding: '10px 14px',
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.14)',
        borderLeft: '2px solid rgba(255,255,255,0.55)',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <motion.div
        style={{
          position: 'absolute', top: 0, width: '60%', height: '100%',
          background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.04), transparent)',
          pointerEvents: 'none',
        }}
        animate={{ left: ['-60%', '160%'] }}
        transition={{ duration: 2.4, repeat: Infinity, repeatDelay: 4 }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <motion.div
          animate={{ opacity: [1, 0.3, 1] }}
          transition={{ duration: 1.2, repeat: Infinity }}
          style={{ width: 5, height: 5, borderRadius: '50%', background: 'rgba(255,255,255,0.80)', flexShrink: 0 }}
        />
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.40)', letterSpacing: '0.12em' }}>
          NETWORK EVENT
        </span>
      </div>
      <p style={{ fontFamily: SANS, fontSize: 12, color: 'rgba(255,255,255,0.72)', margin: '6px 0 0', lineHeight: 1.45, fontWeight: 500 }}>
        {text}
      </p>
    </motion.div>
  )
}

function MessageCard({ msg, isNew }: { msg: AgentMessage; isNew: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = TYPE_CONFIG[msg.type]
  const clusterAlpha = 0.90 - (msg.clusterId - 1) * 0.18

  return (
    <motion.div
      key={msg.id}
      layout
      initial={{ opacity: 0, x: 16, filter: 'blur(4px)' }}
      animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
      exit={{ opacity: 0, height: 0, marginBottom: 0 }}
      transition={{ type: 'spring', stiffness: 360, damping: 34 }}
      onClick={() => setExpanded(e => !e)}
      style={{
        padding: '10px 14px 10px 12px',
        borderBottom: '1px solid rgba(255,255,255,0.04)',
        cursor: 'pointer',
        position: 'relative',
        transition: 'background 0.12s',
      }}
      whileHover={{ backgroundColor: 'rgba(255,255,255,0.025)' }}
    >
      {/* Left accent bar */}
      <div style={{
        position: 'absolute',
        left: 0, top: 8, bottom: 8,
        width: 2,
        background: `rgba(255,255,255,${cfg.barAlpha * clusterAlpha})`,
        borderRadius: 1,
      }} />

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <ClusterDot clusterId={msg.clusterId as ClusterID} size={5} />
        <span style={{
          fontFamily: MONO, fontSize: 9,
          color: `rgba(255,255,255,${clusterAlpha * 0.9})`,
          letterSpacing: '0.06em',
        }}>
          {msg.agentId.toUpperCase()}
        </span>
        <TypeBadge type={msg.type} />
        {isNew && (
          <motion.span
            initial={{ opacity: 1 }}
            animate={{ opacity: 0 }}
            transition={{ delay: 3, duration: 1 }}
            style={{
              fontFamily: MONO, fontSize: 7,
              color: 'rgba(255,255,255,0.55)',
              letterSpacing: '0.14em',
              marginLeft: 2,
            }}
          >
            NEW
          </motion.span>
        )}
        <span style={{ fontFamily: MONO, fontSize: 8, color: 'rgba(255,255,255,0.20)', marginLeft: 'auto' }}>
          R{msg.round} · {msg.timestamp}
        </span>
      </div>

      {/* Reply thread */}
      {msg.replyTo && (
        <div style={{
          marginBottom: 5, paddingLeft: 8,
          borderLeft: '1px solid rgba(255,255,255,0.10)',
          marginLeft: 2,
        }}>
          <span style={{ fontFamily: MONO, fontSize: 8, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.04em' }}>
            ↳ replying to {msg.replyTo}
          </span>
        </div>
      )}

      {/* Message text */}
      <p style={{
        fontFamily: SANS,
        fontSize: 12,
        color: `rgba(255,255,255,${0.55 + (msg.clusterId === 1 ? 0.08 : 0)})`,
        lineHeight: 1.55,
        margin: 0,
        paddingLeft: 11,
        ...(!expanded ? {
          overflow: 'hidden',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
        } : {}),
      } as React.CSSProperties}>
        {msg.text}
      </p>

      {/* Footer — cluster name + expand hint */}
      <div style={{ display: 'flex', alignItems: 'center', marginTop: 6, paddingLeft: 11 }}>
        <span style={{ fontFamily: MONO, fontSize: 8, color: 'rgba(255,255,255,0.20)' }}>
          {CLUSTERS.find(c => c.id === msg.clusterId)?.name}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 7, color: 'rgba(255,255,255,0.16)', marginLeft: 'auto' }}>
          {expanded ? '↑ collapse' : '↓ expand'}
        </span>
      </div>
    </motion.div>
  )
}

// ─── Activity bar per cluster ─────────────────────────────────────────────────

function ClusterActivityBar({ clusterId, count, max }: { clusterId: ClusterID; count: number; max: number }) {
  const cluster = CLUSTERS.find(c => c.id === clusterId)!
  const pct = max > 0 ? (count / max) * 100 : 0
  const alpha = 0.90 - (clusterId - 1) * 0.18

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <ClusterDot clusterId={clusterId} size={5} />
      <span style={{ fontFamily: MONO, fontSize: 8, color: `rgba(255,255,255,${alpha * 0.8})`, minWidth: 58, letterSpacing: '0.04em' }}>
        {cluster.name.toUpperCase().replace('CLUSTER ', 'C')}
      </span>
      <div style={{ flex: 1, height: 2, background: 'rgba(255,255,255,0.07)', borderRadius: 1, overflow: 'hidden' }}>
        <motion.div
          style={{ height: '100%', borderRadius: 1, background: `rgba(255,255,255,${alpha})` }}
          animate={{ width: `${pct}%` }}
          transition={{ type: 'spring', stiffness: 80, damping: 20 }}
        />
      </div>
      <span style={{ fontFamily: MONO, fontSize: 8, color: 'rgba(255,255,255,0.30)', minWidth: 16, textAlign: 'right' }}>
        {count}
      </span>
    </div>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────

import type { SettlementPreview } from '@/lib/api/orchestrator'

export function RightPanel({
  messages,
  totalSlots,
  live = false,
  finalAnswer = null,
  citations = [],
  settlement = null,
}: {
  messages?: AgentMessage[]
  totalSlots?: number
  live?: boolean
  finalAnswer?: string | null
  citations?: string[]
  settlement?: SettlementPreview | null
}) {
  const sourceMessages = messages ?? []
  const [visibleCount, setVisibleCount] = useState(0)
  const [filter, setFilter] = useState<FilterType>('all')
  const feedRef = useRef<HTMLDivElement>(null)
  const prevCount = useRef(0)
  const prevSourceLenRef = useRef(0)

  // New job / cleared feed: drop stagger so we do not flash stale cards.
  useEffect(() => {
    const len = sourceMessages.length
    if (len < prevSourceLenRef.current) {
      setVisibleCount(0)
    }
    prevSourceLenRef.current = len
  }, [sourceMessages.length])

  // Live backend: show the full transcript immediately (multi-agent bursts are common).
  useEffect(() => {
    if (!live) return
    setVisibleCount(sourceMessages.length)
  }, [live, sourceMessages.length])

  // Mock / replay: reveal one card at a time.
  useEffect(() => {
    if (live) return
    if (visibleCount >= sourceMessages.length) return
    const t = setTimeout(() => setVisibleCount((c) => c + 1), 820)
    return () => clearTimeout(t)
  }, [live, visibleCount, sourceMessages.length])

  useEffect(() => {
    if (feedRef.current && visibleCount > prevCount.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight
    }
    prevCount.current = visibleCount
  }, [visibleCount])

  const effectiveVisibleCount = visibleCount
  const visible = sourceMessages.slice(0, effectiveVisibleCount)

  // Cluster message counts
  const clusterCounts = useMemo(() => {
    const counts: Record<ClusterID, number> = { 1: 0, 2: 0, 3: 0, 4: 0 }
    visible.forEach(m => { if (m.clusterId in counts) counts[m.clusterId as ClusterID]++ })
    return counts
  }, [visible])

  const maxCount = Math.max(...Object.values(clusterCounts), 1)

  // Type counts
  const typeCounts = useMemo(() => {
    const c: Record<string, number> = { propose: 0, critique: 0, agree: 0, cluster: 0 }
    visible.forEach(m => { c[m.type] = (c[m.type] ?? 0) + 1 })
    return c
  }, [visible])

  const filtered = filter === 'all' ? visible : visible.filter(m => m.type === filter)

  const FILTERS: { key: FilterType; label: string }[] = [
    { key: 'all',      label: 'ALL' },
    { key: 'propose',  label: 'PROPOSE' },
    { key: 'critique', label: 'CRITIQUE' },
    { key: 'agree',    label: 'AGREE' },
  ]

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      background: '#030303',
      borderLeft: '1px solid rgba(255,255,255,0.06)',
      fontFamily: SANS,
    }}>

      {/* ── Header ── */}
      <div style={{
        padding: '14px 14px 10px',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.16em' }}>
            AGENT COMMUNICATIONS
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <motion.div
              animate={{ opacity: [1, 0.25, 1] }}
              transition={{ duration: 1.4, repeat: Infinity }}
              style={{ width: 5, height: 5, borderRadius: '50%', background: 'rgba(255,255,255,0.75)' }}
            />
            <motion.span
              key={visible.length}
              initial={{ opacity: 0.4 }}
              animate={{ opacity: 1 }}
              style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.40)' }}
            >
              {visible.length} / {sourceMessages.length}
              {totalSlots ? ` · ${totalSlots} slots` : ''}
            </motion.span>
          </div>
        </div>

        {/* Stats row */}
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
          gap: 1, background: 'rgba(255,255,255,0.05)',
          border: '1px solid rgba(255,255,255,0.07)',
          borderRadius: 2, overflow: 'hidden',
          marginBottom: 10,
        }}>
          {[
            { label: 'PROPOSE',  value: typeCounts.propose ?? 0 },
            { label: 'CRITIQUE', value: typeCounts.critique ?? 0 },
            { label: 'AGREE',    value: typeCounts.agree ?? 0 },
          ].map(({ label, value }) => (
            <div key={label} style={{
              padding: '7px 0',
              textAlign: 'center',
              background: '#030303',
              borderRight: '1px solid rgba(255,255,255,0.06)',
            }}>
              <div style={{ fontFamily: MONO, fontSize: 14, color: 'rgba(255,255,255,0.75)', letterSpacing: '-0.02em' }}>
                {value}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 7, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.10em', marginTop: 2 }}>
                {label}
              </div>
            </div>
          ))}
        </div>

        {/* Filter tabs */}
        <div style={{ display: 'flex', gap: 4 }}>
          {FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              style={{
                fontFamily: MONO,
                fontSize: 8,
                letterSpacing: '0.10em',
                padding: '4px 8px',
                border: '1px solid',
                borderColor: filter === f.key ? 'rgba(255,255,255,0.35)' : 'rgba(255,255,255,0.10)',
                borderRadius: 1,
                background: filter === f.key ? 'rgba(255,255,255,0.08)' : 'transparent',
                color: filter === f.key ? 'rgba(255,255,255,0.75)' : 'rgba(255,255,255,0.28)',
                cursor: 'pointer',
                transition: 'all 0.12s',
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Final answer (after merge) ── */}
      {finalAnswer && (
        <motion.div
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          style={{
            margin: '10px 14px 0',
            padding: '10px 12px',
            background: 'rgba(90, 220, 140, 0.06)',
            border: '1px solid rgba(90, 220, 140, 0.25)',
            borderRadius: 3,
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 8, letterSpacing: '0.16em', color: 'rgba(120,240,170,0.75)', marginBottom: 6 }}>
            FINAL · CHORUS MERGE
          </div>
          <div style={{ fontFamily: SANS, fontSize: 12, color: 'rgba(255,255,255,0.88)', lineHeight: 1.45 }}>
            {finalAnswer}
          </div>
          {citations.length > 0 && (
            <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {citations.map((c) => (
                <span
                  key={c}
                  style={{
                    fontFamily: MONO, fontSize: 8, letterSpacing: '0.08em',
                    padding: '2px 6px', borderRadius: 2,
                    background: 'rgba(120,240,170,0.10)', color: 'rgba(180,255,210,0.85)',
                  }}
                >
                  {c}
                </span>
              ))}
            </div>
          )}
        </motion.div>
      )}

      {/* ── Feed ── */}
      <div
        ref={feedRef}
        style={{ flex: 1, overflowY: 'auto', scrollbarWidth: 'none' }}
      >
        <AnimatePresence initial={false}>
          {filtered.map((msg, idx) => {
            if (msg.type === 'cluster') {
              return (
                <div key={msg.id} style={{ padding: '0 14px' }}>
                  <ClusterBanner text={msg.text} />
                </div>
              )
            }
            const isNew = idx === filtered.length - 1 && effectiveVisibleCount > 0
            return <MessageCard key={msg.id} msg={msg} isNew={isNew} />
          })}
        </AnimatePresence>

        {effectiveVisibleCount === 0 && (
          <div style={{ padding: '32px 14px', textAlign: 'center' }}>
            <motion.div
              animate={{ opacity: [0.2, 0.5, 0.2] }}
              transition={{ duration: 2, repeat: Infinity }}
              style={{ fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.30)', letterSpacing: '0.14em' }}
            >
              {live ? 'AWAITING BACKEND EVENTS…' : 'AWAITING AGENTS…'}
            </motion.div>
          </div>
        )}

        {filter !== 'all' && filtered.length === 0 && effectiveVisibleCount > 0 && (
          <div style={{ padding: '20px 14px', textAlign: 'center' }}>
            <p style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.22)', margin: 0 }}>
              No {filter.toUpperCase()} messages yet
            </p>
          </div>
        )}
      </div>

      {/* ── Cluster activity ── */}
      <div style={{
        padding: '12px 14px',
        borderTop: '1px solid rgba(255,255,255,0.06)',
        flexShrink: 0,
      }}>
        <span style={{ fontFamily: MONO, fontSize: 8, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.14em', display: 'block', marginBottom: 10 }}>
          CLUSTER ACTIVITY
        </span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          {([1, 2, 3, 4] as ClusterID[]).map(cid => (
            <ClusterActivityBar
              key={cid}
              clusterId={cid}
              count={clusterCounts[cid]}
              max={maxCount}
            />
          ))}
        </div>
      </div>

      {/* ── Settlement panel ── */}
      {settlement && (
        <div style={{
          padding: '10px 14px',
          borderTop: '1px solid rgba(255,255,255,0.06)',
          flexShrink: 0,
          background: 'rgba(255,255,255,0.015)',
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            marginBottom: 8,
          }}>
            <span style={{ fontFamily: MONO, fontSize: 8, letterSpacing: '0.14em', color: 'rgba(255,255,255,0.40)' }}>
              SETTLEMENT · {settlement.total_pool.toFixed(0)} POOL
            </span>
            {settlement.receipt?.signature && (
              <span
                title={`Ed25519 · ${settlement.receipt.pubkey?.slice(0, 16)}…`}
                style={{ fontFamily: MONO, fontSize: 7, color: 'rgba(120,240,170,0.85)', letterSpacing: '0.08em' }}
              >
                ✓ {settlement.receipt.signature.slice(0, 14)}…
              </span>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {Object.entries(settlement.payouts).map(([slot, total]) => {
              const br = settlement.payout_breakdown?.[slot]
              const title = br
                ? `floor ${br.floor} + consensus ${br.consensus_bonus} + dissent ${br.dissent_bonus} = ${br.total}`
                : `${total}`
              return (
                <div
                  key={slot}
                  title={title}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.72)',
                  }}
                >
                  <span style={{ minWidth: 64, color: 'rgba(255,255,255,0.45)' }}>{slot}</span>
                  <div style={{ flex: 1, height: 3, background: 'rgba(255,255,255,0.05)', borderRadius: 1, overflow: 'hidden' }}>
                    <div
                      style={{
                        height: '100%',
                        width: `${Math.min(100, (total / Math.max(1, settlement.total_pool)) * 100 * settlement.eligible_agents)}%`,
                        background: 'rgba(255,255,255,0.55)',
                      }}
                    />
                  </div>
                  <span style={{ minWidth: 44, textAlign: 'right', color: 'rgba(255,255,255,0.85)' }}>
                    {total.toFixed(2)}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Cluster legend ── */}
      <div style={{
        padding: '10px 14px',
        borderTop: '1px solid rgba(255,255,255,0.05)',
        display: 'flex', gap: 10, flexWrap: 'wrap', flexShrink: 0,
      }}>
        {CLUSTERS.map(c => (
          <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <ClusterDot clusterId={c.id as ClusterID} size={5} />
            <span style={{ fontFamily: SANS, fontSize: 9, color: 'rgba(255,255,255,0.28)' }}>{c.name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
