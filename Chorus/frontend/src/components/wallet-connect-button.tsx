'use client'

import { WalletMultiButton } from '@solana/wallet-adapter-react-ui'
import { PAYMENT_ENABLED } from '@/lib/solana/env'

export function WalletConnectButton({ className }: { className?: string }) {
  if (!PAYMENT_ENABLED) return null
  return (
    <div className={className}>
      <WalletMultiButton />
    </div>
  )
}
