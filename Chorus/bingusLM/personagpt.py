#!/usr/bin/env python3
"""
Fine-tune GPT-2 on google/Synthetic-Persona-Chat with a persona-conditioned head.

**Training objective:** next-token CE on **every** response token. For each position ``k``,
context is ``prompt_ids + response_ids[:k]``; the target is ``response_ids[k]``. This matches
autoregressive generation (predict next token from prefix). Older versions trained only the
**last** response token, which made val loss misleading and sampling garbage.

Architecture:
  - GPT-2 small backbone (frozen or trainable)
  - MiniLM encodes the persona string → 384-d vector
  - Last hidden state of GPT-2 (768-d) × MiniLM persona (384-d) → pairwise outer product → (768, 384)
  - 5× PersonaBlock: scale each of 768 columns by a unique scalar, add bias, ReLU
  - Linear(384, vocab_size) → logits

Data: google/Synthetic-Persona-Chat (Hub columns: ``user 1 personas``, ``Best Generated Conversation``;
legacy list columns ``User 1 Persona`` / ``Conversation`` are still supported).

Cache: by default uses ``<repo>/hf_cache`` (writable in Docker). Override with ``HF_CACHE=/path``.

Usage:
  pip install torch transformers datasets sentence-transformers tqdm

  # Full fine-tune (both GPT-2 and head):
  python scripts/train_persona_gpt2.py

  # Freeze GPT-2, only train persona head:
  python scripts/train_persona_gpt2.py --freeze-gpt2

  # Quick smoke test (100 steps):
  python scripts/train_persona_gpt2.py --max-steps 100 --batch-size 2
"""

from __future__ import annotations

import argparse
import bisect
import math
import os
import random
import re
from pathlib import Path
from typing import Any, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import amp
from torch.utils.data import DataLoader, Dataset

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **kw):
        return x

from transformers import GPT2Model, GPT2Tokenizer
from sentence_transformers import SentenceTransformer
from datasets import load_dataset


# ---------------------------------------------------------------------------
# Hugging Face cache (writable; avoids read-only /root/.cache in containers)
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1] if here.parent.name == "scripts" else here.parent


def setup_hf_cache() -> Path:
    """
    Point HF_HOME / datasets / hub caches at ``hf_cache`` under repo root by default.
    Set ``HF_CACHE=/workspace/hf_cache`` (or any writable path) to override.
    """
    raw = os.environ.get("HF_CACHE", "").strip()
    if raw:
        cache = Path(raw).expanduser().resolve()
    else:
        cache = (_repo_root() / "hf_cache").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache)
    os.environ["HF_DATASETS_CACHE"] = str(cache / "datasets")
    os.environ["HF_HUB_CACHE"] = str(cache / "hub")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")
    # Transformers / hub use HF_HOME/hub; explicit hub cache helps older stacks
    os.environ.setdefault("TRANSFORMERS_CACHE", str(cache / "transformers"))
    print(f"HF cache: {cache}", flush=True)
    return cache


# ---------------------------------------------------------------------------
# PersonaBlock: pairwise product → per-column scale + bias → ReLU
# ---------------------------------------------------------------------------


class PersonaBlock(nn.Module):
    """
    Input:  P  (B, 768, 384)  — pairwise product matrix
    Output: P' (B, 768, 384)

    For each of the 768 "rows" (GPT-2 dims), multiply every element in that
    row by a learned scalar, add a learned bias, then ReLU.
    """

    def __init__(self, gpt_dim: int = 768, minilm_dim: int = 384) -> None:
        super().__init__()
        self.scale = nn.Parameter(torch.ones(gpt_dim))
        self.bias = nn.Parameter(torch.zeros(gpt_dim))

    def forward(self, P: torch.Tensor) -> torch.Tensor:
        s = self.scale.view(1, -1, 1)
        b = self.bias.view(1, -1, 1)
        return F.relu(P * s + b)


class PersonaHead(nn.Module):
    def __init__(
        self,
        gpt_dim: int = 768,
        minilm_dim: int = 384,
        num_blocks: int = 5,
        vocab_size: int = 50257,
    ) -> None:
        super().__init__()
        self.gpt_dim = gpt_dim
        self.minilm_dim = minilm_dim

        self.blocks = nn.ModuleList(
            [PersonaBlock(gpt_dim, minilm_dim) for _ in range(num_blocks)]
        )
        self.lm_head = nn.Linear(minilm_dim, vocab_size, bias=False)

    def forward(self, gpt_h: torch.Tensor, persona_e: torch.Tensor) -> torch.Tensor:
        P = gpt_h.unsqueeze(2) * persona_e.unsqueeze(1)
        for block in self.blocks:
            P = block(P)
        pooled = P.mean(dim=1)
        return self.lm_head(pooled)


