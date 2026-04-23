"""Thin async wrapper around Solana RPC for escrow verification.

Heavy deps (`solders`, `solana-py`) are imported lazily so the orchestrator
still starts in shadow-mode environments without them installed. Callers
that hit the on-chain paths will get a clear ImportError if the extras are
missing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _rpc_url() -> str:
    return os.getenv("ORC_SOLANA_RPC", "https://api.devnet.solana.com").strip()


def _program_id_str() -> str:
    pid = os.getenv("ORC_ESCROW_PROGRAM_ID", "").strip()
    if not pid:
        raise RuntimeError("ORC_ESCROW_PROGRAM_ID is not set")
    return pid


def _usdc_mint_str() -> str:
    mint = os.getenv("ORC_USDC_MINT", "").strip()
    if not mint:
        raise RuntimeError("ORC_USDC_MINT is not set")
    return mint


@dataclass(frozen=True)
class DepositRecord:
    tx_signature: str
    payer: str
    amount_uc: int
    job_id_hex: str
    slot: int


class SolanaClientError(RuntimeError):
    pass


async def _load():
    try:
        from solana.rpc.async_api import AsyncClient  # type: ignore
        from solders.pubkey import Pubkey  # type: ignore
        from solders.signature import Signature  # type: ignore
    except ImportError as exc:
        raise SolanaClientError(
            "Solana deps not installed. Run `pip install solana solders` to enable on-chain mode."
        ) from exc
    return AsyncClient, Pubkey, Signature


async def verify_deposit(
    tx_signature: str,
    *,
    expected_payer: str,
    expected_job_id_hex: str,
    expected_min_amount_uc: int,
) -> DepositRecord:
    """Fetch a confirmed tx and validate it's a Deposited event for this job.

    Raises SolanaClientError if the tx is missing, unconfirmed, or does not
    match the expected fields.
    """
    AsyncClient, Pubkey, Signature = await _load()
    sig = Signature.from_string(tx_signature)
    program_id = Pubkey.from_string(_program_id_str())

    async with AsyncClient(_rpc_url()) as client:
        resp = await client.get_transaction(
            sig,
            encoding="jsonParsed",
            commitment="confirmed",
            max_supported_transaction_version=0,
        )

    tx = getattr(resp, "value", None)
    if tx is None:
        raise SolanaClientError(f"tx {tx_signature} not found / not confirmed")

    meta = getattr(tx, "meta", None)
    if meta is None or getattr(meta, "err", None) is not None:
        raise SolanaClientError(f"tx {tx_signature} failed on-chain: {getattr(meta, 'err', None)}")

    logs = list(getattr(meta, "log_messages", []) or [])
    parsed = _parse_deposited_event_from_logs(logs, program_id)
    if parsed is None:
        raise SolanaClientError(
            f"no Deposited event for program {program_id} in tx {tx_signature}"
        )

    if parsed["job_id_hex"] != expected_job_id_hex.lower():
        raise SolanaClientError(
            f"job_id mismatch: tx has {parsed['job_id_hex']}, expected {expected_job_id_hex.lower()}"
        )
    if parsed["payer"] != expected_payer:
        raise SolanaClientError(
            f"payer mismatch: tx has {parsed['payer']}, expected {expected_payer}"
        )
    if parsed["amount"] < expected_min_amount_uc:
        raise SolanaClientError(
            f"amount underpaid: tx has {parsed['amount']}, expected >= {expected_min_amount_uc}"
        )

    slot = getattr(tx, "slot", 0) or 0
    return DepositRecord(
        tx_signature=tx_signature,
        payer=parsed["payer"],
        amount_uc=parsed["amount"],
        job_id_hex=parsed["job_id_hex"],
        slot=int(slot),
    )


def _parse_deposited_event_from_logs(logs: list[str], program_id) -> dict[str, Any] | None:
    """Anchor programs emit events as base64-encoded `Program data:` log lines.

    We don't have the full IDL at runtime; we rely on the canonical Anchor
    event-discriminator layout: 8-byte discriminator (first 8 bytes of the
    sha256 of 'event:Deposited') followed by the event struct, Borsh-encoded.

    On failure returns None so the caller can surface a generic "event not
    found" error (rather than half-decoded garbage).
    """
    try:
        import base64
        import hashlib

        from solders.pubkey import Pubkey  # type: ignore
    except ImportError:
        return None

    discriminator = hashlib.sha256(b"event:Deposited").digest()[:8]
    for line in logs:
        if not line.startswith("Program data:"):
            continue
        b64 = line.split("Program data:", 1)[1].strip()
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        if not raw.startswith(discriminator):
            continue
        body = raw[8:]
        # Event layout: job_id [u8;32] | payer Pubkey (32) | amount u64 LE
        if len(body) < 32 + 32 + 8:
            continue
        job_id = body[:32]
        payer_bytes = body[32:64]
        amount = int.from_bytes(body[64:72], "little", signed=False)
        try:
            payer = str(Pubkey.from_bytes(payer_bytes))
        except Exception:
            continue
        return {
            "job_id_hex": job_id.hex(),
            "payer": payer,
            "amount": amount,
        }
    return None


def chain_enabled() -> bool:
    return bool(os.getenv("ORC_ESCROW_PROGRAM_ID", "").strip())
