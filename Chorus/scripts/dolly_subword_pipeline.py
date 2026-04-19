#!/usr/bin/env python3
"""
End-to-end Dolly 15k → CSV → ByteLevel BPE subword vocab → token-ID JSONL (+ meta).

**Download / read:** same NDJSON source as `export_dolly_prompt_response.py` (Hugging Face),
via Polars `hf://...`, unless `--csv` points at an existing CSV (skip HF).

**Train:** Hugging Face `tokenizers` ByteLevel BPE (GPT-2-style bytes + merges). Spaces and
punctuation are **inside** subword pieces (no separate space_id table like `csv_to_token_ids.py`).

**Outputs (under `--out-dir`):**
  - `dolly_prompt_response.csv` - unless `--csv` is set (then vocab/jsonl still use that file)
  - `dolly_subword_tokenizer.json` - full tokenizer (use this to decode IDs exactly)
  - `dolly_subword_vocab.json` - same shape as `dolly_vocab.json`: `{"vocabulary":[{"word","id"},...],"size":N}`
  - `dolly_subword_token_ids.jsonl` - one object per row: `{"prompt_ids":[...],"response_ids":[...]}`
  - `dolly_subword_token_ids_meta.json` - paths, sizes, special token ids

Install (once):

  pip install polars fsspec huggingface_hub tokenizers

Optional project extra (if you add it to pyproject): `pip install -e ".[dolly]"`

Examples:

  python scripts/dolly_subword_pipeline.py --out-dir dolly_bpe_out
  python scripts/dolly_subword_pipeline.py --out-dir dolly_bpe_out --csv dolly_prompt_response.csv
  python scripts/dolly_subword_pipeline.py --out-dir dolly_bpe_out --limit-rows 2000 --vocab-size 4096
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterable

DEFAULT_HF_NDJSON = (
    "hf://datasets/databricks/databricks-dolly-15k/databricks-dolly-15k.jsonl"
)


def _require(name: str) -> None:
    print(
        f"Missing dependency {name!r}. Install with:\n"
        f"  pip install polars fsspec huggingface_hub tokenizers",
        file=sys.stderr,
    )
    raise SystemExit(1)


try:
    import polars as pl
except ImportError:
    _require("polars")

try:
    from tokenizers import Tokenizer
    from tokenizers.decoders import ByteLevel as ByteLevelDecoder
    from tokenizers.models import BPE
    from tokenizers.pre_tokenizers import ByteLevel
    from tokenizers.trainers import BpeTrainer
except ImportError:
    _require("tokenizers")


def build_prompt_response_frame(df: pl.DataFrame) -> pl.DataFrame:
    instr = pl.col("instruction").fill_null("").cast(pl.Utf8)
    ctx = pl.col("context").fill_null("").cast(pl.Utf8)
    prompt = (
        instr
        + pl.when(ctx.str.strip_chars().str.len_chars() > 0)
        .then(pl.lit("\n\n### Context\n") + ctx)
        .otherwise(pl.lit(""))
    ).alias("prompt")
    return df.select(
        prompt,
        pl.col("response").fill_null("").cast(pl.Utf8).alias("response"),
    )


def load_or_build_csv(
    hf_input: str,
    csv_path: Path,
    limit_rows: int,
    reuse_csv: Path | None,
) -> None:
    if reuse_csv is not None:
        if not reuse_csv.is_file():
            raise SystemExit(f"--csv {reuse_csv}: file not found")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if limit_rows:
            with reuse_csv.open(newline="", encoding="utf-8") as src, csv_path.open(
                "w", newline="", encoding="utf-8"
            ) as dst:
                r = csv.DictReader(src)
                if not r.fieldnames:
                    raise SystemExit(f"{reuse_csv}: empty or invalid CSV")
                w = csv.DictWriter(dst, fieldnames=r.fieldnames, extrasaction="ignore")
                w.writeheader()
                for i, row in enumerate(r):
                    if i >= limit_rows:
                        break
                    w.writerow(row)
            print(f"Copied first {limit_rows} rows from {reuse_csv} → {csv_path}")
        else:
            import shutil

            shutil.copyfile(reuse_csv, csv_path)
            print(f"Copied {reuse_csv} → {csv_path}")
        return

    df = pl.read_ndjson(hf_input)
    if limit_rows:
        df = df.head(limit_rows)
    out = build_prompt_response_frame(df)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    out.write_csv(csv_path)
    print(f"Wrote {out.height} rows to {csv_path}")


def iter_training_texts(csv_path: Path, limit_rows: int) -> Iterable[str]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "prompt" not in r.fieldnames or "response" not in r.fieldnames:
            raise SystemExit(f"{csv_path}: need prompt,response columns; got {r.fieldnames!r}")
        n = 0
        for row in r:
            yield row.get("prompt") or ""
            yield row.get("response") or ""
            n += 1
            if limit_rows and n >= limit_rows:
                break


def train_bytelevel_bpe(
    texts: Iterable[str],
    vocab_size: int,
    unk_token: str,
    pad_token: str,
) -> Tokenizer:
    tokenizer = Tokenizer(BPE(unk_token=unk_token))
    tokenizer.pre_tokenizer = ByteLevel(add_prefix_space=False)
    tokenizer.decoder = ByteLevelDecoder()
    trainer = BpeTrainer(
        vocab_size=vocab_size,
        min_frequency=2,
        show_progress=True,
        special_tokens=[unk_token, pad_token],
    )
    tokenizer.train_from_iterator(texts, trainer=trainer)
    return tokenizer


def vocab_json_from_tokenizer(tokenizer: Tokenizer) -> dict:
    m = tokenizer.get_vocab()
    # stable: sort by id
    pairs = sorted(m.items(), key=lambda kv: kv[1])
    vocab_list = [{"word": tok, "id": i} for tok, i in pairs]
    return {"vocabulary": vocab_list, "size": len(vocab_list)}


def write_token_jsonl(
    tokenizer: Tokenizer,
    csv_path: Path,
    out_jsonl: Path,
    limit_rows: int,
) -> int:
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with csv_path.open(newline="", encoding="utf-8") as f, out_jsonl.open(
        "w", encoding="utf-8"
    ) as out:
        r = csv.DictReader(f)
        for row in r:
            prompt = row.get("prompt") or ""
            response = row.get("response") or ""
            rec = {
                "prompt_ids": tokenizer.encode(prompt).ids,
                "response_ids": tokenizer.encode(response).ids,
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if limit_rows and n >= limit_rows:
                break
    return n


def main() -> None:
    p = argparse.ArgumentParser(description="Dolly HF → CSV → BPE vocab → token ID JSONL.")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("dolly_subword_out"),
        help="Directory for all outputs",
    )
    p.add_argument(
        "--hf-input",
        default=DEFAULT_HF_NDJSON,
        help="NDJSON URI (default: Hugging Face Dolly 15k)",
    )
    p.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Use this existing CSV instead of downloading (copied to out-dir)",
    )
    p.add_argument("--vocab-size", type=int, default=16000)
    p.add_argument(
        "--limit-rows",
        type=int,
        default=0,
        help="Cap CSV rows and JSONL lines (0 = all). Also caps text used for BPE training.",
    )
    p.add_argument(
        "--unk-token",
        default="<unk>",
        help="UNK special token string for BpeTrainer",
    )
    p.add_argument(
        "--pad-token",
        default="<pad>",
        help="PAD special token string for BpeTrainer",
    )
    args = p.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "dolly_prompt_response.csv"
    tok_path = out_dir / "dolly_subword_tokenizer.json"
    vocab_json_path = out_dir / "dolly_subword_vocab.json"
    jsonl_path = out_dir / "dolly_subword_token_ids.jsonl"
    meta_path = out_dir / "dolly_subword_token_ids_meta.json"

    load_or_build_csv(
        hf_input=args.hf_input,
        csv_path=csv_path,
        limit_rows=args.limit_rows,
        reuse_csv=args.csv,
    )

    print("Training ByteLevel BPE…")
    train_texts = iter_training_texts(csv_path, args.limit_rows)
    tokenizer = train_bytelevel_bpe(
        train_texts,
        vocab_size=args.vocab_size,
        unk_token=args.unk_token,
        pad_token=args.pad_token,
    )
    tokenizer.save(str(tok_path))
    print(f"Wrote tokenizer to {tok_path}")

    payload = vocab_json_from_tokenizer(tokenizer)
    with vocab_json_path.open("w", encoding="utf-8") as vf:
        json.dump(payload, vf, ensure_ascii=False, indent=2)
    print(f"Wrote vocab reference ({payload['size']} types) to {vocab_json_path}")

    n_lines = write_token_jsonl(tokenizer, csv_path, jsonl_path, args.limit_rows)
    print(f"Wrote {n_lines} lines to {jsonl_path}")

    vocab = tokenizer.get_vocab()
    unk_id = vocab.get(args.unk_token)
    pad_id = vocab.get(args.pad_token)
    meta = {
        "vocab_path": str(vocab_json_path.resolve()),
        "tokenizer_path": str(tok_path.resolve()),
        "vocab_token_count": len(vocab),
        "encoding": "ByteLevel BPE (Hugging Face tokenizers); spaces/punct are inside subword tokens",
        "unk_token": args.unk_token,
        "unk_id": unk_id,
        "pad_token": args.pad_token,
        "pad_id": pad_id,
        "hf_input": args.hf_input if args.csv is None else None,
        "csv_path": str(csv_path.resolve()),
        "max_token_id_exclusive": len(vocab),
        "note": (
            "IDs are contiguous 0..vocab_token_count-1. For decode, load tokenizer from tokenizer_path; "
            "dolly_subword_vocab.json is a human-readable id↔piece table in the same shape as dolly_vocab.json."
        ),
    }
    with meta_path.open("w", encoding="utf-8") as mf:
        json.dump(meta, mf, ensure_ascii=False, indent=2)
    print(f"Wrote meta to {meta_path}")


if __name__ == "__main__":
    main()
