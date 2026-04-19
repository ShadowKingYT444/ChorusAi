#!/usr/bin/env python3
"""
Train ``distlm.autoreg_model.AutoregPredictor`` on Dolly CSV with **Qwen2** encoder + frozen Qwen ``lm_head``.

Encoder: ``AutoModel`` from ``--qwen-model`` (default Qwen2-0.5B, hidden 896). **By default the full
encoder loads (~494M trainable params)**. For ~half the depth use ``--qwen-encoder-halve`` or
``--qwen-encoder-layers 12`` (first N blocks; weights copied from HF). Optional
``--encoder-attention-dropout`` sets Qwen ``attention_dropout`` when rebuilding (``-1`` = HF default).

The predictor's ``lm_head`` is Qwen's frozen output head (896→151936); train ``proj_to_qwen`` + blocks.
``--dropout`` still controls dropout inside ``AutoregPredictor`` only.

- **Default** (``--train-on prompt_response``): ``prompt + "\\n\\n" + response`` (optional BOS per tokenizer).
  CE only on **response** next-token targets.

**Interrupt:** Ctrl+C saves current weights to ``--out`` with ``"interrupted": true``.
**Resume:** ``--resume /path/to/autoreg_checkpoint.pt`` (loads head + optional encoder_state).

Requires: torch, transformers, tqdm.
"""

from __future__ import annotations

import argparse
import copy
import csv
import math
import os
import random
import sys
from pathlib import Path


def _infer_repo_root() -> Path:
    here = Path(__file__).resolve()
    if here.name == "train_autoreg_qwen.py" and here.parent.name == "scripts":
        return here.parents[1]
    if here.name == "train_qwen.py":
        return here.parent
    if here.parent.name == "scripts":
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
_DEFAULT_CSV = _DATA_DIR / "dolly_prompt_response.csv"
_DEFAULT_OUT = _DATA_DIR / "autoreg_qwen_checkpoint.pt"
_CSV_NAME = "dolly_prompt_response.csv"

_DEFAULT_QWEN_MODEL = "Qwen/Qwen2-0.5B"
_QWEN_HIDDEN = 896
_QWEN_VOCAB = 151_936


def _search_first_existing(search_names: tuple[str, ...]) -> Path | None:
    bases = [ROOT, Path.cwd(), ROOT / "out", Path.cwd() / "out", _DATA_DIR]
    seen: set[str] = set()
    for base in bases:
        try:
            b = base.resolve()
        except OSError:
            continue
        key = str(b)
        if key in seen:
            continue
        seen.add(key)
        for name in search_names:
            cand = b / name
            if cand.is_file():
                return cand.resolve()
    return None


