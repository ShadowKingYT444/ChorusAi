"""Pytest hooks: default orchestrator tests to fast hash embeddings (no MiniLM download)."""

from __future__ import annotations

import os

# Before importing `orchestrator`, keep CI fast and deterministic.
os.environ.setdefault("ORC_EMBEDDING_BACKEND", "hash")
# Bypass POST /jobs rate limiter during tests that spam job creation.
os.environ.setdefault("ORC_RATELIMIT_BYPASS", "1")
