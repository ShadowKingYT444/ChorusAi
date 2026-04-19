'use client'

import { motion, AnimatePresence } from 'framer-motion'
import { useEffect, useRef } from 'react'
import { Cpu, User2 } from 'lucide-react'

export interface AgentResponse {
  peerId: string
  model: string
  text: string
  latencyMs?: number
  status: 'thinking' | 'streaming' | 'done' | 'error'
}

export interface ChorusRoundState {
  round: number
  responses: AgentResponse[]
}

export interface ChatTurn {
  id: string
  role: 'user' | 'chorus'
  text?: string
  voicesRequested?: number
  responses?: AgentResponse[]
  roundStates?: ChorusRoundState[]
  consensus?: string
  currentRound?: number
  totalRounds?: number
  createdAt: number
}

const AGENT_COLORS = [
  'rgba(180,200,255,0.85)',
  'rgba(200,180,255,0.85)',
  'rgba(160,220,200,0.85)',
  'rgba(255,200,170,0.85)',
  'rgba(230,180,220,0.85)',
  'rgba(180,230,170,0.85)',
]

function hashColor(peerId: string) {
  let h = 0
  for (let i = 0; i < peerId.length; i++) h = (h * 31 + peerId.charCodeAt(i)) >>> 0
  return AGENT_COLORS[h % AGENT_COLORS.length]
}

function getRoundStates(turn: ChatTurn): ChorusRoundState[] {
  if (turn.roundStates?.length) {
    return [...turn.roundStates]
      .sort((a, b) => a.round - b.round)
      .map((roundState) => ({
        round: roundState.round,
        responses: roundState.responses ?? [],
      }))
  }

  return [
    {
      round: Math.max(1, turn.currentRound ?? 1),
      responses: turn.responses ?? [],
    },
  ]
}

