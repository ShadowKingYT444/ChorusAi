"""Pytest hooks: default orchestrator tests to fast hash embeddings (no MiniLM download)."""

from __future__ import annotations

import os

# Before importing `orchestrator`, keep CI fast and deterministic.
os.environ.setdefault("ORC_EMBEDDING_BACKEND", "hash")
# Bypass POST /jobs rate limiter during tests that spam job creation.
os.environ.setdefault("ORC_RATELIMIT_BYPASS", "1")
os.environ.setdefault("ORC_REQUIRE_WORKSPACE_AUTH", "1")
os.environ.setdefault("ORC_BOOTSTRAP_WORKSPACE_ID", "local-dev")
os.environ.setdefault("ORC_BOOTSTRAP_TOKEN", "chorus-local-dev-token")
