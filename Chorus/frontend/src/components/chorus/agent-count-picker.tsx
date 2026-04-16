'use client'

import { motion } from 'framer-motion'
import { Minus, Plus, Users } from 'lucide-react'
import { useCallback, useMemo } from 'react'
import type { NetworkStatus } from '@/hooks/use-network-status'

interface Props {
  value: number
  onChange: (n: number) => void
  status: NetworkStatus
  maxVoices?: number
}

export function AgentCountPicker({ value, onChange, status, maxVoices }: Props) {
  const max = Math.max(1, maxVoices ?? status.online)
  const clamped = Math.min(Math.max(1, value), max)
  const pct = (clamped / max) * 100

  const presets = useMemo(() => {
    const set = new Set<number>()
    set.add(1)
    if (max >= 3) set.add(3)
    if (max >= 5) set.add(5)
    if (max >= 10) set.add(10)
    set.add(max)
    return Array.from(set).filter((n) => n <= max).sort((a, b) => a - b)
  }, [max])

  const set = useCallback(
    (n: number) => onChange(Math.min(Math.max(1, n), max)),
    [max, onChange],
  )

  return (
    <div
      className="flex flex-col gap-2.5 rounded-xl px-3 py-2.5"
      style={{
        background: 'rgba(255,255,255,0.025)',
        border: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-white/70">
          <Users className="w-3.5 h-3.5" />
          <span className="font-mono text-[10.5px] tracking-[0.08em] uppercase">
            Voices
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => set(clamped - 1)}
            disabled={clamped <= 1}
            className="w-6 h-6 grid place-items-center rounded-md text-white/70 hover:text-white
              hover:bg-white/5 disabled:opacity-30 disabled:pointer-events-none transition-colors"
            aria-label="decrement voices"
          >
            <Minus className="w-3 h-3" />
          </button>
          <motion.div
            key={clamped}
            initial={{ y: -3, opacity: 0.4 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ type: 'spring', stiffness: 520, damping: 30 }}
            className="font-mono text-[14px] text-white/95 tabular-nums min-w-[36px] text-center"
          >
            {clamped}
            <span className="text-white/35">/{max}</span>
          </motion.div>
          <button
            type="button"
            onClick={() => set(clamped + 1)}
            disabled={clamped >= max}
            className="w-6 h-6 grid place-items-center rounded-md text-white/70 hover:text-white
              hover:bg-white/5 disabled:opacity-30 disabled:pointer-events-none transition-colors"
            aria-label="increment voices"
          >
            <Plus className="w-3 h-3" />
          </button>
        </div>
      </div>

      <div className="relative h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
        <motion.div
          className="absolute top-0 left-0 h-full rounded-full"
          animate={{ width: `${pct}%` }}
          transition={{ type: 'spring', stiffness: 260, damping: 28 }}
          style={{
            background:
              'linear-gradient(90deg, rgba(180,200,255,0.85) 0%, rgba(255,255,255,0.95) 100%)',
            boxShadow: '0 0 12px rgba(180,200,255,0.45)',
          }}
        />
        <input
          type="range"
          min={1}
          max={max}
          step={1}
          value={clamped}
          onChange={(e) => set(Number(e.target.value))}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
          aria-label="number of voices"
        />
      </div>

      <div className="flex flex-wrap gap-1">
        {presets.map((p) => {
          const active = p === clamped
          return (
            <button
              key={p}
              type="button"
              onClick={() => set(p)}
              className="px-2 py-0.5 rounded-md font-mono text-[10px] tabular-nums transition-colors"
              style={{
                background: active ? 'rgba(255,255,255,0.14)' : 'transparent',
                color: active ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.55)',
                border: '1px solid',
                borderColor: active ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.08)',
              }}
            >
              {p === max ? `all · ${p}` : p}
            </button>
          )
        })}
      </div>
    </div>
  )
}
