'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import type { ClusterID } from '@/lib/mock-data'

// ─── Deterministic RNG (inline, no import needed) ────────────────────────────
class RNG {
  private state: number
  constructor(seed: number) { this.state = (seed >>> 0) || 1 }
  float(): number {
    this.state = (Math.imul(this.state, 1664525) + 1013904223) >>> 0
    return this.state / 4294967296
  }
  int(min: number, max: number): number {
    return Math.floor(this.float() * (max - min + 1)) + min
  }
}

// ─── Types ────────────────────────────────────────────────────────────────────
interface GNode {
  id: number; clusterId: number
  type: 'hub' | 'med' | 'small'
  bx: number; by: number; bz: number
  baseR: number
  dpX: number; dpY: number; dpZ: number
  dAmp: number
  pulseP: number
  hasRing: boolean; ringPhase: number
  sx: number; sy: number; sc: number
  flash: number
  agentCode: string; role: string; segment: string; metric: string
  conns: number
}
interface GEdge { a: number; b: number; long: boolean }
interface Sig    { ei: number; t: number; spd: number }
interface Star   { nx: number; ny: number; op: number; sz: number; gal: boolean }
interface Evt    { id: number; msg: string; dim: boolean; ts: string }
interface NDisp  {
  label: string; type: string; role: string; segment: string
  metric: string; conns: number; agentCode: string
}

const FOCAL   = 750
const PANEL_W = 260

const HUB_ROLES = [
  'Membrane Orchestrator', 'Combination Layer Coordinator',
  'Watchdog Intelligence Hub', 'Consensus Synthesis Engine',
  'Signal Aggregator', 'Policy Evaluator',
]
const MED_ROLES = [
  'Pricing Agent', 'Growth Agent', 'Retention Analyst',
  'Critic Agent', 'Revenue Modeler', 'Churn Detector',
  'Compliance Monitor', 'Tool Call Auditor', 'Adoption Curve', 'Risk Assessor',
]
const SML_ROLES = [
  'Signal Tracker', 'Data Sensor', 'Latency Monitor',
  'Audit Classifier', 'SOC 2 Probe', 'Cost Attributor',
  'Fleet Health Sensor', 'Policy Engine Tap', 'Metric Collector',
]
const SEGS = ['Membrane', 'Combination', 'Watchdog', 'Auditor', 'Orchestration', 'Consensus']
const STATUSES = [
  'Mapping agent network topology…', 'Processing Membrane layer signals…',
  'Synchronising Combination layer…', 'Watchdog running policy checks…',
  'Aggregating consensus signals…', 'Compiling fleet activity matrix…',
  'Tool call audit: 108k processed…',
]

export interface NodeGraph3DProps {
  round?: number
  totalRounds?: number
  nodesContributing?: number
  feedPreview?: Array<{ agentId: string; text: string; clusterId: ClusterID }>
  onOpenFeed?: () => void
}

