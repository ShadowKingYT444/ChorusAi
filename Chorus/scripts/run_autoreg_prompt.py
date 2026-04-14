#!/usr/bin/env python3
"""
Load a trained AutoregPredictor checkpoint + BPE tokenizer + encoder; autoregress from a prompt.

Example:

  python scripts/run_autoreg_prompt.py \\
    --checkpoint autoreg_checkpoint.pt \\
    --tokenizer out/dolly_bpe_tokenizer.json \\
    --prompt "What is 2+2?"
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import torch

from chorus.autoreg_model import AutoregPredictor
from chorus.prefix_encoder import make_prefix_encoder


def _torch_load(path: Path) -> dict:
    kw: dict = {"map_location": "cpu"}
    try:
        if "weights_only" in inspect.signature(torch.load).parameters:
            kw["weights_only"] = False
    except (TypeError, ValueError):
        pass
    return torch.load(path, **kw)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("--prompt", type=str, default="", help="If empty, read stdin line")
    p.add_argument("--max-tokens", type=int, default=64)
    p.add_argument("--minilm-model", default=None, help="Override; else use checkpoint value")
    p.add_argument(
        "--embed-mode",
        choices=("sentence", "token_last"),
        default=None,
        help="Override checkpoint (sentence vs last HF token hidden)",
    )
    args = p.parse_args()

    from tokenizers import Tokenizer

    ck = _torch_load(args.checkpoint.expanduser().resolve())
    cfg = ck.get("config", {})
    vs = int(ck.get("vocab_size", cfg.get("vocab_size", 16000)))
    minilm = args.minilm_model or ck.get("minilm_model", "sentence-transformers/all-MiniLM-L6-v2")
    emode = args.embed_mode or ck.get("embed_mode") or cfg.get("embed_mode", "sentence")
    dim = int(ck.get("dim", cfg.get("dim", 384)))
    enc_stored = ck.get("encoder_dim", cfg.get("encoder_dim"))
    if enc_stored is None:
        enc_arg = None
    else:
        ei = int(enc_stored)
        enc_arg = ei if ei != dim else None

    model = AutoregPredictor(vocab_size=vs, dim=dim, encoder_dim=enc_arg)
    model.load_state_dict(ck["model_state"], strict=False)
    model.eval()

    tok = Tokenizer.from_file(str(args.tokenizer.expanduser().resolve()))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    prefix_enc = make_prefix_encoder(emode, minilm, device)

    prompt = args.prompt.strip()
    if not prompt:
        prompt = sys.stdin.readline().strip()

    text = prompt
    out_ids: list[int] = []
    with torch.no_grad():
        for _ in range(args.max_tokens):
            t = prefix_enc.encode(text)
            logits = model(t)
            tid = int(logits.argmax(dim=-1).item())
            out_ids.append(tid)
            text = text + tok.decode([tid])

    print("token_ids:", out_ids)
    print("decoded:", tok.decode(out_ids))
    print("full_text:", text)


if __name__ == "__main__":
    main()
