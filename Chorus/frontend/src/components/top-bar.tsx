'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { motion } from 'framer-motion'
import { TextDisperse } from '@/components/ui/text-disperse'
import { useSimulation } from '@/hooks/use-simulation'
import { useReducedMotion } from '@/hooks/use-reduced-motion'

const NAV_ITEMS = [
  { label: 'HOME',    path: '/', appendJob: false },
  { label: 'NETWORK', path: '/app', appendJob: true },
  { label: 'FEED',    path: '/app/feed', appendJob: true },
  { label: 'RESULTS', path: '/app/results', appendJob: true },
] as const

export function TopBar() {
  const pathname = usePathname()
  const job = useSimulation()
  const reducedMotion = useReducedMotion()

  return (
    <header
      className="flex items-center justify-between px-5 shrink-0"
      style={{
        height: 48,
        background: 'rgba(0,0,0,0.95)',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        backdropFilter: 'blur(20px)',
      }}
      role="banner"
    >
      {/* Left — logotype */}
      <motion.div
        whileTap={{ scale: reducedMotion ? 1 : 0.97 }}
        transition={{ duration: 0.15, ease: [0.23, 1, 0.32, 1] }}
        className="w-[200px] shrink-0"
      >
        <Link href="/" className="flex items-center gap-2.5 cursor-pointer transition-opacity duration-150 hover:opacity-80">
          <span className="font-mono font-bold text-sm text-white leading-none" style={{ letterSpacing: '-0.02em' }}>
            DL
          </span>
          <span className="text-white/20 font-mono text-xs">|</span>
          <TextDisperse className="font-sans text-sm text-white/50 tracking-tight w-auto ml-1 -mt-0.5" style={{ fontSize: '14px' }}>
            CHORUS
          </TextDisperse>
        </Link>
      </motion.div>

      {/* Center — nav & context */}
      <div className="flex flex-1 items-center justify-center gap-8 overflow-hidden">
        <nav
          className="flex items-center rounded-sm overflow-hidden shrink-0 relative"
          style={{ border: '1px solid rgba(255,255,255,0.08)' }}
          aria-label="Main Navigation"
        >
          {NAV_ITEMS.map((item, idx) => {
            const isActive = item.path === '/' ? pathname === '/' : pathname === item.path
            const jobSuffix =
              item.appendJob && job?.jobId ? `?job_id=${encodeURIComponent(job.jobId)}` : ''

            return (
              <Link
                key={item.label}
                href={item.path === '/' ? '/' : `${item.path}${jobSuffix}`}
                className="relative px-4 min-h-[44px] md:min-h-0 md:py-1.5 font-sans text-[11px] tracking-wide focus-visible:outline-none flex items-center"
                style={{
                  color: isActive ? 'rgba(255,255,255,0.92)' : 'rgba(255,255,255,0.50)',
                  borderRight: idx < NAV_ITEMS.length - 1 ? '1px solid rgba(255,255,255,0.08)' : 'none',
                  transition: 'color 150ms ease-out',
                }}
              >
                {/* Sliding active background */}
                {isActive && (
                  <motion.span
                    layoutId={reducedMotion ? undefined : 'nav-active-bg'}
                    className="absolute inset-0"
                    style={{ background: 'rgba(255,255,255,0.07)' }}
                    transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
                  />
                )}
                <span className="relative z-10">{item.label}</span>
              </Link>
            )
          })}
        </nav>

        {job && (
          <div
            className="hidden lg:flex items-center whitespace-nowrap overflow-hidden"
            style={{
              maxWidth: 320,
              WebkitMaskImage: 'linear-gradient(90deg, transparent, #000 10%, #000 90%, transparent)'
            }}
          >
            <span className="font-mono text-[10px] text-white/40 tracking-[0.15em] uppercase truncate">
              SIMULATING:{' '}
              <span className="text-white/75 normal-case tracking-normal">
                &quot;{job.prompt}&quot;
              </span>
            </span>
          </div>
        )}
      </div>

      {/* Right — status (static dot — always online, no decorative pulse) */}
      <div className="flex items-center gap-2 w-[200px] justify-end">
        <span
          className="w-1.5 h-1.5 rounded-full shrink-0"
          style={{ background: 'var(--color-secure)' }}
          aria-hidden
        />
        <span className="font-mono text-[10px] tracking-widest" style={{ color: 'rgba(255,255,255,0.50)' }}>
          NETWORK ONLINE
        </span>
      </div>
    </header>
  )
}
