from __future__ import annotations

from math import floor

from orchestrator.models import JobRecord, PruneStatus


def compute_settlement(job: JobRecord, w_c: float = 1.0, w_f: float = 0.5) -> dict:
    eligible_slot_ids = [
        slot_id
        for slot_id, slot in job.slots.items()
        if slot.status != PruneStatus.pruned
    ]
    if not eligible_slot_ids:
        eligible_slot_ids = list(job.slots.keys())

    n = len(eligible_slot_ids)
    pool = float(job.spec.payout)
    avg = pool / n
    floor_each = 0.75 * avg
    floor_total = floor_each * n
    extra_pool = pool - floor_total

    impacts: dict[str, float] = {}
    for slot_id in eligible_slot_ids:
        slot = job.slots[slot_id]
        impacts[slot_id] = (w_c * slot.c_impact) + (w_f * slot.f_impact)

    total_impact = sum(impacts.values())
    raw_payouts: dict[str, float] = {}
    for slot_id in eligible_slot_ids:
        if total_impact <= 0:
            extra = extra_pool / n
        else:
            extra = extra_pool * (impacts[slot_id] / total_impact)
        raw_payouts[slot_id] = floor_each + extra

    rounded = _largest_remainder_round(raw_payouts, pool)
    return {
        "total_pool": pool,
        "eligible_agents": n,
        "floor_each": floor_each,
        "extra_pool": extra_pool,
        "impact_weights": impacts,
        "payouts": rounded,
    }


def _largest_remainder_round(payouts: dict[str, float], total: float) -> dict[str, float]:
    cents_total = int(round(total * 100))
    base_cents: dict[str, int] = {}
    remainders: list[tuple[str, float]] = []

    running = 0
    for slot_id, amount in payouts.items():
        raw_cents = amount * 100
        cents = floor(raw_cents)
        base_cents[slot_id] = cents
        running += cents
        remainders.append((slot_id, raw_cents - cents))

    needed = max(cents_total - running, 0)
    remainders.sort(key=lambda x: x[1], reverse=True)
    for i in range(needed):
        slot_id = remainders[i % len(remainders)][0]
        base_cents[slot_id] += 1

    return {slot_id: cents / 100.0 for slot_id, cents in base_cents.items()}
