#!/usr/bin/env python3
"""
Next-token **accuracy** on Dolly ``prompt,response`` rows using **Qwen2-0.5B** (full causal LM).

For each row: tokenize ``prompt + "\\n\\n" + response``, then measure how often
``argmax(logits[t-1]) == response_token[t]`` for positions in the **response** span
(same framing as ``train_autoreg_qwen`` prompt+response).

Runs **twice** by default: full depth, then **half** ``num_hidden_layers`` (weights copied from HF
first N blocks; nothing else changed).

Usage::

  python scripts/eval_qwen_dolly_accuracy.py --csv dolly_prompt_response.csv --limit 500

Requires: torch, transformers, tqdm.
"""

from __future__ import annotations

import argparse
import copy
import csv
import math
import os
import sys
from pathlib import Path

import torch
import torch.nn.functional as F


def _infer_repo_root() -> Path:
    here = Path(__file__).resolve()
    if here.name == "eval_qwen_dolly_accuracy.py" and here.parent.name == "scripts":
        return here.parents[1]
    return here.parent


def _root() -> Path:
    env = os.environ.get("DISTLM_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _infer_repo_root()


ROOT = _root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DEFAULT_CSV = ROOT / "dolly_prompt_response.csv"
_DEFAULT_MODEL = "Qwen/Qwen2-0.5B"


def load_rows(csv_path: Path, limit: int) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "prompt" not in r.fieldnames or "response" not in r.fieldnames:
            raise SystemExit(f"CSV needs prompt,response; got {r.fieldnames!r}")
        for i, row in enumerate(r):
            rows.append((row.get("prompt") or "", row.get("response") or ""))
            if limit and i + 1 >= limit:
                break
    return rows


def encode_prompt_response(
    tokenizer,
    prompt: str,
    response: str,
    *,
    max_seq_len: int,
    prepend_bos: bool,
) -> tuple[list[int], int] | None:
    response = (response or "").strip()
    if not response:
        return None
    bos_id = tokenizer.bos_token_id if prepend_bos else None
    prompt = (prompt or "").strip()
    prefix = (prompt + "\n\n") if prompt else ""
    prompt_ids = tokenizer.encode(prefix, add_special_tokens=False)
    if bos_id is not None:
        prompt_ids = [bos_id] + prompt_ids
    resp_ids = tokenizer.encode(response, add_special_tokens=False)
    if not resp_ids:
        return None
    prompt_len = len(prompt_ids)
    ids = prompt_ids + resp_ids
    if len(ids) > max_seq_len:
        take = max_seq_len - prompt_len
        if take < 1:
            return None
        ids = prompt_ids + resp_ids[:take]
    if len(ids) < 2 or prompt_len >= len(ids):
        return None
    return ids, prompt_len


def build_qwen_causal_lm(
    model_name: str,
    *,
    num_hidden_layers: int = 0,
    halve: bool = False,
    trust_remote_code: bool = True,
):
    """``num_hidden_layers`` 0 = full HF; else first N layers copied from pretrained."""
    from transformers import AutoConfig, AutoModelForCausalLM

    cfg = AutoConfig.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    full_n = int(cfg.num_hidden_layers)
    if num_hidden_layers > 0:
        want_n = min(int(num_hidden_layers), full_n)
    elif halve:
        want_n = max(1, full_n // 2)
    else:
        want_n = full_n

    if want_n == full_n:
        return AutoModelForCausalLM.from_pretrained(
            model_name,
            trust_remote_code=trust_remote_code,
        )

    cfg_new = copy.deepcopy(cfg)
    cfg_new.num_hidden_layers = want_n
    model = AutoModelForCausalLM.from_config(cfg_new, trust_remote_code=trust_remote_code)
    ref = AutoModelForCausalLM.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    tgt_sd = model.state_dict()
    ref_sd = ref.state_dict()
    not_copied: list[str] = []
    for k in tgt_sd:
        if k in ref_sd and ref_sd[k].shape == tgt_sd[k].shape:
            tgt_sd[k].copy_(ref_sd[k])
        else:
            not_copied.append(k)
    model.load_state_dict(tgt_sd)
    del ref
    print(
        f"  Built CausalLM: num_hidden_layers={want_n} (HF={full_n}); "
        f"weights not copied (shape mismatch): {len(not_copied)}",
        flush=True,
    )
    return model


def eval_accuracy(
    model,
    tokenizer,
    rows: list[tuple[str, str]],
    *,
    device: torch.device,
    max_seq_len: int,
    prepend_bos: bool,
    use_amp: bool,
) -> tuple[float, float, int]:
    """
    Returns (token_accuracy, mean_ce, total_response_tokens).
    CE is mean cross-entropy over response next-token predictions (natural log).
    """
    model.eval()
    total_correct = 0
    total_tok = 0
    ce_sum = 0.0

    with torch.no_grad():
        for prompt, response in rows:
            enc = encode_prompt_response(
                tokenizer, prompt, response, max_seq_len=max_seq_len, prepend_bos=prepend_bos
            )
            if enc is None:
                continue
            ids, prompt_len = enc
            L = len(ids)
            if L < 2 or prompt_len >= L:
                continue

            input_ids = torch.tensor([ids], dtype=torch.long, device=device)
            attn = torch.ones_like(input_ids)

            with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
                out = model(input_ids=input_ids, attention_mask=attn)
                logits = out.logits.float()

            # logits[:, i] predicts token at i+1
            pred_logits = logits[:, prompt_len - 1 : L - 1, :]
            targets = input_ids[:, prompt_len:L]
            pred = pred_logits.argmax(dim=-1)
            correct = (pred == targets).sum().item()
            n = targets.numel()
            total_correct += correct
            total_tok += n

            ce = F.cross_entropy(
                pred_logits.reshape(-1, pred_logits.size(-1)),
                targets.reshape(-1),
                reduction="sum",
            )
            ce_sum += float(ce)

    if total_tok == 0:
        return 0.0, float("inf"), 0
    acc = total_correct / total_tok
    mean_ce = ce_sum / total_tok
    return acc, mean_ce, total_tok


def main() -> None:
    p = argparse.ArgumentParser(description="Qwen2-0.5B next-token accuracy on Dolly CSV")
    p.add_argument("--csv", type=Path, default=_DEFAULT_CSV, help="Dolly prompt,response CSV")
    p.add_argument("--qwen-model", type=str, default=_DEFAULT_MODEL)
    p.add_argument("--limit", type=int, default=500, help="Max rows (0 = all)")
    p.add_argument("--max-seq-len", type=int, default=2048)
    p.add_argument("--prepend-bos", action="store_true", help="Prepend tokenizer BOS to prompt if set")
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--amp", action="store_true", help="autocast fp16 on CUDA")
    p.add_argument("--skip-full", action="store_true", help="Only run half-layer model")
    p.add_argument("--skip-half", action="store_true", help="Only run full model")
    args = p.parse_args()

    csv_path = args.csv.expanduser().resolve()
    if not csv_path.is_file():
        raise SystemExit(f"CSV not found: {csv_path}")

    try:
        from transformers import AutoTokenizer
    except ImportError as e:
        raise SystemExit("pip install transformers") from e

    limit = args.limit if args.limit > 0 else 0
    rows = load_rows(csv_path, limit)
    if not rows:
        raise SystemExit("No rows loaded")

    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    use_amp = bool(args.amp and device.type == "cuda")

    print(f"Rows: {len(rows)}  device={device}  amp={use_amp}", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(args.qwen_model, trust_remote_code=True)

    def run_one(*, halve: bool, num_layers: int) -> None:
        model = build_qwen_causal_lm(
            args.qwen_model,
            num_hidden_layers=num_layers,
            halve=halve,
        )
        nl = int(model.config.num_hidden_layers)
        label = f"Qwen num_hidden_layers={nl}"
        print(f"\n=== {label} ===", flush=True)
        model.to(device)
        acc, mean_ce, ntok = eval_accuracy(
            model,
            tokenizer,
            rows,
            device=device,
            max_seq_len=args.max_seq_len,
            prepend_bos=args.prepend_bos,
            use_amp=use_amp,
        )
        ppl = math.exp(mean_ce) if mean_ce < 100 else float("inf")
        n_correct = int(round(acc * ntok))
        print(f"  response next-token accuracy: {acc:.6f}  ({n_correct}/{ntok} tokens)", flush=True)
        print(f"  mean CE (nats): {mean_ce:.4f}  perplexity: {ppl:.2f}", flush=True)
        print(f"  total response tokens evaluated: {ntok}", flush=True)
        del model
        if device.type == "cuda":
            torch.cuda.empty_cache()

    if not args.skip_full:
        run_one(halve=False, num_layers=0)
    if not args.skip_half:
        run_one(halve=True, num_layers=0)


if __name__ == "__main__":
    main()