class PersonaGPT2(nn.Module):
    def __init__(
        self,
        gpt2_name: str = "gpt2",
        minilm_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        num_blocks: int = 5,
        freeze_gpt2: bool = False,
        hf_cache: Optional[Path] = None,
    ) -> None:
        super().__init__()

        cache_kw = {}
        if hf_cache is not None:
            cache_kw["cache_dir"] = str(hf_cache / "transformers")

        self.gpt2 = GPT2Model.from_pretrained(gpt2_name, **cache_kw)
        self.gpt2_config = self.gpt2.config
        gpt_dim = self.gpt2_config.n_embd

        if freeze_gpt2:
            for p in self.gpt2.parameters():
                p.requires_grad = False

        st_kw: dict = {}
        if hf_cache is not None:
            st_kw["cache_folder"] = str(hf_cache)
        self.minilm = SentenceTransformer(minilm_name, **st_kw)
        for p in self.minilm.parameters():
            p.requires_grad = False

        st = self.minilm
        minilm_dim = (
            st.get_embedding_dimension()
            if hasattr(st, "get_embedding_dimension")
            else st.get_sentence_embedding_dimension()
        )

        self.head = PersonaHead(
            gpt_dim=gpt_dim,
            minilm_dim=minilm_dim,
            num_blocks=num_blocks,
            vocab_size=self.gpt2_config.vocab_size,
        )

    def encode_personas(self, persona_texts: list[str], device: torch.device) -> torch.Tensor:
        # MiniLM stays frozen; embeddings must not be inference tensors or autograd
        # errors when they are multiplied with GPT-2 hidden states in the head.
        with torch.no_grad():
            embs = self.minilm.encode(
                persona_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_tensor=True,
            )
            embs = embs.to(device)
        return embs.clone()

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        persona_emb: torch.Tensor,
    ) -> torch.Tensor:
        outputs = self.gpt2(
            input_ids=input_ids,
            attention_mask=attention_mask,
        )
        last_hidden = outputs.last_hidden_state
        lengths = attention_mask.sum(dim=1).long() - 1
        batch_idx = torch.arange(last_hidden.size(0), device=last_hidden.device)
        gpt_h = last_hidden[batch_idx, lengths]
        return self.head(gpt_h, persona_emb)


def build_persona_string(persona_list: list[str]) -> str:
    return " ".join(persona_list) if persona_list else ""


def _norm_key(s: str) -> str:
    return str(s).lower().replace(" ", "").replace("_", "")


def _row_get(row: dict[str, Any], *names: str) -> Any:
    """Case/spacing-tolerant lookup for HF dataset rows."""
    for n in names:
        if n in row:
            return row[n]
    want = {_norm_key(n) for n in names}
    for k, v in row.items():
        if _norm_key(k) in want:
            return v
    return None


def extract_last_user_turn(conversation: list[str]) -> tuple[str, str]:
    if len(conversation) < 2:
        return "", conversation[0] if conversation else ""

    user2_turns = [(i, t) for i, t in enumerate(conversation) if i % 2 == 1]
    if not user2_turns:
        return "", ""

    last_idx, response = user2_turns[-1]
    prompt = " ".join(conversation[:last_idx])
    return prompt, response


_USER2_SPLIT = re.compile(r"\s*User\s*2\s*:", re.IGNORECASE)


def extract_last_user2_from_flat_text(text: str) -> tuple[str, str]:
    """
    ``Best Generated Conversation`` is one string with ``User 1:`` / ``User 2:`` turns.
    Prompt = text before the last ``User 2:``; response = text after it.
    """
    if not text or not str(text).strip():
        return "", ""
    text = str(text).strip()
    matches = list(_USER2_SPLIT.finditer(text))
    if not matches:
        mid = max(1, len(text) // 2)
        return text[:mid].strip(), text[mid:].strip()
    last = matches[-1]
    prompt = text[: last.start()].strip()
    response = text[last.end() :].strip()
    return prompt, response


def persona_string_from_row(row: dict[str, Any]) -> str:
    raw = _row_get(
        row,
        "user 1 personas",
        "User 1 Persona",
        "user 1 persona",
        "User 1 Personas",
    )
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (list, tuple)):
        return build_persona_string([str(x) for x in raw])
    return str(raw).strip()


