"""P5 window: the sustained rate and stage profile of one write path on a kept pack.

    PYTHONPATH=. uv run python research/write-batch-2026-11/window.py --path batch \
        --pack ~/Sites/prism-write-rate/data/write-rate-2026-11/m4/current-a/400/pack --stored 400 --facts 200 \
        --out research/write-batch-2026-11/results/window-batch-400.json

The kept pack is copied to ``/tmp/p5`` first and never changed. The facts written continue the observation list the
pack was built from (observation ``stored`` onward), through P2's ``write_current``: ``ingest_batch`` of ``--batch``
caller-stated facts with ``wait_for_extraction=True``, then ``process_pending`` until nothing waits. So a window's
time is the time until every fact is published, committed and searchable (for the batch path, the time includes the
batch's one lexical commit).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import time

import batch as harness

common = harness.common


async def main(args) -> dict:
    common.blocked_environment()
    harness.instrument()
    provider = await common.embedding_provider()
    settings = harness.PATHS[args.path]
    directory = os.path.join(harness.SCRATCH, "window", f"{args.path}-{args.stored}-b{args.batch}")
    if os.path.exists(directory):
        shutil.rmtree(directory)
    shutil.copytree(os.path.expanduser(args.pack), os.path.join(directory, "pack"))
    engine, guard = await common.new_engine(directory, provider, **settings)
    messages = common.observations(args.stored + args.facts)[args.stored:]
    try:
        before = common.snapshot()
        batch_seconds = []
        started = time.perf_counter()
        for offset in range(0, len(messages), args.batch):
            batch_started = time.perf_counter()
            await common.write_current(engine, messages[offset:offset + args.batch], batch=args.batch)
            batch_seconds.append(time.perf_counter() - batch_started)
        seconds = time.perf_counter() - started
        change = common.delta(before, common.snapshot())
        status = await engine.process_pending(user_id=common.USER, budget_ms=0)
        assert not guard.misses, f"a model was called: {guard.misses[:3]}"
        lexical_waiting = engine._lexical_index._uncommitted
    finally:
        await engine.close()
    facts = len(messages)
    return {
        "path": args.path, "settings": settings, "pack": args.pack, "stored_at_start": args.stored,
        "facts": facts, "batch": args.batch, "seconds": round(seconds, 3),
        "facts_per_second": round(facts / seconds, 2), "ms_per_fact": round(1000 * seconds / facts, 2),
        "ms_per_batch": harness.quantiles([1000 * value for value in batch_seconds]),
        "stage_ms_per_fact": {key: round(1000 * value / facts, 3)
                              for key, value in sorted(change["time"].items(), key=lambda kv: -kv[1])},
        "stage_calls_per_fact": {key: round(value / facts, 3) for key, value in sorted(change["calls"].items())},
        "sql_per_fact": {key: round(value / facts, 3)
                         for key, value in sorted(change["sql"].items(), key=lambda kv: -kv[1])},
        "index_work_per_fact": {key: round(value / facts, 3) for key, value in sorted(change["counts"].items())},
        "pending_after": status.pending, "failed_after": status.failed,
        "lexical_uncommitted_after": lexical_waiting, "model_calls_missed": len(guard.misses),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", choices=tuple(harness.PATHS), required=True)
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--facts", type=int, default=200)
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({key: result[key] for key in ("path", "stored_at_start", "facts", "batch", "facts_per_second",
                                                   "ms_per_fact", "pending_after", "failed_after",
                                                   "lexical_uncommitted_after")}), flush=True)
    top = list(result["stage_ms_per_fact"].items())[:14]
    print("stages ms/fact:", ", ".join(f"{key} {value}" for key, value in top), flush=True)
    print("SQL/fact:", result["sql_per_fact"].get("(total)"), flush=True)
