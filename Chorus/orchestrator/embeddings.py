from __future__ import annotations

import hashlib
import math
import os
import threading
from typing import Sequence

import numpy as np

# all-MiniLM-L6-v2 produces 384-dim L2-normalized vectors when normalize_embeddings=True.
_DEFAULT_MINILM = "sentence-transformers/all-MiniLM-L6-v2"
_MINILM_DIM = 384


class EmbeddingService:
    """
    Embeddings for kNN / impact.

    - **minilm** (default): `sentence-transformers` + `all-MiniLM-L6-v2` (override with `ORC_MINILM_MODEL`).
    - **hash**: deterministic vectors (no ML deps); default dim **384** (same as MiniLM) — override with `ORC_HASH_EMBED_DIM`.
    """

    def __init__(self, dimension: int | None = None) -> None:
        self.backend = os.getenv("ORC_EMBEDDING_BACKEND", "minilm").strip().lower()
        self.model_name = os.getenv("ORC_MINILM_MODEL", _DEFAULT_MINILM).strip()
        self._model = None
        self._lock = threading.Lock()
        # hash mode dimension (minilm uses _MINILM_DIM)
        self._hash_dim = dimension if dimension is not None else int(os.getenv("ORC_HASH_EMBED_DIM", "384"))

    @property
    def embedding_dim(self) -> int:
        return self._hash_dim if self.backend == "hash" else _MINILM_DIM

    def _ensure_minilm(self) -> None:
        if self.backend != "minilm":
            return
        if self._model is not None:
            return
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name)

    def _hash_embed(self, text: str, dim: int) -> list[float]:
        digest = hashlib.shake_256(text.encode("utf-8")).digest(dim * 4)
        values: list[float] = []
        for i in range(dim):
            start = i * 4
            raw = int.from_bytes(digest[start : start + 4], byteorder="big", signed=False)
            values.append((raw / 4294967295.0) * 2.0 - 1.0)
        return self._normalize(values)

    def embed(self, text: str) -> list[float]:
        """Encode a single string (blocking). Prefer `embed_batch` inside async code."""
        stripped = text.strip()
        if not stripped:
            return [0.0] * self.embedding_dim
        if self.backend == "hash":
            return self._hash_embed(stripped, self._hash_dim)
        self._ensure_minilm()
        assert self._model is not None
        vec = self._model.encode(
            stripped,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        arr = np.asarray(vec, dtype=np.float64)
        if arr.ndim == 1:
            return arr.tolist()
        return arr[0].tolist()

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode many strings in one model forward where possible (blocking)."""
        cleaned = [t.strip() if t else "" for t in texts]
        if not cleaned:
            return []
        dim = self.embedding_dim
        if self.backend == "hash":
            return [self._hash_embed(t, self._hash_dim) if t else [0.0] * dim for t in cleaned]

        self._ensure_minilm()
        assert self._model is not None
        # Replace empties with a single space so the model gets a defined input; zero out after.
        feed = [t if t else " " for t in cleaned]
        mat = self._model.encode(
            list(feed),
            batch_size=int(os.getenv("ORC_EMBED_BATCH_SIZE", "32")),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        arr = np.asarray(mat, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        out: list[list[float]] = []
        for i, t in enumerate(cleaned):
            if not t:
                out.append([0.0] * dim)
            else:
                out.append(arr[i].tolist())
        return out

    @staticmethod
    def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
        """Cosine similarity in [-1, 1] (0 if either vector has zero norm)."""
        dot = sum(float(x) * float(y) for x, y in zip(a, b))
        a_norm = math.sqrt(sum(float(x) * float(x) for x in a))
        b_norm = math.sqrt(sum(float(y) * float(y) for y in b))
        if a_norm == 0.0 or b_norm == 0.0:
            return 0.0
        v = dot / (a_norm * b_norm)
        return max(-1.0, min(1.0, v))

    @staticmethod
    def cosine_distance(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        a_norm = math.sqrt(sum(x * x for x in a))
        b_norm = math.sqrt(sum(y * y for y in b))
        if a_norm == 0 or b_norm == 0:
            return 1.0
        cosine_sim = dot / (a_norm * b_norm)
        return 1.0 - cosine_sim

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:
            return [0.0 for _ in v]
        return [x / norm for x in v]
