'use client'

import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

interface Beam {
  x: number
  y: number
  width: number
  length: number
  angle: number
  speed: number
  opacity: number
  hue: number
  pulse: number
  pulseSpeed: number
}

function createBeam(width: number, height: number): Beam {
  const angle = -35 + Math.random() * 10
  return {
    x: Math.random() * width * 1.5 - width * 0.25,
    y: Math.random() * height * 1.5 - height * 0.25,
    width: 30 + Math.random() * 60,
    length: height * 2.5,
    angle,
    speed: 0.6 + Math.random() * 1.2,
    opacity: 0.12 + Math.random() * 0.16,
    hue: 190 + Math.random() * 70,
    pulse: Math.random() * Math.PI * 2,
    pulseSpeed: 0.02 + Math.random() * 0.03,
  }
}

const OPACITY_MAP = {
  subtle: 0.7,
  medium: 0.85,
  strong: 1,
} as const

interface BeamsBackgroundProps {
  className?: string
  intensity?: keyof typeof OPACITY_MAP
}

export function BeamsBackground({
  className,
  intensity = 'strong',
}: BeamsBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const beamsRef = useRef<Beam[]>([])
  const rafRef = useRef<number>(0)
  const TOTAL_BEAMS = 30

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const opacityMultiplier = OPACITY_MAP[intensity]

    function updateSize() {
      if (!canvas) return
      const dpr = window.devicePixelRatio || 1
      canvas.width = window.innerWidth * dpr
      canvas.height = window.innerHeight * dpr
      canvas.style.width = `${window.innerWidth}px`
      canvas.style.height = `${window.innerHeight}px`
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)
      beamsRef.current = Array.from({ length: TOTAL_BEAMS }, () =>
        createBeam(window.innerWidth, window.innerHeight)
      )
    }

    function resetBeam(beam: Beam, index: number) {
      const column = index % 3
      const spacing = window.innerWidth / 3
      beam.y = window.innerHeight + 100
      beam.x = column * spacing + spacing / 2 + (Math.random() - 0.5) * spacing * 0.5
      beam.width = 100 + Math.random() * 100
      beam.speed = 0.5 + Math.random() * 0.4
      beam.hue = 190 + (index * 70) / TOTAL_BEAMS
      beam.opacity = 0.2 + Math.random() * 0.1
      return beam
    }

    function drawBeam(beam: Beam) {
      if (!ctx) return
      ctx.save()
      ctx.translate(beam.x, beam.y)
      ctx.rotate((beam.angle * Math.PI) / 180)

      const pulsingOpacity =
        beam.opacity * (0.8 + Math.sin(beam.pulse) * 0.2) * opacityMultiplier

      const gradient = ctx.createLinearGradient(0, 0, 0, beam.length)
      gradient.addColorStop(0,   `hsla(${beam.hue}, 85%, 65%, 0)`)
      gradient.addColorStop(0.1, `hsla(${beam.hue}, 85%, 65%, ${pulsingOpacity * 0.5})`)
      gradient.addColorStop(0.4, `hsla(${beam.hue}, 85%, 65%, ${pulsingOpacity})`)
      gradient.addColorStop(0.6, `hsla(${beam.hue}, 85%, 65%, ${pulsingOpacity})`)
      gradient.addColorStop(0.9, `hsla(${beam.hue}, 85%, 65%, ${pulsingOpacity * 0.5})`)
      gradient.addColorStop(1,   `hsla(${beam.hue}, 85%, 65%, 0)`)

      ctx.fillStyle = gradient
      ctx.fillRect(-beam.width / 2, 0, beam.width, beam.length)
      ctx.restore()
    }

    function animate() {
      if (!canvas || !ctx) return
      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight)
      ctx.filter = 'blur(35px)'

      beamsRef.current.forEach((beam, index) => {
        beam.y -= beam.speed
        beam.pulse += beam.pulseSpeed
        if (beam.y + beam.length < -100) resetBeam(beam, index)
        drawBeam(beam)
      })

      rafRef.current = requestAnimationFrame(animate)
    }

    updateSize()
    window.addEventListener('resize', updateSize)
    animate()

    return () => {
      window.removeEventListener('resize', updateSize)
      cancelAnimationFrame(rafRef.current)
    }
  }, [intensity])

  return (
    <div className={cn('absolute inset-0 overflow-hidden', className)}>
      <canvas
        ref={canvasRef}
        className="absolute inset-0"
        style={{ filter: 'blur(15px)' }}
      />
      <motion.div
        className="absolute inset-0 bg-black/10"
        animate={{ opacity: [0.05, 0.15, 0.05] }}
        transition={{ duration: 10, ease: 'easeInOut', repeat: Infinity }}
        style={{ backdropFilter: 'blur(50px)' }}
      />
    </div>
  )
}
