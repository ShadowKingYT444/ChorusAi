"""Phase 1 shadow-mode billing for Chorus P2P compute payments.

Pure functions only — NO Solana, NO wallet I/O, NO DB. This module computes
the accounting that the future Solana settlement program will mirror.

Units:
    uc = micro-USDC.  1 USDC == 1_000_000 uc.
    MTOK = 1,000,000 tokens.

Pricing is per-MTOK and is multiplied by tokens then divided by MTOK to get uc.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

PLATFORM_FEE_BPS = 50  # 0.5 %
_BPS_DENOM = 10_000
_MTOK = 1_000_000

# (price_in_per_mtok_uc, price_out_per_mtok_uc)
MODEL_PRICING: dict[str, tuple[int, int]] = {
    "default":   (100, 300),   # $0.10 / $0.30
    "llama3:8b": (100, 300),
    "llama3:70b": (700, 1500),
    "qwen2:7b":  (80, 250),
    "mistral:7b": (100, 300),
}


@dataclass(frozen=True)
class ComputeShare:
    """One peer's contribution to a single round of a job."""

    peer_id: str
    wallet_address: str | None
    cost_uc: int


def price_for(model_id: str | None) -> tuple[int, int]:
    """Return (price_in_uc, price_out_uc) per MTOK for `model_id`.

    Unknown / None model falls back to the 'default' bucket so shadow-mode
    metering never crashes on an unfamiliar peer announcement.
    """
    if not model_id:
        return MODEL_PRICING["default"]
    return MODEL_PRICING.get(model_id, MODEL_PRICING["default"])


def compute_cost_uc(
    model_id: str | None,
    tokens_in: int,
    tokens_out: int,
    wall_ms: int,
) -> int:
    """Token-based compute cost in micro-USDC.

    `wall_ms` is accepted but not yet priced; reserved for a future
    GPU-seconds dimension when peers advertise hardware class.
    """
    del wall_ms  # reserved
    price_in, price_out = price_for(model_id)
    ti = max(0, int(tokens_in))
    to = max(0, int(tokens_out))
    # Integer math; truncates toward zero — acceptable at micro-USDC precision.
    return (ti * price_in + to * price_out) // _MTOK


def quote_job(
    model_ids: list[str],
    agent_count: int,
    expected_tokens_in: int,
    expected_tokens_out: int,
) -> dict:
    """Pre-flight quote across an ensemble of models.

    Returns {subtotal_uc, platform_fee_uc, total_uc}. `agent_count` multiplies
    the per-model-round cost because each agent slot incurs one inference.
    """
    agents = max(1, int(agent_count))
    if not model_ids:
        model_ids = ["default"]
    per_agent = 0
    for mid in model_ids:
        per_agent += compute_cost_uc(mid, expected_tokens_in, expected_tokens_out, 0)
    # Average across the ensemble (each agent runs one model, not all).
    subtotal = (per_agent * agents) // max(1, len(model_ids))
    fee = _platform_fee(subtotal)
    return {
        "subtotal_uc": subtotal,
        "platform_fee_uc": fee,
        "total_uc": subtotal + fee,
    }


def split_payout(shares: Iterable[ComputeShare]) -> dict:
    """Aggregate per-peer shares and skim the platform fee.

    Returns:
        {
            subtotal_uc,          # sum of raw compute costs
            platform_fee_uc,      # 0.5% floor-at-1 if subtotal>0
            per_supplier: [{peer_id, wallet, amount_uc}, ...],
        }

    Each supplier's amount is their pro-rata fraction of (subtotal - fee),
    rounded down; any rounding dust goes to the first supplier to keep the
    invariant sum(per_supplier) + platform_fee == subtotal.
    """
    shares_list = [s for s in shares if s.cost_uc > 0]
    subtotal = sum(s.cost_uc for s in shares_list)
    fee = _platform_fee(subtotal)
    net = subtotal - fee

    per_supplier: list[dict] = []
    if not shares_list or net <= 0:
        # Degenerate: nothing to distribute. Still emit rows so callers can audit.
        for s in shares_list:
            per_supplier.append(
                {"peer_id": s.peer_id, "wallet": s.wallet_address, "amount_uc": 0}
            )
        return {
            "subtotal_uc": subtotal,
            "platform_fee_uc": fee,
            "per_supplier": per_supplier,
        }

    # Aggregate per peer_id (a peer may have multiple round shares).
    agg: dict[str, dict] = {}
    order: list[str] = []
    for s in shares_list:
        row = agg.get(s.peer_id)
        if row is None:
            row = {"peer_id": s.peer_id, "wallet": s.wallet_address, "_cost": 0}
            agg[s.peer_id] = row
            order.append(s.peer_id)
        row["_cost"] += s.cost_uc
        # Prefer the first non-null wallet seen for this peer.
        if row["wallet"] is None and s.wallet_address is not None:
            row["wallet"] = s.wallet_address

    distributed = 0
    for pid in order:
        row = agg[pid]
        amount = (row["_cost"] * net) // subtotal
        distributed += amount
        per_supplier.append(
            {"peer_id": pid, "wallet": row["wallet"], "amount_uc": amount}
        )
    # Dust from floor division → first supplier.
    dust = net - distributed
    if dust > 0 and per_supplier:
        per_supplier[0]["amount_uc"] += dust

    return {
        "subtotal_uc": subtotal,
        "platform_fee_uc": fee,
        "per_supplier": per_supplier,
    }


def _platform_fee(subtotal_uc: int) -> int:
    """0.5% of subtotal, floored at 1 uc when subtotal is positive."""
    if subtotal_uc <= 0:
        return 0
    fee = (subtotal_uc * PLATFORM_FEE_BPS) // _BPS_DENOM
    return fee if fee >= 1 else 1