export default function NodeGraph3D({
  round = 1,
  totalRounds = 3,
  feedPreview = [],
  onOpenFeed,
}: NodeGraph3DProps) {
  const canvasRef  = useRef<HTMLCanvasElement>(null)
  const rafRef     = useRef<number>(0)
  const statusRef  = useRef<HTMLDivElement | null>(null)
  const statusI    = useRef(0)
  const nodesRef   = useRef<GNode[]>([])
  const edgesRef   = useRef<GEdge[]>([])
  const hovRef     = useRef(-1)
  const logBuf     = useRef<Evt[]>([])
  const logId      = useRef(0)

  const rotRef  = useRef({ y: 0, x: 0.22 })
  const velRef  = useRef({ y: 0, x: 0 })
  const zoomRef = useRef<number>(1.2)
  const dragRef = useRef<{ active: boolean; lx: number; ly: number }>({ active: false, lx: 0, ly: 0 })
  // Selection focus — refs so RAF loop reads without stale closure + re-renders
  const selectedRef   = useRef<number>(-1)   // selected node id
  const connectedRef  = useRef<Set<number>>(new Set()) // ids directly connected to selected

  const [sel,   setSel]   = useState<NDisp | null>(null)
  const [tab,   setTab]   = useState<'live' | 'node'>('live')
  const [evts,  setEvts]  = useState<Evt[]>([])
  const [stats, setStats] = useState({ sig: 0, pkt: 0, den: '0%' })

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
    const sensitivity = 0.005
    velRef.current.y = dx * sensitivity
    velRef.current.x = dy * sensitivity
    rotRef.current.y += velRef.current.y
    rotRef.current.x = Math.max(-0.6, Math.min(0.8, rotRef.current.x + velRef.current.x))
  }, [])

  const onPointerUp = useCallback(() => {
    dragRef.current.active = false
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d') as CanvasRenderingContext2D
    if (!ctx) return
    const cvs = canvas

    const dpr = window.devicePixelRatio || 1
    let W = 0, H = 0
    const rng = new RNG(0xdeadbeef) // fixed seed — stable graph

    // ── Zoom Wheel Event ───────────────────────────────────────────────────
    const handleWheel = (e: WheelEvent) => {
      e.preventDefault()
      const d = e.deltaY > 0 ? -0.05 : 0.05
      zoomRef.current = Math.max(0.1, Math.min(8.0, zoomRef.current * (1 + d)))
    }
    cvs.addEventListener('wheel', handleWheel, { passive: false })

    // ── Stars ──────────────────────────────────────────────────────────────
    const stars: Star[] = Array.from({ length: 420 }, () => ({
      nx: rng.float(), ny: rng.float(),
      op: 0.18 + rng.float() * 0.45,
      sz: 0.35 + rng.float() * 1.15,
      gal: rng.float() < 0.07,
    }))

    // ── Cluster centers ────────────────────────────────────────────────────
    const NC = rng.int(18, 24)
    const centers: { cx: number; cy: number; cz: number }[] = []
    for (let ci = 0; ci < NC; ci++) {
      let cx = 0, cy = 0, cz = 0, ok = false, tries = 0
      while (!ok && tries++ < 50) {
        const u  = rng.float() * 2 - 1
        const th = rng.float() * Math.PI * 2
        const sr = Math.sqrt(1 - u * u)
        
        // Use identical radius scale for spherical distribution
        const radius = 350 + rng.float() * 250
        cx = sr * Math.cos(th) * radius
        cy = u * radius
        cz = sr * Math.sin(th) * radius
        
        ok = centers.every(c => Math.hypot(c.cx - cx, c.cy - cy, c.cz - cz) > 180)
      }
      centers.push({ cx, cy, cz })
    }

    // ── Build nodes ────────────────────────────────────────────────────────
    const nodes: GNode[] = []
    let hubN = 0, medN = 0, ringCount = 0

    for (let ci = 0; ci < NC; ci++) {
      const { cx, cy, cz } = centers[ci]

      const hubsHere = rng.int(1, 2)
      for (let h = 0; h < hubsHere; h++) {
        const ring = ringCount < 3 && rng.float() < 0.6
        if (ring) ringCount++
        nodes.push({
          id: nodes.length, clusterId: ci, type: 'hub',
          bx: cx + (rng.float() - .5) * 45,
          by: cy + (rng.float() - .5) * 45,
          bz: cz + (rng.float() - .5) * 45,
          baseR: 8 + rng.float() * 4,
          dpX: rng.float() * Math.PI * 2, dpY: rng.float() * Math.PI * 2, dpZ: rng.float() * Math.PI * 2,
          dAmp: 1.8 + rng.float() * 2.2,
          pulseP: rng.float() * Math.PI * 2,
          hasRing: ring, ringPhase: rng.float() * Math.PI * 2,
          sx: 0, sy: 0, sc: 1, flash: 0,
          agentCode: `H-${String(++hubN).padStart(2, '0')}`,
          role: HUB_ROLES[ci % HUB_ROLES.length],
          segment: SEGS[rng.int(0, SEGS.length - 1)],
          metric: `${380 + rng.int(0, 600)} sig/s`,
          conns: 0,
        })
      }

      const medN2 = rng.int(2, 5)
      for (let m = 0; m < medN2; m++) {
        nodes.push({
          id: nodes.length, clusterId: ci, type: 'med',
          bx: cx + (rng.float() - .5) * 130,
          by: cy + (rng.float() - .5) * 130,
          bz: cz + (rng.float() - .5) * 130,
          baseR: 2.5 + rng.float() * 2,
          dpX: rng.float() * Math.PI * 2, dpY: rng.float() * Math.PI * 2, dpZ: rng.float() * Math.PI * 2,
          dAmp: 1.2 + rng.float() * 1.8,
          pulseP: 0, hasRing: false, ringPhase: 0,
          sx: 0, sy: 0, sc: 1, flash: 0,
          agentCode: `A-${String(++medN).padStart(3, '0')}`,
          role: MED_ROLES[m % MED_ROLES.length],
          segment: SEGS[rng.int(0, SEGS.length - 1)],
          metric: `${45 + rng.int(0, 50)}% adopt`,
          conns: 0,
        })
      }

      const smlN = rng.int(8, 14)
      for (let s = 0; s < smlN; s++) {
        nodes.push({
          id: nodes.length, clusterId: ci, type: 'small',
          bx: cx + (rng.float() - .5) * 200,
          by: cy + (rng.float() - .5) * 200,
          bz: cz + (rng.float() - .5) * 200,
          baseR: 0.8 + rng.float() * 1.2,
          dpX: rng.float() * Math.PI * 2, dpY: rng.float() * Math.PI * 2, dpZ: rng.float() * Math.PI * 2,
          dAmp: 0.8 + rng.float() * 1.8,
          pulseP: 0, hasRing: false, ringPhase: 0,
          sx: 0, sy: 0, sc: 1, flash: 0,
          agentCode: `S-${String(nodes.length + 1).padStart(3, '0')}`,
          role: SML_ROLES[s % SML_ROLES.length],
          segment: SEGS[rng.int(0, SEGS.length - 1)],
          metric: `+${40 + rng.int(0, 45)} sent`,
          conns: 0,
        })
      }
    }
    nodesRef.current = nodes

    // ── Build edges ────────────────────────────────────────────────────────
    const edges: GEdge[] = []
    function addEdge(a: number, b: number, long: boolean) {
      if (edges.some(e => (e.a === a && e.b === b) || (e.a === b && e.b === a))) return
      edges.push({ a, b, long })
      nodes[a].conns++
      nodes[b].conns++
    }

    for (let ci = 0; ci < NC; ci++) {
      const cn = nodes.filter(n => n.clusterId === ci)
      for (let i = 0; i < cn.length; i++) {
        const a = cn[i]
        const sorted = cn
          .filter((_, j) => j !== i)
          .map(b => ({ b, d: Math.hypot(b.bx - a.bx, b.by - a.by, b.bz - a.bz) }))
          .sort((x, y) => x.d - y.d)
        const maxC = a.type === 'hub' ? 9 : a.type === 'med' ? 6 : 3
        let added = 0
        for (const { b, d } of sorted) {
          if (added >= maxC) break
          if (rng.float() < Math.max(0.1, 1 - d / 280)) {
            addEdge(a.id, b.id, false)
            added++
          }
        }
      }
    }

    // Dense inter-cluster: every cluster pair gets many bridges, always routed through hubs
    for (let ci = 0; ci < NC; ci++) {
      for (let cj = ci + 1; cj < NC; cj++) {
        const d = Math.hypot(centers[ci].cx - centers[cj].cx, centers[ci].cy - centers[cj].cy, centers[ci].cz - centers[cj].cz)
        if (d > 750) continue // Do not connect opposite ends of the sphere

        const bridges = rng.int(1, 4)
        const aN = nodes.filter(n => n.clusterId === ci)
        const bN = nodes.filter(n => n.clusterId === cj)
        // Prefer hub-to-hub first
        const hubsA = aN.filter(n => n.type === 'hub')
        const hubsB = bN.filter(n => n.type === 'hub')
        for (const ha of hubsA) {
          for (const hb of hubsB) {
            addEdge(ha.id, hb.id, true)
          }
        }
        // Then extra random bridges
        for (let b = 0; b < bridges; b++) {
          const a  = aN[rng.int(0, aN.length - 1)]
          const bb = bN[rng.int(0, bN.length - 1)]
          if (a && bb) addEdge(a.id, bb.id, true)
        }
      }
    }
    edgesRef.current = edges

    // ── Signals ────────────────────────────────────────────────────────────
    const sigs: Sig[] = []
    function spawnSig() {
      if (!edges.length) return
      sigs.push({ ei: rng.int(0, edges.length - 1), t: 0, spd: 0.004 + rng.float() * 0.014 })
    }
    for (let i = 0; i < 40; i++) spawnSig()

    // ── 3D projection ──────────────────────────────────────────────────────
    function project(wx: number, wy: number, wz: number): [number, number, number] {
      const ry = rotRef.current.y
      const rx = rotRef.current.x
      const cosY = Math.cos(ry), sinY = Math.sin(ry)
      const prX =  wx * cosY + wz * sinY
      const prZ = -wx * sinY + wz * cosY
      const cosX = Math.cos(rx), sinX = Math.sin(rx)
      const prY  = wy * cosX - prZ * sinX
      const prZ2 = wy * sinX + prZ * cosX
      const z = prZ2 + FOCAL
      if (z < 1) return [-99999, -99999, 0]
      const s  = (FOCAL / z) * zoomRef.current
      const cW = W - PANEL_W
      const vs = Math.min(cW, H) / 820
      return [cW / 2 + prX * s * vs, H / 2 + prY * s * vs, s]
    }

    function resize() {
      W = cvs.clientWidth
      H = cvs.clientHeight
      cvs.width  = W * dpr
      cvs.height = H * dpr
      ctx.scale(dpr, dpr)
    }

    // ── Draw node ──────────────────────────────────────────────────────────
    function drawNode(n: GNode, t: number) {
      const { sx, sy, sc } = n
      if (sc <= 0) return

      const sel = selectedRef.current
      const isSelected  = sel === n.id
      const isConnected = sel >= 0 && connectedRef.current.has(n.id)
      const isFocused   = sel < 0 || isSelected || isConnected
      const dimFactor   = isFocused ? 1 : 0.12   // non-connected nodes nearly invisible

      let r = n.baseR * sc
      if (n.type === 'hub') r += 1.6 * Math.sin(t * 1.05 + n.pulseP) * sc
      if (isSelected) r *= 1.35    // selected node pops bigger
      r = Math.max(0.7, r)

      const depth = n.type === 'hub'
        ? Math.max(0.5, Math.min(1, sc * 1.5))
        : Math.max(0.15, Math.min(1, sc * 1.8))
      const fb = n.flash > 0 ? 1.5 : 1

      if (n.type !== 'small' || sc > 0.6) {
        const haloR = r * (n.type === 'hub' ? 4.5 : 3.2)
        const halo  = ctx.createRadialGradient(sx, sy, r * 0.6, sx, sy, haloR)
        const haloBoost = isSelected ? 1.8 : isConnected ? 1.3 : 1
        if (n.type === 'hub') {
          halo.addColorStop(0, `rgba(220,238,255,${0.32 * depth * fb * dimFactor * haloBoost})`)
          halo.addColorStop(0.5, `rgba(200,225,255,${0.08 * depth * dimFactor})`)
          halo.addColorStop(1, 'rgba(200,220,255,0)')
        } else {
          halo.addColorStop(0, `rgba(255,255,255,${0.2 * depth * fb * dimFactor * haloBoost})`)
          halo.addColorStop(1, 'rgba(255,255,255,0)')
        }
        ctx.beginPath()
        ctx.arc(sx, sy, haloR, 0, Math.PI * 2)
        ctx.fillStyle = halo
        ctx.fill()
      }

      const hlX = sx - r * 0.32, hlY = sy - r * 0.28
      const sph = ctx.createRadialGradient(hlX, hlY, 0, sx, sy, r)
      if (n.type === 'hub') {
        sph.addColorStop(0,    `rgba(255,255,255,${depth * fb * dimFactor})`)
        sph.addColorStop(0.25, `rgba(235,248,255,${0.96 * depth * dimFactor})`)
        sph.addColorStop(0.6,  `rgba(170,210,245,${0.72 * depth * dimFactor})`)
        sph.addColorStop(0.88, `rgba(110,165,215,${0.22 * depth * dimFactor})`)
        sph.addColorStop(1,    'rgba(60,110,180,0)')
      } else if (n.type === 'med') {
        sph.addColorStop(0,   `rgba(255,255,255,${0.95 * depth * fb * dimFactor})`)
        sph.addColorStop(0.4, `rgba(225,238,250,${0.72 * depth * dimFactor})`)
        sph.addColorStop(0.85,`rgba(185,210,235,${0.15 * depth * dimFactor})`)
        sph.addColorStop(1,   'rgba(180,205,230,0)')
      } else {
        sph.addColorStop(0,    `rgba(255,255,255,${0.9 * depth * fb * dimFactor})`)
        sph.addColorStop(0.55, `rgba(215,230,245,${0.45 * depth * dimFactor})`)
        sph.addColorStop(1,    'rgba(200,220,240,0)')
      }
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2); ctx.fillStyle = sph; ctx.fill()

      if (r > 3) {
        const sX = sx - r * 0.38, sY = sy - r * 0.35, sR = r * 0.28
        const spec = ctx.createRadialGradient(sX, sY, 0, sX, sY, sR)
        spec.addColorStop(0,   `rgba(255,255,255,${0.65 * depth * dimFactor})`)
        spec.addColorStop(0.6, `rgba(255,255,255,${0.12 * depth * dimFactor})`)
        spec.addColorStop(1,   'rgba(255,255,255,0)')
        ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2); ctx.fillStyle = spec; ctx.fill()
      }

      // Selection ring — solid for selected, dashed for connected
      if (isSelected) {
        ctx.beginPath(); ctx.arc(sx, sy, r + 7, 0, Math.PI * 2)
        ctx.strokeStyle = 'rgba(255,255,255,0.55)'; ctx.lineWidth = 1.5
        ctx.setLineDash([]); ctx.stroke()
        // Extra outer pulse
        ctx.beginPath(); ctx.arc(sx, sy, r + 14, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(255,255,255,${0.18 + 0.12 * Math.sin(t * 3)})`
        ctx.lineWidth = 0.8; ctx.stroke()
      } else if (isConnected && sel >= 0) {
        ctx.beginPath(); ctx.arc(sx, sy, r + 5, 0, Math.PI * 2)
        ctx.strokeStyle = 'rgba(255,255,255,0.28)'; ctx.lineWidth = 1
        ctx.setLineDash([3, 4]); ctx.stroke(); ctx.setLineDash([])
      } else if (hovRef.current === n.id && sel < 0) {
        ctx.beginPath(); ctx.arc(sx, sy, r * 3.2, 0, Math.PI * 2)
        ctx.strokeStyle = 'rgba(255,255,255,0.2)'; ctx.lineWidth = 1.2
        ctx.setLineDash([4, 7]); ctx.stroke(); ctx.setLineDash([])
      }

      if (n.type === 'hub' && sc > 0.35) {
        const la = Math.min((sc - 0.35) * 4, 0.65) * depth * dimFactor
        ctx.font = `500 ${Math.round(Math.min(10 * sc * 1.3, 12))}px var(--font-geist-mono, monospace)`
        ctx.fillStyle = `rgba(255,255,255,${la})`
        ctx.fillText(n.agentCode, sx + r + 5 * sc, sy + 4)
        ctx.beginPath(); ctx.moveTo(sx + r, sy); ctx.lineTo(sx + r + 4 * sc, sy + 4)
        ctx.strokeStyle = `rgba(255,255,255,${la * 0.35})`; ctx.lineWidth = 0.5; ctx.stroke()
      }
    }

    function drawRing(n: GNode, t: number) {
      const { sx, sy, sc } = n
      const r = n.baseR * sc
      const depth = Math.max(0.05, sc * 0.9)
      const ph = t * 0.15 + n.ringPhase
      ctx.save(); ctx.translate(sx, sy)
      ctx.rotate(ph)
      ctx.beginPath(); ctx.ellipse(0, 0, r * 3.2, r * 0.9, 0, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(255,255,255,${0.11 * depth})`; ctx.lineWidth = Math.max(0.4, 0.75 * sc); ctx.stroke()
      ctx.rotate(0.55)
      ctx.beginPath(); ctx.ellipse(0, 0, r * 3.8, r * 0.68, Math.PI / 5, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(255,255,255,${0.07 * depth})`; ctx.lineWidth = Math.max(0.3, 0.5 * sc); ctx.stroke()
      ctx.rotate(-0.25)
      ctx.beginPath(); ctx.ellipse(0, 0, r * 2.8, r * 1.1, -Math.PI / 4, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(255,255,255,${0.05 * depth})`; ctx.lineWidth = Math.max(0.25, 0.4 * sc); ctx.stroke()
      ctx.restore()
    }

    function drawEdge(e: GEdge, t: number) {
      const na = nodes[e.a], nb = nodes[e.b]
      if (!na || !nb) return
      const { sx: x1, sy: y1, sc: s1 } = na
      const { sx: x2, sy: y2, sc: s2 } = nb
      if (x1 < -500 || x2 < -500) return
      const avgSc = (s1 + s2) * 0.5
      if (avgSc < 0.03) return

      // Focus: is this edge connected to the selected node?
      const sel = selectedRef.current
      const edgeIsConnected = sel >= 0 && (e.a === sel || e.b === sel)
      const edgeIsDimmed    = sel >= 0 && !edgeIsConnected
      const edgeDimFactor   = edgeIsDimmed ? 0.04 : 1  // dim non-connected edges heavily

      const dist = Math.hypot(x1 - x2, y1 - y2)
      const dFac = Math.max(0, 1 - dist / 520)
      let alpha: number, lw: number
      if (e.long) {
        const pulse = edgeIsConnected
          ? 0.9 + 0.1 * Math.sin(t * 2.5 + na.id * 0.3)   // fast bright pulse for selected
          : 0.65 + 0.35 * Math.sin(t * 1.2 + na.id * 0.3)
        alpha = Math.min(0.22, avgSc * 0.28) * pulse * (0.5 + 0.5 * dFac) * edgeDimFactor
        if (edgeIsConnected) alpha = Math.min(0.85, alpha * 4)
        lw    = edgeIsConnected ? Math.max(1.2, avgSc * 1.8) : Math.max(0.4, avgSc * 0.6)
        if (alpha < 0.005) return
        const grad = ctx.createLinearGradient(x1, y1, x2, y2)
        if (edgeIsConnected) {
          grad.addColorStop(0,   `rgba(255,255,255,${alpha})`)
          grad.addColorStop(0.5, `rgba(200,225,255,${alpha * 1.3})`)
          grad.addColorStop(1,   `rgba(255,255,255,${alpha})`)
        } else {
          grad.addColorStop(0,   `rgba(200,220,255,${alpha})`)
          grad.addColorStop(0.5, `rgba(255,255,255,${alpha * 1.4})`)
          grad.addColorStop(1,   `rgba(200,220,255,${alpha})`)
        }
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2)
        ctx.strokeStyle = grad; ctx.lineWidth = lw; ctx.stroke()
      } else {
        alpha = Math.min(0.18, avgSc * 0.25) * (0.55 + 0.45 * dFac) * edgeDimFactor
        if (edgeIsConnected) alpha = Math.min(0.95, alpha * 5)
        lw    = edgeIsConnected ? Math.max(1.0, avgSc * 1.5) : Math.max(0.25, avgSc * 0.5)
        if (alpha < 0.003) return
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2)
        ctx.strokeStyle = `rgba(255,255,255,${alpha})`; ctx.lineWidth = lw; ctx.stroke()
      }
    }

    function drawSig(sig: Sig) {
      const e = edges[sig.ei]
      if (!e) return
      const na = nodes[e.a], nb = nodes[e.b]
      if (!na || !nb) return
      const px  = na.sx + (nb.sx - na.sx) * sig.t
      const py  = na.sy + (nb.sy - na.sy) * sig.t
      const sc  = na.sc + (nb.sc - na.sc) * sig.t
      const r   = Math.max(1, 2.8 * sc)
      const g = ctx.createRadialGradient(px, py, 0, px, py, r * 5)
      g.addColorStop(0,   'rgba(255,255,255,0.95)')
      g.addColorStop(0.3, 'rgba(220,235,255,0.3)')
      g.addColorStop(1,   'rgba(200,220,255,0)')
      ctx.beginPath(); ctx.arc(px, py, r * 5, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill()
      ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2); ctx.fillStyle = 'rgba(255,255,255,0.98)'; ctx.fill()
      const dx = nb.sx - na.sx, dy = nb.sy - na.sy
      const tl = Math.min(0.09, 40 / (Math.hypot(dx, dy) + 1))
      const tx = px - dx * tl, ty = py - dy * tl
      const tr = ctx.createLinearGradient(px, py, tx, ty)
      tr.addColorStop(0, `rgba(255,255,255,${0.55 * sc})`)
      tr.addColorStop(1, 'rgba(255,255,255,0)')
      ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(tx, ty)
      ctx.strokeStyle = tr; ctx.lineWidth = r * 0.85; ctx.stroke()
    }

    function pushEvt(msg: string, dim = false) {
      const d = new Date()
      const ts = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
      logBuf.current.unshift({ id: logId.current++, msg, dim, ts })
      if (logBuf.current.length > 30) logBuf.current.pop()
    }

    function hitTest(cx: number, cy: number): number {
      if (cx > W - PANEL_W - 8) return -1
      let best = -1, bd = 55
      for (const n of nodes) {
        const thr = Math.max(11, n.baseR * n.sc * 2.2)
        const d   = Math.hypot(cx - n.sx, cy - n.sy)
        if (d < thr && d < bd) { bd = d; best = n.id }
      }
      return best
    }

    function getDisp(n: GNode): NDisp {
      return {
        label: n.agentCode,
        type:  n.type === 'hub' ? 'Hub Orchestrator' : n.type === 'med' ? 'Analyst Agent' : 'Sensor Node',
        role: n.role, segment: n.segment, metric: n.metric, conns: n.conns,
        agentCode: n.agentCode,
      }
    }

    function onCanvasClick(e: MouseEvent) {
      if (dragRef.current.active) return
      const r = cvs.getBoundingClientRect()
      const i = hitTest(e.clientX - r.left, e.clientY - r.top)
      if (i >= 0) {
        // Toggle selection
        if (selectedRef.current === i) {
          // Deselect
          selectedRef.current = -1
          connectedRef.current = new Set()
          setSel(null)
        } else {
          selectedRef.current = i
          // Build connected set: all nodes directly linked by an edge
          const connected = new Set<number>([i])
          for (const edge of edges) {
            if (edge.a === i) connected.add(edge.b)
            if (edge.b === i) connected.add(edge.a)
          }
          connectedRef.current = connected
          setSel(getDisp(nodes[i]))
          setTab('node')
          pushEvt(`INSPECTING: ${nodes[i].agentCode}`)
        }
      } else {
        // Click empty space — deselect
        selectedRef.current = -1
        connectedRef.current = new Set()
        setSel(null)
      }
    }

    function onCanvasMove(e: MouseEvent) {
      if (dragRef.current.active) return
      const r = cvs.getBoundingClientRect()
      hovRef.current = hitTest(e.clientX - r.left, e.clientY - r.top)
    }

    cvs.addEventListener('click', onCanvasClick)
    cvs.addEventListener('mousemove', onCanvasMove)

    const si = setInterval(() => {
      statusI.current = (statusI.current + 1) % STATUSES.length
      if (statusRef.current) statusRef.current.textContent = STATUSES[statusI.current]
    }, 1800)
    const li = setInterval(() => {
      if (logBuf.current.length) setEvts(logBuf.current.slice(0, 25))
    }, 800)
    const sti = setInterval(() => {
      setStats({ sig: 400 + Math.floor(Math.random() * 500), pkt: sigs.length, den: `${(62 + Math.random() * 30).toFixed(1)}%` })
    }, 2000)

    let frame = 0, lastSig = 0

    function loop(ts: number) {
      rafRef.current = requestAnimationFrame(loop)
      frame++
      const t = ts / 1000

      if (!dragRef.current.active) {
        velRef.current.y *= 0.965
        velRef.current.x *= 0.965
        rotRef.current.y += velRef.current.y + 0.0007
        rotRef.current.x += velRef.current.x
        rotRef.current.x = Math.max(-0.6, Math.min(0.8, rotRef.current.x))
      }

      if (frame - lastSig > 4 + Math.floor(rng.float() * 7)) {
        spawnSig(); lastSig = frame
        if (sigs.length > 55) sigs.shift()
      }
      for (let i = sigs.length - 1; i >= 0; i--) {
        sigs[i].t += sigs[i].spd
        if (sigs[i].t >= 1) {
          const dest = nodes[edges[sigs[i].ei]?.b]
          if (dest) {
            dest.flash = 9
            if (dest.type === 'hub' && frame % 10 === 0) pushEvt(`SIGNAL → ${dest.agentCode}`)
            else if (frame % 45 === 0) pushEvt(`PKT: ${dest.agentCode}`, true)
          }
          sigs.splice(i, 1)
        }
      }
      for (const n of nodes) { if (n.flash > 0) n.flash-- }

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

      // Stars
      for (const s of stars) {
        if (s.gal) continue
        const x = s.nx * cW, y = s.ny * H
        ctx.beginPath(); ctx.arc(x, y, s.sz, 0, Math.PI * 2)
        ctx.fillStyle = `rgba(255,255,255,${s.op})`; ctx.fill()
      }
      ctx.filter = 'blur(1.5px)'
      for (const s of stars) {
        if (!s.gal) continue
        const x = s.nx * cW, y = s.ny * H
        const g = ctx.createRadialGradient(x, y, 0, x, y, s.sz * 6)
        g.addColorStop(0, `rgba(210,230,255,${s.op * 0.5})`)
        g.addColorStop(1, 'rgba(200,220,255,0)')
        ctx.beginPath(); ctx.arc(x, y, s.sz * 6, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill()
      }
      ctx.filter = 'none'

      const haze = ctx.createRadialGradient(cW/2, H/2, 0, cW/2, H/2, Math.min(cW, H) * 0.62)
      haze.addColorStop(0, 'rgba(18,18,26,0.5)')
      haze.addColorStop(0.6, 'rgba(8,8,14,0.25)')
      haze.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = haze; ctx.fillRect(0, 0, cW, H)

      const sorted = [...nodes].sort((a, b) => a.sc - b.sc)
      const sortedEdges = [...edges].sort((a, b) =>
        ((nodes[a.a].sc + nodes[a.b].sc) - (nodes[b.a].sc + nodes[b.b].sc))
      )

      // Cluster nebula glows (behind everything)
      const seenClusters = new Set<number>()
      for (const n of sorted) {
        if (n.type !== 'hub' || seenClusters.has(n.clusterId)) continue
        seenClusters.add(n.clusterId)
        if (n.sx < -200 || n.sx > cW + 200) continue
        const pulse = 0.4 + 0.6 * Math.sin(t * 0.4 + n.clusterId * 1.3)
        const nebulaR = 180 * n.sc + pulse * 40
        const nebula = ctx.createRadialGradient(n.sx, n.sy, 0, n.sx, n.sy, nebulaR)
        nebula.addColorStop(0,   `rgba(160,200,255,${0.04 * n.sc * pulse})`)
        nebula.addColorStop(0.4, `rgba(120,170,240,${0.025 * n.sc})`)
        nebula.addColorStop(1,   'rgba(80,120,200,0)')
        ctx.beginPath(); ctx.arc(n.sx, n.sy, nebulaR, 0, Math.PI * 2)
        ctx.fillStyle = nebula; ctx.fill()
      }

      for (const e of sortedEdges) drawEdge(e, t)
      for (const sig of sigs) drawSig(sig)
      for (const n of sorted) {
        if (n.hasRing && n.sc > 0.08 && n.sx > -200 && n.sx < cW + 200) drawRing(n, t)
      }
      for (const n of sorted) {
        if (n.sx < -200 || n.sx > cW + 200 || n.sy < -200 || n.sy > H + 200) continue
        drawNode(n, t)
      }

      const vig = ctx.createRadialGradient(cW/2, H/2, Math.min(cW,H)*0.36, cW/2, H/2, Math.min(cW,H)*0.88)
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

    setTimeout(() => {
      pushEvt('MEMBRANE NETWORK ONLINE')
      pushEvt(`NODES: ${nodes.length}`, true)
      pushEvt(`EDGES: ${edges.length}`, true)
    }, 500)

    return () => {
      cancelAnimationFrame(rafRef.current)
      ro.disconnect()
      clearInterval(si); clearInterval(li); clearInterval(sti)
      cvs.removeEventListener('click', onCanvasClick)
      cvs.removeEventListener('mousemove', onCanvasMove)
      cvs.removeEventListener('wheel', handleWheel)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ─── Sync feed preview events ──────────────────────────────────────────────
  useEffect(() => {
    feedPreview.forEach(m => {
      const d = new Date()
      const ts = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`
      logBuf.current.unshift({ id: logId.current++, msg: `${m.agentId}: ${m.text.slice(0, 60)}`, dim: false, ts })
      if (logBuf.current.length > 30) logBuf.current.pop()
    })
  }, [feedPreview])

  // ─── JSX ──────────────────────────────────────────────────────────────────
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

      {/* Drag hint */}
      <div
        className="absolute top-8 pointer-events-none"
        style={{ left: 0, right: PANEL_W, textAlign: 'center' }}
      >
        <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 8, color: 'rgba(255,255,255,0.18)', letterSpacing: '0.3em', textTransform: 'uppercase' }}>
          drag to rotate · click node to inspect
        </span>
      </div>

      {/* Round indicator */}
      <div className="absolute top-6 left-6 pointer-events-none flex items-center gap-3">
        <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 9, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.18em', textTransform: 'uppercase' }}>
          Round {round}
        </span>
        <div style={{ display: 'flex', gap: 4 }}>
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              style={{
                width: 18, height: 2, borderRadius: 1,
                background: i < round ? 'rgba(255,255,255,0.7)' : 'rgba(255,255,255,0.12)',
                transition: 'background 0.4s ease',
              }}
            />
          ))}
        </div>
      </div>

      {/* Status bottom */}
      <div
        className="absolute bottom-10 pointer-events-none flex flex-col items-center gap-3"
        style={{ left: 0, right: PANEL_W, margin: '0 auto' }}
      >
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {[0, .3, .6].map(d => (
            <div key={d} style={{ width: 4, height: 4, borderRadius: '50%', background: 'rgba(255,255,255,0.30)', animation: 'pulse 1.5s ease-in-out infinite', animationDelay: `${d}s` }} />
          ))}
        </div>
        <div
          ref={statusRef}
          style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 10, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.18em', textTransform: 'uppercase', textAlign: 'center', minWidth: 300 }}
        >
          Mapping agent network topology…
        </div>
      </div>

      {/* ── Side Panel ────────────────────────────────────────────────────────── */}
      <div
        data-panel="true"
        className="absolute top-0 right-0 h-full flex flex-col"
        style={{ width: PANEL_W, background: 'rgba(3,3,3,0.95)', borderLeft: '1px solid rgba(255,255,255,0.07)' }}
      >
        <div style={{ padding: '20px 16px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 8, color: 'rgba(255,255,255,0.18)', letterSpacing: '0.3em', textTransform: 'uppercase', marginBottom: 12 }}>
            Membrane Network Monitor
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            {(['live', 'node'] as const).map(t => (
              <button key={t} onClick={() => setTab(t)}
                style={{
                  flex: 1, padding: '6px 0', fontFamily: 'var(--font-geist-mono)', fontSize: 8,
                  letterSpacing: '0.2em', textTransform: 'uppercase', cursor: 'pointer',
                  background: tab === t ? 'rgba(255,255,255,0.08)' : 'transparent',
                  color:      tab === t ? 'rgba(255,255,255,0.75)' : 'rgba(255,255,255,0.2)',
                  border:     tab === t ? '1px solid rgba(255,255,255,0.16)' : '1px solid rgba(255,255,255,0.05)',
                }}
              >
                {t === 'live' ? 'Live Data' : 'Node Info'}
              </button>
            ))}
          </div>
        </div>

        {tab === 'live' && (
          <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            {/* Stats grid */}
            <div style={{ padding: '12px 16px', borderBottom: '1px solid rgba(255,255,255,0.05)', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 12px' }}>
              {[
                { l: 'SIGNALS/S', v: String(stats.sig) },
                { l: 'ACTIVE PKT', v: String(stats.pkt) },
                { l: 'MESH DENS',  v: stats.den },
                { l: 'NODES',      v: String(nodesRef.current.length) },
              ].map(({ l, v }) => (
                <div key={l}>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.16)', marginBottom: 2, letterSpacing: '0.1em' }}>{l}</div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 13, color: 'rgba(255,255,255,0.62)' }}>{v}</div>
                </div>
              ))}
            </div>
            {/* Breakdown */}
            <div style={{ padding: '10px 16px', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
              <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.45)', letterSpacing: '0.1em', marginBottom: 8 }}>BREAKDOWN</div>
              {[{l:'Hub',c:'#ffffff',p:6},{l:'Analyst',c:'#b8b8b8',p:32},{l:'Sensor',c:'#606060',p:62}].map(({l,c,p}) => (
                <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <div style={{ width: 6, height: 6, borderRadius: '50%', background: c, flexShrink: 0 }} />
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 8, color: 'rgba(255,255,255,0.55)', width: 52, flexShrink: 0 }}>{l}</span>
                  <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.05)' }}>
                    <div style={{ height: '100%', width: `${p}%`, background: c, opacity: 0.4 }} />
                  </div>
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.14)' }}>{p}%</span>
                </div>
              ))}
            </div>
            {/* Event log */}
            <div style={{ padding: '10px 12px 4px', fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.14)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>Event Log</div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 12px', display: 'flex', flexDirection: 'column', gap: 2, minHeight: 0, scrollbarWidth: 'none' }}>
              {evts.map(e => (
                <div key={e.id} style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 6, color: 'rgba(255,255,255,0.30)', flexShrink: 0 }}>{e.ts}</span>
                  <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 8, lineHeight: 1.4, wordBreak: 'break-all', color: e.dim ? 'rgba(200,200,200,0.75)' : 'rgba(255,255,255,0.92)' }}>
                    {e.msg}
                  </span>
                </div>
              ))}
              {evts.length === 0 && <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 8, color: 'rgba(255,255,255,0.10)', textAlign: 'center', marginTop: 16 }}>Initializing…</div>}
            </div>
          </div>
        )}

        {tab === 'node' && (
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
            {sel ? (
              <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.16)', letterSpacing: '0.2em', marginBottom: 4 }}>AGENT ID</div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 14, color: 'white', fontWeight: 700 }}>{sel.label}</div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 9, color: 'rgba(255,255,255,0.28)', marginTop: 2 }}>{sel.agentCode}</div>
                </div>
                <div style={{ height: 1, background: 'rgba(255,255,255,0.06)' }} />
                {[{ k: 'Type', v: sel.type }, { k: 'Role', v: sel.role }, { k: 'Layer', v: sel.segment }].map(({ k, v }) => (
                  <div key={k}>
                    <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.16)', letterSpacing: '0.2em', marginBottom: 2 }}>{k}</div>
                    <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 9, color: 'rgba(255,255,255,0.48)', lineHeight: 1.5 }}>{v}</div>
                  </div>
                ))}
                <div style={{ height: 1, background: 'rgba(255,255,255,0.06)' }} />
                <div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.16)', letterSpacing: '0.2em', marginBottom: 8 }}>METRICS</div>
                  {[{ k: 'Primary', v: sel.metric }, { k: 'Connections', v: String(sel.conns) }, { k: 'Status', v: 'ACTIVE' }, { k: 'Uptime', v: '99.97%' }].map(({ k, v }) => (
                    <div key={k} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                      <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 8, color: 'rgba(255,255,255,0.18)' }}>{k}</span>
                      <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 9, color: 'rgba(255,255,255,0.65)' }}>{v}</span>
                    </div>
                  ))}
                </div>
                <div style={{ height: 1, background: 'rgba(255,255,255,0.06)' }} />
                <div>
                  <div style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.16)', letterSpacing: '0.2em', marginBottom: 4 }}>FUNCTION</div>
                  <p style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 8, color: 'rgba(255,255,255,0.26)', lineHeight: 1.6, margin: 0 }}>
                    {sel.type === 'Hub Orchestrator'
                      ? 'Coordinates signal routing across the Membrane layer. Aggregates sub-agent inferences into the master consensus synthesis.'
                      : sel.type === 'Analyst Agent'
                      ? `Processes ${sel.role} events. Outputs structured debate positions and confidence-weighted policy vectors.`
                      : `Monitors "${sel.role}" feeds in real-time. Emits polarity scores into the Watchdog classification chain.`}
                  </p>
                </div>
              </div>
            ) : (
              <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, padding: 24, textAlign: 'center' }}>
                <div style={{ fontSize: 28, color: 'rgba(255,255,255,0.05)' }}>◎</div>
                <p style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 8, color: 'rgba(255,255,255,0.16)', lineHeight: 1.6, margin: 0 }}>
                  Click any node in the graph<br />to inspect its role and metrics
                </p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                  {[{c:'#ffffff',l:'Hub — orchestrators'},{c:'#b8b8b8',l:'Analyst — debate'},{c:'#606060',l:'Sensor — signals'}].map(({c,l}) => (
                    <div key={l} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ width: 6, height: 6, borderRadius: '50%', background: c }} />
                      <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.16)' }}>{l}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Footer */}
        <div style={{ padding: '12px 16px', borderTop: '1px solid rgba(255,255,255,0.06)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {onOpenFeed ? (
            <button
              onClick={onOpenFeed}
              style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.2em', background: 'none', border: 'none', cursor: 'pointer', textTransform: 'uppercase' }}
            >
              Open Feed →
            </button>
          ) : (
            <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.10)', letterSpacing: '0.2em', textTransform: 'uppercase' }}>Membrane Engine</span>
          )}
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <div style={{ width: 4, height: 4, borderRadius: '50%', background: 'rgba(255,255,255,0.25)', animation: 'pulse 2s ease-in-out infinite' }} />
            <span style={{ fontFamily: 'var(--font-geist-mono)', fontSize: 7, color: 'rgba(255,255,255,0.25)', letterSpacing: '0.15em' }}>LIVE</span>
          </div>
        </div>
      </div>
    </div>
  )
}
