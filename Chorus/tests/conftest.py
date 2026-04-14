"""Pytest hooks: default orchestrator tests to fast hash embeddings (no MiniLM download)."""

from __future__ import annotations

import os

# Before importing `orchestrator`, keep CI fast and deterministic.
os.environ.setdefault("ORC_EMBEDDING_BACKEND", "hash")
