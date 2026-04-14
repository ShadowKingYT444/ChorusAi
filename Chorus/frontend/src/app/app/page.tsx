'use client'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import { motion, AnimatePresence } from 'framer-motion'
import { TopBar } from '@/components/top-bar'
import { AGENT_MESSAGES, SIMULATION_RESULTS, type ClusterID } from '@/lib/mock-data'
import { useSharedJobRuntime } from '@/lib/runtime/job-runtime-provider'
import { mockSimElapsedMs } from '@/lib/runtime/mock-sim-clock'
import { isOrchestratorConfigured } from '@/lib/api/orchestrator'
import { useEffect, useState } from 'react'

const SANS = 'var(--font-geist-sans)'
const MONO = 'var(--font-geist-mono)'

// Skeletal loader for the 3D graph — matches the canvas layout without generic spinner text
function NetworkSkeleton() {
  return (
    <div style={{
      width: '100%', height: 'calc(100dvh - 48px)',
      background: '#000', position: 'relative', overflow: 'hidden',
    }}>
      {/* Simulated node blobs */}
      {[
        { w: 6, h: 6, top: '38%', left: '42%', opacity: 0.18 },
        { w: 4, h: 4, top: '28%', left: '58%', opacity: 0.12 },
        { w: 5, h: 5, top: '55%', left: '35%', opacity: 0.10 },
        { w: 4, h: 4, top: '45%', left: '62%', opacity: 0.12 },
        { w: 3, h: 3, top: '32%', left: '30%', opacity: 0.09 },
        { w: 3, h: 3, top: '60%', left: '55%', opacity: 0.09 },
      ].map((n, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            top: n.top, left: n.left,
            width: n.w * 4, height: n.h * 4,
            borderRadius: '50%',
            background: `rgba(255,255,255,${n.opacity})`,
            animation: 'shimmer 2s ease-in-out infinite',
            animationDelay: `${i * 0.18}s`,
          }}
        />
      ))}
      {/* Status label */}
      <div style={{
        position: 'absolute', bottom: 28, left: '50%', transform: 'translateX(-50%)',
        fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.25)',
        letterSpacing: '0.18em', textTransform: 'uppercase',
      }}>
        Initialising network
      </div>
    </div>
  )
}

const NodeGraph3D = dynamic(() => import('@/components/node-graph-3d'), {
  ssr: false,
  loading: () => <NetworkSkeleton />,
})

function NetworkPageContent() {
  const router = useRouter()
  const runtime = useSharedJobRuntime()
  const useBackend = isOrchestratorConfigured() && runtime.session?.mode === 'backend'
  const mockSession = !useBackend && runtime.session?.mode === 'mock' ? runtime.session : null
  const [mockElapsed, setMockElapsed] = useState(0)

  useEffect(() => {
    if (!mockSession) return
    const tick = () => setMockElapsed(mockSimElapsedMs(mockSession.createdAt))
    tick()
    const id = setInterval(tick, 400)
    return () => clearInterval(id)
  }, [mockSession?.createdAt, mockSession])

  const mockRound = !useBackend && mockSession
    ? mockElapsed < 5000 ? 1 : mockElapsed < 10000 ? 2 : 3
    : 1
  const mockDone = Boolean(!useBackend && mockSession && mockElapsed >= 17000)
  const mockMessageIndex = !useBackend && mockSession
    ? Math.min(Math.floor(mockElapsed / 800), AGENT_MESSAGES.length)
    : 0

  const round = useBackend ? runtime.currentRound : mockRound
  const totalRounds = useBackend ? runtime.totalRounds : SIMULATION_RESULTS.rounds
  const nodesContributing = useBackend
    ? runtime.results.nodesContributing
    : SIMULATION_RESULTS.nodesContributing
  const visibleMessages = useBackend ? runtime.messages : AGENT_MESSAGES.slice(0, mockMessageIndex)
  const simulationDone = useBackend ? runtime.status === 'completed' : mockDone

  const feedPreview = visibleMessages.slice(-3).map(m => ({
    agentId: m.agentId,
    text: m.text,
    clusterId: m.clusterId as ClusterID,
  }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100dvh', background: '#000', overflow: 'hidden', position: 'relative' }}>
      <TopBar />
      <NodeGraph3D
        round={round}
        totalRounds={totalRounds}
        nodesContributing={nodesContributing}
        feedPreview={feedPreview}
        onOpenFeed={() => {
          const suffix = runtime.session?.jobId ? `?job_id=${encodeURIComponent(runtime.session.jobId)}` : ''
          router.push(`/app/feed${suffix}`)
        }}
      />

      {/* Manual "View Results" button — appears when simulation completes */}
      <AnimatePresence>
        {simulationDone && (
          <motion.div
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            transition={{ type: 'spring', stiffness: 220, damping: 26 }}
            style={{
              position: 'absolute',
              bottom: 28,
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 40,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 8,
              pointerEvents: 'auto',
            }}
          >
            <span style={{ fontFamily: MONO, fontSize: 9, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.18em' }}>
              SIMULATION COMPLETE
            </span>
            <motion.button
              onClick={() => {
                const suffix = runtime.session?.jobId ? `?job_id=${encodeURIComponent(runtime.session.jobId)}` : ''
                router.push(`/app/results${suffix}`)
              }}
              whileHover={{ scale: 1.03, boxShadow: '0 0 48px 14px rgba(255,255,255,0.22)' }}
              whileTap={{ scale: 0.97 }}
              transition={{ type: 'spring', stiffness: 400, damping: 22 }}
              style={{
                fontFamily: MONO,
                fontSize: 11,
                letterSpacing: '0.14em',
                color: '#000',
                background: 'rgba(255,255,255,0.92)',
                border: 'none',
                borderRadius: 2,
                padding: '10px 28px',
                cursor: 'pointer',
                boxShadow: '0 0 32px 8px rgba(255,255,255,0.12)',
              }}
            >
              VIEW RESULTS →
            </motion.button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default function NetworkPage() {
  return <NetworkPageContent />
}
