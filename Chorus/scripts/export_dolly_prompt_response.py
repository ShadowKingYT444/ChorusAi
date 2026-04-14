#!/usr/bin/env python3
"""
Read Databricks Dolly 15k from Hugging Face (NDJSON) and write a 2-column CSV: prompt, response.

  pip install 'polars[fsspec]' huggingface_hub
  python scripts/export_dolly_prompt_response.py -o dolly_prompt_response.csv

`prompt` = instruction, with optional ### Context block when `context` is non-empty.
"""

from __future__ import annotations

import argparse

import polars as pl


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "-o",
        "--output",
        default="dolly_prompt_response.csv",
        help="Output CSV path",
    )
    p.add_argument(
        "-i",
        "--input",
        default="hf://datasets/databricks/databricks-dolly-15k/databricks-dolly-15k.jsonl",
        help="NDJSON path (default: Hugging Face Dolly 15k)",
    )
    args = p.parse_args()

    df = pl.read_ndjson(args.input)

    instr = pl.col("instruction").fill_null("").cast(pl.Utf8)
    ctx = pl.col("context").fill_null("").cast(pl.Utf8)
    prompt = (
        instr
        + pl.when(ctx.str.strip_chars().str.len_chars() > 0)
        .then(pl.lit("\n\n### Context\n") + ctx)
        .otherwise(pl.lit(""))
    ).alias("prompt")

    out = df.select(
        prompt,
        pl.col("response").fill_null("").cast(pl.Utf8).alias("response"),
    )
    out.write_csv(args.output)
    print(f"Wrote {out.height} rows × {out.width} columns to {args.output!r}")


if __name__ == "__main__":
    main()
