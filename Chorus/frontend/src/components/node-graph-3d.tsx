'use client'

import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import Link from 'next/link'
import type { ClusterEdge, ClusterEntry, PeerEntry } from '@/lib/api/orchestrator'
import { useClusters } from '@/hooks/use-clusters'

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
  clusterId: string | null
}
interface GEdge {
  a: number
  b: number
  weight: number
  crossCluster: boolean
  intra: boolean // intra-cluster ring fallback edge
}
interface Sig { ei: number; t: number; spd: number }
interface ClusterColor { h: number; s: number; l: number; css: string }

const FOCAL = 750
const PANEL_W = 260
const UNASSIGNED_CLUSTER_ID = '__unassigned__'

// Deterministic hash so positions stay stable across renders for same peer id.
function hash01(s: string, salt: number): number {
  let h = 2166136261 ^ salt
  for (let i = 0; i < s.length; i++) {
    h = (h ^ s.charCodeAt(i)) >>> 0
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) / 4294967296)
}

// Fibonacci-sphere point (unit vector) at index i of n.
function fibSpherePoint(i: number, n: number): { x: number; y: number; z: number } {
  const golden = Math.PI * (3 - Math.sqrt(5))
  const denom = Math.max(1, n - 1)
  const y = n === 1 ? 0 : 1 - (i / denom) * 2
  const radius = Math.sqrt(Math.max(0, 1 - y * y))
  const theta = golden * i
  return { x: Math.cos(theta) * radius, y, z: Math.sin(theta) * radius }
}

function clusterColor(clusterId: string): ClusterColor {
  const h = Math.floor(hash01(clusterId, 0) * 360)
  const s = 70
  const l = 60
  return { h, s, l, css: `hsl(${h} ${s}% ${l}%)` }
}

export interface NodeGraph3DProps {
  peers: PeerEntry[]
  messages?: Array<{ peerId: string; text: string }>
  onOpenFeed?: () => void
  clusters?: ClusterEntry[]
  edges?: ClusterEdge[]
}