class PersonaChatDataset(Dataset):
    def __init__(
        self,
        split: str,
        tokenizer: GPT2Tokenizer,
        max_length: int = 256,
        max_examples: int = 0,
        datasets_cache: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.tokenizer = tokenizer
        self.max_length = max_length

        if datasets_cache is not None:
            raw = load_dataset(
                "google/Synthetic-Persona-Chat",
                split=split,
                cache_dir=str(datasets_cache),
            )
        else:
            raw = load_dataset("google/Synthetic-Persona-Chat", split=split)

        self._personas: list[str] = []
        self._prompt_ids: list[list[int]] = []
        self._response_ids: list[list[int]] = []

        for row in raw:
            persona_str = persona_string_from_row(row)

            best = _row_get(row, "Best Generated Conversation", "best generated conversation")
            legacy_conv = _row_get(row, "Conversation", "conversation")

            if best is not None and str(best).strip():
                prompt, response = extract_last_user2_from_flat_text(str(best))
            elif legacy_conv:
                conv = legacy_conv if isinstance(legacy_conv, list) else [str(legacy_conv)]
                prompt, response = extract_last_user_turn(conv)
            else:
                continue

            if not persona_str.strip():
                persona_str = prompt[:512] if prompt else "synthetic persona"

            if not response.strip():
                continue

            prompt_ids = (
                self.tokenizer.encode(prompt, add_special_tokens=False) if prompt else []
            )
            response_ids = self.tokenizer.encode(response, add_special_tokens=True)
            if not response_ids:
                continue

            self._personas.append(persona_str)
            self._prompt_ids.append(prompt_ids)
            self._response_ids.append(response_ids)

            if max_examples and len(self._personas) >= max_examples:
                break

        self._cum: list[int] = [0]
        for r in self._response_ids:
            self._cum.append(self._cum[-1] + len(r))

    @property
    def num_conversations(self) -> int:
        """HF rows kept (one persona+dialogue example each)."""
        return len(self._personas)

    def __len__(self) -> int:
        """Next-token supervision positions (one per response token)."""
        return self._cum[-1]

    def __getitem__(self, idx: int) -> dict:
        if idx < 0 or idx >= len(self):
            raise IndexError(idx)
        ex_i = bisect.bisect_right(self._cum, idx) - 1
        k = idx - self._cum[ex_i]
        persona = self._personas[ex_i]
        prompt_ids = self._prompt_ids[ex_i]
        response_ids = self._response_ids[ex_i]
        # Predict response_ids[k] from context prompt + first k response tokens.
        input_ids = (prompt_ids + response_ids[:k])[-self.max_length :]
        target_id = response_ids[k]

        return {
            "persona": persona,
            "input_ids": input_ids,
            "target_id": target_id,
        }


def collate_fn(batch: list[dict], pad_token_id: int) -> dict:
    personas = [b["persona"] for b in batch]
    target_ids = torch.tensor([b["target_id"] for b in batch], dtype=torch.long)

    max_len = max(len(b["input_ids"]) for b in batch)
    input_ids = torch.zeros(len(batch), max_len, dtype=torch.long)
    attention_mask = torch.zeros(len(batch), max_len, dtype=torch.long)

    for i, b in enumerate(batch):
        ids = b["input_ids"]
        L = len(ids)
        input_ids[i, -L:] = torch.tensor(ids, dtype=torch.long)
        attention_mask[i, -L:] = 1

    return {
        "personas": personas,
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "target_ids": target_ids,
    }


def train(args: argparse.Namespace, hf_cache: Path) -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    tok_kw = {}
    if hf_cache is not None:
        tok_kw["cache_dir"] = str(hf_cache / "transformers")

    tokenizer = GPT2Tokenizer.from_pretrained(args.gpt2, **tok_kw)
    tokenizer.pad_token = tokenizer.eos_token

    datasets_cache = hf_cache / "datasets" if hf_cache else None

    print("Loading dataset...")
    train_ds = PersonaChatDataset(
        "train", tokenizer, max_length=args.max_length,
        max_examples=args.max_examples,
        datasets_cache=datasets_cache,
    )
    val_ds = PersonaChatDataset(
        args.val_split, tokenizer, max_length=args.max_length,
        max_examples=max(0, args.max_examples // 5) if args.max_examples else 0,
        datasets_cache=datasets_cache,
    )
    npos_tr, nconv_tr = len(train_ds), train_ds.num_conversations
    npos_va, nconv_va = len(val_ds), val_ds.num_conversations
    avg_tok = npos_tr / max(nconv_tr, 1)
    batches = (npos_tr + args.batch_size - 1) // max(args.batch_size, 1)
    print(
        f"Train: {npos_tr:,} next-token positions from {nconv_tr:,} conversations "
        f"(~{avg_tok:.1f} response tokens / conv); "
        f"~{batches:,} batches/epoch @ batch {args.batch_size}"
    )
    print(
        f"Val:   {npos_va:,} positions from {nconv_va:,} conversations "
        f"(split={args.val_split!r})"
    )

    if len(train_ds) == 0:
        peek = load_dataset(
            "google/Synthetic-Persona-Chat",
            split="train",
            cache_dir=str(datasets_cache) if datasets_cache else None,
        )
        row0 = peek[0]
        raise SystemExit(
            "No training examples parsed from google/Synthetic-Persona-Chat. "
            f"First row keys: {sorted(row0.keys())}. "
            "Expected string columns like 'user 1 personas' and 'Best Generated Conversation'."
        )

    _collate = lambda b: collate_fn(b, tokenizer.pad_token_id)
    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        collate_fn=_collate, num_workers=args.num_workers, pin_memory=(device.type == "cuda"),
    )
    val_loader = None
    if len(val_ds) > 0:
        val_loader = DataLoader(
            val_ds, batch_size=args.batch_size, shuffle=False,
            collate_fn=_collate, num_workers=args.num_workers,
        )
    else:
        print(
            f"Warning: val split {args.val_split!r} produced 0 examples; skipping validation.",
            flush=True,
        )

    print("Building model...")
    model = PersonaGPT2(
        gpt2_name=args.gpt2,
        minilm_name=args.minilm,
        num_blocks=args.num_blocks,
        freeze_gpt2=args.freeze_gpt2,
        hf_cache=hf_cache,
    ).to(device)

    n_total = sum(p.numel() for p in model.parameters() if p.requires_grad)
    n_head = sum(p.numel() for p in model.head.parameters())
    print(f"Trainable params: {n_total:,}  (head: {n_head:,})")

    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=args.lr,
        weight_decay=args.weight_decay,
    )

    total_steps = min(args.max_steps, len(train_loader) * args.epochs) if args.max_steps else len(train_loader) * args.epochs
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=max(total_steps, 1), eta_min=args.lr * 0.1,
    )

    use_amp = device.type == "cuda" and args.amp
    amp_device = device.type if device.type in ("cuda", "cpu") else "cpu"
    scaler = amp.GradScaler(amp_device, enabled=use_amp)
    ce = nn.CrossEntropyLoss()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    global_step = 0
    best_val = float("inf")

    for epoch in range(args.epochs):
        model.train()
        total_loss = 0.0
        n_seen = 0

        pbar = tqdm(train_loader, desc=f"epoch {epoch+1}/{args.epochs}")
        for batch in pbar:
            if args.max_steps and global_step >= args.max_steps:
                break

            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            target_ids = batch["target_ids"].to(device)
            personas = batch["personas"]

            persona_emb = model.encode_personas(personas, device)

            optimizer.zero_grad()
            with amp.autocast(device_type=amp_device, enabled=use_amp):
                logits = model(input_ids, attention_mask, persona_emb)
                loss = ce(logits, target_ids)

            scaler.scale(loss).backward()

            if args.grad_clip > 0:
                scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(model.parameters(), args.grad_clip)

            scaler.step(optimizer)
            scaler.update()
            scheduler.step()

            li = loss.item()
            total_loss += li * input_ids.size(0)
            n_seen += input_ids.size(0)
            global_step += 1

            pbar.set_postfix(
                loss=f"{li:.4f}",
                avg=f"{total_loss/n_seen:.4f}",
                lr=f"{optimizer.param_groups[0]['lr']:.2e}",
                step=global_step,
            )

        train_loss = total_loss / max(n_seen, 1)

        if val_loader is not None:
            model.eval()
            vtotal, vn = 0.0, 0
            with torch.no_grad():
                for batch in tqdm(val_loader, desc="val", leave=False):
                    input_ids = batch["input_ids"].to(device)
                    attention_mask = batch["attention_mask"].to(device)
                    target_ids = batch["target_ids"].to(device)
                    persona_emb = model.encode_personas(batch["personas"], device)

                    with amp.autocast(device_type=amp_device, enabled=use_amp):
                        logits = model(input_ids, attention_mask, persona_emb)
                        loss = ce(logits, target_ids)

                    vtotal += loss.item() * input_ids.size(0)
                    vn += input_ids.size(0)

            val_loss = vtotal / max(vn, 1)
            print(
                f"epoch {epoch+1}: train={train_loss:.4f} "
                f"val={val_loss:.4f} "
                f"val_ppl={math.exp(val_loss):.1f}"
            )
        else:
            val_loss = float("nan")
            print(f"epoch {epoch+1}: train={train_loss:.4f}  val=n/a")

        should_save = val_loader is None or val_loss < best_val
        if should_save and val_loader is not None:
            best_val = val_loss
        if should_save:
            torch.save({
                "epoch": epoch + 1,
                "global_step": global_step,
                "model_state": model.state_dict(),
                "val_loss": val_loss,
                "args": vars(args),
            }, out_path)
            print(f"  Saved checkpoint → {out_path}")

        if args.max_steps and global_step >= args.max_steps:
            print("Reached --max-steps, stopping.")
            break

    if val_loader is not None:
        print(f"Done. Best val loss: {best_val:.4f}  (ppl {math.exp(best_val):.1f})")
    else:
        print("Done. (No validation split; checkpoint is from the last epoch.)")


