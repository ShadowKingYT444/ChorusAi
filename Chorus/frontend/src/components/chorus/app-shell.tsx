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
  getJobResponses,
  getOrCreatePeerId,
  invokeBroadcastCompletions,
  isOrchestratorConfigured,
  openSignalingSocket,
  type JobResponseRow,
} from '@/lib/api/orchestrator'
import { writeSimulationSession } from '@/lib/runtime/session'
import {
  getChat,
  upsertChat,
  type ChatRecord,
} from '@/lib/runtime/chat-history'

function uid() {
  return Math.random().toString(36).slice(2, 10)
}

export function ChorusAppShell() {
  const router = useRouter()
  const status = useNetworkStatus(4000)
  const [activeChatId, setActiveChatId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [draft, setDraft] = useState('')
  const [voices, setVoices] = useState(3)
  const [sending, setSending] = useState(false)
  const [title, setTitle] = useState('New conversation')
  const [error, setError] = useState<string | null>(null)
  const cancelRef = useRef(false)

  const clampedVoices = Math.min(Math.max(1, voices), Math.max(1, status.online))

  const persist = useCallback(
    (id: string, nextTurns: ChatTurn[], nextTitle: string, nextVoices: number) => {
      const existing = getChat(id)
      const record: ChatRecord = {
        id,
        title: nextTitle,
        turns: nextTurns,
        voices: nextVoices,
        createdAt: existing?.createdAt ?? Date.now(),
        updatedAt: Date.now(),
      }
      upsertChat(record)
    },
    [],
  )

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
    if (!draft.trim() || sending || status.online < 1) return
    if (!isOrchestratorConfigured()) {
      setError('No orchestrator configured. Visit /setup or /join to connect.')
      return
    }
    const prompt = draft.trim()
    setDraft('')
    setError(null)
    setSending(true)
    cancelRef.current = false

    const chatId = activeChatId ?? `chat-${uid()}`
    if (!activeChatId) setActiveChatId(chatId)

    const nextTitle = title === 'New conversation' ? prompt.slice(0, 60) : title
    if (title === 'New conversation') setTitle(nextTitle)

    const userTurnId = `u-${uid()}`
    const chorusTurnId = `a-${uid()}`
    const selected = status.peers.slice(0, clampedVoices)

    const committedTurns: ChatTurn[] = [
      ...turns,
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
    ]
    setTurns(committedTurns)
    persist(chatId, committedTurns, nextTitle, clampedVoices)

    let finalTurns = committedTurns

    try {
      const plan = await createBroadcastPlan({ prompt, timeout_ms: 30_000 })

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

      finalTurns = await collectJobResponses({
        jobId: plan.job_id,
        chorusTurnId,
        selectedPeerIds: selected.map((p) => p.peer_id),
        startingTurns: committedTurns,
        setTurns,
        cancelRef,
        onPersist: (ts) => persist(chatId, ts, nextTitle, clampedVoices),
      })
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Launch failed'
      setError(message)
      const failed = finalTurns.map((t) =>
        t.id === chorusTurnId ? { ...t, consensus: `Launch failed — ${message}` } : t,
      )
      setTurns(failed)
      persist(chatId, failed, nextTitle, clampedVoices)
    } finally {
      setSending(false)
    }
  }, [
    draft,
    sending,
    status.online,
    status.peers,
    clampedVoices,
    title,
    turns,
    activeChatId,
    persist,
  ])

  const hasTurns = turns.length > 0

  const openNetwork = useCallback(() => router.push('/app'), [router])

  const bottomHint = useMemo(() => {
    if (status.mode === 'unconfigured') {
      return 'No orchestrator set — share a host URL via /join or /setup to connect.'
    }
    if (status.mode === 'offline') {
      return 'Orchestrator unreachable — check the host is running.'
    }
    if (status.online === 0) {
      return 'No peers online — ask someone to join via /join.'
    }
    return null
  }, [status])

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

async function collectJobResponses(opts: {
  jobId: string
  chorusTurnId: string
  selectedPeerIds: string[]
  startingTurns: ChatTurn[]
  setTurns: React.Dispatch<React.SetStateAction<ChatTurn[]>>
  cancelRef: React.MutableRefObject<boolean>
  onPersist: (ts: ChatTurn[]) => void
}): Promise<ChatTurn[]> {
  const {
    jobId,
    chorusTurnId,
    selectedPeerIds,
    startingTurns,
    setTurns,
    cancelRef,
    onPersist,
  } = opts

  let current = startingTurns
  const seen = new Set<string>()
  const targetCount = selectedPeerIds.length
  let gotCount = 0

  const applyRow = (row: JobResponseRow) => {
    const peerKey = `${row.peer_id}#${row.instance_id ?? ''}`
    if (seen.has(peerKey)) return
    const text = (row.text ?? '').trim()
    const errMsg = row.error?.trim()
    if (!text && !errMsg) return
    seen.add(peerKey)
    gotCount++
    current = current.map((t) => {
      if (t.id !== chorusTurnId || !t.responses) return t
      const idx = t.responses.findIndex(
        (r) => r.peerId === row.peer_id && r.status !== 'done' && r.status !== 'error',
      )
      if (idx < 0) {
        return {
          ...t,
          responses: [
            ...t.responses,
            {
              peerId: row.peer_id,
              model: row.model ?? 'unknown',
              text: text || errMsg || '',
              latencyMs: row.latency_ms ?? undefined,
              status: errMsg && !text ? 'error' : 'done',
            },
          ],
        }
      }
      const next = [...t.responses]
      next[idx] = {
        ...next[idx],
        model: row.model ?? next[idx].model,
        text: text || errMsg || '',
        latencyMs: row.latency_ms ?? undefined,
        status: errMsg && !text ? 'error' : 'done',
      }
      return { ...t, responses: next }
    })
    setTurns(current)
    onPersist(current)
  }

  const deadline = Date.now() + 60_000
  while (Date.now() < deadline && gotCount < targetCount && !cancelRef.current) {
    try {
      const { responses } = await getJobResponses(jobId)
      for (const row of responses) applyRow(row)
      if (gotCount >= targetCount) break
    } catch {
      /* keep polling */
    }
    await sleep(1500)
  }

  // Finalize any still-pending responses as timed out.
  current = current.map((t) => {
    if (t.id !== chorusTurnId || !t.responses) return t
    const next = t.responses.map((r) =>
      r.status === 'thinking' || r.status === 'streaming'
        ? { ...r, status: 'error' as const, text: r.text || 'No response (timed out)' }
        : r,
    )
    const donePeers = next.filter((r) => r.status === 'done').length
    const consensus =
      donePeers === 0
        ? 'No peer returned a response in time.'
        : donePeers === 1
        ? 'Single voice responded — see above.'
        : `Consensus across ${donePeers} voice${donePeers === 1 ? '' : 's'}.`
    return { ...t, responses: next, consensus }
  })
  setTurns(current)
  onPersist(current)

  return current
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}