export default function NodeGraph3D({
  peers,
  messages = [],
  onOpenFeed,
  clusters: clustersProp,
  edges: edgesProp,
}: NodeGraph3DProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>(0)
  const nodesRef = useRef<GNode[]>([])
  const edgesRef = useRef<GEdge[]>([])
  const edgeWeightTotalRef = useRef(0)
  const clusterColorsRef = useRef<Map<string, ClusterColor>>(new Map())
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

  // Fallback to hook when props aren't supplied.
  const hookState = useClusters()
  const clusters: ClusterEntry[] = clustersProp ?? hookState.clusters
  const realEdges: ClusterEdge[] = edgesProp ?? hookState.edges
  const clustersMode = hookState.mode
  const clustersStats = hookState.stats


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

  // Stable keys so we recompute layout only when the peer/cluster sets change.
  const peerKey = useMemo(
    () => peers.map((p) => p.peer_id).sort().join('|'),
    [peers],
  )
  const clusterKey = useMemo(
    () =>
      clusters
        .map((c) => `${c.id}:${[...c.peer_ids].sort().join(',')}`)
        .sort()
        .join('|'),
    [clusters],
  )
  const edgeKey = useMemo(
    () =>
      realEdges
        .map((e) => {
          const [a, b] = e.source < e.target ? [e.source, e.target] : [e.target, e.source]
          return `${a}-${b}:${e.weight}`
        })
        .sort()
        .join('|'),
    [realEdges],
  )

  // Compute nodes/edges/colors in a memo so render can read sizes, then effect
  // syncs into refs so the animation loop can mutate per-frame transient state
  // (flash, sx/sy/sc) without React churn.
  const graphBuild = useMemo(() => {
    const totalPeers = peers.length
    if (totalPeers === 0) {
      return {
        nodes: [] as GNode[],
        edges: [] as GEdge[],
        colors: new Map<string, ClusterColor>(),
        weightTotal: 0,
      }
    }

    // Map peer_id → clusterId. A peer could belong to many clusters in principle;
    // first-seen wins so colors stay deterministic.
    const peerCluster = new Map<string, string>()
    for (const c of clusters) {
      for (const pid of c.peer_ids) {
        if (!peerCluster.has(pid)) peerCluster.set(pid, c.id)
      }
    }

    // Assign any stray peers to the unassigned bucket.
    const unassigned: string[] = []
    for (const p of peers) {
      if (!peerCluster.has(p.peer_id)) {
        peerCluster.set(p.peer_id, UNASSIGNED_CLUSTER_ID)
        unassigned.push(p.peer_id)
      }
    }

    // Stable ordered list of cluster ids we actually need to place.
    const activeClusterIds: string[] = []
    for (const c of clusters) {
      if (c.peer_ids.some((pid) => peerCluster.get(pid) === c.id)) {
        activeClusterIds.push(c.id)
      }
    }
    if (unassigned.length > 0) activeClusterIds.push(UNASSIGNED_CLUSTER_ID)
    // Deterministic ordering by hash so centroid layout is stable.
    activeClusterIds.sort((a, b) => hash01(a, 42) - hash01(b, 42))

    const rOuter = 280 + Math.min(180, totalPeers * 3)
    const centroidOf = new Map<string, { x: number; y: number; z: number }>()
    for (let i = 0; i < activeClusterIds.length; i++) {
      const unit = fibSpherePoint(i, activeClusterIds.length)
      centroidOf.set(activeClusterIds[i], {
        x: unit.x * rOuter,
        y: unit.y * rOuter,
        z: unit.z * rOuter,
      })
    }

    // Cache cluster colors
    const colors = new Map<string, ClusterColor>()
    for (const cid of activeClusterIds) {
      colors.set(cid, clusterColor(cid))
    }

    // Group peer indices by cluster, preserving peers[] order for stability.
    const peerIdxByCluster = new Map<string, number[]>()
    for (const cid of activeClusterIds) peerIdxByCluster.set(cid, [])
    peers.forEach((p, idx) => {
      const cid = peerCluster.get(p.peer_id) ?? UNASSIGNED_CLUSTER_ID
      const list = peerIdxByCluster.get(cid)
      if (list) list.push(idx)
    })

    const positions: { x: number; y: number; z: number }[] = new Array(peers.length)
    const jitter = 10
    for (const [cid, idxs] of peerIdxByCluster) {
      const centroid = centroidOf.get(cid)
      if (!centroid) continue
      const size = idxs.length
      const rLocal = 40 + Math.sqrt(Math.max(1, size)) * 22
      idxs.forEach((peerIdx, i) => {
        const peerId = peers[peerIdx].peer_id
        const unit = fibSpherePoint(i, size)
        positions[peerIdx] = {
          x: centroid.x + unit.x * rLocal + (hash01(peerId, 1) - 0.5) * jitter,
          y: centroid.y + unit.y * rLocal + (hash01(peerId, 2) - 0.5) * jitter,
          z: centroid.z + unit.z * rLocal + (hash01(peerId, 3) - 0.5) * jitter,
        }
      })
    }

    const nodes: GNode[] = peers.map((p, i) => {
      const cid = peerCluster.get(p.peer_id) ?? null
      return {
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
        clusterId: cid === UNASSIGNED_CLUSTER_ID ? null : cid,
      }
    })

    // Index map: peer_id → internal index
    const idxOf = new Map<string, number>()
    nodes.forEach((n) => idxOf.set(n.peerId, n.id))

    // Build edge list: prefer real co-job edges.
    const edges: GEdge[] = []
    const edgeSet = new Set<string>()
    let weightTotal = 0
    for (const e of realEdges) {
      const ai = idxOf.get(e.source)
      const bi = idxOf.get(e.target)
      if (ai === undefined || bi === undefined || ai === bi) continue
      const [lo, hi] = ai < bi ? [ai, bi] : [bi, ai]
      const key = `${lo}-${hi}`
      if (edgeSet.has(key)) continue
      edgeSet.add(key)
      const na = nodes[lo]
      const nb = nodes[hi]
      const cross =
        !!na.clusterId && !!nb.clusterId && na.clusterId !== nb.clusterId
      const w = Math.max(0.1, e.weight || 1)
      edges.push({ a: lo, b: hi, weight: w, crossCluster: cross, intra: false })
      weightTotal += w
      na.conns++
      nb.conns++
    }

    // Fallback: if nothing, draw a thin intra-cluster ring so the shape is readable.
    if (edges.length < 1) {
      for (const [, idxs] of peerIdxByCluster) {
        if (idxs.length < 2) continue
        for (let i = 0; i < idxs.length; i++) {
          const a = idxs[i]
          const b = idxs[(i + 1) % idxs.length]
          const [lo, hi] = a < b ? [a, b] : [b, a]
          const key = `${lo}-${hi}`
          if (edgeSet.has(key)) continue
          edgeSet.add(key)
          edges.push({ a: lo, b: hi, weight: 0.4, crossCluster: false, intra: true })
          weightTotal += 0.4
          nodes[lo].conns++
          nodes[hi].conns++
        }
      }
    }

    return { nodes, edges, colors, weightTotal }
    // peerKey/clusterKey/edgeKey collapse the identity-heavy deps into stable
    // strings; the underlying arrays are intentionally not in the dep list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peerKey, clusterKey, edgeKey])

  // Sync memoised build into refs for the animation loop + reset selection
  // when the node set shrinks below the selected index.
  useEffect(() => {
    nodesRef.current = graphBuild.nodes
    edgesRef.current = graphBuild.edges
    clusterColorsRef.current = graphBuild.colors
    edgeWeightTotalRef.current = graphBuild.weightTotal
    if (selectedRef.current >= graphBuild.nodes.length) {
      selectedRef.current = -1
      connectedRef.current = new Set()
      setSel(null)
    }
  }, [graphBuild])

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

    // Weighted edge pick, biased by edge.weight so hot edges pulse more.
    function pickEdgeIndex(): number {
      const edges = edgesRef.current
      if (edges.length === 0) return -1
      const total = edgeWeightTotalRef.current
      if (total <= 0) return Math.floor(Math.random() * edges.length)
      let r = Math.random() * total
      for (let i = 0; i < edges.length; i++) {
        r -= edges[i].weight
        if (r <= 0) return i
      }
      return edges.length - 1
    }

    function loop(ts: number) {
      rafRef.current = requestAnimationFrame(loop)
      const t = ts / 1000
      const nodes = nodesRef.current
      const edges = edgesRef.current
      const colors = clusterColorsRef.current

      if (!dragRef.current.active) {
        velRef.current.y *= 0.965
        velRef.current.x *= 0.965
        rotRef.current.y += velRef.current.y + 0.0005
      }

      // Spawn signals (biased by weight)
      if (edges.length > 0 && ts - lastSpawn > 180) {
        const ei = pickEdgeIndex()
        if (ei >= 0) {
          sigs.push({ ei, t: 0, spd: 0.006 + Math.random() * 0.012 })
        }
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

      const selId = selectedRef.current

      // Edges
      for (const e of edges) {
        const na = nodes[e.a], nb = nodes[e.b]
        if (!na || !nb) continue
        if (na.sx < -500 || nb.sx < -500) continue
        const avg = (na.sc + nb.sc) * 0.5
        const isConnected = selId >= 0 && (e.a === selId || e.b === selId)
        const dim = selId >= 0 && !isConnected ? 0.07 : 1
        let alpha =
          Math.min(0.28, avg * 0.3 * (0.5 + Math.min(1.5, e.weight * 0.6))) * dim
        if (e.intra) alpha *= 0.45
        if (isConnected) alpha = Math.min(0.95, alpha * 5)
        if (alpha < 0.005) continue

        let strokeColor: string
        if (e.crossCluster) {
          strokeColor = `rgba(180,230,255,${alpha})`
        } else if (na.clusterId && colors.has(na.clusterId)) {
          const c = colors.get(na.clusterId)!
          strokeColor = `hsla(${c.h}, ${c.s}%, ${c.l}%, ${alpha})`
        } else {
          strokeColor = `rgba(255,255,255,${alpha})`
        }

        const widthBase = Math.max(0.5, Math.min(2.5, e.weight * 0.6))
        const lw = isConnected
          ? Math.max(1, widthBase * 1.4 * Math.max(0.6, avg))
          : Math.max(0.3, widthBase * Math.max(0.4, avg))

        ctx.beginPath()
        ctx.moveTo(na.sx, na.sy)
        ctx.lineTo(nb.sx, nb.sy)
        ctx.strokeStyle = strokeColor
        ctx.lineWidth = lw
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
        const isSelected = selId === n.id
        const isConnected = selId >= 0 && connectedRef.current.has(n.id)
        const focused = selId < 0 || isSelected || isConnected
        const dim = focused ? 1 : 0.14
        let r = n.baseR * n.sc + (isSelected ? 3 : 0)
        if (n.flash > 0) r *= 1.25
        r = Math.max(0.9, r)
        const depth = Math.max(0.3, Math.min(1, n.sc * 1.5))

        const col = n.clusterId ? colors.get(n.clusterId) : undefined

        // Halo
        const haloR = r * 3.6
        const halo = ctx.createRadialGradient(n.sx, n.sy, r * 0.6, n.sx, n.sy, haloR)
        if (col) {
          halo.addColorStop(0, `hsla(${col.h}, ${col.s}%, ${Math.min(85, col.l + 15)}%, ${0.32 * depth * dim})`)
          halo.addColorStop(1, `hsla(${col.h}, ${col.s}%, ${col.l}%, 0)`)
        } else {
          halo.addColorStop(0, `rgba(220,238,255,${0.26 * depth * dim})`)
          halo.addColorStop(1, 'rgba(200,220,255,0)')
        }
        ctx.beginPath(); ctx.arc(n.sx, n.sy, haloR, 0, Math.PI * 2); ctx.fillStyle = halo; ctx.fill()

        // Sphere
        const sph = ctx.createRadialGradient(n.sx - r * 0.3, n.sy - r * 0.3, 0, n.sx, n.sy, r)
        if (col) {
          sph.addColorStop(0, `hsla(0, 0%, 100%, ${depth * dim})`)
          sph.addColorStop(0.5, `hsla(${col.h}, ${col.s}%, ${Math.min(80, col.l + 10)}%, ${0.8 * depth * dim})`)
          sph.addColorStop(1, `hsla(${col.h}, ${col.s}%, ${Math.max(30, col.l - 20)}%, 0)`)
        } else {
          sph.addColorStop(0, `rgba(255,255,255,${depth * dim})`)
          sph.addColorStop(0.5, `rgba(200,225,255,${0.7 * depth * dim})`)
          sph.addColorStop(1, 'rgba(120,160,220,0)')
        }
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
  const selectedClusterColor = sel?.clusterId ? clusterColor(sel.clusterId) : undefined
  const selectedClusterLabel = sel?.clusterId
    ? clusters.find((c) => c.id === sel.clusterId)?.label ?? sel.clusterId
    : null

  const jobsObserved = clustersStats?.total_jobs_observed ?? 0
  const clustersOffline = !showEmpty && clustersMode === 'offline'

  // Legend rows: only clusters that have peers present in the current set.
  const legendRows = useMemo(() => {
    const present = new Set(peers.map((p) => p.peer_id))
    return clusters
      .map((c) => ({
        id: c.id,
        label: c.label,
        count: c.peer_ids.filter((pid) => present.has(pid)).length,
      }))
      .filter((c) => c.count > 0)
      .sort((a, b) => b.count - a.count)
  }, [clusters, peers])

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
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              gap: 8,
            }}
          >
            <span>Chorus Network Monitor</span>
            {clustersOffline && (
              <span style={{ color: 'rgba(220,150,150,0.55)', letterSpacing: '0.2em' }}>
                clusters offline
              </span>
            )}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px 12px' }}>
            <Stat label="PEERS" value={peers.length.toString()} />
            <Stat label="CLUSTERS" value={legendRows.length.toString()} />
            <Stat label="EDGES" value={graphBuild.edges.length.toString()} />
            <Stat label="MODELS" value={new Set(peers.map((p) => p.model)).size.toString()} />
            <Stat label="JOBS" value={jobsObserved.toString()} />
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
            <Row k="Address" v={sel.address ?? '-'} />
            <div>
              <Label>CLUSTER</Label>
              <div
                style={{
                  marginTop: 2,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: selectedClusterColor
                      ? `hsl(${selectedClusterColor.h} ${selectedClusterColor.s}% ${selectedClusterColor.l}%)`
                      : 'rgba(255,255,255,0.25)',
                    flexShrink: 0,
                  }}
                />
                <span
                  style={{
                    fontFamily: 'var(--font-geist-mono)',
                    fontSize: 10,
                    color: 'rgba(255,255,255,0.72)',
                    wordBreak: 'break-all',
                    lineHeight: 1.4,
                  }}
                >
                  {selectedClusterLabel ?? '-'}
                </span>
              </div>
            </div>
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

            {legendRows.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <Label>CLUSTER LEGEND</Label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
                  {legendRows.map((row) => {
                    const c = clusterColor(row.id)
                    return (
                      <div
                        key={row.id}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                        }}
                      >
                        <span
                          style={{
                            width: 6,
                            height: 6,
                            borderRadius: '50%',
                            background: `hsl(${c.h} ${c.s}% ${c.l}%)`,
                            flexShrink: 0,
                          }}
                        />
                        <span
                          style={{
                            fontFamily: 'var(--font-geist-mono)',
                            fontSize: 9,
                            color: 'rgba(255,255,255,0.72)',
                            flex: 1,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {row.label}
                        </span>
                        <span
                          style={{
                            fontFamily: 'var(--font-geist-mono)',
                            fontSize: 8,
                            color: 'rgba(255,255,255,0.35)',
                            flexShrink: 0,
                          }}
                        >
                          {row.count}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
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
