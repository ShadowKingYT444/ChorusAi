"""
Prefix text → 384-d vector for ``AutoregPredictor``.

- **sentence**: SentenceTransformer pooled sentence embedding (original).
- **token_last**: last-layer hidden state at the **last real token** (contextual token
  representation of the full prefix), L2-normalized - *not* one vector per BPE target id;
  the backbone uses its own (WordPiece) tokenization of the prefix string.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F


def last_nonpad_hidden(
    last_hidden: torch.Tensor,
    attention_mask: torch.Tensor,
) -> torch.Tensor:
    """(B, L, H) + (B, L) mask → (B, H) at last non-padding index per row."""
    lengths = attention_mask.sum(dim=1).long() - 1
    lengths = lengths.clamp(min=0)
    b = torch.arange(last_hidden.size(0), device=last_hidden.device, dtype=torch.long)
    return last_hidden[b, lengths]


def encode_prefixes_sentence(
    embedder,
    texts: list[str],
    *,
    batch_size: int,
) -> list[list[float]]:
    """SentenceTransformer batched encode → list of float lists (dim = model output)."""
    try:
        embs = embedder.encode(
            texts,
            normalize_embeddings=True,
            batch_size=batch_size,
            show_progress_bar=False,
        )
    except TypeError:
        embs = embedder.encode(texts, normalize_embeddings=True, batch_size=batch_size)
    out: list[list[float]] = []
    for j in range(len(texts)):
        row = embs[j]
        if hasattr(row, "tolist"):
            row = row.tolist()
        else:
            row = list(row)
        out.append(row)
    return out


def encode_prefixes_token_last(
    texts: list[str],
    *,
    model_id: str,
    device: torch.device,
    batch_size: int,
    max_length: int = 512,
    normalize: bool = True,
) -> list[list[float]]:
    """
    Transformer last hidden state at the last non-pad token (prefix context).

    Output dimension equals the backbone ``hidden_size`` (e.g. 384). If ``AutoregPredictor``
    uses a smaller ``dim``, apply ``encoder_proj`` in the model.
    """
    from transformers import AutoModel, AutoTokenizer

    hf_tok = AutoTokenizer.from_pretrained(model_id)
    model = AutoModel.from_pretrained(model_id).to(device).eval()

    out: list[list[float]] = []
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            inputs = hf_tok(
                chunk,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            ).to(device)
            h = model(**inputs).last_hidden_state
            vecs = last_nonpad_hidden(h, inputs["attention_mask"])
            if normalize:
                vecs = F.normalize(vecs, dim=-1, p=2, eps=1e-12)
            for row in range(vecs.size(0)):
                out.append(vecs[row].cpu().float().tolist())
    return out


class SentencePrefixEncoder:
    """One pooled sentence embedding per prefix (matches original training)."""

    def __init__(self, model_id: str, device: torch.device) -> None:
        from sentence_transformers import SentenceTransformer

        self._st = SentenceTransformer(model_id)
        self.device = device

    def encode(self, prefix: str) -> torch.Tensor:
        """(1, D) float32 on ``self.device``."""
        emb = self._st.encode(prefix, normalize_embeddings=True)
        t = torch.as_tensor(emb, dtype=torch.float32)
        if t.dim() == 1:
            t = t.unsqueeze(0)
        return t.to(self.device)


class TokenLastPrefixEncoder:
    """Last transformer layer, last non-pad token - contextual embedding of the prefix."""

    def __init__(self, model_id: str, device: torch.device) -> None:
        from transformers import AutoModel, AutoTokenizer

        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModel.from_pretrained(model_id).to(device).eval()
        self.device = device

    def encode(self, prefix: str) -> torch.Tensor:
        """(1, D) float32 on ``self.device``."""
        with torch.no_grad():
            inputs = self.tok(
                prefix,
                return_tensors="pt",
                truncation=True,
                max_length=512,
            ).to(self.device)
            h = self.model(**inputs).last_hidden_state
            v = last_nonpad_hidden(h, inputs["attention_mask"])
            v = F.normalize(v, dim=-1, p=2, eps=1e-12)
        return v.float()


def make_prefix_encoder(
    mode: str,
    model_id: str,
    device: torch.device,
) -> SentencePrefixEncoder | TokenLastPrefixEncoder:
    if mode == "sentence":
        return SentencePrefixEncoder(model_id, device)
    if mode == "token_last":
        return TokenLastPrefixEncoder(model_id, device)
    raise ValueError(f"Unknown embed_mode: {mode!r}")
