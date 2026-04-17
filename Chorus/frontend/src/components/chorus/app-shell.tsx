'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ChorusSidebar } from './sidebar'
import { ChorusTopBar } from './top-bar'
import { ChorusComposer } from './composer'
import { ChorusWelcome } from './welcome'
import { ChorusChatStream, type ChatTurn } from './chat-stream'
import { useNetworkStatus } from '@/hooks/use-network-status'
import {
  createJob,
  isOrchestratorConfigured,
  registerJobAgents,
  type PeerEntry,
} from '@/lib/api/orchestrator'
import { writeSimulationSession } from '@/lib/runtime/session'
import { getChat, upsertChat } from '@/lib/runtime/chat-history'

const ACTIVE_CHAT_KEY = 'chorus_active_chat_id'

const DEFAULT_JOB_CONTEXT =
  'You are participating in a distributed Chorus debate. Answer directly, add concrete reasoning, and adapt when peer context appears in later rounds.'
const AUTO_FILL_MAX_VOICES = 8
const SYNTHETIC_MODEL_POOL = [
  'qwen2.5:0.5b',
  'llama3.2:1b',
  'gemma2:2b',
  'phi3:mini',
  'mistral-nemo',
]
const SYNTHETIC_NAME_POOL = [
  'analyst',
  'reviewer',
  'strategist',
  'challenger',
  'synthesizer',
  'architect',
  'researcher',
  'auditor',
]

type LaunchParticipant = {
  peer: PeerEntry
  completionBaseUrl: string
}

function makeSyntheticParticipant(index: number): LaunchParticipant {
  const name = SYNTHETIC_NAME_POOL[index % SYNTHETIC_NAME_POOL.length]
  const peerId = `chorus-${name}-${index + 1}`
  const now = Math.round(Date.now() / 1000)
  return {
    peer: {
      peer_id: peerId,
      model: SYNTHETIC_MODEL_POOL[index % SYNTHETIC_MODEL_POOL.length],
      joined_at: now,
      status: 'idle',
      verified: false,
    },
    completionBaseUrl: `synthetic://${peerId}`,
  }
}

function buildLaunchParticipants(
  peers: PeerEntry[],
  requestedVoices: number,
  autoFill: boolean,
): {
  participants: LaunchParticipant[]
  liveCount: number
  syntheticCount: number
} {
  const addressedPeers = [...peers]
    .filter((peer) => Boolean(peer.address?.trim()))
    .sort((a, b) => {
      if (a.verified !== b.verified) return a.verified ? -1 : 1
      return a.peer_id.localeCompare(b.peer_id)
    })

  const liveTarget = autoFill
    ? Math.min(requestedVoices, Math.min(1, addressedPeers.length))
    : Math.min(requestedVoices, addressedPeers.length)

  const live = addressedPeers.slice(0, liveTarget).map((peer) => ({
    peer,
    completionBaseUrl: peer.address?.trim() ?? '',
  }))

  if (!autoFill && live.length < requestedVoices) {
    return {
      participants: live,
      liveCount: live.length,
      syntheticCount: 0,
    }
  }

  const syntheticCount = Math.max(0, requestedVoices - live.length)
  const synthetics = Array.from({ length: syntheticCount }, (_, index) => makeSyntheticParticipant(index))

  return {
    participants: [...live, ...synthetics],
    liveCount: live.length,
    syntheticCount,
  }
}

