// Chorus per-prompt escrow. A payer locks USDC under a job_id; the platform
// authority settles by distributing to supplier token accounts and the
// platform treasury, refunding any remainder to the payer. If the platform
// never settles, the payer can reclaim after MIN_RECLAIM_SECS.

use anchor_lang::prelude::*;
use anchor_spl::associated_token::AssociatedToken;
use anchor_spl::token::{self, Mint, Token, TokenAccount, Transfer};

declare_id!("11111111111111111111111111111111");

pub const CONFIG_SEED: &[u8] = b"config";
pub const JOB_SEED: &[u8] = b"job";
pub const VAULT_SEED: &[u8] = b"vault";
pub const MIN_RECLAIM_SECS: i64 = 24 * 60 * 60;
pub const MAX_SHARES_PER_SETTLE: usize = 16;
pub const BPS_DENOMINATOR: u64 = 10_000;

#[program]
pub mod chorus_escrow {
    use super::*;

    pub fn initialize(ctx: Context<Initialize>, platform_fee_bps: u16) -> Result<()> {
        require!(platform_fee_bps as u64 <= BPS_DENOMINATOR, EscrowError::FeeTooHigh);
        let cfg = &mut ctx.accounts.config;
        cfg.authority = ctx.accounts.authority.key();
        cfg.treasury = ctx.accounts.treasury.key();
        cfg.mint = ctx.accounts.mint.key();
        cfg.platform_fee_bps = platform_fee_bps;
        cfg.bump = ctx.bumps.config;
        Ok(())
    }

    pub fn set_authority(ctx: Context<SetAuthority>, new_authority: Pubkey) -> Result<()> {
        ctx.accounts.config.authority = new_authority;
        Ok(())
    }

    pub fn set_fee(ctx: Context<SetAuthority>, platform_fee_bps: u16) -> Result<()> {
        require!(platform_fee_bps as u64 <= BPS_DENOMINATOR, EscrowError::FeeTooHigh);
        ctx.accounts.config.platform_fee_bps = platform_fee_bps;
        Ok(())
    }

    pub fn deposit(ctx: Context<Deposit>, job_id: [u8; 32], amount: u64) -> Result<()> {
        require!(amount > 0, EscrowError::ZeroAmount);
        require!(
            ctx.accounts.payer_ata.mint == ctx.accounts.config.mint,
            EscrowError::WrongMint,
        );

        let cpi = Transfer {
            from: ctx.accounts.payer_ata.to_account_info(),
            to: ctx.accounts.vault_ata.to_account_info(),
            authority: ctx.accounts.payer.to_account_info(),
        };
        token::transfer(
            CpiContext::new(ctx.accounts.token_program.to_account_info(), cpi),
            amount,
        )?;

        let job = &mut ctx.accounts.job;
        job.payer = ctx.accounts.payer.key();
        job.job_id = job_id;
        job.locked = amount;
        job.mint = ctx.accounts.config.mint;
        job.created_at = Clock::get()?.unix_timestamp;
        job.bump = ctx.bumps.job;
        job.vault_bump = ctx.bumps.vault_ata;

        emit!(Deposited {
            job_id,
            payer: job.payer,
            amount,
        });
        Ok(())
    }

