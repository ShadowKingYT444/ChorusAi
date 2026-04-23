export const SOLANA_CLUSTER = (process.env.NEXT_PUBLIC_SOLANA_CLUSTER ?? 'devnet') as
  | 'devnet'
  | 'mainnet-beta'
  | 'testnet'

export const SOLANA_RPC =
  process.env.NEXT_PUBLIC_SOLANA_RPC ??
  (SOLANA_CLUSTER === 'mainnet-beta'
    ? 'https://api.mainnet-beta.solana.com'
    : SOLANA_CLUSTER === 'testnet'
    ? 'https://api.testnet.solana.com'
    : 'https://api.devnet.solana.com')

export const ESCROW_PROGRAM_ID = process.env.NEXT_PUBLIC_ESCROW_PROGRAM_ID ?? ''
export const USDC_MINT = process.env.NEXT_PUBLIC_USDC_MINT ?? ''

export const PAYMENT_ENABLED = ESCROW_PROGRAM_ID.length > 0 && USDC_MINT.length > 0
