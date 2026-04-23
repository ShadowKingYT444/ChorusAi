'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { motion } from 'framer-motion'
import { Sparkles } from 'lucide-react'
import { useSimulation } from '@/hooks/use-simulation'
import { useReducedMotion } from '@/hooks/use-reduced-motion'
import { WalletConnectButton } from '@/components/wallet-connect-button'

const NAV_ITEMS = [
  { label: 'REVIEW', path: '/', appendJob: false },
  { label: 'TRACE', path: '/app', appendJob: true },
  { label: 'LIVE', path: '/app/feed', appendJob: true },
  { label: 'REPORT', path: '/app/results', appendJob: true },
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
      {/* Left - logotype */}
      <motion.div
        whileTap={{ scale: reducedMotion ? 1 : 0.97 }}
        transition={{ duration: 0.15, ease: [0.23, 1, 0.32, 1] }}
        className="w-[200px] shrink-0"
      >
        <Link href="/" className="flex items-center gap-2.5 cursor-pointer transition-opacity duration-150 hover:opacity-80">
          <span
            className="relative grid place-items-center w-7 h-7 rounded-lg shrink-0"
            style={{
              background: 'linear-gradient(135deg, rgba(180,200,255,0.25), rgba(255,255,255,0.05))',
              border: '1px solid rgba(255,255,255,0.1)',
            }}
          >
            <Sparkles className="w-3.5 h-3.5 text-white/85" />
          </span>
          <span
            className="font-sans text-[14px] text-white/95 tracking-tight"
            style={{ fontWeight: 600 }}
          >
            Chorus
          </span>
        </Link>
      </motion.div>

      {/* Center - nav & context */}
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
              REVIEWING:{' '}
              <span className="text-white/75 normal-case tracking-normal">
                &quot;{job.prompt}&quot;
              </span>
            </span>
          </div>
        )}
      </div>

      {/* Right - status + wallet connect */}
      <div className="flex items-center gap-3 w-[260px] justify-end">
        <WalletConnectButton className="scale-[0.78] origin-right" />
        <span
          className="w-1.5 h-1.5 rounded-full shrink-0"
          style={{ background: 'var(--color-secure)' }}
          aria-hidden
        />
        <span className="font-mono text-[10px] tracking-widest" style={{ color: 'rgba(255,255,255,0.50)' }}>
          PRIVATE REVIEW ONLINE
        </span>
      </div>
    </header>
  )
}
