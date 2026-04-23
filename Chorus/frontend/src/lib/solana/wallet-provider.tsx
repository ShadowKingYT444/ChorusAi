'use client'

import { useMemo, type ReactNode } from 'react'
import { ConnectionProvider, WalletProvider } from '@solana/wallet-adapter-react'
import { WalletModalProvider } from '@solana/wallet-adapter-react-ui'
import { PhantomWalletAdapter, SolflareWalletAdapter } from '@solana/wallet-adapter-wallets'
import { WalletAdapterNetwork } from '@solana/wallet-adapter-base'

import { SOLANA_CLUSTER, SOLANA_RPC } from './env'

import '@solana/wallet-adapter-react-ui/styles.css'

export function SolanaProviders({ children }: { children: ReactNode }) {
  const network =
    SOLANA_CLUSTER === 'mainnet-beta'
      ? WalletAdapterNetwork.Mainnet
      : SOLANA_CLUSTER === 'testnet'
      ? WalletAdapterNetwork.Testnet
      : WalletAdapterNetwork.Devnet

  const wallets = useMemo(
    () => [new PhantomWalletAdapter(), new SolflareWalletAdapter({ network })],
    [network],
  )

  return (
    <ConnectionProvider endpoint={SOLANA_RPC}>
      <WalletProvider wallets={wallets} autoConnect>
        <WalletModalProvider>{children}</WalletModalProvider>
      </WalletProvider>
    </ConnectionProvider>
  )
}
