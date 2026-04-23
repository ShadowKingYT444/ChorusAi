# chorus_escrow

Per-prompt USDC escrow on Solana for Chorus. A payer locks USDC under a
`job_id` (32-byte id); the platform authority settles by paying suppliers
and the platform treasury; any remainder refunds to the payer. If no
settlement happens within `MIN_RECLAIM_SECS` (24 h), the payer can reclaim
the full balance.

## Instructions

- `initialize(platform_fee_bps)` — one-time setup. Stores authority, treasury token account, USDC mint, fee.
- `set_authority(new_authority)` — rotate the settlement key.
- `set_fee(platform_fee_bps)` — update the fee (≤ 10_000 bps = 100%).
- `deposit(job_id, amount)` — payer locks USDC for a quoted job. Creates a per-job escrow PDA + vault token account.
- `settle(job_id, shares)` — authority distributes supplier shares + platform fee, refunds payer the remainder, and closes the escrow account. Supplier token accounts are passed in `remaining_accounts` in the same order as `shares`.
- `reclaim(job_id)` — payer recovers funds after 24 h if settle never happened.

## Deploy (devnet)

Prereqs: `rustup`, `solana-cli 1.18+`, `anchor-cli 0.30.1`, `yarn`.

```bash
cd Chorus/chorus_escrow
yarn install
solana config set --url devnet
solana-keygen new -o ~/.config/solana/id.json   # if needed
solana airdrop 2

anchor build
anchor keys sync                                # writes real program id into lib.rs + Anchor.toml
anchor build                                    # rebuild with the new id
anchor deploy --provider.cluster devnet

# copy the printed Program Id into the backend + frontend env:
#   ORC_ESCROW_PROGRAM_ID=<pubkey>
#   NEXT_PUBLIC_ESCROW_PROGRAM_ID=<pubkey>

# initialize once (see tests/initialize.ts)
anchor run initialize -- --fee-bps 50
```

## Devnet USDC mint

`4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`

(or mint your own test token with `spl-token create-token --decimals 6`.)
