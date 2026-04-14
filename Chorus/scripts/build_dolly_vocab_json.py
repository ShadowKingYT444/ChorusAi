#!/usr/bin/env python3
"""
Build a vocabulary JSON from dolly_prompt_response.csv (or similar 2-column CSV).

Rules:
- Walk the text and take **contiguous runs of letters** `[A-Za-z]` (case folded to lower).
- **Spaces, digits, punctuation** break runs (they do not appear inside a segment).
- From each run, emit **every substring of length 2, 3, and 4** (overlapping n-grams).

Output JSON shape:
  { "vocabulary": [ {"word": "ab", "id": 0}, ... ], "size": N }

IDs are assigned in **sorted word order** (stable, reproducible).

Usage:
  python scripts/build_dolly_vocab_json.py \\
    --csv dolly_prompt_response.csv \\
    --out dolly_vocab.json
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
import re


_LETTERS = re.compile(r"[A-Za-z]+")


def iter_segments(run: str) -> str:
    """Yield all 2-, 3-, and 4-letter substrings of a single lowercase letter run."""
    run = run.lower()
    L = len(run)
    if L < 2:
        return
    for n in (2, 3, 4):
        if L < n:
            continue
        for i in range(L - n + 1):
            yield run[i : i + n]


def iter_from_text(text: str) -> str:
    for m in _LETTERS.finditer(text):
        yield from iter_segments(m.group())


def main() -> None:
    p = argparse.ArgumentParser(description="Build 2–4 letter n-gram vocab JSON from CSV.")
    p.add_argument("--csv", type=Path, default=Path("dolly_prompt_response.csv"))
    p.add_argument("--out", type=Path, default=Path("dolly_vocab.json"))
    p.add_argument(
        "--columns",
        default="prompt,response",
        help="Comma-separated column names to scan (default: prompt,response)",
    )
    p.add_argument(
        "--with-counts",
        action="store_true",
        help="Also write 'counts' as word -> occurrence count (larger file).",
    )
    args = p.parse_args()

    cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    counts: Counter[str] = Counter()

    with args.csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in cols if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"CSV missing columns {missing}; have {reader.fieldnames!r}")
        for row in reader:
            for c in cols:
                cell = row.get(c) or ""
                counts.update(iter_from_text(cell))

    words = sorted(counts.keys())
    vocab = [{"word": w, "id": i} for i, w in enumerate(words)]
    payload: dict = {"vocabulary": vocab, "size": len(vocab)}
    if args.with_counts:
        payload["counts"] = {w: counts[w] for w in words}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as out:
        json.dump(payload, out, ensure_ascii=False, indent=2)
    print(f"Wrote {len(vocab)} unique segments to {args.out}")


if __name__ == "__main__":
    main()
