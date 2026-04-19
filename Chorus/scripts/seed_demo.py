"""
Seed a Chorus SQLite database with completed demo jobs.

This is useful when you want `/chats` populated on a fresh local database before
running a live demo. The script is idempotent: it uses INSERT OR IGNORE keyed on
`job_id` and `(job_id, seq)`.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

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
    cited_set = set(cited)
    cited_share = 0.75 * payout
    base_share = 0.25 * payout
    n_cited = max(1, len(cited_set))
    n_all = max(1, len(slot_ids))
    per_cited = round(cited_share / n_cited, 2)
    per_base = round(base_share / n_all, 2)

    allocations = []
    for slot_id in slot_ids:
        amt = per_base + (per_cited if slot_id in cited_set else 0.0)
        allocations.append(
            {
                "slot_id": slot_id,
                "payout": round(amt, 2),
                "cited": slot_id in cited_set,
                "impact": round(0.9 if slot_id in cited_set else 0.3, 3),
            }
        )
    return {
        "payout_total": payout,
        "currency": "CHOR",
        "allocations": allocations,
        "receipt": {
            "signature": "seed:" + "a" * 64,
            "issued_at": time.time(),
            "pubkey": "seed-demo-pubkey",
        },
    }


def _event_set(
    rounds: int,
    slot_ids: list[str],
    final_text: str,
    citations: list[str],
    settlement: dict,
    t0: float,
) -> list[dict]:
    events: list[dict] = []
    ts = t0

    def push(event_type: str, payload: dict) -> None:
        nonlocal ts
        ts += 0.25
        events.append({"type": event_type, "ts": ts, "payload": payload})

    push("round_started", {"round": 1})
    for slot_id in slot_ids:
        push(
            "agent_line",
            {
                "round": 1,
                "slot_id": slot_id,
                "payload": {
                    "status": "valid",
                    "latency_ms": 820 + (hash(slot_id) % 300),
                    "snippet": f"[{slot_id}] round-1 take: drafting analysis with cited tradeoffs.",
                },
            },
        )
    for index, slot_id in enumerate(slot_ids):
        dst = slot_ids[(index + 1) % len(slot_ids)]
        push(
            "edge",
            {
                "round": 1,
                "slot_id": slot_id,
                "payload": {"from": slot_id, "to": dst, "kind": "nearest"},
            },
        )

    if rounds >= 2:
        push("round_started", {"round": 2})
        for slot_id in slot_ids:
            push(
                "agent_line",
                {
                    "round": 2,
                    "slot_id": slot_id,
                    "payload": {
                        "status": "valid",
                        "latency_ms": 720 + (hash(slot_id) % 250),
                        "snippet": f"[{slot_id}] round-2 refinement: responding to peers, sharpening claims.",
                    },
                },
            )

    push("final_answer", {"round": rounds, "payload": {"text": final_text, "citations": citations}})
    push(
        "job_done",
        {
            "round": rounds,
            "payload": {
                "settlement_preview": settlement,
                "final_answer": final_text,
                "citations": citations,
            },
        },
    )
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
    now = time.time()
    created_at = now - age_seconds
    completed_at = created_at + 12.0

    cur = conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,))
    if cur.fetchone() is not None:
        return False

    spec = {
        "context": context,
        "prompt": prompt,
        "agent_count": agent_count,
        "rounds": rounds,
        "payout": payout,
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
    for seq, event in enumerate(events, start=1):
        conn.execute(
            """
            INSERT OR IGNORE INTO events (job_id, seq, ts, type, payload_json)
            VALUES (?,?,?,?,?)
            """,
            (job_id, seq, float(event["ts"]), event["type"], json.dumps(event["payload"])),
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a Chorus SQLite database with demo chats.")
    parser.add_argument(
        "--db-path",
        default=os.getenv("CHORUS_DB_PATH", "./chorus.db"),
        help="SQLite database path (default: CHORUS_DB_PATH or ./chorus.db).",
    )
    args = parser.parse_args()

    db_path = args.db_path.replace("\\", "/")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA_SQL)

        jobs = [
            {
                "job_id": "seed-demo-rust-migration",
                "prompt": "Should we migrate to Rust for the payload processor?",
                "context": (
                    "Current implementation is Node.js, about 120k rps at peak, p99 latency "
                    "180ms, and GC pauses are causing tail-latency spikes. Team size is 6 "
                    "engineers and only 2 are familiar with Rust."
                ),
                "agent_count": 4,
                "rounds": 2,
                "payout": 100.0,
                "final_answer": (
                    "Migrate the payload hot path, roughly 20 percent of modules, to Rust and "
                    "keep the rest in Node. [slot-0] cited throughput gains while [slot-2] "
                    "highlighted team ramp-up cost."
                ),
                "citations": ["slot-0", "slot-2"],
                "slot_ids": ["slot-0", "slot-1", "slot-2", "slot-3"],
                "age_seconds": 3600 * 3,
            },
            {
                "job_id": "seed-demo-finetune-budget",
                "prompt": "What is the cheapest fine-tune budget for a 7B model on 10k examples?",
                "context": (
                    "Base model is Llama-3 7B; dataset is 10k instruction pairs averaging "
                    "512 tokens; spot GPUs are acceptable; inference latency is not critical."
                ),
                "agent_count": 4,
                "rounds": 2,
                "payout": 100.0,
                "final_answer": (
                    "$180-$240 using LoRA on a single A100 spot instance for 4-6 hours; "
                    "[slot-1] settled on the lower end by batching more aggressively."
                ),
                "citations": ["slot-1", "slot-3"],
                "slot_ids": ["slot-0", "slot-1", "slot-2", "slot-3"],
                "age_seconds": 3600,
            },
        ]

        seeded = 0
        for job in jobs:
            if _seed_job(conn, **job):
                seeded += 1

        conn.commit()
        print(f"seeded {seeded} jobs to {db_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