@torch.no_grad()
def generate_response(
    model: PersonaGPT2,
    tokenizer: GPT2Tokenizer,
    persona: str,
    prompt: str,
    device: torch.device,
    max_new_tokens: int = 60,
    temperature: float = 0.8,
    top_k: int = 50,
) -> str:
    model.eval()
    persona_emb = model.encode_personas([persona], device)

    input_ids = tokenizer.encode(prompt, return_tensors="pt").to(device)
    generated = input_ids[0].tolist()

    for _ in range(max_new_tokens):
        ids = torch.tensor([generated[-256:]], dtype=torch.long, device=device)
        mask = torch.ones_like(ids)
        logits = model(ids, mask, persona_emb)
        logits = logits / max(temperature, 1e-6)

        if top_k > 0:
            topk_vals, _ = torch.topk(logits, top_k)
            logits[logits < topk_vals[:, -1:]] = float("-inf")

        probs = F.softmax(logits, dim=-1)
        next_id = torch.multinomial(probs, num_samples=1).item()
        generated.append(next_id)

        if next_id == tokenizer.eos_token_id:
            break

    return tokenizer.decode(generated[len(input_ids[0]):], skip_special_tokens=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Fine-tune GPT-2 with persona-conditioned head")
    p.add_argument("--gpt2", default="gpt2",
                   help="GPT-2 variant: gpt2 | gpt2-medium | distilgpt2")
    p.add_argument("--minilm", default="sentence-transformers/all-MiniLM-L6-v2")
    p.add_argument("--num-blocks", type=int, default=5)
    p.add_argument("--freeze-gpt2", action="store_true")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--grad-clip", type=float, default=1.0)
    p.add_argument("--max-length", type=int, default=256)
    p.add_argument("--max-examples", type=int, default=0)
    p.add_argument("--max-steps", type=int, default=0)
    p.add_argument("--num-workers", type=int, default=2)
    p.add_argument("--amp", action="store_true", default=True)
    p.add_argument("--no-amp", dest="amp", action="store_false")
    p.add_argument("--out", default="out/persona_gpt2.pt")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--hf-cache",
        type=Path,
        default=None,
        help="Writable Hugging Face cache dir (default: <repo>/hf_cache or env HF_CACHE)",
    )
    p.add_argument(
        "--val-split",
        default="validation",
        choices=("train", "validation", "test"),
        help="HF split used for validation metrics (default: validation)",
    )

    args = p.parse_args()

    hf_cache = args.hf_cache
    if hf_cache is not None:
        os.environ["HF_CACHE"] = str(hf_cache.expanduser().resolve())
    hf_cache = setup_hf_cache()

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    train(args, hf_cache)


if __name__ == "__main__":
    main()
