#!/usr/bin/env python3
"""
Train a tiny next-token model: token id -> nn.Embedding(V, 64) -> Linear(64, V).

Loss: cross-entropy. Data: consecutive (prev_id, next_id) pairs from Dolly response BPE ids.

Use ``--overfit`` to train on 100%% of pairs with no validation split (memorize the set; raise
``--epochs`` / ``--lr`` as needed). Saves two checkpoints (embedding table and head).

  python scripts/train_small_id_model.py --csv out/dolly_prompt_response.csv \\
    --tokenizer out/dolly_bpe_tokenizer.json

Requires: torch, tokenizers, tqdm (optional).
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
from pathlib import Path


def _infer_repo_root() -> Path:
    here = Path(__file__).resolve()
    if here.name == "train_small_id_model.py" and here.parent.name == "scripts":
        return here.parents[1]
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
_TOKENIZER_FILENAMES = ("dolly_bpe_tokenizer.json", "dolly_subword_tokenizer.json")
_CSV_NAME = "dolly_prompt_response.csv"


def _search_first_existing(search_names: tuple[str, ...]) -> Path | None:
    bases: list[Path] = [ROOT, Path.cwd(), ROOT / "out", Path.cwd() / "out", _DATA_DIR]
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
        f"Data dir (CHORUS_DATA_DIR): {_DATA_DIR}\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **kwargs):  # type: ignore[misc]
        return x


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


def build_id_pairs(
    rows: list[tuple[str, str]],
    tokenizer,
    vocab_size: int,
) -> list[tuple[int, int]]:
    """Consecutive (prev_token_id, next_token_id) within each response."""
    pairs: list[tuple[int, int]] = []
    vs = vocab_size
    for _prompt, response in tqdm(rows, desc="Token pairs"):
        enc = tokenizer.encode(response)
        ids = enc.ids
        if len(ids) < 2:
            continue
        for i in range(len(ids) - 1):
            a, b = int(ids[i]), int(ids[i + 1])
            if 0 <= a < vs and 0 <= b < vs:
                pairs.append((a, b))
    return pairs


class IdPairDataset(Dataset):
    def __init__(self, pairs: list[tuple[int, int]]) -> None:
        self.pairs = pairs

    def __len__(self) -> int:
        return len(self.pairs)

    def __getitem__(self, i: int) -> tuple[torch.Tensor, torch.Tensor]:
        a, b = self.pairs[i]
        return torch.tensor(a, dtype=torch.long), torch.tensor(b, dtype=torch.long)


class SmallIdModel(nn.Module):
    """token_id -> Embedding(V, dim) -> Linear(dim, V) logits."""

    def __init__(self, vocab_size: int, dim: int = 64) -> None:
        super().__init__()
        self.vocab_size = vocab_size
        self.dim = dim
        self.embed = nn.Embedding(vocab_size, dim)
        self.head = nn.Linear(dim, vocab_size)

    def forward(self, token_ids: torch.Tensor) -> torch.Tensor:
        x = self.embed(token_ids)
        return self.head(x)


def main() -> None:
    p = argparse.ArgumentParser(description="Train id->64->V next-token CE model; save embed + head.")
    p.add_argument("--csv", type=Path, default=_DEFAULT_CSV)
    p.add_argument("--tokenizer", type=Path, default=_DEFAULT_TOKENIZER)
    p.add_argument("--vocab-size", type=int, default=16000)
    p.add_argument("--dim", type=int, default=64, help="Embedding width (intermediate dim).")
    p.add_argument("--batch-size", type=int, default=16384, help="Default 16k (16384).")
    p.add_argument(
        "--epochs",
        type=int,
        default=None,
        help="Training epochs (default: 5, or 100 with --overfit).",
    )
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument(
        "--val-fraction",
        type=float,
        default=0.2,
        help="Validation fraction (default 0.2). Ignored with --overfit.",
    )
    p.add_argument(
        "--overfit",
        action="store_true",
        help="Train on all pairs (no val split). Use high --epochs to drive train CE down.",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--limit-rows", type=int, default=0, help="Max CSV rows (0=all).")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=_DATA_DIR,
        help="Directory for embed_1to64.pt and head_64to16000.pt",
    )
    p.add_argument("--freeze-embed", action="store_true", help="Do not train embedding (still saved).")
    p.add_argument("--freeze-head", action="store_true", help="Do not train head (still saved).")
    args = p.parse_args()
    if args.epochs is None:
        args.epochs = 100 if args.overfit else 5

    args.csv = _resolve_csv_arg(args.csv)
    args.tokenizer = _resolve_tokenizer_arg(args.tokenizer)
    _require_file(args.csv, "CSV")
    _require_file(args.tokenizer, "tokenizer JSON")

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(args.tokenizer))
    vocab_size = args.vocab_size
    rows = load_rows(args.csv, args.limit_rows)
    pairs = build_id_pairs(rows, tok, vocab_size)
    if not pairs:
        raise SystemExit("No (prev, next) token pairs; check CSV / tokenizer / vocab-size.")

    random.shuffle(pairs)
    n = len(pairs)
    if args.overfit:
        n_train, n_val = n, 0
        train_pairs = pairs
        val_pairs: list[tuple[int, int]] = []
        print(f"Overfit mode: {n_train:,} training pairs, no validation.", flush=True)
    else:
        n_val = max(1, int(round(n * args.val_fraction)))
        n_train = n - n_val
        if n_train < 1:
            raise SystemExit("Need at least 2 pairs for train/val split.")
        train_pairs = pairs[:n_train]
        val_pairs = pairs[n_train:]

    train_ds = IdPairDataset(train_pairs)
    val_ds = IdPairDataset(val_pairs) if val_pairs else None

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = SmallIdModel(vocab_size, args.dim).to(device)

    if args.freeze_embed:
        for p_ in model.embed.parameters():
            p_.requires_grad = False
    if args.freeze_head:
        for p_ in model.head.parameters():
            p_.requires_grad = False

    trainable = [p for p in model.parameters() if p.requires_grad]
    if not trainable:
        raise SystemExit("Nothing to train: both --freeze-embed and --freeze-head.")

    opt = torch.optim.AdamW(trainable, lr=args.lr)
    ce = nn.CrossEntropyLoss()

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        drop_last=False,
        num_workers=0,
    )
    val_loader = (
        DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=0)
        if val_ds is not None
        else None
    )

    for epoch in range(args.epochs):
        model.train()
        total = 0.0
        n_seen = 0
        n_correct = 0
        for inp, tgt in tqdm(train_loader, desc=f"train {epoch+1}/{args.epochs}"):
            inp = inp.to(device)
            tgt = tgt.to(device)
            opt.zero_grad()
            logits = model(inp)
            loss = ce(logits, tgt)
            loss.backward()
            opt.step()
            bs = inp.size(0)
            total += loss.item() * bs
            n_seen += bs
            pred = logits.argmax(dim=-1)
            n_correct += (pred == tgt).sum().item()
        train_loss = total / max(1, n_seen)
        train_acc = n_correct / max(1, n_seen)

        if val_loader is not None:
            model.eval()
            v_total = 0.0
            v_n = 0
            with torch.no_grad():
                for inp, tgt in val_loader:
                    inp = inp.to(device)
                    tgt = tgt.to(device)
                    logits = model(inp)
                    loss = ce(logits, tgt)
                    bs = inp.size(0)
                    v_total += loss.item() * bs
                    v_n += bs
            val_loss = v_total / max(1, v_n)
            print(
                f"epoch {epoch+1}: train_ce={train_loss:.4f} train_acc={train_acc:.4f} val_ce={val_loss:.4f}",
                flush=True,
            )
        else:
            print(
                f"epoch {epoch+1}: train_ce={train_loss:.6f} train_acc={train_acc:.4f}",
                flush=True,
            )

    args.out_dir = args.out_dir.expanduser().resolve()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    embed_path = args.out_dir / "embed_1to64.pt"
    head_path = args.out_dir / "head_64to16000.pt"

    meta = {
        "vocab_size": vocab_size,
        "dim": args.dim,
        "train_pairs": n_train,
        "val_pairs": n_val,
    }
    torch.save({"state_dict": model.embed.state_dict(), "meta": meta}, embed_path)
    torch.save({"state_dict": model.head.state_dict(), "meta": meta}, head_path)
    print(f"Saved {embed_path}", flush=True)
    print(f"Saved {head_path}", flush=True)


if __name__ == "__main__":
    main()
