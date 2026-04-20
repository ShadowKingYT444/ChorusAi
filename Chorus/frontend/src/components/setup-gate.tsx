'use client'

import { usePathname, useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { isOrchestratorConfigured, isSavedModelVerified } from '@/lib/api/orchestrator'
import { readWorkspaceId, readWorkspaceToken } from '@/lib/workspace-config'

const PUBLIC_PATHS = new Set(['/setup', '/join'])

function isPublicPath(pathname: string | null): boolean {
  if (!pathname) return false
  if (PUBLIC_PATHS.has(pathname)) return true
  if (pathname.startsWith('/setup/')) return true
  if (pathname.startsWith('/join/')) return true
  if (pathname.startsWith('/api/')) return true
  return false
}

function hasCompletedSetup(): boolean {
  return (
    isSavedModelVerified() &&
    isOrchestratorConfigured() &&
    readWorkspaceId().trim().length > 0 &&
    readWorkspaceToken().trim().length > 0
  )
}

export function SetupGate({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const publicPath = isPublicPath(pathname)
  const allowed = publicPath || hasCompletedSetup()

  useEffect(() => {
    if (!publicPath && !allowed) {
      router.replace('/setup')
    }
  }, [allowed, publicPath, router])

  if (!allowed) return null
  return <>{children}</>
}
