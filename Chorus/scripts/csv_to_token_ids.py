#!/usr/bin/env python3
"""
Read CSV(s) with text columns + dolly_vocab.json, write each row as lists of integer token IDs.

Letter runs are encoded with **greedy longest match** using only 2–4 character pieces that exist in
the vocab (tries length 4, then 3, then 2). If no match at a position, emits **UNK** and advances one
character.

**Spaces** → one reserved ID.  
**Any other single character that is not a letter** (punctuation, digits, newlines from CSV fields,
etc.) → **one ID per distinct character**, assigned in sorted order after UNK.

Outputs:
  --out-jsonl   default: one JSON object per line: {"prompt_ids":[...],"response_ids":[...]}
  --out-meta    default: *_meta.json documenting space_id, unk_id, and char→id map

Usage:
  python scripts/csv_to_token_ids.py \\
    --vocab dolly_vocab.json \\
    --csv dolly_prompt_response.csv \\
    --out-jsonl dolly_token_ids.jsonl \\
    --out-meta dolly_token_ids_meta.json
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable


def load_vocab(path: Path) -> dict[str, int]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    out: dict[str, int] = {}
    for row in data["vocabulary"]:
        out[str(row["word"])] = int(row["id"])
    return out


def collect_nonalpha_chars(texts: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    for t in texts:
        for ch in t:
            if ch != " " and not ch.isalpha():
                seen.add(ch)
    return sorted(seen)


def build_special_maps(
    word_to_id: dict[str, int],
    nonalpha_chars: list[str],
) -> tuple[int, int, dict[str, int], int]:
    """
    Returns (space_id, unk_id, char_to_id, next_id_after_specials).

    Layout:
      [0 .. vocab_size-1]  vocabulary words (from JSON)
      [vocab_size]         SPACE
      [vocab_size + 1]     UNK (unknown letter / no greedy match)
      [vocab_size + 2 ..]  one id per distinct non-space, non-alpha char (sorted)
    """
    n = len(word_to_id)
    space_id = n
    unk_id = n + 1
    char_to_id: dict[str, int] = {}
    cur = n + 2
    for ch in nonalpha_chars:
        char_to_id[ch] = cur
        cur += 1
    return space_id, unk_id, char_to_id, cur


def encode_text(
    text: str,
    word_to_id: dict[str, int],
    space_id: int,
    unk_id: int,
    char_to_id: dict[str, int],
) -> list[int]:
    ids: list[int] = []
    i = 0
    L = len(text)
    while i < L:
        c = text[i]
        if c == " ":
            ids.append(space_id)
            i += 1
            continue
        if c.isalpha():
            j = i
            while j < L and text[j].isalpha():
                j += 1
            run = text[i:j].lower()
            p = 0
            R = len(run)
            while p < R:
                matched = False
                for ln in (4, 3, 2):
                    if p + ln <= R:
                        piece = run[p : p + ln]
                        if piece in word_to_id:
                            ids.append(word_to_id[piece])
                            p += ln
                            matched = True
                            break
                if not matched:
                    ids.append(unk_id)
                    p += 1
            i = j
            continue
        ids.append(char_to_id.get(c, unk_id))
        i += 1
    return ids


def iter_csv_text_cells(paths: list[Path], columns: list[str]) -> Iterable[str]:
    for path in paths:
        with path.open(newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            miss = [c for c in columns if c not in (r.fieldnames or [])]
            if miss:
                raise SystemExit(f"{path}: missing columns {miss}; have {r.fieldnames!r}")
            for row in r:
                for c in columns:
                    cell = row.get(c) or ""
                    yield cell


def main() -> None:
    p = argparse.ArgumentParser(description="Encode CSV text columns to token ID lists.")
    p.add_argument("--vocab", type=Path, default=Path("dolly_vocab.json"))
    p.add_argument(
        "--csv",
        type=Path,
        action="append",
        dest="csvs",
        default=[],
        help="CSV path (repeat for multiple files). Default: dolly_prompt_response.csv",
    )
    p.add_argument(
        "--columns",
        default="prompt,response",
        help="Comma-separated columns to encode (default: prompt,response)",
    )
    p.add_argument("--out-jsonl", type=Path, default=Path("dolly_token_ids.jsonl"))
    p.add_argument("--out-meta", type=Path, default=Path("dolly_token_ids_meta.json"))
    p.add_argument(
        "--limit-rows",
        type=int,
        default=0,
        help="If >0, only process this many rows per CSV (debug).",
    )
    args = p.parse_args()

    csvs = args.csvs if args.csvs else [Path("dolly_prompt_response.csv")]
    cols = [c.strip() for c in args.columns.split(",") if c.strip()]

    word_to_id = load_vocab(args.vocab)
    # Pass 1: scan all text to collect non-alpha chars (except space) for stable ID table
    def all_cells() -> Iterable[str]:
        for path in csvs:
            with path.open(newline="", encoding="utf-8") as f:
                r = csv.DictReader(f)
                miss = [c for c in cols if c not in (r.fieldnames or [])]
                if miss:
                    raise SystemExit(f"{path}: missing columns {miss}")
                lim = args.limit_rows
                n = 0
                for row in r:
                    for c in cols:
                        yield row.get(c) or ""
                    n += 1
                    if lim and n >= lim:
                        break

    nonalpha = collect_nonalpha_chars(all_cells())
    space_id, unk_id, char_to_id, next_id = build_special_maps(word_to_id, nonalpha)

    meta = {
        "vocab_path": str(args.vocab),
        "vocab_token_count": len(word_to_id),
        "space_id": space_id,
        "unk_id": unk_id,
        "space_char": " ",
        "description": (
            "IDs 0..vocab_token_count-1 are 2–4 letter n-gram types from build_dolly_vocab_json.py. "
            "space_id is used for ASCII space. unk_id for unknown letters or unmappable chars. "
            "Other keys in char_to_id are single-character tokens (punctuation, digits, newline, etc.)."
        ),
        "char_to_id": char_to_id,
        "max_token_id_exclusive": next_id,
    }
    args.out_meta.parent.mkdir(parents=True, exist_ok=True)
    with args.out_meta.open("w", encoding="utf-8") as mf:
        json.dump(meta, mf, ensure_ascii=False, indent=2)

    # Pass 2: write JSONL
    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with args.out_jsonl.open("w", encoding="utf-8") as out:
        for path in csvs:
            with path.open(newline="", encoding="utf-8") as f:
                r = csv.DictReader(f)
                miss = [c for c in cols if c not in (r.fieldnames or [])]
                if miss:
                    raise SystemExit(f"{path}: missing columns {miss}")
                lim = args.limit_rows
                n = 0
                for row in r:
                    rec = {
                        f"{c}_ids": encode_text(
                            row.get(c) or "",
                            word_to_id,
                            space_id,
                            unk_id,
                            char_to_id,
                        )
                        for c in cols
                    }
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_written += 1
                    n += 1
                    if lim and n >= lim:
                        break

    print(f"Wrote {n_written} lines to {args.out_jsonl}")
    print(f"Wrote meta (space_id={space_id}, unk_id={unk_id}, {len(char_to_id)} char tokens) to {args.out_meta}")


if __name__ == "__main__":
    main()
