"""Shared parts of the P5 batch write path measurements (REPORT.md).

Builds on the P2 harness (``research/write-rate-2026-11/common.py``): the same observations, the same engine
(product defaults, classic mode), the same guard that fails any model call, and the same stage timers and SQL
counter, plus timers on the parts the batch write path adds.

Three write paths are named here:

- ``current``: the product defaults.
- ``prototype``: P2's filtered entity lookup alone (``enable_filtered_entity_lookup``), the path behind P2's
  3.49 facts a second at 20,000 stored.
- ``batch``: ``enable_batch_write_path``, the four changes together.
"""
from __future__ import annotations

import functools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "write-rate-2026-11"))
import common  # noqa: E402

SCRATCH = "/tmp/p5"
PATHS = {"current": {}, "prototype": dict(common.PROTOTYPE), "batch": {"enable_batch_write_path": True}}


def instrument() -> None:
    """P2's timers, plus the batch path's parts. ``_flush`` is timed once, at the outermost call.

    With the batch path on, ``_flush`` opens its batch and calls itself once; without this, the flush's time and
    calls would be counted twice.
    """
    common.instrument()
    from prme.ingestion import pipeline, planning
    from prme.privacy.admission import AdmissionCoordinator
    from prme.storage import lexical_index
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.privacy import PrivacyRepository

    timed_flush = pipeline.IngestionPipeline._flush
    plain_flush = timed_flush.__wrapped__

    @functools.wraps(plain_flush)
    async def flush(self, *args, **kwargs):
        if "IngestionPipeline._flush" in common.STACK.get():
            return await plain_flush(self, *args, **kwargs)
        return await timed_flush(self, *args, **kwargs)

    pipeline.IngestionPipeline._flush = flush

    real_deferred = lexical_index.LexicalIndex._do_stage_deferred

    @functools.wraps(real_deferred)
    def stage_deferred(self, documents):
        common.COUNTS["lexical.stage_documents"] += len(documents)
        return real_deferred(self, documents)

    lexical_index.LexicalIndex._do_stage_deferred = stage_deferred
    common.wrap(lexical_index.LexicalIndex, "_do_stage_deferred")
    common.wrap(lexical_index.LexicalIndex, "commit_deferred")
    common.wrap(pipeline.IngestionPipeline, "_finish_batch")
    common.wrap(planning.PlanningGraph, "claim_targets")
    common.wrap(DuckPGQGraphStore, "fact_targets_by_key")
    common.wrap(AdmissionCoordinator, "require_legacy_page")
    common.wrap(PrivacyRepository, "legacy_page")
    common.wrap(PrivacyRepository, "records_in_page")


def quantiles(values: list[float]) -> dict:
    import statistics

    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 3)

    return {"count": len(ordered), "p50": at(0.5), "p95": at(0.95), "max": round(ordered[-1], 3),
            "mean": round(statistics.fmean(ordered), 3)}
