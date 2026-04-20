'use client'

import { ChevronDown, Share, SquarePen } from 'lucide-react'
import { NetworkChip } from './network-chip'
import type { NetworkStatus } from '@/hooks/use-network-status'

interface Props {
  title: string
  status: NetworkStatus
  onNewChat: () => void
}

export function ChorusTopBar({ title, status, onNewChat }: Props) {
  return (
    <header
      className="h-14 shrink-0 flex items-center justify-between px-4"
      style={{
        background: 'rgba(10,10,12,0.6)',
        borderBottom: '1px solid rgba(255,255,255,0.05)',
        backdropFilter: 'blur(12px)',
      }}
    >
      <div className="flex items-center gap-3 min-w-0">
        <button
          type="button"
          className="group flex items-center gap-1.5 px-2.5 py-1.5 rounded-md hover:bg-white/5 transition-colors"
        >
          <span className="font-sans text-[13px] text-white/90 truncate max-w-[360px]">
            {title}
          </span>
          <ChevronDown className="w-3.5 h-3.5 text-white/45 group-hover:text-white/75 transition-colors" />
        </button>
        <span className="font-mono text-[10px] text-white/30 tracking-[0.08em] uppercase hidden md:inline">
          · private review workspace
        </span>
      </div>

      <div className="flex items-center gap-2">
        <NetworkChip status={status} />
        <button
          onClick={onNewChat}
          aria-label="new review"
          className="p-2 rounded-md text-white/65 hover:text-white hover:bg-white/5 transition-colors"
        >
          <SquarePen className="w-4 h-4" />
        </button>
        <button
          aria-label="share"
          className="p-2 rounded-md text-white/65 hover:text-white hover:bg-white/5 transition-colors"
        >
          <Share className="w-4 h-4" />
        </button>
      </div>
    </header>
  )
}
