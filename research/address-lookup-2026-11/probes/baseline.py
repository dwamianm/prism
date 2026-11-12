"""P7 measurement: what get_assertion_state costs today, and where the time goes.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/baseline.py \
        --pack /tmp/p6/lookup/pack --stored 20500 --calls 10 \
        --out research/address-lookup-2026-11/results/baseline-20500.json

Two parts:

1. Wall time of ``engine.get_assertion_state`` over ``--calls`` repeats of one subject/predicate pair that matches.
2. A stage breakdown of the same call, instrumented by wrapping ``engine.scan_nodes`` so each page's SQL time,
   row-materialization time and Python filter time are recorded separately. Nothing in ``src/`` changes; the wrappers
   live here.

The store is copied for each run because a read stores a receipt and grows the pack, which would make successive
measurements incomparable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

SCRATCH = os.environ.get("P7_SCRATCH", "/tmp/p7")
USER = "robot-1"


def blocked_environment() -> None:
    os.environ.update({
        "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
        "PRME_EXTRACTION_API_KEY": "",
    })


def quantiles(values: list[float]) -> dict:
    ordered = sorted(values)
    def at(fraction: float) -> float:
        if not ordered:
            return float("nan")
        index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
        return round(ordered[index], 3)
    return {"count": len(ordered), "p50": at(0.50), "p95": at(0.95), "p99": at(0.99),
            "max": round(max(ordered), 3) if ordered else float("nan"),
            "mean": round(statistics.fmean(ordered), 3) if ordered else float("nan")}


async def build_engine(directory: str, pack: str):
    from prme.storage.embedding import create_embedding_provider
    from prme.storage.engine import MemoryEngine
    from tests.contract.harness import product_config

    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    shutil.copytree(pack, os.path.join(directory, "pack"))
    config = product_config(directory)
    provider = create_embedding_provider(config.embedding)
    engine = await MemoryEngine.create(config, embedding_provider=provider)
    return engine


async def main(args) -> dict:
    blocked_environment()
    from prme.models.temporal import AssertionStateQuery
    from prme.types import Scope

    engine = await build_engine(os.path.join(SCRATCH, f"baseline-{args.stored}"), args.pack)
    pairs = args.pairs
    report = {"pack": args.pack, "stored": args.stored, "calls": args.calls, "pairs": [], "stages": []}

    reference = datetime(2026, 12, 1, tzinfo=timezone.utc)
    for subject, predicate in pairs:
        query = AssertionStateQuery(subject=subject, predicate=predicate, scope=Scope.PERSONAL,
                                    valid_at=reference)
        times = []
        state = None
        for _ in range(args.calls):
            started = time.perf_counter()
            state = await engine.get_assertion_state(query, user_id=USER)
            times.append(1000 * (time.perf_counter() - started))
        report["pairs"].append({
            "subject": subject, "predicate": predicate, "ms": quantiles(times),
            "scanned_nodes": state.scanned_nodes, "matched_records": state.matched_records,
            "status": state.status, "current_candidate_count": state.current_candidate_count,
            "exclusions": dict(state.exclusions),
        })

    # Stage breakdown for one matching pair: SQL, row materialization, Python filter.
    subject, predicate = pairs[0]
    scan = engine.scan_nodes
    stages = {"pages": 0, "sql_ms": 0.0, "python_ms": 0.0}

    async def timed_scan(**kwargs):
        started = time.perf_counter()
        page = await scan(**kwargs)
        stages["python_ms"] += 1000 * (time.perf_counter() - started)
        stages["pages"] += 1
        return page

    engine.scan_nodes = timed_scan
    query = AssertionStateQuery(subject=subject, predicate=predicate, scope=Scope.PERSONAL,
                                valid_at=reference)
    started = time.perf_counter()
    state = await engine.get_assertion_state(query, user_id=USER)
    total = 1000 * (time.perf_counter() - started)
    engine.scan_nodes = scan
    report["stages"] = [{"pair": f"{subject} / {predicate}", "total_ms": round(total, 3),
                         "pages": stages["pages"],
                         "python_ms": round(stages["python_ms"], 3),
                         "outside_scan_ms": round(total - stages["python_ms"], 3),
                         "scanned_nodes": state.scanned_nodes, "matched": state.matched_records}]

    await engine.close()
    return report


def pick_pairs(pack: str, count: int) -> list[tuple[str, str]]:
    """Draw pairs from the pack: matching, non-matching, several-claim, superseded and archived."""
    import duckdb

    conn = duckdb.connect(os.path.join(pack, "memory.duckdb"), read_only=True)
    rows = conn.execute(
        "SELECT json_extract_string(metadata,'$.subject') AS s, "
        "json_extract_string(metadata,'$.predicate') AS p, count(*) AS n "
        "FROM nodes WHERE s IS NOT NULL GROUP BY 1,2 ORDER BY n DESC"
    ).fetchall()
    conn.close()
    rng = random.Random(7)
    multi = [(s, p) for s, p, n in rows if n > 1]
    single = [(s, p) for s, p, n in rows if n == 1]
    chosen: list[tuple[str, str]] = []
    chosen.extend(rng.sample(multi, min(len(multi), count // 3)))
    chosen.extend(rng.sample(single, min(len(single), count // 3)))
    chosen.extend((s, p) for s, p, _ in rng.sample(rows, min(len(rows), count - len(chosen))))
    return chosen[:count]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--calls", type=int, default=10)
    parser.add_argument("--out", default=None)
    arguments = parser.parse_args()
    pairs = pick_pairs(arguments.pack, 3)
    arguments.pairs = pairs
    result = asyncio.run(main(arguments))
    text = json.dumps(result, indent=1)
    if arguments.out:
        with open(arguments.out, "w") as handle:
            handle.write(text)
    print(text, flush=True)
