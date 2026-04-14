#!/usr/bin/env python3
"""
Single-file Qwen2.5-0.5B finetuner with alternating-layer pruning.

Loads ``Qwen/Qwen2.5-0.5B``, drops every other transformer layer
(keeps even indices 0, 2, 4 …), then finetunes on data/data.jsonl
with cross-entropy only on the *answer* tokens.

Run (from repo root):

  python scripts/finetune_qwen_pruned.py

  # optional overrides
  python scripts/finetune_qwen_pruned.py \\
      --model Qwen/Qwen2.5-0.5B \\
      --data  data/data.jsonl   \\
      --epochs 5 --lr 2e-4 --batch-size 4 --out out/qwen_pruned.pt
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import DataLoader, Dataset

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **kwargs):  # type: ignore[misc]
        return x


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    env = os.environ.get("DISTLM_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    p = Path(__file__).resolve()
    return p.parents[1] if p.parent.name == "scripts" else p.parent


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DEFAULT_DATA   = ROOT / "data" / "data.jsonl"
_DEFAULT_OUT    = ROOT / "out" / "qwen_pruned.pt"
_DEFAULT_MODEL  = "Qwen/Qwen2.5-0.5B"


# ---------------------------------------------------------------------------
# layer pruning
# ---------------------------------------------------------------------------

def prune_alternating_layers(model: nn.Module, keep: str = "even") -> nn.Module:
    """
    Remove every other transformer layer from a HuggingFace Qwen2-style model.

    ``keep="even"``  → keeps layers 0, 2, 4 …  (drops 1, 3, 5 …)
    ``keep="odd"``   → keeps layers 1, 3, 5 …  (drops 0, 2, 4 …)

    Works with any model whose decoder layers live at
    ``model.model.layers`` (Qwen2, LLaMA, Mistral, …).

    Returns the mutated model (in-place).
    """
    layers: nn.ModuleList = model.model.layers  # type: ignore[attr-defined]
    original_n = len(layers)

    if keep == "even":
        kept = [layers[i] for i in range(original_n) if i % 2 == 0]
    else:
        kept = [layers[i] for i in range(original_n) if i % 2 == 1]

    model.model.layers = nn.ModuleList(kept)

    # Update the config so generation / attention helpers agree on depth
    if hasattr(model.config, "num_hidden_layers"):
        model.config.num_hidden_layers = len(kept)

    print(
        f"[prune] {original_n} → {len(kept)} layers "
        f"(kept {'even' if keep == 'even' else 'odd'} indices)",
        flush=True,
    )
    return model


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{lineno}: invalid JSON – {e}") from e
            q = (obj.get("question") or obj.get("prompt") or obj.get("q") or "").strip()
            a = (obj.get("answer")   or obj.get("response") or obj.get("a") or "").strip()
            if a:
                rows.append((q, a))
    return rows


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------

class QADataset(Dataset):
    """
    Each item: full token ids + the index where the answer starts.
    Loss is computed only on answer tokens.
    """

    def __init__(
        self,
        rows: list[tuple[str, str]],
        tokenizer,
        max_seq_len: int,
    ) -> None:
        self.items: list[tuple[list[int], int]] = []  # (ids, answer_start)
        sep = "\n\n"
        for q, a in rows:
            prompt = (q + sep) if q else ""
            prompt_ids = tokenizer.encode(prompt, add_special_tokens=False)
            answer_ids = tokenizer.encode(a,      add_special_tokens=False)
            if not answer_ids:
                continue
            ids = prompt_ids + answer_ids
            if len(ids) > max_seq_len:
                take = max_seq_len - len(prompt_ids)
                if take < 1:
                    continue
                ids = prompt_ids + answer_ids[:take]
            if len(ids) < 2:
                continue
            self.items.append((ids, len(prompt_ids)))

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int):
        ids, ans_start = self.items[idx]
        return torch.tensor(ids, dtype=torch.long), ans_start


def collate_fn(pad_id: int):
    def _collate(batch):
        tensors, ans_starts = zip(*batch)
        padded = pad_sequence(list(tensors), batch_first=True, padding_value=pad_id)
        lengths    = torch.tensor([t.shape[0] for t in tensors], dtype=torch.long)
        ans_starts = torch.tensor(list(ans_starts),              dtype=torch.long)
        return padded, lengths, ans_starts
    return _collate


# ---------------------------------------------------------------------------
# loss
# ---------------------------------------------------------------------------

def compute_loss(
    model: nn.Module,
    ids: torch.Tensor,
    lengths: torch.Tensor,
    ans_starts: torch.Tensor,
    *,
    device: torch.device,
    use_amp: bool,
    label_smoothing: float = 0.0,
) -> torch.Tensor:
    ids        = ids.to(device)
    lengths    = lengths.to(device)
    ans_starts = ans_starts.to(device)

    b, t = ids.shape
    pos  = torch.arange(t, device=device).unsqueeze(0)          # (1, t)
    attn = (pos < lengths.unsqueeze(1)).long()                   # (B, t)

    with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
        out    = model(input_ids=ids, attention_mask=attn)
        logits = out.logits                                       # (B, t, V)

    # shift: predict position s from position s-1
    shift_logits = logits[:, :-1].contiguous()                   # (B, t-1, V)
    shift_labels = ids[:, 1:].clone()                            # (B, t-1)

    target_pos = pos[:, 1:]                                      # positions 1..t-1

    # mask: only answer tokens that are within the real sequence
    valid = (
        (target_pos >= ans_starts.unsqueeze(1)) &               # inside answer
        (target_pos  < lengths.unsqueeze(1))                     # within real length
    )
    shift_labels[~valid] = -100

    loss = F.cross_entropy(
        shift_logits.view(-1, shift_logits.size(-1)),
        shift_labels.view(-1),
        ignore_index=-100,
        label_smoothing=label_smoothing,
    )
    return loss if torch.isfinite(loss) else logits.sum() * 0.0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Finetune pruned Qwen2.5-0.5B on QA data."
    )
    ap.add_argument("--model",           default=_DEFAULT_MODEL,
                    help="HuggingFace model id or local path")
    ap.add_argument("--data",            type=Path, default=_DEFAULT_DATA)
    ap.add_argument("--out",             type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--keep-layers",     choices=("even", "odd"), default="even",
                    help="which set of alternating layers to keep after pruning")
    ap.add_argument("--max-seq-len",     type=int,   default=256)
    ap.add_argument("--epochs",          type=int,   default=5)
    ap.add_argument("--batch-size",      type=int,   default=4)
    ap.add_argument("--grad-accum",      type=int,   default=4)
    ap.add_argument("--lr",              type=float, default=2e-4)
    ap.add_argument("--weight-decay",    type=float, default=0.01)
    ap.add_argument("--max-grad-norm",   type=float, default=1.0)
    ap.add_argument("--label-smoothing", type=float, default=0.0)
    ap.add_argument("--val-split",       type=float, default=0.10,
                    help="fraction of rows held out for validation")
    ap.add_argument("--seed",            type=int,   default=42)
    ap.add_argument("--amp",             action="store_true",
                    help="enable fp16 AMP (CUDA only)")
    ap.add_argument("--cpu",             action="store_true")
    ap.add_argument("--no-prune",        action="store_true",
                    help="skip layer pruning (full model)")
    args = ap.parse_args()

    # ── reproducibility ────────────────────────────────────────────────────
    random.seed(args.seed)
    torch.manual_seed(args.seed)

    # ── device ─────────────────────────────────────────────────────────────
    device = torch.device(
        "cpu" if args.cpu or not torch.cuda.is_available() else "cuda"
    )
    use_amp = bool(args.amp and device.type == "cuda")
    print(f"Device: {device}  AMP: {use_amp}", flush=True)

    # ── tokenizer + model ──────────────────────────────────────────────────
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as e:
        raise SystemExit("pip install transformers") from e

    print(f"Loading tokenizer/model '{args.model}' …", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id

    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.float32,
        trust_remote_code=True,
    )

    # ── pruning ────────────────────────────────────────────────────────────
    if not args.no_prune:
        model = prune_alternating_layers(model, keep=args.keep_layers)

    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Trainable params: {n_params:,}", flush=True)
    model = model.to(device)

    # ── data ───────────────────────────────────────────────────────────────
    data_path = args.data.expanduser().resolve()
    if not data_path.is_file():
        raise SystemExit(f"Data file not found: {data_path}")

    rows = load_jsonl(data_path)
    print(f"Loaded {len(rows)} QA pairs from {data_path}", flush=True)
    if len(rows) < 2:
        raise SystemExit("Need at least 2 rows.")

    random.shuffle(rows)
    n_val   = max(1, int(round(len(rows) * args.val_split)))
    n_val   = min(n_val, len(rows) - 1)
    val_rows   = rows[:n_val]
    train_rows = rows[n_val:]
    print(f"Split: {len(train_rows)} train / {len(val_rows)} val", flush=True)

    pad_id = tokenizer.pad_token_id
    train_ds = QADataset(train_rows, tokenizer, args.max_seq_len)
    val_ds   = QADataset(val_rows,   tokenizer, args.max_seq_len)
    if len(train_ds) == 0:
        raise SystemExit("No training examples after tokenization.")

    collate = collate_fn(pad_id)
    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        collate_fn=collate, num_workers=0, pin_memory=(device.type == "cuda"),
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size, shuffle=False,
        collate_fn=collate, num_workers=0, pin_memory=(device.type == "cuda"),
    )

    # ── optimiser + scaler ─────────────────────────────────────────────────
    opt = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    scaler = None
    if use_amp:
        try:
            scaler = torch.amp.GradScaler(device.type, enabled=True)
        except (TypeError, AttributeError):
            scaler = torch.cuda.amp.GradScaler(enabled=True)

    # ── training loop ──────────────────────────────────────────────────────
    best_val   = float("inf")
    out_path   = args.out.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def optimizer_step() -> None:
        if use_amp and scaler is not None:
            scaler.unscale_(opt)
        if args.max_grad_norm > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
        if use_amp and scaler is not None:
            scaler.step(opt)
            scaler.update()
        else:
            opt.step()

    for epoch in range(args.epochs):
        # ── train ──────────────────────────────────────────────────────────
        model.train()
        total_loss, n_batches, accum = 0.0, 0, 0
        opt.zero_grad(set_to_none=True)

        pbar = tqdm(train_loader, desc=f"train {epoch+1}/{args.epochs}")
        for ids, lengths, ans_starts in pbar:
            loss = compute_loss(
                model, ids, lengths, ans_starts,
                device=device, use_amp=use_amp,
                label_smoothing=args.label_smoothing,
            ) / args.grad_accum

            if use_amp and scaler is not None:
                scaler.scale(loss).backward()
            else:
                loss.backward()

            accum += 1
            if accum % args.grad_accum == 0:
                optimizer_step()
                opt.zero_grad(set_to_none=True)

            total_loss += float(loss.detach()) * args.grad_accum
            n_batches  += 1
            pbar.set_postfix(loss=f"{total_loss / n_batches:.4f}")

        if accum % args.grad_accum != 0:
            optimizer_step()
            opt.zero_grad(set_to_none=True)

        # ── validate ───────────────────────────────────────────────────────
        model.eval()
        val_loss, val_n = 0.0, 0
        with torch.no_grad():
            for ids, lengths, ans_starts in val_loader:
                val_loss += float(
                    compute_loss(
                        model, ids, lengths, ans_starts,
                        device=device, use_amp=use_amp,
                    )
                )
                val_n += 1
        val_ce = val_loss / max(1, val_n)
        print(f"epoch {epoch+1}  train_ce={total_loss/max(1,n_batches):.4f}  val_ce={val_ce:.4f}", flush=True)

        if val_ce < best_val:
            best_val = val_ce
            # save full HuggingFace checkpoint (weights + config)
            hf_dir = out_path.with_suffix("")  # out/qwen_pruned/
            model.save_pretrained(str(hf_dir))
            tokenizer.save_pretrained(str(hf_dir))
            # also save a lightweight .pt snapshot
            torch.save(
                {
                    "model_state":       model.state_dict(),
                    "config":            model.config.to_dict(),
                    "best_val_ce":       best_val,
                    "epochs_run":        epoch + 1,
                    "keep_layers":       args.keep_layers,
                    "layers_kept":       len(model.model.layers),
                },
                out_path,
            )
            print(f"  ✓ saved  {hf_dir}  (best val_ce={best_val:.4f})", flush=True)

    print(f"\nDone.  best_val_ce={best_val:.4f}", flush=True)
    print(f"HF checkpoint: {out_path.with_suffix('')}", flush=True)


if __name__ == "__main__":
    main()
