#!/usr/bin/env python3
"""
Train :class:`distlm.autoreg_model.CausalTransformerLM` (causal transformer: RoPE MHSA + SwiGLU FFN)
with the repo BPE tokenizer (``data/tokenizer.json``)
and QA data as **JSONL** by default: ``data.jsonl`` at the repo root (one JSON object per line,
``question`` / ``answer`` or ``prompt`` / ``response``).

**Loss vs inference**

- **Teacher-forced CE** (default): one forward; next-token loss on answer tokens only when using
  ``--train-on prompt_response``. Padding is excluded from loss and attention.

- **Scheduled sampling** (``--scheduled-sampling-p > 0``): previous answer tokens in the prefix are
  sometimes greedy predictions (closer to autoregressive inference; slower).

Requires: torch, tqdm, tokenizers (``pip install -e '.[train]'``).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from pathlib import Path
from typing import Protocol


def _infer_repo_root() -> Path:
    here = Path(__file__).resolve()
    if here.name == "train_scalar_qv_lm.py" and here.parent.name == "scripts":
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


def _data_dir() -> Path:
    raw = os.environ.get("DISTLM_DATA_DIR", "out").strip() or "out"
    p = Path(raw).expanduser()
    return p.resolve() if p.is_absolute() else (ROOT / p).resolve()


_DATA_DIR = _data_dir()
_DEFAULT_OUT = _DATA_DIR / "causal_lm.pt"
_DEFAULT_TOKENIZER = ROOT / "data" / "tokenizer.json"
_DEFAULT_QA_PATH = ROOT / "data.jsonl"


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

from distlm.autoreg_model import CausalTransformerLM


class EncodeFn(Protocol):
    def __call__(self, text: str) -> list[int]: ...


def load_tokenizers_bpe(path: Path) -> tuple[object, int, int]:
    """Return (Tokenizer, vocab_size, pad_id)."""
    try:
        from tokenizers import Tokenizer as HFTokenizer
    except ImportError as e:
        raise SystemExit("Install tokenizers: pip install tokenizers") from e

    tok = HFTokenizer.from_file(str(path))
    vs = int(tok.get_vocab_size())
    pad_id = 1
    with path.open(encoding="utf-8") as f:
        meta = json.load(f)
    for t in meta.get("added_tokens", []):
        c = (t.get("content") or "").upper()
        if "PAD" in c:
            pad_id = int(t["id"])
            break
    for name in ("<pad>", "<PAD>", "[PAD]"):
        tid = tok.token_to_id(name)
        if tid is not None:
            pad_id = int(tid)
            break
    return tok, vs, pad_id


def encode_with_tokenizer(tok: object, text: str) -> list[int]:
    enc = tok.encode(text)
    return list(enc.ids)


def _qa_from_obj(obj: dict) -> tuple[str, str] | None:
    q = (obj.get("question") or obj.get("prompt") or obj.get("q") or "").strip()
    a = (obj.get("answer") or obj.get("response") or obj.get("a") or "").strip()
    if not a:
        return None
    return (q, a)


def _parse_data_json_root(data: object, *, path: Path) -> list[tuple[str, str]]:
    if isinstance(data, list):
        seq = data
    elif isinstance(data, dict):
        seq = None
        for key in ("data", "items", "qa", "examples", "train", "records"):
            v = data.get(key)
            if isinstance(v, list):
                seq = v
                break
        if seq is None:
            raise SystemExit(
                f"{path}: expected a list or dict with a list value (e.g. data/qa/examples); got keys {list(data)[:20]}"
            )
    else:
        raise SystemExit(f"{path}: root must be a list or object")

    rows: list[tuple[str, str]] = []
    for obj in seq:
        if not isinstance(obj, dict):
            continue
        pair = _qa_from_obj(obj)
        if pair is not None:
            rows.append(pair)
    return rows


def _parse_jsonl_lines(raw: str, *, path: Path) -> list[tuple[str, str]]:
    """One JSON object per line (same as .jsonl)."""
    rows: list[tuple[str, str]] = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}: invalid JSON on line {line_no}: {e}") from e
        if not isinstance(obj, dict):
            continue
        pair = _qa_from_obj(obj)
        if pair is not None:
            rows.append(pair)
    return rows


def load_qa_file(path: Path) -> list[tuple[str, str]]:
    """
    Load QA rows: **JSONL** (``.jsonl`` or one object per line) or a single JSON array/object.

    Files named ``*.jsonl`` are read as newline-delimited JSON only (no trial ``json.loads`` on the
    whole file). Other paths try one JSON value first, then fall back on "Extra data" to JSONL.
    """
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return _parse_jsonl_lines(raw, path=path)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        if getattr(e, "msg", "") == "Extra data" or "Extra data" in str(e):
            return _parse_jsonl_lines(raw, path=path)
        raise SystemExit(f"{path}: invalid JSON: {e}") from e

    return _parse_data_json_root(data, path=path)


def load_jsonl_qa(path: Path, limit: int) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(encoding="utf-8") as f:
        for line in tqdm(f, desc="Loading JSONL"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            q = (obj.get("question") or obj.get("prompt") or "").strip()
            a = (obj.get("answer") or obj.get("response") or "").strip()
            if not a:
                continue
            rows.append((q, a))
            if limit and len(rows) >= limit:
                break
    return rows


def load_csv_pr(path: Path, limit: int) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "prompt" not in r.fieldnames or "response" not in r.fieldnames:
            raise SystemExit(f"CSV needs prompt,response columns; got {r.fieldnames!r}")
        bar_kw: dict = {"desc": "Loading CSV"}
        if limit:
            bar_kw["total"] = limit
        for i, row in enumerate(tqdm(r, **bar_kw)):
            rows.append((row.get("prompt") or "", row.get("response") or ""))
            if limit and i + 1 >= limit:
                break
    return rows


def encode_qa(
    encode: EncodeFn,
    question: str,
    answer: str,
    *,
    max_seq_len: int,
    train_on: str,
) -> tuple[list[int], int] | None:
    answer = (answer or "").strip()
    if not answer:
        return None
    if train_on == "response":
        ids = encode(answer)
        ids = ids[:max_seq_len]
        if len(ids) < 2:
            return None
        return ids, 0

    q = (question or "").strip()
    prefix = (q + "\n\n") if q else ""
    prompt_ids = encode(prefix)
    resp_ids = encode(answer)
    if not resp_ids:
        return None
    prompt_len = len(prompt_ids)
    ids = prompt_ids + resp_ids
    if len(ids) > max_seq_len:
        take = max_seq_len - prompt_len
        if take < 1:
            return None
        resp_ids = resp_ids[:take]
        ids = prompt_ids + resp_ids
    if len(ids) < 2 or prompt_len >= len(ids):
        return None
    return ids, prompt_len


class TokenIdsDataset(Dataset):
    def __init__(self, sequences: list[list[int]], prompt_lens: list[int], pad_id: int) -> None:
        self.sequences = sequences
        self.prompt_lens = prompt_lens
        self.pad_id = pad_id

    def __len__(self) -> int:
        return len(self.sequences)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int, int]:
        ids = self.sequences[idx]
        pl = self.prompt_lens[idx]
        t = torch.tensor(ids, dtype=torch.long)
        return t, len(ids), pl


def collate_pad(
    batch: list[tuple[torch.Tensor, int, int]], pad_id: int
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    tensors, lens, pls = zip(*batch)
    padded = pad_sequence(list(tensors), batch_first=True, padding_value=pad_id)
    return padded, torch.tensor(lens, dtype=torch.long), torch.tensor(pls, dtype=torch.long)


def train_val_split_n_val(n: int, val_fraction: float, *, min_val: int) -> int:
    """
    Validation count in ``[1, n - 1]``. For small ``n``, bumps val up to ``min_val`` so val loss is
    not computed on 1-2 sequences (very noisy).
    """
    if n < 2:
        raise ValueError("need at least 2 sequences")
    n_val = max(1, int(round(n * val_fraction)))
    if n > min_val + 1:
        n_val = max(n_val, min(min_val, n - 1))
    return min(n_val, n - 1)


def make_grad_scaler(*, enabled: bool, device: torch.device):
    if not enabled:
        return None
    try:
        return torch.amp.GradScaler(device.type, enabled=True)  # type: ignore[attr-defined]
    except (TypeError, AttributeError):
        return torch.cuda.amp.GradScaler(enabled=True)


def loss_teacher_forced(
    model: nn.Module,
    ids: torch.Tensor,
    lengths: torch.Tensor,
    prompt_lens: torch.Tensor,
    *,
    vocab_size: int,
    pad_id: int,
    train_on: str,
    use_amp: bool,
    device: torch.device,
    label_smoothing: float = 0.0,
) -> torch.Tensor:
    b, t = ids.shape
    attn = torch.arange(t, device=device).unsqueeze(0) < lengths.unsqueeze(1)
    with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
        logits = model(ids, attention_mask=attn)
    shift_logits = logits[:, :-1].contiguous().view(-1, vocab_size)
    pos = torch.arange(t - 1, device=device, dtype=torch.long).unsqueeze(0)
    target_idx = pos + 1
    valid_next = target_idx < lengths.unsqueeze(1)
    if train_on == "prompt_response":
        valid_response = target_idx >= prompt_lens.unsqueeze(1)
        valid_next = valid_next & valid_response
    shift_labels = ids[:, 1:].clone()
    shift_labels[~valid_next] = -100
    shift_labels = shift_labels.contiguous().view(-1)
    loss = F.cross_entropy(
        shift_logits,
        shift_labels,
        ignore_index=-100,
        label_smoothing=label_smoothing,
    )
    if not torch.isfinite(loss):
        loss = logits.sum() * 0.0
    return loss


def loss_scheduled_sampling(
    model: nn.Module,
    ids: torch.Tensor,
    lengths: torch.Tensor,
    prompt_lens: torch.Tensor,
    *,
    train_on: str,
    use_amp: bool,
    device: torch.device,
    sample_p: float,
) -> torch.Tensor:
    """
    One forward per timestep s: predict ``ids[:, s]`` from prefix length ``s``.
    With probability ``sample_p``, previous answer positions in the prefix use greedy predictions
    instead of ground truth (scheduled sampling; closer to autoregressive inference).
    """
    b, t_max = ids.shape
    total = ids.new_zeros((), dtype=torch.float32)
    n_terms = 0
    running = ids.detach().clone()

    for s in range(1, t_max):
        if not (lengths > s).any():
            break
        prefix = running[:, :s].contiguous()
        attn = torch.arange(s, device=device).unsqueeze(0) < lengths.unsqueeze(1)
        attn = attn[:, :s]
        with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
            logits_s = model(prefix, attention_mask=attn)[:, -1, :]

        tgt = ids[:, s]
        if train_on == "prompt_response":
            train_here = s >= prompt_lens
        else:
            train_here = torch.ones(b, dtype=torch.bool, device=device)
        mask = (lengths > s) & train_here

        if mask.any():
            ce = F.cross_entropy(logits_s, tgt, reduction="none")
            total = total + ce[mask].sum()
            n_terms += int(mask.sum().item())

        with torch.no_grad():
            pred = logits_s.argmax(dim=-1)
            next_tok = ids[:, s].clone()
            if sample_p > 0.0:
                u = torch.rand(b, device=device)
                use_pred = (u < sample_p) & (s >= prompt_lens) & (lengths > s)
                next_tok = torch.where(use_pred, pred, ids[:, s])
            running[:, s] = next_tok

    if n_terms == 0:
        return ids.float().sum() * 0.0
    return total / n_terms


def main() -> None:
    p = argparse.ArgumentParser(description="Train CausalTransformerLM (data/tokenizer.json + QA JSONL).")
    p.add_argument(
        "--tokenizer-json",
        type=Path,
        default=_DEFAULT_TOKENIZER,
        help="Hugging Face tokenizers JSON (default: <repo>/data/tokenizer.json).",
    )
    p.add_argument(
        "--data-jsonl",
        "--data-json",
        type=Path,
        default=_DEFAULT_QA_PATH,
        dest="qa_path",
        help="QA file: JSONL (one object per line) or single JSON array/object. Default: <repo>/data.jsonl.",
    )
    p.add_argument("--train-jsonl", type=Path, default=None, help="Alternative data path (same JSONL format as --data-jsonl).")
    p.add_argument("--csv", type=Path, default=None, help="Alternative: CSV prompt,response.")
    p.add_argument("--max-seq-len", type=int, default=512)
    p.add_argument("--train-on", choices=("prompt_response", "response"), default="prompt_response")
    p.add_argument("--limit", type=int, default=0, help="Max rows (0 = all).")
    p.add_argument(
        "--val-fraction",
        type=float,
        default=0.05,
        help="Fraction for val; combined with --min-val-samples so tiny corpora do not use 1-example val.",
    )
    p.add_argument(
        "--min-val-samples",
        type=int,
        default=4,
        help="Minimum val sequences when n is large enough (stabilizes val CE on small JSONLs).",
    )
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--grad-accum", type=int, default=1)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument(
        "--label-smoothing",
        type=float,
        default=0.0,
        help="CE label smoothing (e.g. 0.05) to reduce memorization on tiny data.",
    )
    p.add_argument(
        "--max-grad-norm",
        type=float,
        default=1.0,
        help="Clip gradient L2 norm (0 = disabled).",
    )
    p.add_argument(
        "--early-stopping-patience",
        type=int,
        default=0,
        help="Stop if val CE does not improve for this many epochs (0 = disabled).",
    )
    p.add_argument(
        "--early-stopping-min-delta",
        type=float,
        default=0.0,
        help="Minimum val CE improvement to count as better.",
    )
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--n-layers", type=int, default=8)
    p.add_argument("--n-heads", type=int, default=8)
    p.add_argument("--dim", type=int, default=384)
    p.add_argument("--mlp-hidden", type=int, default=1536)
    p.add_argument(
        "--scheduled-sampling-p",
        type=float,
        default=0.0,
        help="If >0, train with scheduled sampling on answer tokens (slower, closer to inference).",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--amp", action="store_true")
    p.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = p.parse_args()

    if args.dim % args.n_heads != 0:
        raise SystemExit(f"--dim ({args.dim}) must be divisible by --n-heads ({args.n_heads}).")

    tok_path = args.tokenizer_json.expanduser().resolve()
    if not tok_path.is_file():
        raise SystemExit(f"Tokenizer not found: {tok_path}")
    tok, vocab_size, pad_id = load_tokenizers_bpe(tok_path)

    def encode(text: str) -> list[int]:
        return encode_with_tokenizer(tok, text)

    if args.train_jsonl is not None:
        rows = load_jsonl_qa(args.train_jsonl.expanduser().resolve(), args.limit)
    elif args.csv is not None:
        rows = load_csv_pr(args.csv.expanduser().resolve(), args.limit)
    else:
        qa = args.qa_path.expanduser().resolve()
        if not qa.is_file():
            raise SystemExit(
                f"QA data not found: {qa}\n"
                "Use --data-jsonl PATH (default is <repo>/data.jsonl) or --train-jsonl / --csv."
            )
        rows = load_qa_file(qa)
        if args.limit:
            rows = rows[: args.limit]

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    sequences: list[list[int]] = []
    prompt_lens: list[int] = []
    for question, answer in tqdm(rows, desc="Tokenizing"):
        enc = encode_qa(
            encode,
            question,
            answer,
            max_seq_len=args.max_seq_len,
            train_on=args.train_on,
        )
        if enc is None:
            continue
        ids, pl = enc
        if max(ids) >= vocab_size:
            continue
        sequences.append(ids)
        prompt_lens.append(pl)

    if len(sequences) < 2:
        raise SystemExit("Not enough sequences after tokenization (need >= 2).")

    combined = list(zip(sequences, prompt_lens))
    random.shuffle(combined)
    sequences = [c[0] for c in combined]
    prompt_lens = [c[1] for c in combined]

    n_val = train_val_split_n_val(len(sequences), args.val_fraction, min_val=max(1, args.min_val_samples))
    val_seq = sequences[:n_val]
    val_pl = prompt_lens[:n_val]
    train_seq = sequences[n_val:]
    train_pl = prompt_lens[n_val:]
    if not train_seq:
        train_seq, train_pl = sequences, prompt_lens
        val_seq, val_pl = sequences[:1], prompt_lens[:1]

    print(
        f"Data: {len(train_seq)} train / {len(val_seq)} val sequences (of {len(sequences)}). "
        f"Tiny corpora overfit easily; prefer smaller --dim/--n-layers, higher --dropout, --label-smoothing 0.05.",
        flush=True,
    )
    if len(train_seq) < 200:
        print(
            "Warning: very few training sequences - train loss will drop fast; val CE is only meaningful with enough val data.",
            flush=True,
        )

    train_ds = TokenIdsDataset(train_seq, train_pl, pad_id)
    val_ds = TokenIdsDataset(val_seq, val_pl, pad_id)

    def collate(batch: list[tuple[torch.Tensor, int, int]]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return collate_pad(batch, pad_id)

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate,
        num_workers=0,
        pin_memory=not args.cpu,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collate,
        num_workers=0,
        pin_memory=not args.cpu,
    )

    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    model = CausalTransformerLM(
        vocab_size=vocab_size,
        dim=args.dim,
        n_heads=args.n_heads,
        n_layers=args.n_layers,
        mlp_hidden=args.mlp_hidden,
        dropout=args.dropout,
    ).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    use_amp = bool(args.amp and device.type == "cuda")
    scaler = make_grad_scaler(enabled=use_amp, device=device)

    def compute_loss(
        ids: torch.Tensor,
        lengths: torch.Tensor,
        pls: torch.Tensor,
        *,
        training: bool,
    ) -> torch.Tensor:
        ids = ids.to(device)
        lengths = lengths.to(device)
        pls = pls.to(device)
        if training and args.scheduled_sampling_p > 0.0:
            return loss_scheduled_sampling(
                model,
                ids,
                lengths,
                pls,
                train_on=args.train_on,
                use_amp=use_amp,
                device=device,
                sample_p=args.scheduled_sampling_p,
            )
        return loss_teacher_forced(
            model,
            ids,
            lengths,
            pls,
            vocab_size=vocab_size,
            pad_id=pad_id,
            train_on=args.train_on,
            use_amp=use_amp,
            device=device,
            label_smoothing=args.label_smoothing,
        )

    def optimizer_step() -> None:
        if use_amp and scaler is not None:
            scaler.unscale_(opt)
        if args.max_grad_norm and args.max_grad_norm > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
        if use_amp and scaler is not None:
            scaler.step(opt)
            scaler.update()
        else:
            opt.step()

    best_val = float("inf")
    out_path = args.out.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    accum = 0
    epochs_no_improve = 0
    for epoch in range(args.epochs):
        model.train()
        total_loss = 0.0
        n_batches = 0
        opt.zero_grad(set_to_none=True)
        pbar = tqdm(train_loader, desc=f"train {epoch+1}/{args.epochs}")
        for ids, lengths, pls in pbar:
            loss = compute_loss(ids, lengths, pls, training=True) / args.grad_accum
            if use_amp and scaler is not None:
                scaler.scale(loss).backward()
            else:
                loss.backward()
            accum += 1
            if accum % args.grad_accum == 0:
                optimizer_step()
                opt.zero_grad(set_to_none=True)
            total_loss += float(loss.detach()) * args.grad_accum
            n_batches += 1
            pbar.set_postfix(loss=f"{total_loss / max(1, n_batches):.4f}")
        if accum % args.grad_accum != 0:
            optimizer_step()
            opt.zero_grad(set_to_none=True)

        model.eval()
        val_loss = 0.0
        val_n = 0
        with torch.no_grad():
            for ids, lengths, pls in val_loader:
                loss = compute_loss(ids, lengths, pls, training=False)
                val_loss += float(loss)
                val_n += 1
        val_ce = val_loss / max(1, val_n)
        print(f"epoch {epoch+1} val_ce={val_ce:.4f}", flush=True)
        improved = val_ce < best_val - args.early_stopping_min_delta
        if improved:
            best_val = val_ce
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1

        if improved:
            payload = {
                "model_state": model.state_dict(),
                "vocab_size": vocab_size,
                "pad_token_id": pad_id,
                "tokenizer_json": str(tok_path),
                "config": {
                    "dim": args.dim,
                    "n_heads": args.n_heads,
                    "mlp_hidden": args.mlp_hidden,
                    "n_layers": args.n_layers,
                    "dropout": args.dropout,
                    "max_seq_len": args.max_seq_len,
                    "train_on": args.train_on,
                    "scheduled_sampling_p": args.scheduled_sampling_p,
                    "architecture": "causal_transformer_lm",
                },
                "best_val_ce": best_val,
                "epochs_run": epoch + 1,
            }
            torch.save(payload, out_path)
            print(f"Saved checkpoint to {out_path}", flush=True)

        if (
            not improved
            and args.early_stopping_patience > 0
            and epochs_no_improve >= args.early_stopping_patience
        ):
            print(f"Early stopping (no val improvement for {args.early_stopping_patience} epochs).", flush=True)
            break

    print(f"Done. best_val_ce={best_val:.4f}", flush=True)


if __name__ == "__main__":
    main()
