import type { Metadata } from 'next'
import { Suspense } from 'react'
import { JobRuntimeProvider } from '@/lib/runtime/job-runtime-provider'

export const metadata: Metadata = {
  title: 'MEMBRANE - Console',
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={null}>
      <JobRuntimeProvider>{children}</JobRuntimeProvider>
    </Suspense>
  )
}
