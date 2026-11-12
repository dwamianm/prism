"""P6 bar 3: build a pack through the working tier's drain, for comparison with the current path.

    PYTHONPATH=. uv run python research/working-tier-2026-11/build.py \
        --source ~/Sites/prism-write-rate/data/write-rate-2026-11/m4/current-a/400/pack \
        --stored 400 --facts 50 --path working --out /tmp/p6/build/working-450

The source pack is copied to the output directory, the observations after ``--stored`` are written (through the tier
or through the current path), the drain is finished, and the engine closed so the copy is a closed pack. The facts
are the same list for both paths because ``common.observations`` is deterministic.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness  # noqa: E402

common = harness.common


async def main(args) -> dict:
    common.blocked_environment()
    provider = await common.embedding_provider()
    directory = os.path.abspath(args.out)
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    shutil.copytree(args.source, os.path.join(directory, "pack"))
    engine, guard = await common.new_engine(directory, provider)
    messages = common.observations(args.stored + args.facts)[args.stored:]
    started = time.perf_counter()
    if args.path == "current":
        await common.write_current(engine, messages)
        result = {"path": "current"}
    else:
        import tier

        working = tier.WorkingTier(engine, directory)
        replay = await working.replay()
        accepted = time.perf_counter()
        for message in messages:
            await working.accept(message, user_id=common.USER)
        accepted_seconds = time.perf_counter() - accepted
        drain_started = time.perf_counter()
        batches = 0
        while working.backlog(user_id=common.USER):
            stats = await working.drain_once(user_id=common.USER, batch=args.batch)
            batches += 1
            if stats.appended == 0 and stats.dropped == 0:
                break
        result = {"path": "working", "replay": replay, "accept_seconds": round(accepted_seconds, 3),
                  "drain_seconds": round(time.perf_counter() - drain_started, 3), "drain_batches": batches,
                  "left_in_working_table": working.backlog(user_id=common.USER),
                  "drain_failures": working.stats.failures[:5]}
        working.close()
    result.update({"facts": args.facts, "stored": args.stored, "seconds": round(time.perf_counter() - started, 2),
                   "facts_per_second": round(args.facts / (time.perf_counter() - started), 2),
                   "model_calls_missed": len(guard.misses)})
    assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    await engine.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="a closed pack directory; it is copied first")
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--facts", type=int, required=True)
    parser.add_argument("--path", choices=("current", "working"), required=True)
    parser.add_argument("--batch", type=int, default=25)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    print(json.dumps(result, indent=1), flush=True)
