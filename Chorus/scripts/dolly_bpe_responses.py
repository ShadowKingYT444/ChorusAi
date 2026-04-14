#!/usr/bin/env python3
"""
Download Databricks Dolly 15k, train ByteLevel BPE, write vocabulary + one JSON line per row
with **response** token IDs only.

Steps:
  1. Load NDJSON from Hugging Face (`hf://...`) or `--csv` with `prompt` + `response` columns.
  2. Parse into `prompt` / `response` (same rules as `export_dolly_prompt_response.py`).
  3. Train BPE on every prompt and every response (better merges than responses alone).
  4. Save tokenizer + vocab JSON (`dolly_vocab.json` shape).
  5. Write `dolly_response_token_ids.jsonl`: each line `{"response_ids":[int,...]}`.

Install:

  pip install polars fsspec huggingface_hub tokenizers

  # or: pip install -e ".[dolly]"

Examples:

  python scripts/dolly_bpe_responses.py --out-dir dolly_bpe_responses
  python scripts/dolly_bpe_responses.py --out-dir out --csv dolly_prompt_response.csv
  python scripts/dolly_bpe_responses.py --out-dir out --vocab-size 8000 --limit-rows 2000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

DEFAULT_HF = "hf://datasets/databricks/databricks-dolly-15k/databricks-dolly-15k.jsonl"


def _die_deps() -> None:
    print("Install: pip install polars fsspec huggingface_hub tokenizers", file=sys.stderr)
    raise SystemExit(1)


try:
    import polars as pl
except ImportError:
    _die_deps()

try:
    from tokenizers import Tokenizer
    from tokenizers.decoders import ByteLevel as ByteLevelDecoder
    from tokenizers.models import BPE
    from tokenizers.pre_tokenizers import ByteLevel
    from tokenizers.trainers import BpeTrainer
except ImportError:
    _die_deps()


def build_prompt_response(df: pl.DataFrame) -> pl.DataFrame:
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


def load_table(
    hf_path: str | None,
    csv_path: Path | None,
    limit_rows: int,
) -> pl.DataFrame:
    if csv_path is not None:
        if not csv_path.is_file():
            raise SystemExit(f"--csv not found: {csv_path}")
        df = pl.read_csv(csv_path)
        need = {"prompt", "response"}
        if not need.issubset(set(df.columns)):
            raise SystemExit(f"--csv must have columns prompt,response; got {df.columns}")
        if limit_rows:
            df = df.head(limit_rows)
        return df
    assert hf_path is not None
    raw = pl.read_ndjson(hf_path)
    if limit_rows:
        raw = raw.head(limit_rows)
    return build_prompt_response(raw)


def train_texts(df: pl.DataFrame) -> Iterable[str]:
    for row in df.iter_rows(named=True):
        yield row["prompt"]
        yield row["response"]


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


def vocab_json(tokenizer: Tokenizer) -> dict:
    m = tokenizer.get_vocab()
    pairs = sorted(m.items(), key=lambda kv: kv[1])
    return {
        "vocabulary": [{"word": tok, "id": i} for tok, i in pairs],
        "size": len(pairs),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Dolly → BPE vocab → response token IDs JSONL.")
    p.add_argument("--out-dir", type=Path, default=Path("dolly_bpe_responses"))
    p.add_argument("--hf-path", default=DEFAULT_HF, help="NDJSON URI (ignored if --csv)")
    p.add_argument("--csv", type=Path, default=None, help="Local CSV with prompt,response")
    p.add_argument("--vocab-size", type=int, default=16000)
    p.add_argument("--limit-rows", type=int, default=0, help="0 = all rows")
    p.add_argument("--unk-token", default="<unk>")
    p.add_argument("--pad-token", default="<pad>")
    args = p.parse_args()

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    print("Loading…")
    df = load_table(
        args.hf_path if args.csv is None else None,
        args.csv,
        args.limit_rows,
    )
    print(f"Rows: {df.height}")

    print("Training BPE (prompt + response per row)…")
    tok = train_bytelevel_bpe(
        train_texts(df),
        vocab_size=args.vocab_size,
        unk_token=args.unk_token,
        pad_token=args.pad_token,
    )

    tok_path = out / "dolly_bpe_tokenizer.json"
    vocab_path = out / "dolly_bpe_vocab.json"
    jsonl_path = out / "dolly_response_token_ids.jsonl"
    meta_path = out / "dolly_bpe_responses_meta.json"

    tok.save(str(tok_path))
    payload = vocab_json(tok)
    with vocab_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    vmap = tok.get_vocab()
    with jsonl_path.open("w", encoding="utf-8") as jf:
        for row in df.iter_rows(named=True):
            r = row["response"] or ""
            rec = {"response_ids": tok.encode(r).ids}
            jf.write(json.dumps(rec, ensure_ascii=False) + "\n")

    meta = {
        "hf_path": args.hf_path if args.csv is None else None,
        "csv_path": str(args.csv.resolve()) if args.csv else None,
        "rows": df.height,
        "vocab_size": payload["size"],
        "tokenizer_path": str(tok_path.resolve()),
        "vocab_path": str(vocab_path.resolve()),
        "response_token_ids_jsonl": str(jsonl_path.resolve()),
        "unk_token": args.unk_token,
        "unk_id": vmap.get(args.unk_token),
        "pad_token": args.pad_token,
        "pad_id": vmap.get(args.pad_token),
        "line_format": '{"response_ids":[...]}',
    }
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"Wrote {tok_path}")
    print(f"Wrote {vocab_path} ({payload['size']} entries)")
    print(f"Wrote {jsonl_path} ({df.height} lines)")
    print(f"Wrote {meta_path}")


if __name__ == "__main__":
    main()
