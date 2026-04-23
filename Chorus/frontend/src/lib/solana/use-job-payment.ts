'use client'

import { useCallback, useState } from 'react'
import { useConnection, useWallet } from '@solana/wallet-adapter-react'
import { PublicKey, Transaction } from '@solana/web3.js'

import { ESCROW_PROGRAM_ID, USDC_MINT, PAYMENT_ENABLED } from './env'
import { buildDepositInstruction, hexToBytes32 } from './escrow-ix'

export interface Quote {
  job_id: string
  job_id_hex: string
  subtotal_uc: number
  platform_fee_uc: number
  total_uc: number
  expires_at: number
  program_id: string
  usdc_mint: string
  cluster: string
}

export interface PayAndConfirmResult {
  job_id: string
  tx_signature: string
  payer: string
  amount_uc: number
  slot: number
}

interface Deps {
  orchestratorBaseUrl: string
  workspaceToken?: string
  workspaceId?: string
}

function authHeaders(deps: Deps): HeadersInit {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (deps.workspaceId) h['X-Chorus-Workspace'] = deps.workspaceId
  if (deps.workspaceToken) h['Authorization'] = `Bearer ${deps.workspaceToken}`
  return h
}

export function useJobPayment(deps: Deps) {
  const { connection } = useConnection()
  const { publicKey, sendTransaction, connected } = useWallet()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const enabled = PAYMENT_ENABLED

  const quote = useCallback(
    async (prompt: string, agentCount: number, models: string[]): Promise<Quote> => {
      const res = await fetch(`${deps.orchestratorBaseUrl}/jobs/quote`, {
        method: 'POST',
        headers: authHeaders(deps),
        body: JSON.stringify({ prompt, agent_count: agentCount, models }),
      })
      if (!res.ok) throw new Error(`quote_failed: ${res.status} ${await res.text()}`)
      return (await res.json()) as Quote
    },
    [deps],
  )

  const payAndConfirm = useCallback(
    async (q: Quote): Promise<PayAndConfirmResult> => {
      if (!enabled) throw new Error('payment_disabled_missing_env')
      if (!connected || !publicKey) throw new Error('wallet_not_connected')
      setBusy(true)
      setError(null)
      try {
        const programId = new PublicKey(ESCROW_PROGRAM_ID)
        const mint = new PublicKey(USDC_MINT)
        const jobIdBytes = hexToBytes32(q.job_id_hex)

        const ix = await buildDepositInstruction({
          programId,
          payer: publicKey,
          mint,
          jobIdBytes,
          amountUc: BigInt(q.total_uc),
        })

        const tx = new Transaction().add(ix)
        tx.feePayer = publicKey
        const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash()
        tx.recentBlockhash = blockhash

        const sig = await sendTransaction(tx, connection)
        await connection.confirmTransaction(
          { signature: sig, blockhash, lastValidBlockHeight },
          'confirmed',
        )

        const res = await fetch(`${deps.orchestratorBaseUrl}/jobs/${q.job_id}/confirm`, {
          method: 'POST',
          headers: authHeaders(deps),
          body: JSON.stringify({ tx_signature: sig, payer: publicKey.toBase58() }),
        })
        if (!res.ok) {
          const body = await res.text()
          throw new Error(`confirm_failed: ${res.status} ${body}`)
        }
        return (await res.json()) as PayAndConfirmResult
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        setError(msg)
        throw e
      } finally {
        setBusy(false)
      }
    },
    [connection, deps, enabled, publicKey, connected, sendTransaction],
  )

  return { enabled, busy, error, quote, payAndConfirm, connected, publicKey }
}
