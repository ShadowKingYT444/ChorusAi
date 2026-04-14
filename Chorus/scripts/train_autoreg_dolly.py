#!/usr/bin/env python3
"""
Train the autoregressive model (chorus.autoreg_model) on Dolly prompt/response pairs.

- Embeds each prefix with MiniLM: either **pooled sentence** (``--embed-mode sentence``) or
  **last transformer token** hidden state (``--embed-mode token_last``), 384-D for ``AutoregPredictor``.
- Loss: cross-entropy over BPE vocab (head: row-softmax pool → 64→vocab).

Requires: torch, sentence-transformers, tokenizers.

Defaults use repo-root paths. If you run a copied `train.py` from `/workspace`, set
`CHORUS_ROOT` or `CHORUS_TOKENIZER` (see error message when files are missing).

  CHORUS_ROOT=/path/to/DistLM python train.py
  python scripts/train_autoreg_dolly.py --tokenizer /path/to/dolly_bpe_tokenizer.json

Default data dir: ``<repo>/out/`` (CSV + BPE tokenizer). Generate with:
  ``python scripts/dolly_bpe_responses.py --out-dir out``

Built (embedding, target) tensors are saved to ``out/dolly_pair_cache.pt`` (see ``--pairs-cache``)
and reused on the next run unless the CSV, tokenizer, or relevant flags change, or you pass
``--rebuild-pairs``.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
from pathlib import Path


def _infer_repo_root() -> Path:
    """
    Repo root for default paths.

    - `scripts/train_autoreg_dolly.py` → parent of `scripts/`
    - `train.py` at repo root → directory containing `train.py`
    If you copy only `train.py` to e.g. `/workspace/train.py`, set `CHORUS_ROOT=/path/to/DistLM`.
    """
    here = Path(__file__).resolve()
    if here.name == "train_autoreg_dolly.py" and here.parent.name == "scripts":
        return here.parents[1]
    if here.name == "train.py":
        return here.parent
    if here.parent.name == "scripts":
        return here.parents[1]
    return here.parent


def _root() -> Path:
    env = os.environ.get("CHORUS_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _infer_repo_root()


ROOT = _root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Defaults: artifacts live under ``<repo>/out/`` (e.g. /workspace/out). Override with CLI / env.
def _data_dir() -> Path:
    raw = os.environ.get("CHORUS_DATA_DIR", "out").strip() or "out"
    p = Path(raw).expanduser()
    return p.resolve() if p.is_absolute() else (ROOT / p).resolve()


_DATA_DIR = _data_dir()
_DEFAULT_CSV = _DATA_DIR / "dolly_prompt_response.csv"
_DEFAULT_TOKENIZER = (
    Path(os.environ["CHORUS_TOKENIZER"]).expanduser().resolve()
    if os.environ.get("CHORUS_TOKENIZER", "").strip()
    else _DATA_DIR / "dolly_bpe_tokenizer.json"
)
_DEFAULT_OUT = _DATA_DIR / "autoreg_checkpoint.pt"
_DEFAULT_PAIRS_CACHE = _DATA_DIR / "dolly_pair_cache.pt"

# BPE pipeline may write either name under ``out/``
_TOKENIZER_FILENAMES = ("dolly_bpe_tokenizer.json", "dolly_subword_tokenizer.json")
_CSV_NAME = "dolly_prompt_response.csv"


def _search_first_existing(search_names: tuple[str, ...]) -> Path | None:
    """Try repo root, cwd, and ``out/`` under each (covers /workspace/out)."""
    bases: list[Path] = [
        ROOT,
        Path.cwd(),
        ROOT / "out",
        Path.cwd() / "out",
        _DATA_DIR,
        ROOT / "dolly_bpe_responses",
    ]
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


def _resolve_tokenizer_arg(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.is_file():
        return path
    found = _search_first_existing(_TOKENIZER_FILENAMES)
    if found is not None:
        print(f"Using tokenizer: {found}", flush=True)
        return found
    return path


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
        f"Data dir (CHORUS_DATA_DIR): {_DATA_DIR}\n"
        "Fix one of:\n"
        "  • Put dolly_prompt_response.csv + dolly_bpe_tokenizer.json under ./out (or set CHORUS_DATA_DIR).\n"
        "  • Set CHORUS_TOKENIZER / pass --tokenizer to the tokenizer JSON.\n"
        "  • Generate: python scripts/dolly_bpe_responses.py --out-dir out\n",
        file=sys.stderr,
    )
    raise SystemExit(1)

import torch
import torch.nn as nn
from torch.optim.lr_scheduler import (
    CosineAnnealingLR,
    LinearLR,
    OneCycleLR,
    ReduceLROnPlateau,
    SequentialLR,
)
from torch.utils.data import DataLoader, Dataset, random_split

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **kwargs):  # type: ignore[misc]
        return x

from chorus.autoreg_model import AutoregPredictor


def _make_lr_scheduler(
    opt: torch.optim.Optimizer,
    args: argparse.Namespace,
    *,
    steps_per_epoch: int,
) -> tuple[object | None, bool]:
    """
    Build LR scheduler.

    Returns (scheduler, step_each_batch). When step_each_batch is True (OneCycleLR),
    call scheduler.step() after every optimizer step; otherwise step once per epoch
    after computing val loss (plateau) or at end of epoch (cosine / warmup_cosine).
    """
    epochs = max(1, args.epochs)
    if args.lr_scheduler == "warmup_cosine":
        we = min(max(0, args.warmup_epochs), epochs)
        if we <= 0:
            sched = CosineAnnealingLR(opt, T_max=epochs, eta_min=args.lr_min)
            return sched, False
        if epochs <= we:
            sched = LinearLR(opt, start_factor=0.01, end_factor=1.0, total_iters=epochs)
            return sched, False
        warmup = LinearLR(opt, start_factor=0.01, end_factor=1.0, total_iters=we)
        cosine = CosineAnnealingLR(opt, T_max=epochs - we, eta_min=args.lr_min)
        sched = SequentialLR(opt, [warmup, cosine], milestones=[we])
        return sched, False
    if args.lr_scheduler == "plateau":
        sched = ReduceLROnPlateau(
            opt,
            mode="min",
            factor=args.lr_factor,
            patience=args.lr_patience,
            min_lr=args.lr_min,
            threshold=args.lr_plateau_threshold,
        )
        return sched, False
    if args.lr_scheduler == "cosine":
        sched = CosineAnnealingLR(opt, T_max=epochs, eta_min=args.lr_min)
        return sched, False
    if args.lr_scheduler == "onecycle":
        total_steps = max(1, steps_per_epoch * epochs)
        sched = OneCycleLR(
            opt,
            max_lr=args.lr,
            total_steps=total_steps,
            pct_start=args.onecycle_pct_start,
            div_factor=25.0,
            final_div_factor=1e4,
        )
        return sched, True
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


class TokenStepDataset(Dataset):
    """Each item: (embedding, target_token_id) for cross-entropy."""

    def __init__(
        self,
        pairs: list[tuple[list[float], int]] | None = None,
        *,
        embeddings: torch.Tensor | None = None,
        targets: torch.Tensor | None = None,
    ) -> None:
        if embeddings is not None and targets is not None:
            self._emb = embeddings
            self._tgt = targets
            self._mode = "tensor"
        elif pairs is not None:
            self.pairs = pairs
            self._mode = "list"
        else:
            raise ValueError("Provide either pairs= or embeddings= and targets=")

    def __len__(self) -> int:
        if self._mode == "tensor":
            return int(self._emb.size(0))
        return len(self.pairs)

    def __getitem__(self, i: int) -> tuple[torch.Tensor, torch.Tensor]:
        if self._mode == "tensor":
            return self._emb[i].float(), self._tgt[i].long()
        emb, tid = self.pairs[i]
        return torch.tensor(emb, dtype=torch.float32), torch.tensor(tid, dtype=torch.long)


def _pairs_to_tensors(pairs: list[tuple[list[float], int]]) -> tuple[torch.Tensor, torch.Tensor]:
    if not pairs:
        return torch.empty(0, 0), torch.empty(0, dtype=torch.long)
    emb = torch.tensor([p[0] for p in pairs], dtype=torch.float32)
    tgt = torch.tensor([p[1] for p in pairs], dtype=torch.long)
    return emb, tgt


def _infer_encoder_dim(model_name: str, embed_mode: str) -> int:
    """Backbone output size (e.g. 384 for MiniLM)."""
    if embed_mode == "sentence":
        from sentence_transformers import SentenceTransformer

        st = SentenceTransformer(model_name)
        return int(st.get_sentence_embedding_dimension())
    from transformers import AutoConfig

    return int(AutoConfig.from_pretrained(model_name).hidden_size)


def _pairs_cache_meta(
    csv_path: Path,
    limit_rows: int,
    max_steps_per_example: int,
    max_pairs: int,
    minilm_model: str,
    tokenizer_path: Path,
    embed_mode: str,
    dim: int,
    encoder_dim: int,
) -> dict:
    st = csv_path.stat()
    return {
        "version": 1,
        "csv": str(csv_path.resolve()),
        "csv_mtime_ns": st.st_mtime_ns,
        "limit_rows": limit_rows,
        "max_steps_per_example": max_steps_per_example,
        "max_pairs": max_pairs,
        "minilm_model": minilm_model,
        "tokenizer": str(tokenizer_path.resolve()),
        "embed_mode": embed_mode,
        "dim": dim,
        "encoder_dim": encoder_dim,
    }


def _load_pairs_cache(path: Path) -> dict:
    kw: dict = {"map_location": "cpu"}
    try:
        import inspect

        if "weights_only" in inspect.signature(torch.load).parameters:
            kw["weights_only"] = False
    except (TypeError, ValueError):
        pass
    return torch.load(path, **kw)


def _save_pairs_cache(path: Path, embeddings: torch.Tensor, targets: torch.Tensor, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save({"embeddings": embeddings, "targets": targets, "meta": meta}, tmp)
    tmp.replace(path)


def _count_pair_steps(
    rows: list[tuple[str, str]],
    tok,
    max_steps_per_example: int,
) -> int:
    total = 0
    for _prompt, response in rows:
        enc = tok.encode(response)
        ids = enc.ids
        if not ids:
            continue
        n = len(ids)
        if max_steps_per_example:
            n = min(n, max_steps_per_example)
        total += n
    return total


def build_pairs(
    rows: list[tuple[str, str]],
    tokenizer,
    embedder,
    max_steps_per_example: int,
    embed_batch_size: int,
    embed_mode: str,
    minilm_model: str,
    device: torch.device,
) -> list[tuple[list[float], int]]:
    from tokenizers import Tokenizer

    from chorus.prefix_encoder import encode_prefixes_sentence, encode_prefixes_token_last

    tok: Tokenizer = tokenizer
    pairs: list[tuple[list[float], int]] = []
    total_steps = _count_pair_steps(rows, tok, max_steps_per_example)
    pbar = tqdm(total=total_steps, desc="Building (embedding, target) pairs", unit="pair")
    for prompt, response in rows:
        enc = tok.encode(response)
        ids = enc.ids
        if not ids:
            continue
        n = len(ids)
        if max_steps_per_example:
            n = min(n, max_steps_per_example)
        texts: list[str] = []
        tids: list[int] = []
        for k in range(n):
            if k == 0:
                texts.append(prompt)
            else:
                texts.append(prompt + tok.decode(ids[:k]))
            tids.append(int(ids[k]))
        for start in range(0, len(texts), embed_batch_size):
            chunk = texts[start : start + embed_batch_size]
            chunk_tids = tids[start : start + embed_batch_size]
            bs = min(embed_batch_size, len(chunk))
            if embed_mode == "sentence":
                assert embedder is not None
                vecs = encode_prefixes_sentence(embedder, chunk, batch_size=bs)
            elif embed_mode == "token_last":
                vecs = encode_prefixes_token_last(
                    chunk,
                    model_id=minilm_model,
                    device=device,
                    batch_size=bs,
                )
            else:
                raise ValueError(f"Unknown embed_mode: {embed_mode}")
            for j in range(len(chunk)):
                pairs.append((vecs[j], chunk_tids[j]))
            pbar.update(len(chunk))
    pbar.close()
    return pairs


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=_DEFAULT_CSV)
    p.add_argument(
        "--tokenizer",
        type=Path,
        default=_DEFAULT_TOKENIZER,
        help=f"BPE tokenizer JSON (default: {_DEFAULT_TOKENIZER})",
    )
    p.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--vocab-size", type=int, default=16000)
    p.add_argument(
        "--dim",
        type=int,
        default=384,
        help="Autoreg block token dimension (must divide 8). Use 64 for a 64-D trunk; "
        "encoder (e.g. 384) is projected with a learned Linear when dim < encoder_dim.",
    )
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument(
        "--batch-size",
        type=int,
        default=512,
        help="Training/val DataLoader batch size (default: 512)",
    )
    p.add_argument(
        "--embed-batch-size",
        type=int,
        default=0,
        help="MiniLM encode batch size during pair build (0 = 30 × --batch-size)",
    )
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument(
        "--weight-decay",
        type=float,
        default=1e-4,
        help="AdamW weight decay (default 1e-4; use 0 to disable)",
    )
    p.add_argument(
        "--grad-clip",
        type=float,
        default=0.0,
        help="Clip gradient L2 norm (0 = disabled; try 1.0 if training is unstable)",
    )
    p.add_argument(
        "--lr-scheduler",
        choices=("warmup_cosine", "plateau", "cosine", "onecycle", "none"),
        default="warmup_cosine",
        help=(
            "warmup_cosine: linear warmup then cosine decay (default); "
            "plateau: reduce LR when val stalls; onecycle: single-cycle per-batch schedule"
        ),
    )
    p.add_argument(
        "--warmup-epochs",
        type=int,
        default=3,
        help="warmup_cosine: linear ramp epochs before cosine decay (default 3)",
    )
    p.add_argument(
        "--lr-factor",
        type=float,
        default=0.7,
        help="ReduceLROnPlateau: multiply LR by this when val stalls (default 0.7)",
    )
    p.add_argument(
        "--lr-patience",
        type=int,
        default=3,
        help="ReduceLROnPlateau: epochs with no val improvement before reducing LR (default 3)",
    )
    p.add_argument(
        "--lr-plateau-threshold",
        type=float,
        default=1e-3,
        help="ReduceLROnPlateau: ignore val improvements smaller than this (default 1e-3)",
    )
    p.add_argument(
        "--onecycle-pct-start",
        type=float,
        default=0.3,
        dest="onecycle_pct_start",
        help="onecycle: fraction of steps spent increasing LR (default 0.3)",
    )
    p.add_argument(
        "--lr-min",
        type=float,
        default=1e-6,
        help="Cosine / warmup tail / plateau floor: minimum LR (default 1e-6)",
    )
    p.add_argument("--limit-rows", type=int, default=0, help="Max CSV rows (0=all)")
    p.add_argument("--max-steps-per-example", type=int, default=0, help="Cap tokens per response (0=all)")
    p.add_argument("--max-pairs", type=int, default=0, help="Cap total training pairs after build (0=all)")
    p.add_argument("--val-fraction", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--minilm-model", default="sentence-transformers/all-MiniLM-L6-v2")
    p.add_argument(
        "--embed-mode",
        choices=("sentence", "token_last"),
        default="token_last",
        help=(
            "sentence: SentenceTransformer pooled embedding; "
            "token_last: last-layer hidden at last token (contextual; requires transformers, hidden 384)"
        ),
    )
    p.add_argument(
        "--pairs-cache",
        type=Path,
        default=_DEFAULT_PAIRS_CACHE,
        help=f"Save/load (embedding, target) pair tensors (default: {_DEFAULT_PAIRS_CACHE})",
    )
    p.add_argument(
        "--rebuild-pairs",
        action="store_true",
        help="Ignore --pairs-cache and rebuild from CSV + MiniLM (overwrites cache)",
    )
    args = p.parse_args()

    args.out = args.out.expanduser().resolve()
    args.tokenizer = _resolve_tokenizer_arg(args.tokenizer)
    args.csv = _resolve_csv_arg(args.csv)
    args.pairs_cache = args.pairs_cache.expanduser().resolve()

    if args.dim % 8 != 0:
        raise SystemExit(f"--dim must be divisible by 8 (pool_kernel), got {args.dim}")

    _require_file(args.tokenizer, "BPE tokenizer JSON")
    _require_file(args.csv, "CSV dataset")
    print(f"ROOT={ROOT}  CHORUS_DATA_DIR={_DATA_DIR}", flush=True)

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(args.tokenizer))
    vs = args.vocab_size
    tok_n = len(tok.get_vocab())
    if tok_n < vs:
        print(
            f"Warning: tokenizer vocab size {tok_n} < --vocab-size {vs}; "
            "targets may include OOV ids.",
            file=sys.stderr,
        )

    encoder_dim = _infer_encoder_dim(args.minilm_model, args.embed_mode)
    cache_meta = _pairs_cache_meta(
        args.csv,
        args.limit_rows,
        args.max_steps_per_example,
        args.max_pairs,
        args.minilm_model,
        args.tokenizer,
        args.embed_mode,
        args.dim,
        encoder_dim,
    )

    emb: torch.Tensor | None = None
    tgt: torch.Tensor | None = None
    loaded_cache = False
    if not args.rebuild_pairs and args.pairs_cache.is_file():
        try:
            blob = _load_pairs_cache(args.pairs_cache)
        except Exception as e:
            print(f"Could not read pair cache ({e}); rebuilding…", flush=True)
        else:
            if blob.get("meta") == cache_meta and "embeddings" in blob and "targets" in blob:
                emb = blob["embeddings"].float()
                tgt = blob["targets"].long()
                loaded_cache = True
                print(
                    f"Loaded {emb.size(0):,} (embedding, target) pairs from {args.pairs_cache}",
                    flush=True,
                )
            else:
                print("Pair cache does not match current CSV/options; rebuilding…", flush=True)

    if not loaded_cache:
        rows = load_rows(args.csv, args.limit_rows)
        print(f"Loaded {len(rows)} (prompt,response) rows")
        embed_bs = args.embed_batch_size if args.embed_batch_size > 0 else 30 * args.batch_size
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        embedder = None
        if args.embed_mode == "sentence":
            from sentence_transformers import SentenceTransformer

            embedder = SentenceTransformer(args.minilm_model)
        elif args.embed_mode == "token_last":
            print(
                f"embed_mode=token_last (HF last-token hidden, model={args.minilm_model})",
                flush=True,
            )
        pairs = build_pairs(
            rows,
            tok,
            embedder,
            args.max_steps_per_example,
            embed_bs,
            args.embed_mode,
            args.minilm_model,
            device,
        )
        if args.max_pairs and len(pairs) > args.max_pairs:
            random.shuffle(pairs)
            pairs = pairs[: args.max_pairs]
        print(f"Total pairs: {len(pairs)}")
        if not pairs:
            raise SystemExit("No training pairs (empty responses?)")
        emb, tgt = _pairs_to_tensors(pairs)
        del pairs
        _save_pairs_cache(args.pairs_cache, emb, tgt, cache_meta)
        print(f"Saved pair cache to {args.pairs_cache}", flush=True)

    assert emb is not None and tgt is not None
    if emb.size(0) == 0:
        raise SystemExit("No training pairs (empty responses?)")

    ds = TokenStepDataset(embeddings=emb, targets=tgt)
    n = len(ds)
    if n < 2:
        raise SystemExit("Need at least 2 training pairs for train/val split.")
    n_val = max(1, int(n * args.val_fraction))
    if n_val >= n:
        n_val = n // 2
    n_train = n - n_val
    train_ds, val_ds = random_split(
        ds,
        [n_train, n_val],
        generator=torch.Generator().manual_seed(args.seed),
    )

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoregPredictor(
        vocab_size=vs,
        dim=args.dim,
        encoder_dim=encoder_dim if encoder_dim != args.dim else None,
    ).to(device)
    bd = model.parameter_count_breakdown()
    print(
        f"Parameters: total={bd['total_trainable']:,} "
        f"(blocks={bd['blocks_total']:,}, head={bd['classifier_total']:,})"
    )
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    if args.lr_scheduler == "onecycle":
        # OneCycleLR assumes base lr = max_lr / div_factor (default div_factor=25)
        for pg in opt.param_groups:
            pg["lr"] = args.lr / 25.0
    sched, sched_per_step = _make_lr_scheduler(opt, args, steps_per_epoch=len(train_loader))
    ce = nn.CrossEntropyLoss()

    def step_batch(batch: tuple[torch.Tensor, torch.Tensor]) -> torch.Tensor:
        emb, target = batch
        emb = emb.to(device)
        target = target.to(device)
        logits = model(emb)
        return ce(logits, target)

    best_val = float("inf")
    for epoch in range(args.epochs):
        model.train()
        total = 0.0
        n_seen = 0
        train_pbar = tqdm(train_loader, desc=f"train {epoch + 1}/{args.epochs}")
        for batch in train_pbar:
            loss = step_batch(batch)
            opt.zero_grad()
            loss.backward()
            if args.grad_clip > 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)
            opt.step()
            if sched_per_step and sched is not None:
                sched.step()
            li = loss.item()
            total += li * batch[0].size(0)
            n_seen += batch[0].size(0)
            train_pbar.set_postfix(
                loss=f"{li:.6f}",
                avg=f"{total / max(n_seen, 1):.6f}",
                lr=f"{opt.param_groups[0]['lr']:.2e}",
            )
        train_loss = total / max(n_seen, 1)

        model.eval()
        vtotal = 0.0
        vn = 0
        with torch.no_grad():
            val_pbar = tqdm(val_loader, desc=f"val {epoch + 1}/{args.epochs}", leave=False)
            for batch in val_pbar:
                loss = step_batch(batch)
                li = loss.item()
                vtotal += li * batch[0].size(0)
                vn += batch[0].size(0)
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
            f"epoch {epoch + 1}: train_ce={train_loss:.6f} val_ce={val_loss:.6f} lr={lr_now:.2e}"
        )
        if val_loss < best_val:
            best_val = val_loss

    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state": model.state_dict(),
            "vocab_size": vs,
            "minilm_model": args.minilm_model,
            "embed_mode": args.embed_mode,
            "dim": args.dim,
            "encoder_dim": encoder_dim,
            "param_count": model.num_parameters(),
            "config": {
                "dim": args.dim,
                "encoder_dim": encoder_dim,
                "hidden": 64,
                "num_blocks": 5,
                "vocab_size": vs,
                "head": "row_softmax_pool_64_to_vocab",
                "embed_mode": args.embed_mode,
            },
        },
        args.out,
    )
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
