'use client'

import { useCallback, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ChorusSidebar } from './sidebar'
import { ChorusTopBar } from './top-bar'
import { ChorusComposer } from './composer'
import { ChorusWelcome } from './welcome'
import { ChorusChatStream, type ChatTurn } from './chat-stream'
import { useNetworkStatus } from '@/hooks/use-network-status'
import {
  createJob,
  getPeers,
  isOrchestratorConfigured,
  registerJobAgents,
  type PeerEntry,
} from '@/lib/api/orchestrator'
import { writeSimulationSession } from '@/lib/runtime/session'
import { getChat } from '@/lib/runtime/chat-history'

const DEFAULT_JOB_CONTEXT =
  'You are participating in a distributed Chorus debate. Answer directly, add concrete reasoning, and adapt when peer context appears in later rounds.'
const DEMO_FILL_MAX_VOICES = 8
const SYNTHETIC_MODEL_POOL = [
  'qwen2.5:0.5b',
  'llama3.2:1b',
  'gemma2:2b',
  'phi3:mini',
  'mistral-nemo',
]
const SYNTHETIC_NAME_POOL = [
  'skeptic',
  'optimist',
  'analyst',
  'contrarian',
  'operator',
  'planner',
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
    completionBaseUrl: `demo://${peerId}`,
  }
}

function buildLaunchParticipants(
  peers: PeerEntry[],
  requestedVoices: number,
  demoAssist: boolean,
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

  const liveTarget = demoAssist
    ? Math.min(requestedVoices, Math.min(1, addressedPeers.length))
    : Math.min(requestedVoices, addressedPeers.length)

  const live = addressedPeers.slice(0, liveTarget).map((peer) => ({
    peer,
    completionBaseUrl: peer.address?.trim() ?? '',
  }))

  if (!demoAssist && live.length < requestedVoices) {
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
  const [demoAssist, setDemoAssist] = useState(true)
  const [sending, setSending] = useState(false)
  const [title, setTitle] = useState('New conversation')
  const [error, setError] = useState<string | null>(null)

  const readyPeerCount = status.peers.filter((peer) => Boolean(peer.address?.trim())).length
  const maxVoices = demoAssist ? Math.max(status.online, DEMO_FILL_MAX_VOICES) : Math.max(1, readyPeerCount)
  const clampedVoices = Math.min(Math.max(1, voices), maxVoices)

  const newChat = useCallback(() => {
    setActiveChatId(null)
    setTurns([])
    setDraft('')
    setTitle('New conversation')
    setError(null)
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
  }, [])

  const send = useCallback(async () => {
    if (!draft.trim() || sending) return
    if (!isOrchestratorConfigured()) {
      setError('No orchestrator configured. Visit /setup or /join to connect.')
      return
    }

    const prompt = draft.trim()
    setError(null)
    setSending(true)

    try {
      const peerSnapshot = await getPeers()
      const launchPlan = buildLaunchParticipants(peerSnapshot.peers, clampedVoices, demoAssist)

      if (!demoAssist && launchPlan.liveCount < clampedVoices) {
        throw new Error(
          `Requested ${clampedVoices} live peers, but only ${launchPlan.liveCount} peer${launchPlan.liveCount === 1 ? '' : 's'} currently expose a public model URL.`,
        )
      }
      if (launchPlan.participants.length === 0) {
        throw new Error('No launch participants available.')
      }

      const created = await createJob({
        context: DEFAULT_JOB_CONTEXT,
        prompt,
        agent_count: launchPlan.participants.length,
        rounds,
        payout: bounty,
        embedding_model_version:
          launchPlan.syntheticCount > 0
            ? `hybrid-demo:${launchPlan.liveCount}-live:${launchPlan.syntheticCount}-synthetic`
            : 'live-consensus',
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
  }, [bounty, clampedVoices, demoAssist, draft, rounds, router, sending])

  const hasTurns = turns.length > 0
  const openNetwork = useCallback(() => router.push('/app'), [router])

  const bottomHint = useMemo(() => {
    if (status.mode === 'unconfigured') {
      return 'No orchestrator set — share a host URL via /join or /setup to connect.'
    }
    if (status.mode === 'offline') {
      return 'Orchestrator unreachable — check the host is running.'
    }
    if (readyPeerCount === 0 && demoAssist) {
      return 'No live peers online — demo fill will simulate the chorus.'
    }
    if (readyPeerCount === 0) {
      return 'No peers online — ask someone to join via /join.'
    }
    if (demoAssist && clampedVoices > readyPeerCount) {
      return `Launching 1 live node and ${clampedVoices - 1} simulated peers.`
    }
    return null
  }, [clampedVoices, demoAssist, readyPeerCount, status])

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
              demoAssist={demoAssist}
              onRoundsChange={setRounds}
              onBountyChange={setBounty}
              onDemoAssistChange={setDemoAssist}
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
              canSendWithoutPeers={demoAssist}
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
  demoAssist,
  onRoundsChange,
  onBountyChange,
  onDemoAssistChange,
}: {
  rounds: number
  bounty: number
  demoAssist: boolean
  onRoundsChange: (value: number) => void
  onBountyChange: (value: number) => void
  onDemoAssistChange: (value: boolean) => void
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
      <button
        type="button"
        onClick={() => onDemoAssistChange(!demoAssist)}
        className="h-fit self-end rounded-xl px-3 py-2 text-left transition-colors"
        style={{
          background: demoAssist ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.04)',
          border: '1px solid',
          borderColor: demoAssist ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.08)',
        }}
      >
        <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/75">
          Demo Fill
        </div>
        <div className="font-sans text-[12px] text-white/55">
          {demoAssist ? '1 live node + synthetic swarm' : 'live peers only'}
        </div>
      </button>
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
