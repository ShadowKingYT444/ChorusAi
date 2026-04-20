'use client'

import { motion } from 'framer-motion'
import { Radio, WifiOff } from 'lucide-react'
import type { NetworkStatus } from '@/hooks/use-network-status'

export function NetworkChip({ status }: { status: NetworkStatus }) {
  const online = status.online
  const isLive = status.mode === 'live'
  const isUnconfigured = status.mode === 'unconfigured'
  const dotColor = isLive
    ? online > 0
      ? 'rgba(120,220,160,0.95)'
      : 'rgba(220,200,120,0.9)'
    : isUnconfigured
    ? 'rgba(180,200,255,0.85)'
    : 'rgba(220,120,120,0.85)'
  const label = isLive
    ? online > 0
      ? `${online} reviewer${online === 1 ? '' : 's'} ready`
      : 'capacity waiting'
    : isUnconfigured
    ? 'no control plane'
    : 'control plane offline'

  return (
    <div
      className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-full select-none"
      style={{
        background: 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.08)',
        backdropFilter: 'blur(8px)',
      }}
    >
      {status.mode === 'offline' || status.mode === 'unconfigured' ? (
        <WifiOff className="w-3 h-3 text-white/55" />
      ) : (
        <motion.span
          className="relative inline-block w-1.5 h-1.5 rounded-full"
          style={{ background: dotColor }}
          animate={isLive ? { scale: [1, 1.35, 1], opacity: [0.7, 1, 0.7] } : undefined}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
      <span className="font-mono text-[10.5px] tracking-[0.08em] text-white/80 tabular-nums">
        {label}
      </span>
      {isLive && <Radio className="w-3 h-3 text-white/35" />}
    </div>
  )
}
