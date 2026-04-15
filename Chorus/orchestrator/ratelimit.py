"""Simple in-memory token-bucket rate limiter keyed by client IP."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import anyio
import sniffio


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucket:
    """Per-IP bucket: 10 tokens max, refill at 10/60 tokens/sec."""

    def __init__(self, capacity: float = 10.0, refill_per_sec: float = 10.0 / 60.0) -> None:
        self.capacity = capacity
        self.refill = refill_per_sec
        self._buckets: dict[str, _Bucket] = {}
        self._locks: dict[str, anyio.Lock] = {}

    def _get_lock(self) -> anyio.Lock:
        try:
            backend = sniffio.current_async_library()
        except sniffio.AsyncLibraryNotFoundError:
            backend = "asyncio"
        lock = self._locks.get(backend)
        if lock is None:
            lock = anyio.Lock()
            self._locks[backend] = lock
        return lock

    async def consume(self, key: str, cost: float = 1.0) -> float | None:
        """Return None if allowed, else retry_after seconds."""
        now = time.monotonic()
        async with self._get_lock():
            b = self._buckets.get(key)
            if b is None:
                b = _Bucket(tokens=self.capacity, updated_at=now)
                self._buckets[key] = b
            elapsed = max(0.0, now - b.updated_at)
            b.tokens = min(self.capacity, b.tokens + elapsed * self.refill)
            b.updated_at = now
            if b.tokens >= cost:
                b.tokens -= cost
                return None
            deficit = cost - b.tokens
            return max(0.001, deficit / self.refill) if self.refill > 0 else 60.0


_JOBS_BUCKET = TokenBucket()
_BYPASS_ENV = "ORC_RATELIMIT_BYPASS"


async def check_rate_limit(client_ip: str) -> float | None:
    """Return retry_after seconds if rate-limited, else None."""
    if os.getenv(_BYPASS_ENV, "").strip() == "1":
        return None
    key = client_ip or "unknown"
    return await _JOBS_BUCKET.consume(key)
