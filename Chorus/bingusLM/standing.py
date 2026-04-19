#!/usr/bin/env python3
"""
Self-contained causal LM: **model + training** in one file (no ``import distlm``).

- **Model:** token embedding → pre-norm transformer (RoPE multi-head causal attention + SwiGLU FFN)
  → RMSNorm → ``lm_head``.
- **Data:** ``data/tokenizer.json`` + ``data.jsonl`` (or ``--data-jsonl`` / ``--train-jsonl`` / ``--csv``).

Run (from repo root):

  python scripts/lm_standalone.py --amp

Requires: torch, tqdm, tokenizers
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


# --- repo paths (for defaults) -------------------------------------------------

def _repo_root() -> Path:
    env = os.environ.get("DISTLM_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    p = Path(__file__).resolve()
    return p.parents[1] if p.parent.name == "scripts" else p.parent


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _data_dir() -> Path:
    raw = os.environ.get("DISTLM_DATA_DIR", "out").strip() or "out"
    x = Path(raw).expanduser()
    return x.resolve() if x.is_absolute() else (ROOT / x).resolve()


_DEFAULT_OUT = _data_dir() / "causal_lm.pt"
_DEFAULT_TOKENIZER = ROOT / "data" / "tokenizer.json"
_DEFAULT_QA_PATH = ROOT / "data.jsonl"


# --- model -------------------------------------------------------------------

DEFAULT_DROPOUT = 0.1
DEFAULT_RESIDUAL_SCALE = 1.0


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        rms = x.float().pow(2).mean(-1, keepdim=True).add(self.eps).rsqrt()
        return (x.float() * rms).to(x.dtype) * self.weight


class RotaryEmbedding(nn.Module):
    def __init__(self, head_dim: int, max_seq_len: int = 2048, theta: float = 10000.0) -> None:
        super().__init__()
        inv_freq = 1.0 / (theta ** (torch.arange(0, head_dim, 2).float() / head_dim))
        self.register_buffer("inv_freq", inv_freq, persistent=False)
        self._build_cache(max_seq_len)

    def _build_cache(self, seq_len: int) -> None:
        t = torch.arange(seq_len, dtype=self.inv_freq.dtype, device=self.inv_freq.device)
        freqs = torch.outer(t, self.inv_freq)
        emb = torch.cat([freqs, freqs], dim=-1)
        self.register_buffer("cos_cached", emb.cos(), persistent=False)
        self.register_buffer("sin_cached", emb.sin(), persistent=False)

    def forward(self, seq_len: int) -> tuple[torch.Tensor, torch.Tensor]:
        if seq_len > self.cos_cached.shape[0]:
            self._build_cache(seq_len)
        return self.cos_cached[:seq_len], self.sin_cached[:seq_len]


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1, x2 = x.chunk(2, dim=-1)
    return torch.cat((-x2, x1), dim=-1)


def apply_rotary_emb(
    q: torch.Tensor, k: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor
) -> tuple[torch.Tensor, torch.Tensor]:
    cos = cos.unsqueeze(0).unsqueeze(0)
    sin = sin.unsqueeze(0).unsqueeze(0)
    q_rot = q * cos + _rotate_half(q) * sin
    k_rot = k * cos + _rotate_half(k) * sin
    return q_rot, k_rot


class MultiHeadCausalAttention(nn.Module):
    def __init__(
        self,
        dim: int = 384,
        n_heads: int = 6,
        *,
        dropout: float = DEFAULT_DROPOUT,
        max_seq_len: int = 2048,
    ) -> None:
        super().__init__()
        assert dim % n_heads == 0, f"dim={dim} must be divisible by n_heads={n_heads}"
        self.dim = dim
        self.n_heads = n_heads
        self.head_dim = dim // n_heads
        self.scale = self.head_dim**-0.5
        self.q_proj = nn.Linear(dim, dim, bias=False)
        self.k_proj = nn.Linear(dim, dim, bias=False)
        self.v_proj = nn.Linear(dim, dim, bias=False)
        self.o_proj = nn.Linear(dim, dim, bias=False)
        self.attn_dropout = nn.Dropout(dropout)
        self.resid_dropout = nn.Dropout(dropout)
        self.rope = RotaryEmbedding(self.head_dim, max_seq_len=max_seq_len)

    def forward(
        self,
        x: torch.Tensor,
        causal_mask: torch.Tensor | None = None,
        key_padding_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        b, l, _ = x.shape
        q = self.q_proj(x).view(b, l, self.n_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(b, l, self.n_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(b, l, self.n_heads, self.head_dim).transpose(1, 2)
        cos, sin = self.rope(l)
        cos, sin = cos.to(q.device, dtype=q.dtype), sin.to(q.device, dtype=q.dtype)
        q, k = apply_rotary_emb(q, k, cos, sin)

        use_sdpa = (
            hasattr(F, "scaled_dot_product_attention")
            and causal_mask is None
            and key_padding_mask is None
        )
        if use_sdpa:
            out = F.scaled_dot_product_attention(
                q, k, v, is_causal=True, dropout_p=self.attn_dropout.p if self.training else 0.0
            )
        else:
            scores = torch.matmul(q, k.transpose(-2, -1)) * self.scale
            if causal_mask is not None:
                scores = scores.masked_fill(causal_mask.unsqueeze(0).unsqueeze(0), float("-inf"))
            else:
                cm = torch.triu(torch.ones(l, l, device=x.device, dtype=torch.bool), diagonal=1)
                scores = scores.masked_fill(cm.unsqueeze(0).unsqueeze(0), float("-inf"))
            if key_padding_mask is not None:
                scores = scores.masked_fill(~key_padding_mask[:, None, None, :], float("-inf"))
            attn = F.softmax(scores, dim=-1)
            attn = torch.nan_to_num(attn, nan=0.0)
            attn = self.attn_dropout(attn)
            out = torch.matmul(attn, v)

        out = out.transpose(1, 2).contiguous().view(b, l, self.dim)
        if key_padding_mask is not None:
            out = out * key_padding_mask.unsqueeze(-1).to(out.dtype)
        return self.resid_dropout(self.o_proj(out))


class SwiGLUFFN(nn.Module):
    def __init__(self, dim: int = 384, hidden_dim: int = 1536, *, dropout: float = DEFAULT_DROPOUT) -> None:
        super().__init__()
        self.w1 = nn.Linear(dim, hidden_dim, bias=False)
        self.w2 = nn.Linear(hidden_dim, dim, bias=False)
        self.w3 = nn.Linear(dim, hidden_dim, bias=False)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dropout(self.w2(F.silu(self.w1(x)) * self.w3(x)))


class AutoregSeqLayer(nn.Module):
    def __init__(
        self,
        dim: int = 384,
        n_heads: int = 6,
        mlp_hidden: int = 1536,
        *,
        dropout: float = DEFAULT_DROPOUT,
        max_seq_len: int = 2048,
        residual_scale: float = DEFAULT_RESIDUAL_SCALE,
    ) -> None:
        super().__init__()
        self.residual_scale = residual_scale
        self.attn_norm = RMSNorm(dim)
        self.attn = MultiHeadCausalAttention(dim, n_heads, dropout=dropout, max_seq_len=max_seq_len)
        self.ffn_norm = RMSNorm(dim)
        self.ffn = SwiGLUFFN(dim, mlp_hidden, dropout=dropout)

    def forward(
        self,
        x: torch.Tensor,
        causal_mask: torch.Tensor | None = None,
        key_padding_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        x = x + self.residual_scale * self.attn(self.attn_norm(x), causal_mask, key_padding_mask)
        x = x + self.residual_scale * self.ffn(self.ffn_norm(x))
        return x


class CausalTransformerLM(nn.Module):
    """Causal LM: ``forward(input_ids, attention_mask=None)`` → ``(B, L, vocab)`` float32 logits."""

    def __init__(
        self,
        vocab_size: int,
        dim: int = 384,
        n_heads: int = 8,
        n_layers: int = 8,
        mlp_hidden: int = 1536,
        *,
        dropout: float = DEFAULT_DROPOUT,
        residual_scale: float = DEFAULT_RESIDUAL_SCALE,
        max_seq_len: int = 2048,
    ) -> None:
        super().__init__()
        if dim % n_heads != 0:
            raise ValueError(f"dim={dim} must be divisible by n_heads={n_heads}")
        self.vocab_size = vocab_size
        self.dim = dim
        self.n_heads = n_heads
        self.n_layers = n_layers
        self.embed = nn.Embedding(vocab_size, dim)
        self.layers = nn.ModuleList(
            [
                AutoregSeqLayer(
                    dim, n_heads, mlp_hidden, dropout=dropout, max_seq_len=max_seq_len, residual_scale=residual_scale
                )
                for _ in range(n_layers)
            ]
        )
        self.final_norm = RMSNorm(dim)
        self.lm_head = nn.Linear(dim, vocab_size, bias=False)
        self._init_weights()

    def _init_weights(self) -> None:
        nn.init.normal_(self.embed.weight, mean=0.0, std=0.02)
        for name, module in self.named_modules():
            if "lm_head" in name or name == "embed":
                continue
            if isinstance(module, nn.Linear):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
        scale = (2 * self.n_layers) ** -0.5
        for layer in self.layers:
            nn.init.normal_(layer.attn.o_proj.weight, mean=0.0, std=0.02 * scale)
            nn.init.normal_(layer.ffn.w2.weight, mean=0.0, std=0.02 * scale)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        x = self.embed(input_ids)
        if attention_mask is not None:
            x = x * attention_mask.unsqueeze(-1).to(x.dtype)
        for layer in self.layers:
            x = layer(x, None, attention_mask)
            if attention_mask is not None:
                x = x * attention_mask.unsqueeze(-1).to(x.dtype)
        x = self.final_norm(x)
        logits = self.lm_head(x)
        return logits.float() if logits.dtype != torch.float32 else logits


# --- data + training (same behavior as scripts/train_scalar_qv_lm.py) ----------

class EncodeFn(Protocol):
    def __call__(self, text: str) -> list[int]: ...


def load_tokenizers_bpe(path: Path) -> tuple[object, int, int]:
    try:
        from tokenizers import Tokenizer as HFTokenizer
    except ImportError as e:
        raise SystemExit("pip install tokenizers") from e
    tok = HFTokenizer.from_file(str(path))
    vs = int(tok.get_vocab_size())
    pad_id = 1
    with path.open(encoding="utf-8") as f:
        meta = json.load(f)
    for t in meta.get("added_tokens", []):
        if "PAD" in (t.get("content") or "").upper():
            pad_id = int(t["id"])
            break
    for name in ("<pad>", "<PAD>", "<redacted_PAD>"):
        tid = tok.token_to_id(name)
        if tid is not None:
            pad_id = int(tid)
            break
    return tok, vs, pad_id


def encode_with_tokenizer(tok: object, text: str) -> list[int]:
    return list(tok.encode(text).ids)


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
            raise SystemExit(f"{path}: expected list root or dict with a list field")
    else:
        raise SystemExit(f"{path}: root must be a list or object")
    rows: list[tuple[str, str]] = []
    for obj in seq:
        if isinstance(obj, dict):
            p = _qa_from_obj(obj)
            if p is not None:
                rows.append(p)
    return rows


def _parse_jsonl_lines(raw: str, *, path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}: invalid JSON line {line_no}: {e}") from e
        if isinstance(obj, dict):
            p = _qa_from_obj(obj)
            if p is not None:
                rows.append(p)
    return rows


def load_qa_file(path: Path) -> list[tuple[str, str]]:
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
            if a:
                rows.append((q, a))
            if limit and len(rows) >= limit:
                break
    return rows


def load_csv_pr(path: Path, limit: int) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "prompt" not in r.fieldnames or "response" not in r.fieldnames:
            raise SystemExit(f"CSV needs prompt,response; got {r.fieldnames!r}")
        for i, row in enumerate(tqdm(r, desc="Loading CSV", total=limit or None)):
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
        ids = encode(answer)[:max_seq_len]
        return (ids, 0) if len(ids) >= 2 else None
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
        ids = prompt_ids + resp_ids[:take]
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
        return torch.tensor(ids, dtype=torch.long), len(ids), self.prompt_lens[idx]


def collate_pad(
    batch: list[tuple[torch.Tensor, int, int]], pad_id: int
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    tensors, lens, pls = zip(*batch)
    padded = pad_sequence(list(tensors), batch_first=True, padding_value=pad_id)
    return padded, torch.tensor(lens, dtype=torch.long), torch.tensor(pls, dtype=torch.long)


def train_val_split_n_val(n: int, val_fraction: float, *, min_val: int) -> int:
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
        valid_next = valid_next & (target_idx >= prompt_lens.unsqueeze(1))
    shift_labels = ids[:, 1:].clone()
    shift_labels[~valid_next] = -100
    loss = F.cross_entropy(
        shift_logits,
        shift_labels.contiguous().view(-1),
        ignore_index=-100,
        label_smoothing=label_smoothing,
    )
    return loss if torch.isfinite(loss) else logits.sum() * 0.0


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
    b, t_max = ids.shape
    total = ids.new_zeros((), dtype=torch.float32)
    n_terms = 0
    running = ids.detach().clone()
    for s in range(1, t_max):
        if not (lengths > s).any():
            break
        prefix = running[:, :s].contiguous()
        attn = (torch.arange(s, device=device).unsqueeze(0) < lengths.unsqueeze(1))[:, :s]
        with torch.autocast(device_type=device.type, dtype=torch.float16, enabled=use_amp):
            logits_s = model(prefix, attention_mask=attn)[:, -1, :]
        tgt = ids[:, s]
        train_here = (s >= prompt_lens) if train_on == "prompt_response" else torch.ones(b, dtype=torch.bool, device=device)
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
    p = argparse.ArgumentParser(description="Standalone causal LM training (model + loop in this file).")
    p.add_argument("--tokenizer-json", type=Path, default=_DEFAULT_TOKENIZER)
    p.add_argument("--data-jsonl", "--data-json", type=Path, default=_DEFAULT_QA_PATH, dest="qa_path")
    p.add_argument("--train-jsonl", type=Path, default=None)
    p.add_argument("--csv", type=Path, default=None)
    p.add_argument("--max-seq-len", type=int, default=512)
    p.add_argument("--train-on", choices=("prompt_response", "response"), default="prompt_response")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--val-fraction", type=float, default=0.05)
    p.add_argument("--min-val-samples", type=int, default=4)
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--grad-accum", type=int, default=1)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--label-smoothing", type=float, default=0.0)
    p.add_argument("--max-grad-norm", type=float, default=1.0)
    p.add_argument("--early-stopping-patience", type=int, default=0)
    p.add_argument("--early-stopping-min-delta", type=float, default=0.0)
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--n-layers", type=int, default=8)
    p.add_argument("--n-heads", type=int, default=8)
    p.add_argument("--dim", type=int, default=384)
    p.add_argument("--mlp-hidden", type=int, default=1536)
    p.add_argument("--scheduled-sampling-p", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--amp", action="store_true")
    p.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = p.parse_args()

    if args.dim % args.n_heads != 0:
        raise SystemExit(f"--dim must be divisible by --n-heads")

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
            raise SystemExit(f"QA file not found: {qa}")
        rows = load_qa_file(qa)
        if args.limit:
            rows = rows[: args.limit]

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    sequences: list[list[int]] = []
    prompt_lens: list[int] = []
    for question, answer in tqdm(rows, desc="Tokenizing"):
        enc = encode_qa(encode, question, answer, max_seq_len=args.max_seq_len, train_on=args.train_on)
        if enc is None:
            continue
        ids, pl = enc
        if max(ids) >= vocab_size:
            continue
        sequences.append(ids)
        prompt_lens.append(pl)

    if len(sequences) < 2:
        raise SystemExit("Need at least 2 sequences after tokenization.")

    combined = list(zip(sequences, prompt_lens))
    random.shuffle(combined)
    sequences = [c[0] for c in combined]
    prompt_lens = [c[1] for c in combined]

    n_val = train_val_split_n_val(len(sequences), args.val_fraction, min_val=max(1, args.min_val_samples))
    val_seq, val_pl = sequences[:n_val], prompt_lens[:n_val]
    train_seq, train_pl = sequences[n_val:], prompt_lens[n_val:]
    if not train_seq:
        train_seq, train_pl = sequences, prompt_lens
        val_seq, val_pl = sequences[:1], prompt_lens[:1]

    print(
        f"Data: {len(train_seq)} train / {len(val_seq)} val sequences. "
        "Tiny data overfits fast - try smaller --dim/--n-layers, higher --dropout, --label-smoothing 0.05.",
        flush=True,
    )
    if len(train_seq) < 200:
        print(
            "Warning: very few training sequences; val CE needs enough val examples to be meaningful.",
            flush=True,
        )

    def collate(batch: list[tuple[torch.Tensor, int, int]]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return collate_pad(batch, pad_id)

    train_loader = DataLoader(
        TokenIdsDataset(train_seq, train_pl, pad_id),
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate,
        num_workers=0,
        pin_memory=not args.cpu,
    )
    val_loader = DataLoader(
        TokenIdsDataset(val_seq, val_pl, pad_id),
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

    def compute_loss(ids: torch.Tensor, lengths: torch.Tensor, pls: torch.Tensor, *, training: bool) -> torch.Tensor:
        ids = ids.to(device)
        lengths = lengths.to(device)
        pls = pls.to(device)
        if training and args.scheduled_sampling_p > 0.0:
            return loss_scheduled_sampling(
                model, ids, lengths, pls, train_on=args.train_on, use_amp=use_amp, device=device, sample_p=args.scheduled_sampling_p
            )
        return loss_teacher_forced(
            model,
            ids,
            lengths,
            pls,
            vocab_size=vocab_size,
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
        total_loss, n_batches = 0.0, 0
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
        val_loss, val_n = 0.0, 0
        with torch.no_grad():
            for ids, lengths, pls in val_loader:
                val_loss += float(compute_loss(ids, lengths, pls, training=False))
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
            torch.save(
                {
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
                        "architecture": "causal_transformer_lm_standalone",
                    },
                    "best_val_ce": best_val,
                    "epochs_run": epoch + 1,
                },
                out_path,
            )
            print(f"Saved {out_path}", flush=True)

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