    pub fn settle<'info>(
        ctx: Context<'_, '_, '_, 'info, Settle<'info>>,
        job_id: [u8; 32],
        shares: Vec<SupplierShare>,
    ) -> Result<()> {
        require!(
            shares.len() <= MAX_SHARES_PER_SETTLE,
            EscrowError::TooManyShares,
        );

        let job = &ctx.accounts.job;
        require!(job.job_id == job_id, EscrowError::JobIdMismatch);

        let cfg = &ctx.accounts.config;
        let total_locked = job.locked;

        // Platform fee is applied to the sum of supplier shares (gross compute
        // cost), not the locked quote. Anything the payer over-quoted is
        // refunded at the end.
        let mut supplier_subtotal: u64 = 0;
        for s in shares.iter() {
            supplier_subtotal = supplier_subtotal
                .checked_add(s.amount)
                .ok_or(EscrowError::Overflow)?;
        }
        let platform_fee = (supplier_subtotal as u128)
            .checked_mul(cfg.platform_fee_bps as u128)
            .ok_or(EscrowError::Overflow)?
            .checked_div(BPS_DENOMINATOR as u128)
            .ok_or(EscrowError::Overflow)? as u64;
        let gross_with_fee = supplier_subtotal
            .checked_add(platform_fee)
            .ok_or(EscrowError::Overflow)?;
        require!(gross_with_fee <= total_locked, EscrowError::ExceedsLocked);

        let remaining = &ctx.remaining_accounts;
        require!(
            remaining.len() == shares.len(),
            EscrowError::SupplierAccountsMismatch,
        );

        let job_key = ctx.accounts.job.key();
        let vault_bump = ctx.accounts.job.vault_bump;
        let seeds: &[&[u8]] = &[VAULT_SEED, job_key.as_ref(), &[vault_bump]];
        let signer_seeds: &[&[&[u8]]] = &[seeds];

        for (share, acct) in shares.iter().zip(remaining.iter()) {
            require!(share.amount > 0, EscrowError::ZeroShare);
            // The token account is validated downstream by the token program
            // (mint match enforced there). We require writability here.
            require!(acct.is_writable, EscrowError::SupplierNotWritable);
            let cpi = Transfer {
                from: ctx.accounts.vault_ata.to_account_info(),
                to: acct.clone(),
                authority: ctx.accounts.vault_ata.to_account_info(),
            };
            token::transfer(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.to_account_info(),
                    cpi,
                    signer_seeds,
                ),
                share.amount,
            )?;
        }

        if platform_fee > 0 {
            let cpi = Transfer {
                from: ctx.accounts.vault_ata.to_account_info(),
                to: ctx.accounts.treasury_ata.to_account_info(),
                authority: ctx.accounts.vault_ata.to_account_info(),
            };
            token::transfer(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.to_account_info(),
                    cpi,
                    signer_seeds,
                ),
                platform_fee,
            )?;
        }

        let refund = total_locked
            .checked_sub(gross_with_fee)
            .ok_or(EscrowError::Overflow)?;
        if refund > 0 {
            let cpi = Transfer {
                from: ctx.accounts.vault_ata.to_account_info(),
                to: ctx.accounts.payer_ata.to_account_info(),
                authority: ctx.accounts.vault_ata.to_account_info(),
            };
            token::transfer(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.to_account_info(),
                    cpi,
                    signer_seeds,
                ),
                refund,
            )?;
        }

        emit!(Settled {
            job_id,
            supplier_subtotal,
            platform_fee,
            refund,
        });
        Ok(())
    }

    pub fn reclaim(ctx: Context<Reclaim>, job_id: [u8; 32]) -> Result<()> {
        let job = &ctx.accounts.job;
        require!(job.job_id == job_id, EscrowError::JobIdMismatch);
        let now = Clock::get()?.unix_timestamp;
        require!(
            now.saturating_sub(job.created_at) >= MIN_RECLAIM_SECS,
            EscrowError::ReclaimTooEarly,
        );

        let job_key = ctx.accounts.job.key();
        let vault_bump = job.vault_bump;
        let seeds: &[&[u8]] = &[VAULT_SEED, job_key.as_ref(), &[vault_bump]];
        let signer_seeds: &[&[&[u8]]] = &[seeds];

        let amount = ctx.accounts.vault_ata.amount;
        if amount > 0 {
            let cpi = Transfer {
                from: ctx.accounts.vault_ata.to_account_info(),
                to: ctx.accounts.payer_ata.to_account_info(),
                authority: ctx.accounts.vault_ata.to_account_info(),
            };
            token::transfer(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.to_account_info(),
                    cpi,
                    signer_seeds,
                ),
                amount,
            )?;
        }

        emit!(Reclaimed { job_id, amount });
        Ok(())
    }
}

#[account]
pub struct Config {
    pub authority: Pubkey,
    pub treasury: Pubkey,
    pub mint: Pubkey,
    pub platform_fee_bps: u16,
    pub bump: u8,
}

impl Config {
    pub const LEN: usize = 8 + 32 + 32 + 32 + 2 + 1;
}

#[account]
pub struct JobEscrow {
    pub payer: Pubkey,
    pub job_id: [u8; 32],
    pub locked: u64,
    pub mint: Pubkey,
    pub created_at: i64,
    pub bump: u8,
    pub vault_bump: u8,
}

