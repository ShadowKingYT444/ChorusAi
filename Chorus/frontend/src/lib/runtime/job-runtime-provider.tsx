'use client'

import { createContext, useContext, type ReactNode } from 'react'
import { useSearchParams } from 'next/navigation'
import { useJobRuntime } from '@/lib/runtime/use-job-runtime'
import type { JobRuntimeState } from '@/lib/runtime/types'

const JobRuntimeContext = createContext<JobRuntimeState | null>(null)

function JobRuntimeProviderInner({ children }: { children: ReactNode }) {
  const routeJobId = useSearchParams().get('job_id')
  const runtime = useJobRuntime(routeJobId)
  return <JobRuntimeContext.Provider value={runtime}>{children}</JobRuntimeContext.Provider>
}

/** One subscription per console session so /app ↔ feed ↔ results does not reconnect WS or drop state. */
export function JobRuntimeProvider({ children }: { children: ReactNode }) {
  return <JobRuntimeProviderInner>{children}</JobRuntimeProviderInner>
}

export function useSharedJobRuntime(): JobRuntimeState {
  const ctx = useContext(JobRuntimeContext)
  if (!ctx) {
    throw new Error('useSharedJobRuntime must be used under JobRuntimeProvider (app console routes).')
  }
  return ctx
}