def _resolve_csv_arg(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.is_file():
        return path
    found = _search_first_existing((_CSV_NAME,))
    if found is not None:
        print(f"Using CSV: {found}", flush=True)
        return found
    return path


def _require_file(path: Path, what: str) -> None:
    if path.is_file():
        return
    print(
        f"Error: {what} not found:\n  {path}\n"
        f"Repo root: {ROOT}\n"
        f"Data dir (DISTLM_DATA_DIR): {_DATA_DIR}\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


import torch
import torch.nn.functional as F
from torch.optim.lr_scheduler import (
    CosineAnnealingLR,
    LinearLR,
    OneCycleLR,
    ReduceLROnPlateau,
    SequentialLR,
)
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import DataLoader, Dataset

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **kwargs):  # type: ignore[misc]
        return x

from distlm.autoreg_model import AutoregPredictor, load_qwen_lm_head


def build_qwen_encoder(
    model_name: str,
    *,
    num_hidden_layers: int = 0,
    encoder_halve: bool = False,
    attention_dropout: float | None = None,
    trust_remote_code: bool = True,
):
    """
    Load Qwen2 ``AutoModel``.

    - ``num_hidden_layers > 0``: keep the first N transformer blocks (weights copied from HF).
    - ``encoder_halve`` and ``num_hidden_layers <= 0``: use ``num_hidden_layers = full // 2``
      (e.g. 24 → 12 for Qwen2-0.5B).
    - Otherwise: full HF depth (``from_pretrained``), unless attention dropout is overridden
      (then rebuild from config + copy weights).
    - ``attention_dropout`` (>= 0): set on config when the attribute exists.
    """
    from transformers import AutoConfig, AutoModel

    cfg = AutoConfig.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    full_n = int(cfg.num_hidden_layers)
    if num_hidden_layers > 0:
        want_n = min(int(num_hidden_layers), full_n)
    elif encoder_halve:
        want_n = max(1, full_n // 2)
    else:
        want_n = full_n
    do_dropout = attention_dropout is not None and float(attention_dropout) >= 0.0
    custom = (want_n != full_n) or do_dropout

    if not custom:
        return AutoModel.from_pretrained(model_name, trust_remote_code=trust_remote_code)

    cfg_new = copy.deepcopy(cfg)
    cfg_new.num_hidden_layers = want_n
    if do_dropout and hasattr(cfg_new, "attention_dropout"):
        cfg_new.attention_dropout = float(attention_dropout)
    elif do_dropout:
        print(
            "Warning: config has no attention_dropout; encoder-attention-dropout ignored.",
            flush=True,
        )

    encoder = AutoModel.from_config(cfg_new, trust_remote_code=trust_remote_code)
    ref = AutoModel.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    tgt_sd = encoder.state_dict()
    ref_sd = ref.state_dict()
    not_copied: list[str] = []
    for k in tgt_sd:
        if k in ref_sd and ref_sd[k].shape == tgt_sd[k].shape:
            tgt_sd[k].copy_(ref_sd[k])
        else:
            not_copied.append(k)
    encoder.load_state_dict(tgt_sd)
    del ref
    ad = getattr(cfg_new, "attention_dropout", "n/a")
    print(
        f"Built Qwen encoder: num_hidden_layers={want_n} (HF={full_n}), "
        f"attention_dropout={ad}; "
        f"weight tensors not copied from HF (missing/shape mismatch): {len(not_copied)}",
        flush=True,
    )
    if not_copied:
        for k in not_copied[:12]:
            print(f"  (random init) {k}", flush=True)
        if len(not_copied) > 12:
            print(f"  ... and {len(not_copied) - 12} more", flush=True)
    return encoder


def _make_lr_scheduler(opt: torch.optim.Optimizer, args: argparse.Namespace, *, steps_per_epoch: int):
    epochs = max(1, args.epochs)
    if args.lr_scheduler == "warmup_cosine":
        we = min(max(0, args.warmup_epochs), epochs)
        if we <= 0:
            return CosineAnnealingLR(opt, T_max=epochs, eta_min=args.lr_min), False
        if epochs <= we:
            return LinearLR(opt, start_factor=0.01, end_factor=1.0, total_iters=epochs), False
        warmup = LinearLR(opt, start_factor=0.01, end_factor=1.0, total_iters=we)
        cosine = CosineAnnealingLR(opt, T_max=epochs - we, eta_min=args.lr_min)
        return SequentialLR(opt, [warmup, cosine], milestones=[we]), False
    if args.lr_scheduler == "plateau":
        return ReduceLROnPlateau(
            opt,
            mode="min",
            factor=args.lr_factor,
            patience=args.lr_patience,
            min_lr=args.lr_min,
            threshold=args.lr_plateau_threshold,
        ), False
    if args.lr_scheduler == "cosine":
        return CosineAnnealingLR(opt, T_max=epochs, eta_min=args.lr_min), False
    if args.lr_scheduler == "onecycle":
        total_steps = max(1, steps_per_epoch * epochs)
        max_lr = [args.lr * args.encoder_lr_mult, args.lr] if len(opt.param_groups) == 2 else args.lr
        return OneCycleLR(
            opt,
            max_lr=max_lr,
            total_steps=total_steps,
            pct_start=args.onecycle_pct_start,
            div_factor=25.0,
            final_div_factor=1e4,
        ), True
    return None, False


def load_rows(csv_path: Path, limit: int) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
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


def _encode_sequence(
    tokenizer,
    prompt: str,
    response: str,
    *,
    max_seq_len: int,
    train_on: str,
    prepend_bos: bool,
) -> tuple[list[int], int] | None:
    response = (response or "").strip()
    if not response:
        return None

    bos_id = tokenizer.bos_token_id if prepend_bos else None

    if train_on == "response":
        ids = tokenizer.encode(response, add_special_tokens=False)
        if bos_id is not None:
            ids = [bos_id] + ids
        ids = ids[:max_seq_len]
        if len(ids) < 2:
            return None
        return ids, 0

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
        resp_ids = resp_ids[:take]
        ids = prompt_ids + resp_ids
    if len(ids) < 2 or prompt_len >= len(ids):
        return None
    return ids, prompt_len


def _checkpoint_payload(
    args: argparse.Namespace,
    *,
    pad_id: int,
    encoder_dim: int,
    head_sd: dict,
    encoder_sd: dict | None,
    freeze_encoder: bool,
    best_val_ce: float,
    stopped_early: bool,
    epochs_run: int,
    interrupted: bool,
) -> dict:
    n_enc_save = sum(p.numel() for p in encoder_sd.values()) if encoder_sd is not None else 0
    n_head_save = sum(p.numel() for p in head_sd.values())
    return {
        "model_state": head_sd,
        "encoder_state": encoder_sd if not freeze_encoder else None,
        "freeze_encoder": freeze_encoder,
        "vocab_size": _QWEN_VOCAB,
        "qwen_model": args.qwen_model,
        "encoder_kind": "causal_qwen2",
        "qwen_encoder_num_hidden_layers": getattr(
            args, "_encoder_num_hidden_layers_effective", None
        ),
        "qwen_encoder_attention_dropout": getattr(
            args, "_encoder_attention_dropout_effective", None
        ),
        "max_seq_len": args.max_seq_len,
        "pad_token_id": pad_id,
        "train_on": args.train_on,
        "dim": args.dim,
        "encoder_dim": encoder_dim,
        "param_count": n_head_save,
        "head_param_count": n_head_save,
        "encoder_param_count": n_enc_save,
        "total_trainable_param_count": n_enc_save + n_head_save,
        "best_val_ce": best_val_ce,
        "stopped_early": stopped_early,
        "epochs_run": epochs_run,
        "interrupted": interrupted,
        "config": {
            "dim": args.dim,
            "encoder_dim": encoder_dim,
            "n_layers": args.n_layers,
            "n_heads": args.n_heads,
            "mlp_hidden": args.mlp_hidden,
            "dropout": args.dropout,
            "residual_scale": args.residual_scale,
            "vocab_size": _QWEN_VOCAB,
            "architecture": "transformer_mhsa_swiglu_qwen_head",
            "encoder_kind": "causal_qwen2",
            "qwen_model": args.qwen_model,
            "qwen_encoder_num_hidden_layers": getattr(
                args, "_encoder_num_hidden_layers_effective", None
            ),
            "qwen_encoder_attention_dropout": getattr(
                args, "_encoder_attention_dropout_effective", None
            ),
            "max_seq_len": args.max_seq_len,
            "pad_token_id": pad_id,
            "train_on": args.train_on,
            "freeze_encoder": freeze_encoder,
            "early_stopping_patience": args.early_stopping_patience,
            "early_stopping_min_delta": args.early_stopping_min_delta,
        },
    }


def _apply_resume_checkpoint(
    ckpt_path: Path,
    *,
    device: torch.device,
    model: AutoregPredictor,
    encoder,
    args: argparse.Namespace,
) -> tuple[float, int]:
    try:
        ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    except TypeError:
        ckpt = torch.load(ckpt_path, map_location=device)
    if "model_state" not in ckpt:
        raise SystemExit(f"Checkpoint missing 'model_state': {ckpt_path}")
    ckpt_vs = int(ckpt.get("vocab_size", -1))
    if ckpt_vs != _QWEN_VOCAB:
        raise SystemExit(
            f"Resume vocab mismatch: checkpoint vocab_size={ckpt_vs}, expected {_QWEN_VOCAB}."
        )
    cfg = ckpt.get("config") or {}
    if cfg.get("dim") is not None and int(cfg["dim"]) != args.dim:
        raise SystemExit(f"Resume dim mismatch: checkpoint dim={cfg['dim']}, --dim={args.dim}")
    if cfg.get("n_layers") is not None and int(cfg["n_layers"]) != args.n_layers:
        raise SystemExit(f"Resume n_layers mismatch: checkpoint n_layers={cfg['n_layers']}")
    ck_enc = cfg.get("qwen_encoder_num_hidden_layers")
    if ck_enc is not None and int(ck_enc) != int(encoder.config.num_hidden_layers):
        raise SystemExit(
            f"Resume Qwen encoder depth mismatch: checkpoint has num_hidden_layers={ck_enc}, "
            f"current encoder has {encoder.config.num_hidden_layers}. Match --qwen-encoder-layers."
        )
    saved_q = ckpt.get("qwen_model")
    if saved_q and saved_q != args.qwen_model:
        print(f"Warning: checkpoint qwen_model={saved_q!r} != --qwen-model={args.qwen_model!r}", flush=True)

    model.load_state_dict(ckpt["model_state"], strict=False)
    enc_sd = ckpt.get("encoder_state")
    if enc_sd is not None:
        encoder.load_state_dict(enc_sd, strict=True)
        print(f"Loaded encoder_state from checkpoint ({len(enc_sd)} tensors).", flush=True)
    else:
        print("No encoder_state in checkpoint; Qwen encoder unchanged from HF init.", flush=True)

    prev_epochs = int(ckpt.get("epochs_run", 0) or 0)
    raw_best = ckpt.get("best_val_ce")
    if args.resume_reset_best or raw_best is None:
        initial_best = float("inf")
    else:
        try:
            initial_best = float(raw_best)
        except (TypeError, ValueError):
            initial_best = float("inf")
        if not math.isfinite(initial_best):
            initial_best = float("inf")
    print(f"Resumed from {ckpt_path} (epochs_run={prev_epochs}, best_val_ce={initial_best})", flush=True)
    return initial_best, prev_epochs


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=_DEFAULT_CSV)
    p.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--resume", type=Path, default=None)
    p.add_argument("--resume-reset-best", action="store_true")
    p.add_argument(
        "--qwen-model",
        default=_DEFAULT_QWEN_MODEL,
        help="Qwen2 HF id (default Qwen/Qwen2-0.5B). Must match at inference.",
    )
    p.add_argument(
        "--qwen-encoder-layers",
        type=int,
        default=0,
        help=(
            "0 = do not set an explicit depth (use --qwen-encoder-halve or full model). "
            "N>0: keep only the first N transformer blocks (weights copied). "
            "For Qwen2-0.5B (24), use 12 or --qwen-encoder-halve."
        ),
    )
    p.add_argument(
        "--qwen-encoder-halve",
        action="store_true",
        help=(
            "Use half the HF transformer depth (first num_hidden_layers//2 blocks, weights copied). "
            "Ignored if --qwen-encoder-layers > 0. Default is OFF - without this flag you get the "
            "full ~494M-param encoder."
        ),
    )
    p.add_argument(
        "--encoder-attention-dropout",
        type=float,
        default=-1.0,
        help=(
            "If >= 0, set Qwen config attention_dropout when building the encoder (custom depth or "
            "dropout forces a config rebuild + weight copy). -1 = HF default."
        ),
    )
    p.add_argument(
        "--dim",
        type=int,
        default=512,
        help="Head width; bridge proj_to_qwen maps dim→896.",
    )
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--encoder-lr-mult", type=float, default=0.05)
    p.add_argument("--freeze-encoder", action="store_true")
    p.add_argument("--n-layers", type=int, default=8)
    p.add_argument("--n-heads", type=int, default=8, help="Must divide --dim evenly.")
    p.add_argument("--mlp-hidden", type=int, default=2048)
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--residual-scale", type=float, default=1.0, dest="residual_scale")
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--grad-clip", type=float, default=1.0)
    p.add_argument(
        "--lr-scheduler",
        choices=("warmup_cosine", "plateau", "cosine", "onecycle", "none"),
        default="warmup_cosine",
    )
    p.add_argument("--warmup-epochs", type=int, default=2)
    p.add_argument("--lr-factor", type=float, default=0.7)
    p.add_argument("--lr-patience", type=int, default=3)
    p.add_argument("--lr-plateau-threshold", type=float, default=1e-3)
    p.add_argument("--onecycle-pct-start", type=float, default=0.3, dest="onecycle_pct_start")
    p.add_argument("--lr-min", type=float, default=1e-6)
    p.add_argument("--limit-rows", type=int, default=0)
    p.add_argument("--max-steps-per-example", type=int, default=0)
    p.add_argument("--max-pairs", type=int, default=0)
    p.add_argument("--val-fraction", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--train-on",
        choices=("prompt_response", "response"),
        default="prompt_response",
    )
    p.add_argument("--max-seq-len", type=int, default=512)
    p.add_argument("--prepend-bos", action="store_true", help="Prepend tokenizer BOS id when set.")
    p.add_argument("--early-stopping-patience", type=int, default=0, dest="early_stopping_patience")
    p.add_argument("--early-stopping-min-delta", type=float, default=0.0, dest="early_stopping_min_delta")
    args = p.parse_args()

    args.out = args.out.expanduser().resolve()
    args.csv = _resolve_csv_arg(args.csv)
    if args.resume is not None:
        args.resume = args.resume.expanduser().resolve()
        _require_file(args.resume, "Resume checkpoint")
    _require_file(args.csv, "CSV dataset")

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    from transformers import AutoTokenizer

    print(f"Loading Qwen tokenizer + encoder: {args.qwen_model}", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(args.qwen_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    enc_do = args.encoder_attention_dropout if args.encoder_attention_dropout >= 0.0 else None
    encoder = build_qwen_encoder(
        args.qwen_model,
        num_hidden_layers=args.qwen_encoder_layers,
        encoder_halve=args.qwen_encoder_halve,
        attention_dropout=enc_do,
        trust_remote_code=True,
    )
    print(
        f"Qwen encoder depth: num_hidden_layers={encoder.config.num_hidden_layers} "
        f"(Qwen2-0.5B full = 24). If this is 24, you did not pass --qwen-encoder-halve or "
        f"--qwen-encoder-layers 12 - encoder stays full size (~494M params).",
        flush=True,
    )
    args._encoder_num_hidden_layers_effective = int(encoder.config.num_hidden_layers)
    args._encoder_attention_dropout_effective = (
        float(getattr(encoder.config, "attention_dropout", -1.0))
        if hasattr(encoder.config, "attention_dropout")
        else None
    )

    pad_id = int(tokenizer.pad_token_id or tokenizer.eos_token_id or 0)
    encoder_dim = int(encoder.config.hidden_size)
    if encoder_dim != _QWEN_HIDDEN:
        print(
            f"Warning: encoder hidden_size={encoder_dim} != {_QWEN_HIDDEN} (0.5B default). "
            "Adjust QWEN_HIDDEN / load_qwen_lm_head if using a different size.",
            flush=True,
        )

    if args.freeze_encoder:
        encoder.eval()
        for p in encoder.parameters():
            p.requires_grad = False
    else:
        for p in encoder.parameters():
            p.requires_grad = True

    print("Loading frozen Qwen lm_head (float32) …", flush=True)
    qwen_head = load_qwen_lm_head(args.qwen_model, dtype=torch.float32)

    rows = load_rows(args.csv, args.limit_rows)
    print(f"Loaded {len(rows)} (prompt,response) rows", flush=True)
    print(
        f"train_on={args.train_on}  max_seq_len={args.max_seq_len}  pad_token_id={pad_id}  "
        f"vocab_size={_QWEN_VOCAB}",
        flush=True,
    )

    sequences: list[tuple[list[int], int]] = []
    for prompt, response in tqdm(rows, desc="Tokenize"):
        out = _encode_sequence(
            tokenizer,
            prompt,
            response,
            max_seq_len=args.max_seq_len,
            train_on=args.train_on,
            prepend_bos=args.prepend_bos,
        )
        if out is None:
            continue
        ids, prompt_len = out
        if args.max_steps_per_example:
            if args.train_on == "response":
                ids = ids[: args.max_steps_per_example]
            else:
                tail = ids[prompt_len :][: args.max_steps_per_example]
                ids = ids[:prompt_len] + tail
        if len(ids) < 2 or (args.train_on == "prompt_response" and prompt_len >= len(ids)):
            continue
        sequences.append((ids, prompt_len))
        if args.max_pairs and len(sequences) >= args.max_pairs:
            break

    if not sequences:
        raise SystemExit("No sequences after tokenization.")

    random.shuffle(sequences)
    n = len(sequences)
    n_val = max(1, int(round(n * args.val_fraction)))
    n_train = n - n_val
    if n_train < 1:
        raise SystemExit("Need at least 2 sequences for train/val split.")
    train_seq = sequences[:n_train]
    val_seq = sequences[n_train:]

    class _SeqDataset(Dataset):
        def __init__(self, seqs: list[tuple[list[int], int]]) -> None:
            self.seqs = seqs

        def __len__(self) -> int:
            return len(self.seqs)

        def __getitem__(self, i: int) -> tuple[torch.Tensor, int, int]:
            ids, prompt_len = self.seqs[i]
            t = torch.tensor(ids, dtype=torch.long)
            return t, int(t.numel()), int(prompt_len)

    def _collate(
        batch: list[tuple[torch.Tensor, int, int]],
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        tensors = [x[0] for x in batch]
        lengths = torch.tensor([x[1] for x in batch], dtype=torch.long)
        prompt_lens = torch.tensor([x[2] for x in batch], dtype=torch.long)
        padded = pad_sequence(tensors, batch_first=True, padding_value=pad_id)
        return padded, lengths, prompt_lens

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    encoder = encoder.to(device)
    qwen_head = qwen_head.to(device)

    train_loader = DataLoader(
        _SeqDataset(train_seq),
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=_collate,
        num_workers=0,
    )
    val_loader = DataLoader(
        _SeqDataset(val_seq),
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=_collate,
        num_workers=0,
    )

    model = AutoregPredictor(
        vocab_size=_QWEN_VOCAB,
        dim=args.dim,
        encoder_dim=encoder_dim if encoder_dim != args.dim else None,
        n_heads=args.n_heads,
        n_layers=args.n_layers,
        mlp_hidden=args.mlp_hidden,
        dropout=args.dropout,
        residual_scale=args.residual_scale,
        max_seq_len=args.max_seq_len,
        qwen_lm_head=qwen_head,
    ).to(device)

    if args.resume is not None:
        best_val, _ = _apply_resume_checkpoint(
            args.resume, device=device, model=model, encoder=encoder, args=args
        )
    else:
        best_val = float("inf")

    bd = model.parameter_count_breakdown()
    n_enc_tr = sum(p.numel() for p in encoder.parameters() if p.requires_grad)
    n_head_tr = bd["total_trainable"]
    enc_mode = "frozen" if args.freeze_encoder else f"trainable ({n_enc_tr:,} params)"
    print(
        f"\nQwen2 encoder ({args.qwen_model}) [{enc_mode}] on {device}\n"
        f"  head trainable params : {n_head_tr:,}\n"
        f"  lm_head (frozen)      : {bd.get('lm_head_frozen', 0):,}\n"
        f"  proj_to_qwen bridge   : {bd.get('proj_to_qwen', 0):,}\n"
        f"  total trainable       : {n_enc_tr + n_head_tr:,}\n",
        flush=True,
    )

    if args.freeze_encoder:
        opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    else:
        opt = torch.optim.AdamW(
            [
                {"params": encoder.parameters(), "lr": args.lr * args.encoder_lr_mult},
                {"params": model.parameters(), "lr": args.lr},
            ],
            weight_decay=args.weight_decay,
        )

    if args.lr_scheduler == "onecycle":
        for pg in opt.param_groups:
            pg["lr"] = pg["lr"] / 25.0

    sched, sched_per_step = _make_lr_scheduler(opt, args, steps_per_epoch=len(train_loader))
    use_amp = device.type == "cuda"
    vs = _QWEN_VOCAB

    def lm_loss(
        ids: torch.Tensor,
        lengths: torch.Tensor,
        prompt_lens: torch.Tensor,
    ) -> torch.Tensor:
        ids = ids.to(device)
        lengths = lengths.to(device)
        prompt_lens = prompt_lens.to(device)
        b, t = ids.shape
        attn_mask = (torch.arange(t, device=device).unsqueeze(0) < lengths.unsqueeze(1)).long()

        if args.freeze_encoder:
            with torch.no_grad():
                with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=use_amp):
                    h = encoder(input_ids=ids, attention_mask=attn_mask).last_hidden_state.float()
        else:
            with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=use_amp):
                h = encoder(input_ids=ids, attention_mask=attn_mask).last_hidden_state.float()

        logits = model(h)

        shift_logits = logits[:, :-1].contiguous().view(-1, vs)
        pos = torch.arange(t - 1, device=device, dtype=torch.long).unsqueeze(0)
        target_idx = pos + 1
        valid_next = target_idx < lengths.unsqueeze(1)
        valid_response = target_idx >= prompt_lens.unsqueeze(1)
        shift_labels = ids[:, 1:].clone()
        shift_labels[~(valid_next & valid_response)] = -100
        shift_labels = shift_labels.contiguous().view(-1)

        loss = F.cross_entropy(shift_logits, shift_labels, ignore_index=-100, reduction="mean")
        if not torch.isfinite(loss):
            loss = logits.sum() * 0.0
        return loss

    best_head_sd: dict | None = None
    best_encoder_sd: dict | None = None
    epochs_no_improve = 0
    stopped_early = False
    last_epoch = -1
    epochs_completed = 0

    try:
        for epoch in range(args.epochs):
            last_epoch = epoch
            model.train()
            if not args.freeze_encoder:
                encoder.train()
            total, n_batches = 0.0, 0
            train_pbar = tqdm(train_loader, desc=f"train {epoch + 1}/{args.epochs}")
            for batch in train_pbar:
                loss = lm_loss(batch[0], batch[1], batch[2])
                opt.zero_grad()
                loss.backward()
                if args.grad_clip > 0:
                    clip_params = list(model.parameters())
                    if not args.freeze_encoder:
                        clip_params += list(encoder.parameters())
                    torch.nn.utils.clip_grad_norm_(clip_params, args.grad_clip)
                opt.step()
                if sched_per_step and sched is not None:
                    sched.step()
                li = loss.item()
                total += li
                n_batches += 1
                lr_post = f"{opt.param_groups[0]['lr']:.2e}"
                if len(opt.param_groups) > 1:
                    lr_post += f"/{opt.param_groups[1]['lr']:.2e}"
                train_pbar.set_postfix(
                    loss=f"{li:.6f}", avg=f"{total / max(n_batches, 1):.6f}", lr=lr_post
                )
            train_loss = total / max(n_batches, 1)

            model.eval()
            encoder.eval()
            vtotal, vn = 0.0, 0
            with torch.no_grad():
                val_pbar = tqdm(val_loader, desc=f"val {epoch + 1}/{args.epochs}", leave=False)
                for batch in val_pbar:
                    loss = lm_loss(batch[0], batch[1], batch[2])
                    li = loss.item()
                    vtotal += li
                    vn += 1
                    val_pbar.set_postfix(loss=f"{li:.6f}", avg=f"{vtotal / max(vn, 1):.6f}")
            val_loss = vtotal / max(vn, 1)

            lr_now = opt.param_groups[0]["lr"]
            if sched is not None and not sched_per_step:
                if isinstance(sched, ReduceLROnPlateau):
                    sched.step(val_loss)
                else:
                    sched.step()
                lr_now = opt.param_groups[0]["lr"]

            print(
                f"epoch {epoch + 1}: train_ce={train_loss:.6f}  val_ce={val_loss:.6f}  lr={lr_now:.2e}"
            )

            improved = val_loss < best_val - args.early_stopping_min_delta
            if improved:
                best_val = val_loss
                best_head_sd = {k: v.detach().cpu() for k, v in model.state_dict().items()}
                if not args.freeze_encoder:
                    best_encoder_sd = {k: v.detach().cpu() for k, v in encoder.state_dict().items()}
                epochs_no_improve = 0
            else:
                epochs_no_improve += 1

            epochs_completed = epoch + 1

            if args.early_stopping_patience > 0 and epochs_no_improve >= args.early_stopping_patience:
                print(
                    f"Early stopping: no improvement for {args.early_stopping_patience} epochs. "
                    f"Best val_ce={best_val:.6f}",
                    flush=True,
                )
                stopped_early = True
                break

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt - saving current weights.", flush=True)
        cur_head = {k: v.detach().cpu() for k, v in model.state_dict().items()}
        cur_enc = None if args.freeze_encoder else {k: v.detach().cpu() for k, v in encoder.state_dict().items()}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload = _checkpoint_payload(
            args,
            pad_id=pad_id,
            encoder_dim=encoder_dim,
            head_sd=cur_head,
            encoder_sd=cur_enc,
            freeze_encoder=args.freeze_encoder,
            best_val_ce=best_val if best_val < float("inf") else float("nan"),
            stopped_early=True,
            epochs_run=max(0, epochs_completed),
            interrupted=True,
        )
        torch.save(payload, args.out)
        print(f"Saved {args.out} (interrupted, epochs={epochs_completed})", flush=True)
        raise SystemExit(130) from None

    if best_head_sd is None:
        best_head_sd = {k: v.detach().cpu() for k, v in model.state_dict().items()}
        if not args.freeze_encoder:
            best_encoder_sd = {k: v.detach().cpu() for k, v in encoder.state_dict().items()}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = _checkpoint_payload(
        args,
        pad_id=pad_id,
        encoder_dim=encoder_dim,
        head_sd=best_head_sd,
        encoder_sd=best_encoder_sd,
        freeze_encoder=args.freeze_encoder,
        best_val_ce=best_val,
        stopped_early=stopped_early,
        epochs_run=last_epoch + 1,
        interrupted=False,
    )
    torch.save(payload, args.out)
    print(f"Saved {args.out} (best val_ce={best_val:.6f}, epochs_run={last_epoch + 1})")


if __name__ == "__main__":
    main()
