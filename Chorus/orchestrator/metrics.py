"""Lightweight in-memory metrics (dict-backed). No deps. Not thread-safe by
design — orchestrator is single-process asyncio so we just bump counters.
Render as Prometheus text or JSON for a UI tile."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

_HIST_BUCKETS_MS = (100, 500, 1000, 2000, 5000, float("inf"))


class _Histogram:
    __slots__ = ("count", "sum", "buckets")

    def __init__(self) -> None:
        self.count = 0
        self.sum = 0.0
        self.buckets = [0] * len(_HIST_BUCKETS_MS)

    def observe(self, value: float) -> None:
        self.count += 1
        self.sum += value
        for i, upper in enumerate(_HIST_BUCKETS_MS):
            if value <= upper:
                self.buckets[i] += 1
                break

    def avg(self) -> float:
        if self.count == 0:
            return 0.0
        return self.sum / self.count


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, float] = defaultdict(float)
        self.gauges: dict[str, float] = defaultdict(float)
        self.histograms: dict[str, _Histogram] = defaultdict(_Histogram)
        self.started_at = time.time()

    def inc(self, name: str, amount: float = 1.0) -> None:
        self.counters[name] += amount

    def gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        self.histograms[name].observe(value)

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "uptime_seconds": round(time.time() - self.started_at, 3),
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "histograms": {
                name: {"count": h.count, "sum": h.sum, "avg_ms": round(h.avg(), 2)}
                for name, h in self.histograms.items()
            },
        }
        prune_attempted = self.counters.get("rounds_slots_total", 0)
        prune_hits = self.counters.get("rounds_slots_pruned", 0)
        out["derived"] = {
            "prune_rate": (prune_hits / prune_attempted) if prune_attempted else 0.0,
        }
        return out

    def render_prom(self) -> str:
        lines = [f"# HELP chorus_uptime_seconds Seconds since orchestrator started"]
        lines.append("# TYPE chorus_uptime_seconds gauge")
        lines.append(f"chorus_uptime_seconds {time.time() - self.started_at:.3f}")
        for name, value in self.counters.items():
            lines.append(f"# TYPE chorus_{name} counter")
            lines.append(f"chorus_{name} {value}")
        for name, value in self.gauges.items():
            lines.append(f"# TYPE chorus_{name} gauge")
            lines.append(f"chorus_{name} {value}")
        for name, h in self.histograms.items():
            lines.append(f"# TYPE chorus_{name} histogram")
            cum = 0
            for i, upper in enumerate(_HIST_BUCKETS_MS):
                cum += h.buckets[i]
                le = "+Inf" if upper == float("inf") else str(int(upper))
                lines.append(f'chorus_{name}_bucket{{le="{le}"}} {cum}')
            lines.append(f"chorus_{name}_sum {h.sum}")
            lines.append(f"chorus_{name}_count {h.count}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()
