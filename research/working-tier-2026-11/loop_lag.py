"""P6 bar 2, the mechanism: does the drainer block the event loop a reader shares with it?

    PYTHONPATH=. uv run python research/working-tier-2026-11/loop_lag.py \
        --pack /tmp/p6/lookup/pack --stored 20500 --facts 40

A 5 ms timer task runs beside the drainer and records how late each tick fires. If the drainer blocks the loop, the
lag spikes to seconds and a reader on the same loop waits that long, whatever the read itself costs. A direct
``lookup`` is timed beside it, so the read's own cost is separate from the wait.
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
    import tier

    directory = os.path.join(harness.SCRATCH, "lag", str(args.stored))
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    shutil.copytree(args.pack, os.path.join(directory, "pack"))
    engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
    working = tier.WorkingTier(engine, directory)
    await working.replay()
    messages = common.observations(args.stored + args.facts)[args.stored:]
    for message in messages:
        await working.accept(message, user_id=common.USER)
    assertion = tier.Assertion(**messages[0]["assertions"][0]).normalized()

    lags, reads = [], []
    stop = asyncio.Event()

    async def timer() -> None:
        period = args.period_ms / 1000
        expected = time.perf_counter() + period
        while not stop.is_set():
            await asyncio.sleep(period)
            now = time.perf_counter()
            lags.append(1000 * max(0.0, now - expected))
            expected = now + period
            started = time.perf_counter()
            await working.lookup(assertion, user_id=common.USER)
            reads.append(1000 * (time.perf_counter() - started))

    probe = asyncio.create_task(timer())
    started = time.perf_counter()
    stats = await working.drain_once(user_id=common.USER, batch=args.facts, budget_ms=args.budget_ms)
    drain_seconds = time.perf_counter() - started
    stop.set()
    await probe
    assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    working.close()
    await engine.close()
    return {"stored": args.stored, "facts": args.facts, "drain_seconds": round(drain_seconds, 2),
            "drain_appended": stats.appended, "drain_dropped": stats.dropped,
            "loop_lag_ms": harness.quantiles(lags), "loop_ticks": len(lags),
            "lookup_ms": harness.quantiles(reads), "lag_over_100ms": sum(1 for v in lags if v > 100),
            "model_calls_missed": len(guard.misses)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--facts", type=int, default=40)
    parser.add_argument("--period-ms", type=float, default=5.0)
    parser.add_argument("--budget-ms", type=float, default=60000)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    print(json.dumps(result, indent=1), flush=True)
