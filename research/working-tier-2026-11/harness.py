"""P6 shared parts: the P2 harness, the tier, and the settings each side is built with.

The world, the engine, the guard that fails any model call, the stage timers and the SQL counter all come from P2's
``common.py`` unchanged. The only addition is the working tier's own timers.
"""
from __future__ import annotations

import functools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

SCRATCH = "/tmp/p6"
USER = common.USER
# The current path's settings, and the tier's (a tier runs the engine's defaults; it adds no setting of its own).
PATHS = {"current": {}, "working": {}}


def instrument() -> None:
    """P2's timers, plus the tier's accept, drain and lookup."""
    common.instrument()
    import tier

    for name in ("accept", "drain_once", "lookup", "replay", "retrieve_with_working"):
        common.wrap(tier.WorkingTier, name, label=f"WorkingTier.{name}")


def quantiles(values: list[float]) -> dict:
    import statistics

    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 3)

    return {"count": len(ordered), "p50": at(0.5), "p95": at(0.95), "p99": at(0.99),
            "max": round(ordered[-1], 3), "mean": round(statistics.fmean(ordered), 3)}
