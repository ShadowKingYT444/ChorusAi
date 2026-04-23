"""Unit tests for orchestrator.billing (Phase 1 shadow-mode accounting)."""

from __future__ import annotations

from orchestrator.billing import (
    ComputeShare,
    MODEL_PRICING,
    PLATFORM_FEE_BPS,
    compute_cost_uc,
    price_for,
    quote_job,
    split_payout,
)


def test_price_for_unknown_falls_back_to_default():
    assert price_for("totally-unknown-model") == MODEL_PRICING["default"]
    assert price_for(None) == MODEL_PRICING["default"]
    assert price_for("llama3:70b") == (700, 1500)


def test_compute_cost_uc_basic_math():
    # 1M input + 1M output on default pricing = 100 + 300 = 400 uc.
    assert compute_cost_uc("default", 1_000_000, 1_000_000, 0) == 400
    # 500k input, 0 output on default = 50 uc.
    assert compute_cost_uc("default", 500_000, 0, 0) == 50
    # Sub-1M tokens: integer division truncates tiny amounts to 0.
    assert compute_cost_uc("default", 100, 100, 0) == 0
    # Negative protection.
    assert compute_cost_uc("default", -5, -5, 0) == 0


def test_compute_cost_uc_ignores_wall_ms():
    """wall_ms is reserved; should not affect the returned cost."""
    a = compute_cost_uc("llama3:8b", 1_000_000, 500_000, 0)
    b = compute_cost_uc("llama3:8b", 1_000_000, 500_000, 999_999)
    assert a == b


def test_quote_job_applies_platform_fee_bps():
    # Single model, single agent, 2M in + 1M out on default: 200 + 300 = 500 uc.
    q = quote_job(["default"], 1, 2_000_000, 1_000_000)
    assert q["subtotal_uc"] == 500
    # 0.5% of 500 = 2.5 → floor = 2.
    assert q["platform_fee_uc"] == 2
    assert (500 * PLATFORM_FEE_BPS) // 10_000 == 2
    assert q["total_uc"] == q["subtotal_uc"] + q["platform_fee_uc"]


def test_quote_job_scales_with_agent_count():
    q1 = quote_job(["default"], 1, 1_000_000, 1_000_000)
    q5 = quote_job(["default"], 5, 1_000_000, 1_000_000)
    # 5 agents ⇒ 5× the subtotal.
    assert q5["subtotal_uc"] == 5 * q1["subtotal_uc"]
    # Unknown model rolls into "default".
    q_unknown = quote_job(["does-not-exist"], 1, 1_000_000, 1_000_000)
    assert q_unknown["subtotal_uc"] == q1["subtotal_uc"]


def test_platform_fee_floor_of_1_uc():
    """Very small subtotals still incur a 1 uc fee (never 0 when subtotal > 0)."""
    # 100 tokens each side on default ⇒ subtotal = 0.
    q_zero = quote_job(["default"], 1, 100, 100)
    assert q_zero["subtotal_uc"] == 0
    assert q_zero["platform_fee_uc"] == 0
    # Tiny positive subtotal — manually construct via split_payout.
    split = split_payout([ComputeShare(peer_id="p1", wallet_address=None, cost_uc=10)])
    assert split["subtotal_uc"] == 10
    # 0.5% of 10 = 0.05 → floored to 0, but floor-min kicks in → 1.
    assert split["platform_fee_uc"] == 1


def test_split_payout_distributes_pro_rata_and_no_dust_loss():
    shares = [
        ComputeShare(peer_id="p1", wallet_address="w1", cost_uc=1000),
        ComputeShare(peer_id="p2", wallet_address="w2", cost_uc=3000),
    ]
    split = split_payout(shares)
    subtotal = split["subtotal_uc"]
    fee = split["platform_fee_uc"]
    paid = sum(row["amount_uc"] for row in split["per_supplier"])
    # Invariant: sum(per_supplier) + fee == subtotal (no leaked uc).
    assert paid + fee == subtotal
    # p2 earned 3× what p1 earned (within 1 uc of dust assigned to p1).
    p1 = next(r for r in split["per_supplier"] if r["peer_id"] == "p1")
    p2 = next(r for r in split["per_supplier"] if r["peer_id"] == "p2")
    assert p2["amount_uc"] >= 3 * (p1["amount_uc"] - 1)


def test_split_payout_aggregates_same_peer_across_rounds():
    shares = [
        ComputeShare(peer_id="p1", wallet_address=None, cost_uc=500),
        ComputeShare(peer_id="p1", wallet_address="w-late", cost_uc=500),
        ComputeShare(peer_id="p2", wallet_address="w2", cost_uc=1000),
    ]
    split = split_payout(shares)
    rows = {r["peer_id"]: r for r in split["per_supplier"]}
    assert set(rows) == {"p1", "p2"}
    # p1 aggregated = 1000, p2 = 1000 → roughly equal.
    assert abs(rows["p1"]["amount_uc"] - rows["p2"]["amount_uc"]) <= 1
    # Wallet preference: first non-null wins.
    assert rows["p1"]["wallet"] == "w-late"


def test_split_payout_empty_and_zero_cost():
    assert split_payout([]) == {
        "subtotal_uc": 0,
        "platform_fee_uc": 0,
        "per_supplier": [],
    }
    # Zero-cost shares (failed rounds) are excluded silently.
    split = split_payout([ComputeShare(peer_id="p", wallet_address=None, cost_uc=0)])
    assert split["subtotal_uc"] == 0
    assert split["per_supplier"] == []
