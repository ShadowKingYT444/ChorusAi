'use client'

import { motion } from 'framer-motion'
import Link from 'next/link'
import {
  Compass,
  MessageSquarePlus,
  Network,
  Rocket,
  Settings2,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  deleteChat,
  formatRelative,
  listChats,
  subscribeChats,
  type ChatRecord,
} from '@/lib/runtime/chat-history'

interface Props {
  onNewChat: () => void
  onSelectChat?: (id: string) => void
  activeId?: string
}

export function ChorusSidebar({ onNewChat, onSelectChat, activeId }: Props) {
  const [chats, setChats] = useState<ChatRecord[]>([])

  useEffect(() => {
    const load = () => setChats(listChats())
    load()
    return subscribeChats(load)
  }, [])

  const sorted = [...chats].sort((a, b) => b.updatedAt - a.updatedAt)

  return (
    <aside
      className="flex flex-col h-full w-[260px] shrink-0"
      style={{
        background: 'rgba(10,10,12,0.78)',
        borderRight: '1px solid rgba(255,255,255,0.06)',
        backdropFilter: 'blur(12px)',
      }}
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-4 h-14 border-b border-white/5">
        <div
          className="relative grid place-items-center w-7 h-7 rounded-lg"
          style={{
            background: 'linear-gradient(135deg, rgba(180,200,255,0.25), rgba(255,255,255,0.05))',
            border: '1px solid rgba(255,255,255,0.1)',
          }}
        >
          <Sparkles className="w-3.5 h-3.5 text-white/85" />
        </div>
        <span
          className="font-sans text-[14px] text-white/95 tracking-tight"
          style={{ fontWeight: 600 }}
        >
          Chorus
        </span>
      </div>

      {/* New chat */}
      <div className="px-3 pt-3">
        <button
          onClick={onNewChat}
          className="group w-full flex items-center gap-2 px-3 py-2.5 rounded-lg transition-all"
          style={{
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.08)',
          }}
        >
          <MessageSquarePlus className="w-4 h-4 text-white/75 group-hover:text-white transition-colors" />
          <span className="font-sans text-[12.5px] text-white/85 group-hover:text-white transition-colors">
            New conversation
          </span>
        </button>
      </div>

      {/* Nav */}
      <nav className="px-3 pt-4 flex flex-col gap-0.5">
        <NavItem icon={<Compass className="w-3.5 h-3.5" />} label="Discover" href="/join" />
        <NavItem icon={<Rocket className="w-3.5 h-3.5" />} label="Get Started" href="/setup" />
        <NavItem icon={<Network className="w-3.5 h-3.5" />} label="Network" href="/app" />
      </nav>

      {/* History */}
      <div className="px-4 pt-5 pb-2 flex items-center justify-between">
        <span className="font-mono text-[9.5px] text-white/35 uppercase tracking-[0.12em]">
          Past chats
        </span>
        <span className="font-mono text-[9.5px] text-white/25 tabular-nums">
          {sorted.length}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {sorted.length === 0 ? (
          <div className="px-3 py-4 font-sans text-[11.5px] text-white/35 leading-relaxed">
            No past chats yet. Send a prompt to the chorus — it will save here.
          </div>
        ) : (
          sorted.map((c, i) => {
            const snippet = firstUserSnippet(c)
            return (
              <motion.div
                key={c.id}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.02 * i }}
                className="group relative"
              >
                <button
                  type="button"
                  onClick={() => onSelectChat?.(c.id)}
                  className="w-full text-left rounded-lg px-3 py-2 transition-colors"
                  style={{
                    background: c.id === activeId ? 'rgba(255,255,255,0.05)' : 'transparent',
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-sans text-[12.5px] text-white/85 truncate">
                      {c.title || 'Untitled'}
                    </span>
                    <span className="font-mono text-[9.5px] text-white/35 shrink-0">
                      {formatRelative(c.updatedAt)}
                    </span>
                  </div>
                  {snippet && (
                    <div className="font-sans text-[11px] text-white/45 truncate mt-0.5">
                      {snippet}
                    </div>
                  )}
                  <div className="mt-1 font-mono text-[9.5px] text-white/35">
                    {c.voices} voice{c.voices === 1 ? '' : 's'} · {c.turns.length} turn
                    {c.turns.length === 1 ? '' : 's'}
                  </div>
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    if (confirm('Delete this chat?')) deleteChat(c.id)
                  }}
                  aria-label="delete chat"
                  className="absolute right-2 top-2 opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-white/10 text-white/50 hover:text-white/90 transition-opacity"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </motion.div>
            )
          })
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-3 border-t border-white/5 flex items-center gap-2">
        <div
          className="w-7 h-7 rounded-full grid place-items-center font-mono text-[11px] text-white/80"
          style={{
            background: 'linear-gradient(135deg, rgba(180,200,255,0.35), rgba(200,180,255,0.18))',
            border: '1px solid rgba(255,255,255,0.1)',
          }}
        >
          T
        </div>
        <div className="flex flex-col min-w-0">
          <span className="font-sans text-[12px] text-white/85 truncate">You</span>
          <span className="font-mono text-[9.5px] text-white/45 truncate">prompter · local</span>
        </div>
        <Link
          href="/setup"
          className="ml-auto p-1.5 rounded-md text-white/55 hover:text-white hover:bg-white/5 transition-colors"
          aria-label="settings"
        >
          <Settings2 className="w-3.5 h-3.5" />
        </Link>
      </div>
    </aside>
  )
}

function firstUserSnippet(c: ChatRecord): string {
  const first = c.turns.find((t) => t.role === 'user' && t.text)
  const text = first?.text ?? ''
  return text.length > 70 ? text.slice(0, 70) + '…' : text
}

function NavItem({
  icon,
  label,
  href,
  active,
}: {
  icon: React.ReactNode
  label: string
  href?: string
  active?: boolean
}) {
  const inner = (
    <div
      className="flex items-center gap-2 px-3 py-1.5 rounded-md transition-colors"
      style={{
        background: active ? 'rgba(255,255,255,0.06)' : 'transparent',
        color: active ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.7)',
      }}
    >
      {icon}
      <span className="font-sans text-[12.5px]">{label}</span>
    </div>
  )
  if (href) return <Link href={href}>{inner}</Link>
  return <button type="button">{inner}</button>
}