impl JobEscrow {
    pub const LEN: usize = 8 + 32 + 32 + 8 + 32 + 8 + 1 + 1;
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct SupplierShare {
    pub amount: u64,
}

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,
    #[account(
        init,
        payer = authority,
        space = Config::LEN,
        seeds = [CONFIG_SEED],
        bump,
    )]
    pub config: Account<'info, Config>,
    pub mint: Account<'info, Mint>,
    #[account(token::mint = mint)]
    pub treasury: Account<'info, TokenAccount>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct SetAuthority<'info> {
    #[account(mut, seeds = [CONFIG_SEED], bump = config.bump, has_one = authority)]
    pub config: Account<'info, Config>,
    pub authority: Signer<'info>,
}

#[derive(Accounts)]
#[instruction(job_id: [u8; 32])]
pub struct Deposit<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump)]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub payer: Signer<'info>,

    pub mint: Account<'info, Mint>,

    #[account(
        init,
        payer = payer,
        space = JobEscrow::LEN,
        seeds = [JOB_SEED, &job_id],
        bump,
    )]
    pub job: Account<'info, JobEscrow>,

    #[account(
        init,
        payer = payer,
        seeds = [VAULT_SEED, job.key().as_ref()],
        bump,
        token::mint = mint,
        token::authority = vault_ata,
    )]
    pub vault_ata: Account<'info, TokenAccount>,

    #[account(mut, token::mint = mint)]
    pub payer_ata: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
    pub rent: Sysvar<'info, Rent>,
}

#[derive(Accounts)]
#[instruction(job_id: [u8; 32])]
pub struct Settle<'info> {
    #[account(seeds = [CONFIG_SEED], bump = config.bump, has_one = authority)]
    pub config: Account<'info, Config>,

    pub authority: Signer<'info>,

    #[account(
        mut,
        close = payer,
        seeds = [JOB_SEED, &job_id],
        bump = job.bump,
        has_one = payer,
    )]
    pub job: Account<'info, JobEscrow>,

    /// CHECK: refund target; only writability required.
    #[account(mut)]
    pub payer: AccountInfo<'info>,

    #[account(
        mut,
        seeds = [VAULT_SEED, job.key().as_ref()],
        bump = job.vault_bump,
    )]
    pub vault_ata: Account<'info, TokenAccount>,

    #[account(mut, token::mint = config.mint, token::authority = payer.key())]
    pub payer_ata: Account<'info, TokenAccount>,

    #[account(mut, address = config.treasury)]
    pub treasury_ata: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
#[instruction(job_id: [u8; 32])]
pub struct Reclaim<'info> {
    #[account(
        mut,
        close = payer,
        seeds = [JOB_SEED, &job_id],
        bump = job.bump,
        has_one = payer,
    )]
    pub job: Account<'info, JobEscrow>,

    #[account(mut)]
    pub payer: Signer<'info>,

    #[account(
        mut,
        seeds = [VAULT_SEED, job.key().as_ref()],
        bump = job.vault_bump,
    )]
    pub vault_ata: Account<'info, TokenAccount>,

    #[account(mut, token::authority = payer.key())]
    pub payer_ata: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[event]
pub struct Deposited {
    pub job_id: [u8; 32],
    pub payer: Pubkey,
    pub amount: u64,
}

#[event]
pub struct Settled {
    pub job_id: [u8; 32],
    pub supplier_subtotal: u64,
    pub platform_fee: u64,
    pub refund: u64,
}

#[event]
pub struct Reclaimed {
    pub job_id: [u8; 32],
    pub amount: u64,
}

#[error_code]
pub enum EscrowError {
    #[msg("platform fee bps cannot exceed 10000")]
    FeeTooHigh,
    #[msg("amount must be greater than zero")]
    ZeroAmount,
    #[msg("supplier share must be greater than zero")]
    ZeroShare,
    #[msg("deposit mint does not match configured mint")]
    WrongMint,
    #[msg("job id in instruction does not match escrow")]
    JobIdMismatch,
    #[msg("requested settlement exceeds locked escrow")]
    ExceedsLocked,
    #[msg("supplier account count does not match shares")]
    SupplierAccountsMismatch,
    #[msg("supplier token account must be writable")]
    SupplierNotWritable,
    #[msg("too many shares in one settle call")]
    TooManyShares,
    #[msg("reclaim window has not elapsed")]
    ReclaimTooEarly,
    #[msg("arithmetic overflow")]
    Overflow,
}