export function ChorusAppShell() {
  const router = useRouter()
  const status = useNetworkStatus(4000)
  const [activeChatId, setActiveChatId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  const [voices, setVoices] = useState(5)
  const [rounds, setRounds] = useState(3)
  const [bounty, setBounty] = useState(0.5)
  const [sending, setSending] = useState(false)
  const [title, setTitle] = useState('New conversation')
  const [error, setError] = useState<string | null>(null)

  const readyPeerCount = status.peers.filter((peer) => Boolean(peer.address?.trim())).length
  const maxVoices = Math.max(status.online, AUTO_FILL_MAX_VOICES)
  const clampedVoices = Math.min(Math.max(1, voices), maxVoices)

  // Track first-mount restore so the auto-save effect doesn't wipe
  // an existing chat with an empty turns array before hydration.
  const hydratedRef = useRef(false)
  const chatCreatedAtRef = useRef<number | null>(null)

  // Restore last active chat from localStorage on mount.
  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      const savedId = window.localStorage.getItem(ACTIVE_CHAT_KEY)
      if (savedId) {
        const rec = getChat(savedId)
        if (rec) {
          setActiveChatId(rec.id)
          setTurns(rec.turns)
          setTitle(rec.title)
          setVoices(Math.max(1, rec.voices))
          chatCreatedAtRef.current = rec.createdAt
        }
      }
    } catch {
      /* noop */
    }
    hydratedRef.current = true
  }, [])

  // Auto-save turns on every change. Creates a chat id lazily on first turn.
  useEffect(() => {
    if (!hydratedRef.current) return
    if (turns.length === 0) return
    const id = activeChatId ?? `chat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    if (!activeChatId) {
      setActiveChatId(id)
      chatCreatedAtRef.current = Date.now()
    }
    const createdAt = chatCreatedAtRef.current ?? Date.now()
    const firstUser = turns.find((t) => t.role === 'user')
    const derivedTitle =
      title !== 'New conversation' ? title : firstUser?.text?.slice(0, 48) ?? 'New conversation'
    upsertChat({
      id,
      title: derivedTitle,
      turns,
      createdAt,
      updatedAt: Date.now(),
      voices,
    })
    try {
      window.localStorage.setItem(ACTIVE_CHAT_KEY, id)
    } catch {
      /* noop */
    }
  }, [turns, activeChatId, title, voices])

  const newChat = useCallback(() => {
    setActiveChatId(null)
    setTurns([])
    setDraft('')
    setTitle('New conversation')
    setError(null)
    chatCreatedAtRef.current = null
    try {
      window.localStorage.removeItem(ACTIVE_CHAT_KEY)
    } catch {
      /* noop */
    }
  }, [])

  const selectChat = useCallback((id: string) => {
    const rec = getChat(id)
    if (!rec) return
    setActiveChatId(rec.id)
    setTurns(rec.turns)
    setTitle(rec.title)
    setVoices(Math.max(1, rec.voices))
    setDraft('')
    setError(null)
    chatCreatedAtRef.current = rec.createdAt
    try {
      window.localStorage.setItem(ACTIVE_CHAT_KEY, rec.id)
    } catch {
      /* noop */
    }
  }, [])

  const send = useCallback(async () => {
    if (!draft.trim() || sending) return

    const prompt = draft.trim()
    setError(null)
    setSending(true)

    if (!isOrchestratorConfigured()) {
      setError('No orchestrator configured. Visit /setup to connect your node.')
      setSending(false)
      return
    }

    try {
      const launchPlan = buildLaunchParticipants(status.peers, clampedVoices, true)
      if (launchPlan.participants.length === 0) {
        throw new Error('Set up your Ollama node first so Chorus has at least one live endpoint.')
      }

      const created = await createJob({
        context: DEFAULT_JOB_CONTEXT,
        prompt,
        agent_count: launchPlan.participants.length,
        rounds,
        payout: bounty,
        embedding_model_version: 'live-consensus',
      })

      await registerJobAgents(created.job_id, {
        slots: Object.fromEntries(
          launchPlan.participants.map((participant) => [
            participant.peer.peer_id,
            {
              completion_base_url: participant.completionBaseUrl,
              external_participant_id: participant.peer.peer_id,
            },
          ]),
        ),
      })

      writeSimulationSession({
        prompt,
        agentCount: launchPlan.participants.length,
        rounds,
        bounty,
        jobId: created.job_id,
        mode: 'backend',
        createdAt: new Date().toISOString(),
        launchedPeers: launchPlan.participants.map((participant) => participant.peer),
      })
      setDraft('')
      router.push(`/app?job_id=${encodeURIComponent(created.job_id)}`)
    } catch (launchError) {
      setError(launchError instanceof Error ? launchError.message : 'Launch failed')
    } finally {
      setSending(false)
    }
  }, [bounty, clampedVoices, draft, rounds, router, sending, status.peers])

  const hasTurns = turns.length > 0
  const openNetwork = useCallback(() => router.push('/app'), [router])

  const bottomHint = useMemo(() => {
    if (status.mode === 'unconfigured') {
      return 'No orchestrator set — run setup to connect your node.'
    }
    if (status.mode === 'offline') {
      return 'Orchestrator unreachable — check the host is running.'
    }
    if (readyPeerCount === 0) {
      return 'Set up your Ollama node before launching a run.'
    }
    return null
  }, [readyPeerCount, status])

  return (
    <div className="flex h-[100dvh] w-[100vw] overflow-hidden" style={{ background: '#0a0a0c' }}>
      <AmbientGlow />
      <ChorusSidebar
        onNewChat={newChat}
        onSelectChat={selectChat}
        activeId={activeChatId ?? undefined}
      />

      <div className="relative flex-1 flex flex-col min-w-0">
        <ChorusTopBar title={title} status={status} onNewChat={newChat} />

        {error && (
          <div
            className="mx-auto mt-3 rounded-lg px-3 py-2 font-mono text-[11px] text-red-200/90"
            style={{ background: 'rgba(160,40,40,0.14)', border: '1px solid rgba(255,120,120,0.22)' }}
          >
            {error}
          </div>
        )}

        {hasTurns ? (
          <ChorusChatStream turns={turns} />
        ) : (
          <ChorusWelcome status={status} onPick={(p) => setDraft(p)} />
        )}

        <div className="shrink-0 px-4 pb-5 pt-2">
          <div className="mx-auto max-w-3xl w-full">
            <LaunchControls
              rounds={rounds}
              bounty={bounty}
              onRoundsChange={setRounds}
              onBountyChange={setBounty}
            />
            {bottomHint && (
              <div className="mb-2 font-mono text-[10.5px] text-white/55 text-center">
                {bottomHint} · <button className="underline" onClick={openNetwork}>open network</button>
              </div>
            )}
            <ChorusComposer
              value={draft}
              onChange={setDraft}
              onSubmit={send}
              disabled={sending}
              voices={clampedVoices}
              onVoicesChange={setVoices}
              status={status}
              maxVoices={maxVoices}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

function LaunchControls({
  rounds,
  bounty,
  onRoundsChange,
  onBountyChange,
}: {
  rounds: number
  bounty: number
  onRoundsChange: (value: number) => void
  onBountyChange: (value: number) => void
}) {
  return (
    <div
      className="mb-3 grid gap-2 md:grid-cols-[1fr_1fr_auto]"
      style={{
        background: 'rgba(255,255,255,0.025)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 14,
        padding: '10px 12px',
      }}
    >
      <SliderControl
        label="Rounds"
        value={rounds}
        min={1}
        max={5}
        step={1}
        display={`${rounds}`}
        onChange={onRoundsChange}
      />
      <SliderControl
        label="Bounty"
        value={bounty}
        min={0.1}
        max={1}
        step={0.05}
        display={`$${bounty.toFixed(2)}`}
        onChange={onBountyChange}
      />
      <div
        className="h-fit self-end rounded-xl px-3 py-2"
        style={{
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/75">
          Swarm
        </div>
        <div className="font-sans text-[12px] text-white/55">
          Multi-round persona chorus
        </div>
      </div>
    </div>
  )
}

function SliderControl({
  label,
  value,
  min,
  max,
  step,
  display,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  display: string
  onChange: (value: number) => void
}) {
  const pct = ((value - min) / (max - min)) * 100
  return (
    <label className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/55">{label}</span>
        <span className="font-mono text-[11px] text-white/85">{display}</span>
      </div>
      <div className="relative h-1.5 overflow-hidden rounded-full bg-white/8">
        <div
          className="absolute left-0 top-0 h-full rounded-full bg-white/85"
          style={{ width: `${pct}%` }}
        />
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        />
      </div>
    </label>
  )
}

function AmbientGlow() {
  return (
    <div aria-hidden className="fixed inset-0 pointer-events-none z-0" style={{ opacity: 0.8 }}>
      <div
        style={{
          position: 'absolute',
          top: '-10%',
          left: '35%',
          width: 680,
          height: 680,
          background:
            'radial-gradient(closest-side, rgba(140,170,255,0.18), rgba(0,0,0,0) 70%)',
          filter: 'blur(24px)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          bottom: '-18%',
          right: '-6%',
          width: 720,
          height: 720,
          background:
            'radial-gradient(closest-side, rgba(200,170,255,0.14), rgba(0,0,0,0) 70%)',
          filter: 'blur(28px)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage:
            'radial-gradient(rgba(255,255,255,0.04) 1px, transparent 1px)',
          backgroundSize: '26px 26px',
          maskImage:
            'radial-gradient(ellipse at 50% 30%, black 40%, transparent 85%)',
        }}
      />
    </div>
  )
}
