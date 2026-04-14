'use client'

import { useCallback, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ChorusSidebar } from './sidebar'
import { ChorusTopBar } from './top-bar'
import { ChorusComposer } from './composer'
import { ChorusWelcome } from './welcome'
import { ChorusChatStream, type AgentResponse, type ChatTurn } from './chat-stream'
import { useNetworkStatus } from '@/hooks/use-network-status'
import {
  createBroadcastPlan,
  getOrCreatePeerId,
  invokeBroadcastCompletions,
  isOrchestratorConfigured,
  openSignalingSocket,
} from '@/lib/api/orchestrator'
import { writeSimulationSession } from '@/lib/runtime/session'

function uid() {
  return Math.random().toString(36).slice(2, 10)
}

const MOCK_FRAGMENTS = [
  'From a systems view, the main risk is coupling the write path.',
  'Disagree — the real bottleneck is the storage migration window.',
  'I would split it: ship the read path first, then the writer behind a flag.',
  'Cheaper option: keep it on the existing stack for another quarter.',
  'Consider the compliance angle — session persistence changes the blast radius.',
  'Run a shadow trial against prod traffic before full rollout.',
]

export function ChorusAppShell() {
  const router = useRouter()
  const status = useNetworkStatus(4000)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  const [voices, setVoices] = useState(3)
  const [sending, setSending] = useState(false)
  const [title, setTitle] = useState('New conversation')
  const [error, setError] = useState<string | null>(null)
  const cancelRef = useRef(false)

  const clampedVoices = Math.min(Math.max(1, voices), Math.max(1, status.online))

  const newChat = useCallback(() => {
    setTurns([])
    setDraft('')
    setTitle('New conversation')
    setError(null)
  }, [])

  const send = useCallback(async () => {
    if (!draft.trim() || sending || status.online < 1) return
    const prompt = draft.trim()
    setDraft('')
    setError(null)
    setSending(true)
    cancelRef.current = false

    if (title === 'New conversation') {
      setTitle(prompt.slice(0, 60))
    }

    const userTurnId = `u-${uid()}`
    const chorusTurnId = `a-${uid()}`
    const selected = status.peers.slice(0, clampedVoices)

    setTurns((t) => [
      ...t,
      {
        id: userTurnId,
        role: 'user',
        text: prompt,
        createdAt: Date.now(),
      },
      {
        id: chorusTurnId,
        role: 'chorus',
        voicesRequested: clampedVoices,
        responses: selected.map<AgentResponse>((p) => ({
          peerId: p.peer_id,
          model: p.model,
          text: '',
          status: 'thinking',
        })),
        createdAt: Date.now(),
      },
    ])

    if (status.mode !== 'live' || !isOrchestratorConfigured()) {
      await simulateChorus(chorusTurnId, prompt, selected, setTurns, cancelRef)
      setSending(false)
      return
    }

    try {
      const plan = await createBroadcastPlan({ prompt, timeout_ms: 15000 })
      await new Promise<void>((resolve, reject) => {
        const to = setTimeout(() => {
          ws?.close()
          reject(new Error('Broadcast handshake timed out'))
        }, 10_000)
        let settled = false
        const ws = openSignalingSocket(getOrCreatePeerId(), 'web-prompter', {
          onEvent: (event) => {
            if (event.type === 'registered') {
              ws.send(
                JSON.stringify({
                  type: 'broadcast_job',
                  job_id: plan.job_id,
                  prompt,
                  timeout_ms: plan.timeout_ms,
                  target_peer_ids: plan.target_peer_ids,
                }),
              )
            } else if (event.type === 'broadcast_started') {
              if (settled) return
              settled = true
              clearTimeout(to)
              ws.close()
              if (!event.ok) return reject(new Error(event.error ?? 'Broadcast failed'))
              invokeBroadcastCompletions({
                job_id: plan.job_id,
                prompt,
                timeout_ms: plan.timeout_ms,
                target_peer_ids: plan.target_peer_ids,
              }).catch(() => undefined).finally(resolve)
            } else if (event.type === 'error' && !settled) {
              settled = true
              clearTimeout(to)
              ws.close()
              reject(new Error(event.detail ?? event.error))
            }
          },
          onError: () => {
            if (settled) return
            settled = true
            clearTimeout(to)
            reject(new Error('Failed to connect signaling socket'))
          },
        })
      })

      writeSimulationSession({
        prompt,
        agentCount: clampedVoices,
        rounds: 1,
        bounty: 0.1,
        jobId: plan.job_id,
        mode: 'backend',
        createdAt: new Date().toISOString(),
      })

      await simulateChorus(chorusTurnId, prompt, selected, setTurns, cancelRef)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Launch failed')
      setTurns((ts) =>
        ts.map((t) =>
          t.id === chorusTurnId
            ? { ...t, consensus: 'Launch failed — see banner above.' }
            : t,
        ),
      )
    } finally {
      setSending(false)
    }
  }, [draft, sending, status.online, status.mode, status.peers, clampedVoices, title])

  const hasTurns = turns.length > 0

  const openNetwork = useCallback(() => router.push('/app'), [router])

  const bottomHint = useMemo(() => {
    if (status.online === 0 && status.mode === 'live') {
      return 'No peers online — ask someone to join via /join.'
    }
    return null
  }, [status])

  return (
    <div className="flex h-[100dvh] w-[100vw] overflow-hidden" style={{ background: '#0a0a0c' }}>
      <AmbientGlow />
      <ChorusSidebar onNewChat={newChat} />

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
            />
          </div>
        </div>
      </div>
    </div>
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

async function simulateChorus(
  turnId: string,
  prompt: string,
  peers: { peer_id: string; model: string }[],
  setTurns: React.Dispatch<React.SetStateAction<ChatTurn[]>>,
  cancelRef: React.MutableRefObject<boolean>,
) {
  await sleep(250)

  // Phase 1: stream fragments into each voice at jittered timings.
  await Promise.all(
    peers.map(async (p, i) => {
      if (cancelRef.current) return
      await sleep(200 + Math.random() * 600)
      const line =
        MOCK_FRAGMENTS[(i + Math.floor(Math.random() * MOCK_FRAGMENTS.length)) % MOCK_FRAGMENTS.length]
      const full = line + ' ' + softEcho(prompt)
      const latency = 300 + Math.floor(Math.random() * 1400)

      // Stream character by character
      let text = ''
      for (const ch of full) {
        if (cancelRef.current) return
        text += ch
        setTurns((ts) =>
          ts.map((t) => {
            if (t.id !== turnId || !t.responses) return t
            const next = t.responses.map((r) =>
              r.peerId === p.peer_id
                ? { ...r, text, status: 'streaming' as const }
                : r,
            )
            return { ...t, responses: next }
          }),
        )
        await sleep(8 + Math.random() * 18)
      }

      setTurns((ts) =>
        ts.map((t) => {
          if (t.id !== turnId || !t.responses) return t
          const next = t.responses.map((r) =>
            r.peerId === p.peer_id
              ? { ...r, latencyMs: latency, status: 'done' as const }
              : r,
          )
          return { ...t, responses: next }
        }),
      )
    }),
  )

  if (cancelRef.current) return

  await sleep(400)

  const consensus =
    peers.length > 1
      ? `Consensus across ${peers.length} voices: ship the read path first behind a flag, defer the write migration until a shadow trial confirms parity. Agreement on risk ordering; remaining tension is on timing.`
      : `Single voice response: proceed with the lowest-risk option — split the rollout and monitor.`

  setTurns((ts) =>
    ts.map((t) => (t.id === turnId ? { ...t, consensus } : t)),
  )
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}

function softEcho(prompt: string) {
  const trimmed = prompt.replace(/\s+/g, ' ').trim()
  if (trimmed.length < 70) return `[re: "${trimmed}"]`
  return `[re: "${trimmed.slice(0, 60)}…"]`
}
