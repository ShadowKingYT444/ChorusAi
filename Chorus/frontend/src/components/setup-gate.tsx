'use client'

import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { isSavedModelVerified } from '@/lib/api/orchestrator'

const PUBLIC_PATHS = new Set(['/setup', '/join'])

function isPublicPath(pathname: string | null): boolean {
  if (!pathname) return false
  if (PUBLIC_PATHS.has(pathname)) return true
  if (pathname.startsWith('/setup/')) return true
  if (pathname.startsWith('/join/')) return true
  if (pathname.startsWith('/api/')) return true
  return false
}

export function SetupGate({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const publicPath = isPublicPath(pathname)
  const [decision, setDecision] = useState<'pending' | 'allow' | 'deny'>(
    publicPath ? 'allow' : 'pending',
  )

  useEffect(() => {
    if (publicPath) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDecision('allow')
      return
    }
    if (isSavedModelVerified()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDecision('allow')
    } else {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDecision('deny')
      router.replace('/setup')
    }
  }, [publicPath, router])

  if (decision !== 'allow') return null
  return <>{children}</>
}
