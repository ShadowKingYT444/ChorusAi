// Hand-crafted `deposit` instruction for the Chorus escrow program.
// Avoids shipping an IDL before the program is deployed; the frontend only
// needs the program id + USDC mint in env.

import {
  PublicKey,
  SystemProgram,
  SYSVAR_RENT_PUBKEY,
  TransactionInstruction,
} from '@solana/web3.js'
import { TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID, getAssociatedTokenAddress } from '@solana/spl-token'

const CONFIG_SEED = new TextEncoder().encode('config')
const JOB_SEED = new TextEncoder().encode('job')
const VAULT_SEED = new TextEncoder().encode('vault')

async function anchorDiscriminator(ixName: string): Promise<Buffer> {
  const preimage = new TextEncoder().encode(`global:${ixName}`)
  const buf = new ArrayBuffer(preimage.byteLength)
  new Uint8Array(buf).set(preimage)
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return Buffer.from(new Uint8Array(digest).slice(0, 8))
}

export function hexToBytes32(hex: string): Uint8Array {
  const clean = hex.startsWith('0x') ? hex.slice(2) : hex
  if (clean.length !== 64) throw new Error(`expected 32-byte hex, got ${clean.length / 2} bytes`)
  const out = new Uint8Array(32)
  for (let i = 0; i < 32; i++) out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16)
  return out
}

export function deriveConfigPda(programId: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([CONFIG_SEED], programId)
}

export function deriveJobPda(programId: PublicKey, jobIdBytes: Uint8Array): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([JOB_SEED, jobIdBytes], programId)
}

export function deriveVaultPda(programId: PublicKey, jobPda: PublicKey): [PublicKey, number] {
  return PublicKey.findProgramAddressSync([VAULT_SEED, jobPda.toBuffer()], programId)
}

export async function buildDepositInstruction(params: {
  programId: PublicKey
  payer: PublicKey
  mint: PublicKey
  jobIdBytes: Uint8Array
  amountUc: bigint
}): Promise<TransactionInstruction> {
  const { programId, payer, mint, jobIdBytes, amountUc } = params
  if (jobIdBytes.length !== 32) throw new Error('jobIdBytes must be 32 bytes')

  const [configPda] = deriveConfigPda(programId)
  const [jobPda] = deriveJobPda(programId, jobIdBytes)
  const [vaultPda] = deriveVaultPda(programId, jobPda)
  const payerAta = await getAssociatedTokenAddress(mint, payer, false)

  const discriminator = await anchorDiscriminator('deposit')
  const amountBuf = Buffer.alloc(8)
  amountBuf.writeBigUInt64LE(amountUc, 0)
  const data = Buffer.concat([discriminator, Buffer.from(jobIdBytes), amountBuf])

  return new TransactionInstruction({
    programId,
    keys: [
      { pubkey: configPda, isSigner: false, isWritable: false },
      { pubkey: payer, isSigner: true, isWritable: true },
      { pubkey: mint, isSigner: false, isWritable: false },
      { pubkey: jobPda, isSigner: false, isWritable: true },
      { pubkey: vaultPda, isSigner: false, isWritable: true },
      { pubkey: payerAta, isSigner: false, isWritable: true },
      { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: ASSOCIATED_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: SYSVAR_RENT_PUBKEY, isSigner: false, isWritable: false },
    ],
    data,
  })
}
