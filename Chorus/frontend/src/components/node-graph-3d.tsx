'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import Link from 'next/link'
import type { PeerEntry } from '@/lib/api/orchestrator'

// ─── Types ────────────────────────────────────────────────────────────────────
interface GNode {
  id: number
  peerId: string
  model: string
  address: string | null
  bx: number; by: number; bz: number
  baseR: number
  dpX: number; dpY: number; dpZ: number
  dAmp: number
  pulseP: number
  sx: number; sy: number; sc: number
  flash: number
  conns: number
}
interface GEdge { a: number; b: number }
interface Sig { ei: number; t: number; spd: number }

const FOCAL = 750
const PANEL_W = 260

// Deterministic hash so positions stay stable across renders for same peer id.
function hash01(s: string, salt: number): number {
  let h = 2166136261 ^ salt
  for (let i = 0; i < s.length; i++) {
    h = (h ^ s.charCodeAt(i)) >>> 0
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) / 4294967296)
}

function layoutSphere(peers: PeerEntry[]): { x: number; y: number; z: number }[] {
  // Fibonacci sphere for even distribution — stable regardless of peer id.
  const n = peers.length
  const pts: { x: number; y: number; z: number }[] = []
  const golden = Math.PI * (3 - Math.sqrt(5))
  const R = 260 + Math.min(200, n * 4)
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / Math.max(1, n - 1)) * 2
    const radius = Math.sqrt(Math.max(0, 1 - y * y))
    const theta = golden * i
    const jitter = 12
    const peer = peers[i].peer_id
    pts.push({
      x: Math.cos(theta) * radius * R + (hash01(peer, 1) - 0.5) * jitter,
      y: y * R + (hash01(peer, 2) - 0.5) * jitter,
      z: Math.sin(theta) * radius * R + (hash01(peer, 3) - 0.5) * jitter,
    })
  }
  return pts
}

export interface NodeGraph3DProps {
  peers: PeerEntry[]
  messages?: Array<{ peerId: string; text: string }>
  onOpenFeed?: () => void
}