export function ChorusChatStream({ turns }: { turns: ChatTurn[] }) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl w-full px-6 py-8 flex flex-col gap-8">
        {turns.map((turn) => (
          <div key={turn.id} className="flex flex-col gap-2.5">
            {turn.role === 'user' ? (
              <UserTurn text={turn.text ?? ''} />
            ) : (
              <ChorusTurn turn={turn} />
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

function UserTurn({ text }: { text: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className="self-end max-w-[85%]"
    >
      <div
        className="flex items-start gap-2.5 flex-row-reverse"
      >
        <div
          className="w-7 h-7 rounded-full grid place-items-center shrink-0"
          style={{
            background: 'linear-gradient(135deg, rgba(180,200,255,0.35), rgba(200,180,255,0.18))',
            border: '1px solid rgba(255,255,255,0.1)',
          }}
        >
          <User2 className="w-3.5 h-3.5 text-white/85" />
        </div>
        <div
          className="px-4 py-2.5 rounded-2xl rounded-tr-sm"
          style={{
            background: 'rgba(255,255,255,0.06)',
            border: '1px solid rgba(255,255,255,0.08)',
          }}
        >
          <p className="font-sans text-[14px] text-white/95 leading-relaxed whitespace-pre-wrap">
            {text}
          </p>
        </div>
      </div>
    </motion.div>
  )
}

function ChorusTurn({ turn }: { turn: ChatTurn }) {
  const roundStates = getRoundStates(turn)
  const currentRound = turn.currentRound ?? roundStates.at(-1)?.round ?? 1
  const activeRound =
    roundStates.find((roundState) => roundState.round === currentRound) ??
    roundStates.at(-1) ?? {
      round: currentRound,
      responses: [],
    }
  const responses = activeRound.responses
  const total = turn.voicesRequested ?? responses.length
  const replied = responses.filter(
    (response) => response.text.trim().length > 0 || response.latencyMs != null,
  ).length
  const totalRounds = Math.max(currentRound, turn.totalRounds ?? currentRound)
  const progressPct = totalRounds > 0 ? (currentRound / totalRounds) * 100 : 0

  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="w-full">
      {/* Status header */}
      <div className="flex items-center gap-2 mb-2 text-white/55">
        <span
          className="w-5 h-5 rounded-md grid place-items-center"
          style={{
            background: 'linear-gradient(135deg, rgba(180,200,255,0.35), rgba(255,255,255,0.05))',
            border: '1px solid rgba(255,255,255,0.1)',
          }}
        >
          <Cpu className="w-3 h-3 text-white/85" />
        </span>
        <span className="font-mono text-[10.5px] tracking-[0.08em] uppercase">
          Chorus · {replied}/{total} replied
        </span>
        <span className="font-mono text-[10px] text-white/40 tracking-[0.08em] uppercase">
          Round {currentRound}/{totalRounds}
        </span>
        {replied < total && (
          <motion.span
            className="w-1.5 h-1.5 rounded-full bg-white/60"
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1.4, repeat: Infinity }}
          />
        )}
      </div>

      <div
        className="mb-3 h-1.5 overflow-hidden rounded-full"
        style={{ background: 'rgba(255,255,255,0.06)' }}
      >
        <motion.div
          className="h-full rounded-full"
          animate={{ width: `${progressPct}%` }}
          transition={{ type: 'spring', stiffness: 220, damping: 26 }}
          style={{
            background:
              'linear-gradient(90deg, rgba(180,200,255,0.45), rgba(255,255,255,0.9))',
            boxShadow: '0 0 14px rgba(180,200,255,0.25)',
          }}
        />
      </div>

      {/* Voice cards */}
      <div className="flex flex-col gap-3">
        {roundStates.map((roundState) => {
          const roundReplied = roundState.responses.filter(
            (response) => response.text.trim().length > 0 || response.latencyMs != null,
          ).length
          const isActiveRound = roundState.round === currentRound
          const pendingCount = isActiveRound ? Math.max(0, total - roundState.responses.length) : 0

          return (
            <div
              key={`round-${roundState.round}`}
              className="rounded-2xl px-3.5 py-3"
              style={{
                background: 'rgba(255,255,255,0.025)',
                border: '1px solid rgba(255,255,255,0.06)',
              }}
            >
              <div className="mb-3 flex items-center justify-between gap-2">
                <span className="font-mono text-[10px] text-white/60 tracking-[0.12em] uppercase">
                  Round {roundState.round}
                </span>
                <span className="font-mono text-[10px] text-white/40 tracking-[0.08em] uppercase">
                  {roundReplied}/{total} replied
                </span>
              </div>

              <div className="grid gap-2 md:grid-cols-2">
                <AnimatePresence initial={false}>
                  {roundState.responses.map((response, index) => (
                    <VoiceCard
                      key={`${roundState.round}-${response.peerId}-${index}`}
                      response={response}
                    />
                  ))}
                  {Array.from({ length: pendingCount }).map((_, index) => (
                    <motion.div
                      key={`pending-${roundState.round}-${index}`}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="rounded-xl px-3.5 py-3 h-[76px] flex items-center gap-2"
                      style={{
                        background: 'rgba(255,255,255,0.02)',
                        border: '1px dashed rgba(255,255,255,0.08)',
                      }}
                    >
                      <motion.span
                        className="w-2 h-2 rounded-full bg-white/45"
                        animate={{ scale: [1, 1.4, 1], opacity: [0.4, 1, 0.4] }}
                        transition={{ duration: 1.2, repeat: Infinity, delay: index * 0.12 }}
                      />
                      <span className="font-mono text-[10.5px] text-white/35 tracking-[0.08em] uppercase">
                        awaiting voice…
                      </span>
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            </div>
          )
        })}
      </div>

      {/* Consensus */}
      {turn.consensus && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="mt-4 rounded-2xl px-4 py-3.5"
          style={{
            background:
              'linear-gradient(135deg, rgba(180,200,255,0.10), rgba(255,255,255,0.03))',
            border: '1px solid rgba(180,200,255,0.22)',
            boxShadow: '0 14px 40px -14px rgba(180,200,255,0.32)',
          }}
        >
          <div className="flex items-center gap-2 mb-1.5">
            <span className="font-mono text-[10px] text-white/70 tracking-[0.12em] uppercase">
              Consensus
            </span>
          </div>
          <p className="font-sans text-[14px] text-white/95 leading-relaxed whitespace-pre-wrap">
            {turn.consensus}
          </p>
        </motion.div>
      )}
    </motion.div>
  )
}

function VoiceCard({ response }: { response: AgentResponse }) {
  const color = hashColor(response.peerId)
  const shortId = response.peerId.slice(0, 10)

  return (
    <motion.div
      initial={{ opacity: 0, y: 6, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: 'spring', stiffness: 260, damping: 24 }}
      className="rounded-xl px-3.5 py-3"
      style={{
        background: 'rgba(255,255,255,0.025)',
        border: '1px solid rgba(255,255,255,0.07)',
      }}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span
          className="w-1.5 h-1.5 rounded-full"
          style={{ background: color, boxShadow: `0 0 8px ${color}` }}
        />
        <span className="font-mono text-[10.5px] text-white/80 tabular-nums">{shortId}</span>
        <span className="font-mono text-[9.5px] text-white/35">· {response.model}</span>
        {response.latencyMs != null && (
          <span className="ml-auto font-mono text-[9.5px] text-white/45 tabular-nums">
            {response.latencyMs}ms
          </span>
        )}
      </div>
      <p className="font-sans text-[13px] text-white/85 leading-relaxed whitespace-pre-wrap">
        {response.text || (
          <span className="text-white/45 italic">thinking…</span>
        )}
        {response.status === 'streaming' && (
          <motion.span
            className="inline-block w-[6px] h-[14px] ml-0.5 align-middle bg-white/70"
            animate={{ opacity: [0, 1, 0] }}
            transition={{ duration: 0.9, repeat: Infinity }}
          />
        )}
      </p>
    </motion.div>
  )
}
