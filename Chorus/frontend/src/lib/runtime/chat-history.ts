import type { ChatTurn } from '@/components/chorus/chat-stream'

const KEY = 'chorus_chat_history_v1'

export interface ChatRecord {
  id: string
  title: string
  turns: ChatTurn[]
  createdAt: number
  updatedAt: number
  voices: number
}

function emitChange() {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('chorus-history-changed'))
}

export function listChats(): ChatRecord[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const arr = JSON.parse(raw) as ChatRecord[]
    return Array.isArray(arr) ? arr : []
  } catch {
    return []
  }
}

export function getChat(id: string): ChatRecord | null {
  return listChats().find((c) => c.id === id) ?? null
}

export function upsertChat(record: ChatRecord): void {
  if (typeof window === 'undefined') return
  const all = listChats()
  const idx = all.findIndex((c) => c.id === record.id)
  const next = idx >= 0
    ? [...all.slice(0, idx), record, ...all.slice(idx + 1)]
    : [record, ...all]
  const trimmed = next.slice(0, 100)
  localStorage.setItem(KEY, JSON.stringify(trimmed))
  emitChange()
}

export function deleteChat(id: string): void {
  if (typeof window === 'undefined') return
  const next = listChats().filter((c) => c.id !== id)
  localStorage.setItem(KEY, JSON.stringify(next))
  emitChange()
}

export function subscribeChats(fn: () => void): () => void {
  if (typeof window === 'undefined') return () => {}
  const handler = () => fn()
  window.addEventListener('chorus-history-changed', handler)
  window.addEventListener('storage', (e) => {
    if (e.key === KEY) fn()
  })
  return () => {
    window.removeEventListener('chorus-history-changed', handler)
  }
}

export function formatRelative(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  const d = Math.floor(s / 86400)
  if (d === 1) return 'yesterday'
  if (d < 7) return `${d}d`
  return new Date(ts).toLocaleDateString()
}
