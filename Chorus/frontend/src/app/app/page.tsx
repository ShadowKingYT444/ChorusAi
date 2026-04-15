'use client'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/top-bar'
import { useSharedJobRuntime } from '@/lib/runtime/job-runtime-provider'
import { useNetworkStatus } from '@/hooks/use-network-status'

const MONO = 'var(--font-geist-mono)'

function NetworkSkeleton() {
  return (
    <div style={{ width: '100%', height: 'calc(100dvh - 48px)', background: '#000', position: 'relative', overflow: 'hidden' }}>
      <div
        style={{
          position: 'absolute', bottom: 28, left: '50%', transform: 'translateX(-50%)',
          fontFamily: MONO, fontSize: 10, color: 'rgba(255,255,255,0.25)',
          letterSpacing: '0.18em', textTransform: 'uppercase',
        }}
      >
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
  const status = useNetworkStatus(4000)

  // Prefer signaling-driven peer list from runtime (subscribed via websocket);
  // fall back to polled /peers snapshot from useNetworkStatus.
  const peers = runtime.connectedPeers.length > 0 ? runtime.connectedPeers : status.peers

  const graphMessages = runtime.messages.map((m) => ({
    peerId: m.slotId.split('#')[0],
    text: m.text,
  }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100dvh', background: '#000', overflow: 'hidden', position: 'relative' }}>
      <TopBar />
      <NodeGraph3D
        peers={peers}
        messages={graphMessages}
        onOpenFeed={() => {
          const suffix = runtime.session?.jobId ? `?job_id=${encodeURIComponent(runtime.session.jobId)}` : ''
          router.push(`/app/feed${suffix}`)
        }}
      />
    </div>
  )
}

export default function NetworkPage() {
  return <NetworkPageContent />
}
