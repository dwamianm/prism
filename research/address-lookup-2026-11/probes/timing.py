"""P7 bar (b) and measure 4: the indexed lookup's p50 and p95 at each pack size, beside the current path's.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/timing.py \
        --pack /tmp/p6/lookup/pack --stored 20500 --calls 10 \
        --out research/address-lookup-2026-11/results/timing-20500.json

Each pack is copied once, the key columns are added and backfilled, and then both lookups are timed over the same
pairs on the same copy. The load average is recorded before and after, because other sessions on this machine move
it and the brief asks for it beside every number.

The current path time is measured first, over every pair, and the indexed time second, over the same pairs, so a
machine that slows down during the run hurts the second side and cannot flatter the result.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(HERE))

from probes.baseline import blocked_environment, build_engine, pick_pairs  # noqa: E402

import keys  # noqa: E402
import lookup  # noqa: E402

SCRATCH = os.environ.get("P7_SCRATCH", "/tmp/p7")
USER = "robot-1"


def load() -> list[float]:
    return [round(value, 2) for value in os.getloadavg()]


def quantiles(values: list[float]) -> dict:
    ordered = sorted(values)
    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
        return round(ordered[index], 3)
    return {"count": len(ordered), "p50": at(0.50), "p95": at(0.95), "max": round(ordered[-1], 3),
            "mean": round(statistics.fmean(ordered), 3)}


async def main(args) -> dict:
    blocked_environment()
    from prme.models.temporal import AssertionStateQuery
    from prme.types import Scope
    from datetime import datetime, timezone

    pairs = pick_pairs(args.pack, args.pairs)
    reference = datetime(2026, 12, 1, tzinfo=timezone.utc)

    engine = await build_engine(os.path.join(SCRATCH, f"timing-{args.stored}"), args.pack)
    graph = engine._graph_store
    started = time.perf_counter()
    keys.add_schema(graph._conn)
    migration = keys.backfill(graph._conn)
    migration["seconds"] = round(time.perf_counter() - started, 3)

    report = {"pack": args.pack, "stored": args.stored, "calls": args.calls,
              "pairs": [f"{subject} / {predicate}" for subject, predicate in pairs],
              "migration": migration, "load_before": load(), "sides": {}}

    for side in ("current", "indexed"):
        per_pair = []
        for subject, predicate in pairs:
            query = AssertionStateQuery(subject=subject, predicate=predicate, scope=Scope.PERSONAL,
                                        valid_at=reference)
            times = []
            state = None
            for _ in range(args.calls):
                began = time.perf_counter()
                if side == "current":
                    state = await engine.get_assertion_state(query, user_id=USER)
                else:
                    state = await lookup.indexed_state(engine, query, user_id=USER)
                times.append(1000 * (time.perf_counter() - began))
            per_pair.append({"pair": f"{subject} / {predicate}", "ms": quantiles(times),
                             "scanned_nodes": state.scanned_nodes,
                             "matched_records": state.matched_records,
                             "status": state.status})
        every_call = [value for entry in per_pair for value in [entry["ms"]["p50"]]]
        report["sides"][side] = {
            "per_pair": per_pair,
            "p50_of_p50": quantiles(every_call),
            "worst_pair_p95": max(entry["ms"]["p95"] for entry in per_pair),
            "best_pair_p95": min(entry["ms"]["p95"] for entry in per_pair),
            "load_after": load(),
        }

    await engine.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--calls", type=int, default=10)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--out", default=None)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    text = json.dumps(result, indent=1)
    if arguments.out:
        with open(arguments.out, "w") as handle:
            handle.write(text)
    summary = {side: {"p50_of_p50": report["p50_of_p50"], "worst_pair_p95": report["worst_pair_p95"],
                      "best_pair_p95": report["best_pair_p95"], "load_after": report["load_after"]}
               for side, report in result["sides"].items()}
    summary["migration"] = result["migration"]
    summary["load_before"] = result["load_before"]
    print(json.dumps(summary, indent=1), flush=True)