export default function NodeGraph3D({ peers, messages = [], onOpenFeed }: NodeGraph3DProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>(0)
  const nodesRef = useRef<GNode[]>([])
  const edgesRef = useRef<GEdge[]>([])
  const hovRef = useRef(-1)

  const rotRef = useRef({ y: 0, x: 0.22 })
  const velRef = useRef({ y: 0, x: 0 })
  const zoomRef = useRef(1.2)
  const dragRef = useRef<{ active: boolean; lx: number; ly: number }>({ active: false, lx: 0, ly: 0 })
  const selectedRef = useRef<number>(-1)
  const connectedRef = useRef<Set<number>>(new Set())

  const [sel, setSel] = useState<GNode | null>(null)
  const [evts, setEvts] = useState<Array<{ id: number; msg: string; ts: string }>>([])
  const evtId = useRef(0)

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('[data-panel]')) return
    dragRef.current = { active: true, lx: e.clientX, ly: e.clientY }
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    velRef.current = { y: 0, x: 0 }
  }, [])

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragRef.current.active) return
    const dx = e.clientX - dragRef.current.lx
    const dy = e.clientY - dragRef.current.ly
    dragRef.current.lx = e.clientX
    dragRef.current.ly = e.clientY
    velRef.current.y = dx * 0.005
    velRef.current.x = dy * 0.005
    rotRef.current.y += velRef.current.y
    rotRef.current.x = Math.max(-0.6, Math.min(0.8, rotRef.current.x + velRef.current.x))
  }, [])

  const onPointerUp = useCallback(() => {
    dragRef.current.active = false
  }, [])

  // Rebuild nodes/edges when peer set changes
  useEffect(() => {
    const positions = layoutSphere(peers)
    const nodes: GNode[] = peers.map((p, i) => ({
      id: i,
      peerId: p.peer_id,
      model: p.model,
      address: p.address ?? null,
      bx: positions[i].x,
      by: positions[i].y,
      bz: positions[i].z,
      baseR: 4 + hash01(p.peer_id, 4) * 2,
      dpX: hash01(p.peer_id, 5) * Math.PI * 2,
      dpY: hash01(p.peer_id, 6) * Math.PI * 2,
      dpZ: hash01(p.peer_id, 7) * Math.PI * 2,
      dAmp: 1 + hash01(p.peer_id, 8) * 1.8,
      pulseP: hash01(p.peer_id, 9) * Math.PI * 2,
      sx: 0, sy: 0, sc: 1, flash: 0,
      conns: 0,
    }))

    // Build edges: each node links to k nearest neighbors.
    const k = Math.min(4, Math.max(0, nodes.length - 1))
    const edges: GEdge[] = []
    const added = new Set<string>()
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i]
      const dists = nodes
        .map((b, j) => ({ j, d: Math.hypot(b.bx - a.bx, b.by - a.by, b.bz - a.bz) }))
        .filter((x) => x.j !== i)
        .sort((x, y) => x.d - y.d)
        .slice(0, k)
      for (const { j } of dists) {
        const key = i < j ? `${i}-${j}` : `${j}-${i}`
        if (added.has(key)) continue
        added.add(key)
        edges.push({ a: i, b: j })
        a.conns++
        nodes[j].conns++
      }
    }
    nodesRef.current = nodes
    edgesRef.current = edges
    // Reset selection if invalid
    if (selectedRef.current >= nodes.length) {
      selectedRef.current = -1
      connectedRef.current = new Set()
      setSel(null)
    }
  }, [peers])

  // Event log from real messages
  useEffect(() => {
    if (messages.length === 0) return
    const last = messages.slice(-3)
    setEvts((prev) => {
      const next = [...prev]
      for (const m of last) {
        const d = new Date()
        const ts = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
        next.unshift({
          id: evtId.current++,
          ts,
          msg: `${m.peerId.slice(0, 8)}…: ${m.text.slice(0, 80)}`,
        })
      }
      return next.slice(0, 25)
    })
    // Flash the matching node on new message
    for (const m of last) {
      const node = nodesRef.current.find((n) => n.peerId === m.peerId)
      if (node) node.flash = 12
    }
  }, [messages])

  // Log peer join/leave
  const prevPeersRef = useRef<string[]>([])
  useEffect(() => {
    const prev = new Set(prevPeersRef.current)
    const next = new Set(peers.map((p) => p.peer_id))
    const joined: string[] = []
    const left: string[] = []
    for (const p of next) if (!prev.has(p)) joined.push(p)
    for (const p of prev) if (!next.has(p)) left.push(p)
    if (joined.length || left.length) {
      setEvts((list) => {
        const d = new Date()
        const ts = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
        const adds = [
          ...joined.map((id) => ({ id: evtId.current++, ts, msg: `JOINED: ${id.slice(0, 12)}…` })),
          ...left.map((id) => ({ id: evtId.current++, ts, msg: `LEFT: ${id.slice(0, 12)}…` })),
        ]
        return [...adds, ...list].slice(0, 25)
      })
    }
    prevPeersRef.current = Array.from(next)
  }, [peers])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d') as CanvasRenderingContext2D
    if (!ctx) return
    const cvs = canvas
    const dpr = window.devicePixelRatio || 1
    let W = 0, H = 0

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault()
      const d = e.deltaY > 0 ? -0.05 : 0.05
      zoomRef.current = Math.max(0.1, Math.min(8.0, zoomRef.current * (1 + d)))
    }
    cvs.addEventListener('wheel', handleWheel, { passive: false })

    function project(wx: number, wy: number, wz: number): [number, number, number] {
      const ry = rotRef.current.y, rx = rotRef.current.x
      const cosY = Math.cos(ry), sinY = Math.sin(ry)
      const prX = wx * cosY + wz * sinY
      const prZ = -wx * sinY + wz * cosY
      const cosX = Math.cos(rx), sinX = Math.sin(rx)
      const prY = wy * cosX - prZ * sinX
      const prZ2 = wy * sinX + prZ * cosX
      const z = prZ2 + FOCAL
      if (z < 1) return [-99999, -99999, 0]
      const s = (FOCAL / z) * zoomRef.current
      const cW = W - PANEL_W
      const vs = Math.min(cW, H) / 820
      return [cW / 2 + prX * s * vs, H / 2 + prY * s * vs, s]
    }

    function resize() {
      W = cvs.clientWidth
      H = cvs.clientHeight
      cvs.width = W * dpr
      cvs.height = H * dpr
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.scale(dpr, dpr)
    }

    function hitTest(cx: number, cy: number): number {
      if (cx > W - PANEL_W - 8) return -1
      let best = -1, bd = 55
      for (const n of nodesRef.current) {
        const thr = Math.max(11, n.baseR * n.sc * 2.2)
        const d = Math.hypot(cx - n.sx, cy - n.sy)
        if (d < thr && d < bd) { bd = d; best = n.id }
      }
      return best
    }

    function onCanvasClick(e: MouseEvent) {
      if (dragRef.current.active) return
      const r = cvs.getBoundingClientRect()
      const i = hitTest(e.clientX - r.left, e.clientY - r.top)
      if (i < 0) {
        selectedRef.current = -1
        connectedRef.current = new Set()
        setSel(null)
        return
      }
      if (selectedRef.current === i) {
        selectedRef.current = -1
        connectedRef.current = new Set()
        setSel(null)
      } else {
        selectedRef.current = i
        const connected = new Set<number>([i])
        for (const edge of edgesRef.current) {
          if (edge.a === i) connected.add(edge.b)
          if (edge.b === i) connected.add(edge.a)
        }
        connectedRef.current = connected
        setSel(nodesRef.current[i] ?? null)
      }
    }

    function onCanvasMove(e: MouseEvent) {
      if (dragRef.current.active) return
      const r = cvs.getBoundingClientRect()
      hovRef.current = hitTest(e.clientX - r.left, e.clientY - r.top)
    }

    cvs.addEventListener('click', onCanvasClick)
    cvs.addEventListener('mousemove', onCanvasMove)

    // Signal particles travel along edges
    const sigs: Sig[] = []
    let lastSpawn = 0

    function loop(ts: number) {
      rafRef.current = requestAnimationFrame(loop)
      const t = ts / 1000
      const nodes = nodesRef.current
      const edges = edgesRef.current

      if (!dragRef.current.active) {
        velRef.current.y *= 0.965
        velRef.current.x *= 0.965
        rotRef.current.y += velRef.current.y + 0.0005
      }

      // Spawn signals
      if (edges.length > 0 && ts - lastSpawn > 180) {
        sigs.push({ ei: Math.floor(Math.random() * edges.length), t: 0, spd: 0.006 + Math.random() * 0.012 })
        lastSpawn = ts
        if (sigs.length > 40) sigs.shift()
      }
      for (let i = sigs.length - 1; i >= 0; i--) {
        sigs[i].t += sigs[i].spd
        if (sigs[i].t >= 1) {
          const dest = nodes[edges[sigs[i].ei]?.b]
          if (dest) dest.flash = Math.max(dest.flash, 6)
          sigs.splice(i, 1)
        }
      }
      for (const n of nodes) if (n.flash > 0) n.flash--

      for (const n of nodes) {
        const dx = n.dAmp * Math.sin(t * 0.26 + n.dpX)
        const dy = n.dAmp * Math.sin(t * 0.21 + n.dpY)
        const dz = n.dAmp * 0.5 * Math.sin(t * 0.18 + n.dpZ)
        const [sx, sy, sc] = project(n.bx + dx, n.by + dy, n.bz + dz)
        n.sx = sx; n.sy = sy; n.sc = sc
      }

      const cW = W - PANEL_W
      ctx.fillStyle = '#000000'
      ctx.fillRect(0, 0, W, H)

      // Background haze
      const haze = ctx.createRadialGradient(cW / 2, H / 2, 0, cW / 2, H / 2, Math.min(cW, H) * 0.62)
      haze.addColorStop(0, 'rgba(18,18,26,0.5)')
      haze.addColorStop(0.6, 'rgba(8,8,14,0.25)')
      haze.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = haze
      ctx.fillRect(0, 0, cW, H)

      const sel = selectedRef.current

      // Edges
      for (const e of edges) {
        const na = nodes[e.a], nb = nodes[e.b]
        if (!na || !nb) continue
        if (na.sx < -500 || nb.sx < -500) continue
        const avg = (na.sc + nb.sc) * 0.5
        const isConnected = sel >= 0 && (e.a === sel || e.b === sel)
        const dim = sel >= 0 && !isConnected ? 0.07 : 1
        let alpha = Math.min(0.22, avg * 0.3) * dim
        if (isConnected) alpha = Math.min(0.9, alpha * 5)
        if (alpha < 0.005) continue
        ctx.beginPath()
        ctx.moveTo(na.sx, na.sy)
        ctx.lineTo(nb.sx, nb.sy)
        ctx.strokeStyle = isConnected
          ? `rgba(200,225,255,${alpha})`
          : `rgba(255,255,255,${alpha})`
        ctx.lineWidth = isConnected ? Math.max(1, avg * 1.4) : Math.max(0.3, avg * 0.5)
        ctx.stroke()
      }

      // Signals
      for (const sig of sigs) {
        const e = edges[sig.ei]
        if (!e) continue
        const na = nodes[e.a], nb = nodes[e.b]
        if (!na || !nb) continue
        const px = na.sx + (nb.sx - na.sx) * sig.t
        const py = na.sy + (nb.sy - na.sy) * sig.t
        const r = Math.max(1, 2 * ((na.sc + nb.sc) * 0.5))
        const g = ctx.createRadialGradient(px, py, 0, px, py, r * 5)
        g.addColorStop(0, 'rgba(255,255,255,0.9)')
        g.addColorStop(1, 'rgba(200,220,255,0)')
        ctx.beginPath(); ctx.arc(px, py, r * 5, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill()
        ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2); ctx.fillStyle = 'rgba(255,255,255,0.95)'; ctx.fill()
      }

      // Nodes
      const sorted = [...nodes].sort((a, b) => a.sc - b.sc)
      for (const n of sorted) {
        if (n.sx < -200 || n.sx > cW + 200 || n.sy < -200 || n.sy > H + 200) continue
        const isSelected = sel === n.id
        const isConnected = sel >= 0 && connectedRef.current.has(n.id)
        const focused = sel < 0 || isSelected || isConnected
        const dim = focused ? 1 : 0.14
        let r = n.baseR * n.sc + (isSelected ? 3 : 0)
        if (n.flash > 0) r *= 1.25
        r = Math.max(0.9, r)
        const depth = Math.max(0.3, Math.min(1, n.sc * 1.5))
        // Halo
        const haloR = r * 3.6
        const halo = ctx.createRadialGradient(n.sx, n.sy, r * 0.6, n.sx, n.sy, haloR)
        halo.addColorStop(0, `rgba(220,238,255,${0.26 * depth * dim})`)
        halo.addColorStop(1, 'rgba(200,220,255,0)')
        ctx.beginPath(); ctx.arc(n.sx, n.sy, haloR, 0, Math.PI * 2); ctx.fillStyle = halo; ctx.fill()
        // Sphere
        const sph = ctx.createRadialGradient(n.sx - r * 0.3, n.sy - r * 0.3, 0, n.sx, n.sy, r)
        sph.addColorStop(0, `rgba(255,255,255,${depth * dim})`)
        sph.addColorStop(0.5, `rgba(200,225,255,${0.7 * depth * dim})`)
        sph.addColorStop(1, 'rgba(120,160,220,0)')
        ctx.beginPath(); ctx.arc(n.sx, n.sy, r, 0, Math.PI * 2); ctx.fillStyle = sph; ctx.fill()

        if (isSelected) {
          ctx.beginPath(); ctx.arc(n.sx, n.sy, r + 7, 0, Math.PI * 2)
          ctx.strokeStyle = 'rgba(255,255,255,0.6)'; ctx.lineWidth = 1.5; ctx.stroke()
        }
        // Label for selected/hover
        if (n.sc > 0.55 && (isSelected || hovRef.current === n.id)) {
          ctx.font = `500 10px var(--font-geist-mono, monospace)`
          ctx.fillStyle = `rgba(255,255,255,${0.85 * dim})`
          ctx.fillText(n.peerId.slice(0, 10), n.sx + r + 6, n.sy + 3)
        }
      }

      // Vignette
      const vig = ctx.createRadialGradient(cW / 2, H / 2, Math.min(cW, H) * 0.36, cW / 2, H / 2, Math.min(cW, H) * 0.88)
      vig.addColorStop(0, 'rgba(0,0,0,0)'); vig.addColorStop(1, 'rgba(0,0,0,0.8)')
      ctx.fillStyle = vig; ctx.fillRect(0, 0, cW, H)

      // Panel border
      ctx.beginPath(); ctx.moveTo(cW, 0); ctx.lineTo(cW, H)
      ctx.strokeStyle = 'rgba(255,255,255,0.07)'; ctx.lineWidth = 1; ctx.stroke()
    }

    const ro = new ResizeObserver(resize)
    ro.observe(cvs)
    resize()
    rafRef.current = requestAnimationFrame(loop)

    return () => {
      cancelAnimationFrame(rafRef.current)
      ro.disconnect()
      cvs.removeEventListener('click', onCanvasClick)
      cvs.removeEventListener('mousemove', onCanvasMove)
      cvs.removeEventListener('wheel', handleWheel)
    }
  }, [])

  const showEmpty = peers.length === 0

  return (
    <div
      className="relative w-full h-full select-none"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      style={{ touchAction: 'none' }}
    >
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full"
        style={{ display: 'block', cursor: 'grab' }}
      />

      {showEmpty && (
        <div
          className="absolute inset-0 grid place-items-center"
          style={{
            paddingRight: PANEL_W,
            background: 'radial-gradient(ellipse at center, rgba(20,24,40,0.4), transparent 70%)',
          }}
        >
          <div className="text-center max-w-sm px-8">
            <div
              className="mx-auto mb-5 w-12 h-12 rounded-full border border-white/15 grid place-items-center"
              style={{ background: 'rgba(255,255,255,0.02)' }}
            >
              <span className="font-mono text-[18px] text-white/50">∅</span>
            </div>
            <h2 className="font-sans text-[16px] text-white/85 mb-2" style={{ fontWeight: 600 }}>
              No peers online
            </h2>
            <p className="font-sans text-[12.5px] text-white/55 leading-relaxed mb-4">
              The network is empty. Share the join link with a peer to light up the graph.
            </p>
            <Link
              href="/join"
              className="inline-block px-4 py-2 rounded-md font-mono text-[11px] tracking-[0.12em] text-white/90"
              style={{ background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.16)' }}
            >
              OPEN JOIN PAGE
            </Link>
          </div>
        </div>
      )}

      {/* Drag hint */}
      {!showEmpty && (
        <div
          className="absolute top-8 pointer-events-none"
          style={{ left: 0, right: PANEL_W, textAlign: 'center' }}
        >
          <span
            style={{
              fontFamily: 'var(--font-geist-mono)',
              fontSize: 8,
              color: 'rgba(255,255,255,0.18)',
              letterSpacing: '0.3em',
              textTransform: 'uppercase',
            }}
          >
            drag to rotate · click node to inspect
          </span>
        </div>
      )}

      {/* ── Side Panel ── */}
      <div
        data-panel="true"
        className="absolute top-0 right-0 h-full flex flex-col"
        style={{ width: PANEL_W, background: 'rgba(3,3,3,0.95)', borderLeft: '1px solid rgba(255,255,255,0.07)' }}
      >
        <div style={{ padding: '20px 16px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div
            style={{
              fontFamily: 'var(--font-geist-mono)',
              fontSize: 8,
              color: 'rgba(255,255,255,0.18)',
              letterSpacing: '0.3em',
              textTransform: 'uppercase',
              marginBottom: 12,
            }}
          >
            Chorus Network Monitor
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 12px' }}>
            <Stat label="PEERS" value={peers.length.toString()} />
            <Stat label="EDGES" value={edgesRef.current.length.toString()} />
            <Stat label="MODELS" value={new Set(peers.map((p) => p.model)).size.toString()} />
            <Stat label="EVENTS" value={evts.length.toString()} />
          </div>
        </div>

        {sel ? (
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16, overflow: 'auto' }}>
            <div>
              <Label>PEER ID</Label>
              <div
                style={{
                  fontFamily: 'var(--font-geist-mono)',
                  fontSize: 11,
                  color: 'white',
                  wordBreak: 'break-all',
                  lineHeight: 1.35,
                }}
              >
                {sel.peerId}
              </div>
            </div>
            <Divider />
            <Row k="Model" v={sel.model} />
            <Row k="Address" v={sel.address ?? '—'} />
            <Row k="Connections" v={String(sel.conns)} />
            <Divider />
            <div>
              <Label>RECENT MESSAGES</Label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
                {messages
                  .filter((m) => m.peerId === sel.peerId)
                  .slice(-4)
                  .reverse()
                  .map((m, i) => (
                    <p
                      key={i}
                      style={{
                        fontFamily: 'var(--font-geist-sans)',
                        fontSize: 11,
                        color: 'rgba(255,255,255,0.65)',
                        margin: 0,
                        lineHeight: 1.4,
                      }}
                    >
                      {m.text.slice(0, 120)}
                      {m.text.length > 120 ? '…' : ''}
                    </p>
                  ))}
                {messages.filter((m) => m.peerId === sel.peerId).length === 0 && (
                  <span
                    style={{
                      fontFamily: 'var(--font-geist-mono)',
                      fontSize: 9,
                      color: 'rgba(255,255,255,0.3)',
                    }}
                  >
                    (no messages yet)
                  </span>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div style={{ flex: 1, overflowY: 'auto', padding: '10px 12px 12px' }}>
            <Label>EVENT LOG</Label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
              {evts.map((e) => (
                <div key={e.id} style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <span
                    style={{
                      fontFamily: 'var(--font-geist-mono)',
                      fontSize: 7,
                      color: 'rgba(255,255,255,0.3)',
                      flexShrink: 0,
                    }}
                  >
                    {e.ts}
                  </span>
                  <span
                    style={{
                      fontFamily: 'var(--font-geist-mono)',
                      fontSize: 9,
                      color: 'rgba(255,255,255,0.82)',
                      wordBreak: 'break-all',
                      lineHeight: 1.4,
                    }}
                  >
                    {e.msg}
                  </span>
                </div>
              ))}
              {evts.length === 0 && (
                <span
                  style={{
                    fontFamily: 'var(--font-geist-mono)',
                    fontSize: 9,
                    color: 'rgba(255,255,255,0.25)',
                  }}
                >
                  Waiting for network activity…
                </span>
              )}
            </div>
          </div>
        )}

        <div
          style={{
            padding: '12px 16px',
            borderTop: '1px solid rgba(255,255,255,0.06)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          {onOpenFeed ? (
            <button
              onClick={onOpenFeed}
              style={{
                fontFamily: 'var(--font-geist-mono)',
                fontSize: 8,
                color: 'rgba(255,255,255,0.4)',
                letterSpacing: '0.2em',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                textTransform: 'uppercase',
              }}
            >
              Open Feed →
            </button>
          ) : (
            <span />
          )}
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <div
              style={{
                width: 4,
                height: 4,
                borderRadius: '50%',
                background: peers.length > 0 ? 'rgba(120,220,160,0.9)' : 'rgba(220,120,120,0.8)',
              }}
            />
            <span
              style={{
                fontFamily: 'var(--font-geist-mono)',
                fontSize: 8,
                color: 'rgba(255,255,255,0.35)',
                letterSpacing: '0.15em',
              }}
            >
              {peers.length > 0 ? 'LIVE' : 'EMPTY'}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div
        style={{
          fontFamily: 'var(--font-geist-mono)',
          fontSize: 7,
          color: 'rgba(255,255,255,0.25)',
          marginBottom: 2,
          letterSpacing: '0.12em',
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontFamily: 'var(--font-geist-mono)',
          fontSize: 13,
          color: 'rgba(255,255,255,0.78)',
        }}
      >
        {value}
      </div>
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: 'var(--font-geist-mono)',
        fontSize: 7,
        color: 'rgba(255,255,255,0.3)',
        letterSpacing: '0.2em',
      }}
    >
      {children}
    </div>
  )
}

function Divider() {
  return <div style={{ height: 1, background: 'rgba(255,255,255,0.06)' }} />
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <Label>{k.toUpperCase()}</Label>
      <div
        style={{
          fontFamily: 'var(--font-geist-mono)',
          fontSize: 10,
          color: 'rgba(255,255,255,0.72)',
          marginTop: 2,
          wordBreak: 'break-all',
          lineHeight: 1.4,
        }}
      >
        {v}
      </div>
    </div>
  )
}
