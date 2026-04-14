#!/usr/bin/env python3
"""
Train only the **GPT-2-style LM head** in ``agenticbanger.GPT2LMHead`` on next-token CE,
with a **frozen** ``transformers.GPT2Model`` backbone (hidden states in, logits out).

Replaces the old Qwen2 full-model trainer.

Examples::

  pip install torch transformers datasets tqdm

  python scripts/train_agenticbanger_gpt2_head.py --max-steps 100 --batch-size 2
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Iterator

import torch
from torch import amp
from torch.utils.data import DataLoader

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(x, **kw):  # type: ignore[misc]
        return x


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1] if here.parent.name == "scripts" else here.parent


def setup_hf_cache() -> Path:
    raw = os.environ.get("HF_CACHE", "").strip()
    if raw:
        cache = Path(raw).expanduser().resolve()
    else:
        cache = (_repo_root() / "hf_cache").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache)
    os.environ["HF_DATASETS_CACHE"] = str(cache / "datasets")
    os.environ["HF_HUB_CACHE"] = str(cache / "hub")
    os.environ.setdefault("TRANSFORMERS_CACHE", str(cache / "transformers"))
    print(f"HF cache: {cache}", flush=True)
    return cache


def _insert_repo_path() -> Path:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def build_lm_dataset(
    *,
    tokenizer: Any,
    max_seq_len: int,
    hub_path: str | None,
    hub_config: str | None,
    hub_split: str,
    text_column: str,
    jsonl_path: Path | None,
    jsonl_text_key: str,
    max_samples: int | None,
) -> Any:
    from datasets import Dataset, load_dataset

    if jsonl_path is not None:
        rows: list[dict[str, Any]] = []
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                t = obj.get(jsonl_text_key)
                if t is None or not str(t).strip():
                    continue
                rows.append({text_column: str(t)})
                if max_samples is not None and len(rows) >= max_samples:
                    break
        if not rows:
            raise ValueError(f"No non-empty texts in {jsonl_path} (key={jsonl_text_key!r})")
        raw = Dataset.from_list(rows)
    else:
        assert hub_path is not None
        kwargs: dict[str, Any] = {"split": hub_split}
        if hub_config is not None:
            kwargs["name"] = hub_config
        raw = load_dataset(hub_path, **kwargs)
        if max_samples is not None:
            raw = raw.select(range(min(max_samples, len(raw))))

    def tokenize_fn(batch: dict[str, list]) -> dict[str, list]:
        return tokenizer(
            batch[text_column],
            truncation=False,
            add_special_tokens=True,
        )

    tok = raw.map(
        tokenize_fn,
        batched=True,
        num_proc=1,
        remove_columns=raw.column_names,
        desc="Tokenizing",
    )

    block = max_seq_len

    def group_texts(examples: dict[str, list]) -> dict[str, list]:
        concatenated: dict[str, list] = {k: sum(examples[k], []) for k in examples.keys()}
        total_length = len(concatenated["input_ids"])
        total_length = (total_length // block) * block
        if total_length == 0:
            return {k: [] for k in examples.keys()}
        out: dict[str, list] = {}
        for k, t in concatenated.items():
            out[k] = [t[i : i + block] for i in range(0, total_length, block)]
        return out

    grouped = tok.map(group_texts, batched=True, num_proc=1, desc="Grouping blocks")
    if len(grouped) == 0:
        raise ValueError(
            "No training blocks after tokenization. Increase data, lower --max-seq-len, "
            "or pick a dataset with longer text."
        )
    grouped = grouped.add_column("labels", grouped["input_ids"])
    grouped.set_format(type="torch", columns=["input_ids", "attention_mask", "labels"])
    return grouped


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train agenticbanger GPT2LMHead (frozen GPT2Model body).")
    p.add_argument("--gpt2-model", default="openai-community/gpt2", help="HF id for tokenizer + frozen backbone.")
    p.add_argument(
        "--init-head-from-hf",
        action="store_true",
        help="Initialize head from HF GPT2LMHeadModel.lm_head (default: random init).",
    )
    p.add_argument("--output", type=Path, default=None, help="Default: out/agenticbanger_gpt2_head.pt")
    p.add_argument("--hub-dataset", default="wikitext")
    p.add_argument("--hub-config", default="wikitext-2-raw-v1")
    p.add_argument("--hub-split", default="train")
    p.add_argument("--text-column", default="text")
    p.add_argument("--jsonl", type=Path, default=None)
    p.add_argument("--text-key", default="text")
    p.add_argument("--max-samples", type=int, default=None)
    p.add_argument("--max-seq-len", type=int, default=512)
    p.add_argument("--batch-size", type=int, default=2)
    p.add_argument("--grad-accum", type=int, default=4)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--warmup-steps", type=int, default=50)
    p.add_argument("--max-steps", type=int, default=2000)
    p.add_argument("--max-grad-norm", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--log-every", type=int, default=10)
    p.add_argument("--save-every", type=int, default=500)
    p.add_argument("--resume", type=Path, default=None)
    p.add_argument("--bf16", action="store_true")
    p.add_argument("--no-shuffle", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    _insert_repo_path()
    setup_hf_cache()
    random.seed(args.seed)
    torch.manual_seed(args.seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_bf16 = bool(args.bf16 and device.type == "cuda" and torch.cuda.is_bf16_supported())
    torch_dtype = torch.bfloat16 if use_bf16 else torch.float32
    print(f"device={device} train_dtype={torch_dtype}", flush=True)

    from transformers import AutoTokenizer, GPT2Model

    tokenizer = AutoTokenizer.from_pretrained(args.gpt2_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    backbone = GPT2Model.from_pretrained(args.gpt2_model, torch_dtype=torch_dtype)
    backbone.eval()
    for p in backbone.parameters():
        p.requires_grad = False
    backbone.to(device)

    from agenticbanger.gpt2_lm_head import GPT2LMHead, GPT2LMHeadConfig, load_gpt2_lm_head_from_hf

    n_embd = backbone.config.n_embd
    vocab_size = backbone.config.vocab_size
    if args.init_head_from_hf:
        head = load_gpt2_lm_head_from_hf(args.gpt2_model, dtype=torch_dtype, device=device)
    else:
        head = GPT2LMHead(GPT2LMHeadConfig(n_embd=n_embd, vocab_size=vocab_size))
        head.to(device=device, dtype=torch_dtype)
    head.train()

    lm = build_lm_dataset(
        tokenizer=tokenizer,
        max_seq_len=args.max_seq_len,
        hub_path=None if args.jsonl else args.hub_dataset,
        hub_config=args.hub_config if not args.jsonl else None,
        hub_split=args.hub_split,
        text_column=args.text_column,
        jsonl_path=args.jsonl,
        jsonl_text_key=args.text_key,
        max_samples=args.max_samples,
    )

    loader = DataLoader(
        lm,
        batch_size=args.batch_size,
        shuffle=not args.no_shuffle,
        drop_last=True,
    )

    opt = torch.optim.AdamW(head.parameters(), lr=args.lr, weight_decay=args.weight_decay)

    def lr_lambda(step: int) -> float:
        if args.warmup_steps <= 0:
            return 1.0
        if step < args.warmup_steps:
            return float(step + 1) / float(args.warmup_steps)
        return 1.0

    sched = torch.optim.lr_scheduler.LambdaLR(opt, lr_lambda)
    start_step = 0
    if args.resume is not None:
        try:
            ck = torch.load(args.resume, map_location="cpu", weights_only=False)
        except TypeError:
            ck = torch.load(args.resume, map_location="cpu")
        head.load_state_dict(ck["head_state"])
        if "optimizer_state" in ck:
            opt.load_state_dict(ck["optimizer_state"])
        start_step = int(ck.get("step", 0))
        if "scheduler_state" in ck:
            sched.load_state_dict(ck["scheduler_state"])
        head.to(device)
        print(f"Resumed from step {start_step}", flush=True)

    out_path = args.output or (_repo_root() / "out" / "agenticbanger_gpt2_head.pt")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scaler = amp.GradScaler(enabled=(device.type == "cuda" and not use_bf16))
    autocast_dtype = torch.bfloat16 if use_bf16 else torch.float16

    step = start_step
    micro = 0
    accum = max(1, args.grad_accum)
    running_loss = 0.0
    pbar = tqdm(total=args.max_steps - start_step, desc="train", unit="step")

    it: Iterator = iter(loader)
    while step < args.max_steps:
        try:
            batch = next(it)
        except StopIteration:
            it = iter(loader)
            batch = next(it)

        input_ids = batch["input_ids"].to(device, non_blocking=True)
        attention_mask = batch["attention_mask"].to(device, non_blocking=True)
        labels = batch["labels"].to(device, non_blocking=True)
        labels = labels.masked_fill(attention_mask == 0, -100)

        with amp.autocast(device_type=device.type, dtype=autocast_dtype, enabled=device.type == "cuda"):
            with torch.no_grad():
                out_b = backbone(input_ids=input_ids, attention_mask=attention_mask)
                hidden = out_b.last_hidden_state
            out_h = head(hidden, labels=labels)
            raw_loss = out_h.loss
            loss = raw_loss / accum if raw_loss is not None else torch.tensor(0.0, device=device)

        if scaler.is_enabled():
            scaler.scale(loss).backward()
        else:
            loss.backward()

        if raw_loss is not None:
            running_loss += float(raw_loss.detach().item())
        micro += 1

        if micro >= accum:
            if scaler.is_enabled():
                scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(head.parameters(), args.max_grad_norm)
            if scaler.is_enabled():
                scaler.step(opt)
                scaler.update()
            else:
                opt.step()
            opt.zero_grad(set_to_none=True)
            sched.step()
            step += 1
            micro = 0
            pbar.update(1)

            if step % args.log_every == 0:
                avg = running_loss / args.log_every
                running_loss = 0.0
                lr = sched.get_last_lr()[0]
                pbar.set_postfix(loss=f"{avg:.4f}", lr=f"{lr:.2e}")

            if step % args.save_every == 0 or step >= args.max_steps:
                ckpt = {
                    "step": step,
                    "head_state": head.state_dict(),
                    "optimizer_state": opt.state_dict(),
                    "scheduler_state": sched.state_dict(),
                    "args": vars(args),
                    "gpt2_model": args.gpt2_model,
                    "n_embd": n_embd,
                    "vocab_size": vocab_size,
                }
                torch.save(ckpt, out_path)
                print(f"Saved checkpoint step={step} → {out_path}", flush=True)

    pbar.close()
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
