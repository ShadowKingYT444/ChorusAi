#!/usr/bin/env python3
"""
seed_demo.py — pre-populate SQLite with 2 completed demo jobs so the UI
sidebar isn't empty on a fresh clone.

Idempotent: uses INSERT OR IGNORE keyed on job_id and events(job_id, seq).
Uses stdlib sqlite3 only. Reads db path from CHORUS_DB_PATH (default ./chorus.db).
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time


# Mirror orchestrator/db.py init_schema so seeding works on an empty DB.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL,
    status TEXT NOT NULL,
    current_round INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    completed_at REAL,
    final_answer TEXT,
    citations_json TEXT,
    settlement_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS peers (
    peer_id TEXT PRIMARY KEY,
    model TEXT,
    address TEXT,
    status TEXT,
    protocol_version TEXT,
    joined_at REAL,
    last_seen REAL,
    pubkey TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(job_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_job_seq ON events(job_id, seq);
CREATE TABLE IF NOT EXISTS _health (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts REAL NOT NULL
);
"""


def _build_settlement(payout: float, slot_ids: list[str], cited: list[str]) -> dict:
    """Produce a realistic-looking settlement_preview with per-slot payouts."""
    cited_set = set(cited)
    # Cited slots get a bigger share; uncited slots still get a small base.
    cited_share = 0.75 * payout
    base_share  = 0.25 * payout
    n_cited = max(1, len(cited_set))
    n_all   = max(1, len(slot_ids))
    per_cited = round(cited_share / n_cited, 2)
    per_base  = round(base_share / n_all, 2)

    allocations = []
    for sid in slot_ids:
        amt = per_base + (per_cited if sid in cited_set else 0.0)
        allocations.append({
            "slot_id": sid,
            "payout":  round(amt, 2),
            "cited":   sid in cited_set,
            "impact":  round(0.9 if sid in cited_set else 0.3, 3),
        })
    return {
        "payout_total": payout,
        "currency": "CHOR",
        "allocations": allocations,
        "receipt": {
            "signature": "seed:" + "a" * 64,
            "issued_at": time.time(),
            "pubkey":    "seed-demo-pubkey",
        },
    }


def _event_set(rounds: int, slot_ids: list[str], final_text: str,
               citations: list[str], settlement: dict, t0: float) -> list[dict]:
    """Build a plausible ~8+ event stream for a completed job."""
    events: list[dict] = []
    ts = t0

    def push(etype: str, payload: dict) -> None:
        nonlocal ts
        ts += 0.25
        events.append({"type": etype, "ts": ts, "payload": payload})

    # Round 1
    push("round_started", {"round": 1})
    for sid in slot_ids:
        push("agent_line", {
            "round": 1, "slot_id": sid,
            "payload": {
                "status":     "valid",
                "latency_ms": 820 + (hash(sid) % 300),
                "snippet":    f"[{sid}] round-1 take: drafting analysis with cited tradeoffs.",
            },
        })
    # edges: each slot finds a nearest neighbor
    for i, sid in enumerate(slot_ids):
        dst = slot_ids[(i + 1) % len(slot_ids)]
        push("edge", {
            "round": 1, "slot_id": sid,
            "payload": {"from": sid, "to": dst, "kind": "nearest"},
        })

    # Round 2 (if present)
    if rounds >= 2:
        push("round_started", {"round": 2})
        for sid in slot_ids:
            push("agent_line", {
                "round": 2, "slot_id": sid,
                "payload": {
                    "status":     "valid",
                    "latency_ms": 720 + (hash(sid) % 250),
                    "snippet":    f"[{sid}] round-2 refinement: responding to peers, sharpening claims.",
                },
            })

    # Final answer + job done
    push("final_answer", {"round": rounds, "payload": {"text": final_text, "citations": citations}})
    push("job_done", {
        "round":   rounds,
        "payload": {"settlement_preview": settlement, "final_answer": final_text, "citations": citations},
    })
    return events


def _seed_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    prompt: str,
    context: str,
    agent_count: int,
    rounds: int,
    payout: float,
    final_answer: str,
    citations: list[str],
    slot_ids: list[str],
    age_seconds: float,
) -> bool:
    now         = time.time()
    created_at  = now - age_seconds
    completed_at = created_at + 12.0  # plausible 12s job runtime

    # Skip if already seeded.
    cur = conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,))
    if cur.fetchone() is not None:
        return False

    spec = {
        "context":     context,
        "prompt":      prompt,
        "agent_count": agent_count,
        "rounds":      rounds,
        "payout":      payout,
    }
    settlement = _build_settlement(payout, slot_ids, citations)

    conn.execute(
        """
        INSERT OR IGNORE INTO jobs
          (job_id, spec_json, status, current_round, created_at, completed_at,
           final_answer, citations_json, settlement_json, error)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job_id,
            json.dumps(spec),
            "completed",
            rounds,
            float(created_at),
            float(completed_at),
            final_answer,
            json.dumps(citations),
            json.dumps(settlement),
            None,
        ),
    )

    events = _event_set(rounds, slot_ids, final_answer, citations, settlement, t0=created_at)
    for seq, ev in enumerate(events, start=1):
        conn.execute(
            """
            INSERT OR IGNORE INTO events (job_id, seq, ts, type, payload_json)
            VALUES (?,?,?,?,?)
            """,
            (job_id, seq, float(ev["ts"]), ev["type"], json.dumps(ev["payload"])),
        )
    return True


def main() -> int:
    db_path = os.getenv("CHORUS_DB_PATH", "./chorus.db")
    # Normalize for Windows backslashes (sqlite accepts both).
    db_path_norm = db_path.replace("\\", "/")

    conn = sqlite3.connect(db_path_norm)
    try:
        conn.executescript(_SCHEMA_SQL)

        jobs = [
            {
                "job_id":      "seed-demo-rust-migration",
                "prompt":      "Should we migrate to Rust for the payload processor?",
                "context":     (
                    "Current implementation is Node.js, ~120k rps at peak, p99 latency 180ms, "
                    "GC pauses causing tail-latency spikes. Team is 6 engineers, 2 familiar with Rust."
                ),
                "agent_count": 4,
                "rounds":      2,
                "payout":      100.0,
                "final_answer": (
                    "Migrate payload hot path (~20% of modules) to Rust, keep rest in Node. "
                    "[slot-0] cited throughput gains, [slot-2] warned on team ramp-up cost."
                ),
                "citations":   ["slot-0", "slot-2"],
                "slot_ids":    ["slot-0", "slot-1", "slot-2", "slot-3"],
                "age_seconds": 3600 * 3,
            },
            {
                "job_id":      "seed-demo-finetune-budget",
                "prompt":      "What is the cheapest fine-tune budget for a 7B model on 10k examples?",
                "context":     (
                    "Base model Llama-3 7B; dataset 10k instruction pairs, avg 512 tokens; "
                    "willing to use spot GPUs; latency for inference not critical."
                ),
                "agent_count": 4,
                "rounds":      2,
                "payout":      100.0,
                "final_answer": (
                    "$180-$240 using LoRA on a single A100 spot instance for 4-6 hours; "
                    "[slot-1] settled on the lower end by batching..."
                ),
                "citations":   ["slot-1", "slot-3"],
                "slot_ids":    ["slot-0", "slot-1", "slot-2", "slot-3"],
                "age_seconds": 3600 * 1,
            },
        ]

        seeded = 0
        for j in jobs:
            if _seed_job(conn, **j):
                seeded += 1

        conn.commit()
        print(f"seeded {seeded} jobs to {db_path_norm}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
