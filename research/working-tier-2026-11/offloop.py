"""P6 bar 2, the bounded fix: is a working-table read answered within 1 s when the loop is blocked?

    PYTHONPATH=. uv run python research/working-tier-2026-11/offloop.py --pack /tmp/p6/lookup/pack --stored 20500 \
        --facts 20

The drainer holds the engine's event loop for seconds at a time during one derivation job (``loop_lag.py``). A read
that runs on that loop therefore waits however long the job holds it. This probe answers the read from a plain
thread instead: the privacy check and the table read are synchronous, so a thread can do both while the loop is
busy. It measures the reader's wait while a drain runs beside it, once through the loop and once off it.

This is the fix the bar 2 result implies. It does not make the drain faster, and it does not change what a read
returns: the same privacy check runs first and the same table is read. It only stops the reader from queueing
behind the drainer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness  # noqa: E402

common = harness.common


async def main(args) -> dict:
    common.blocked_environment()
    provider = await common.embedding_provider()
    import tier

    directory = os.path.join(harness.SCRATCH, "offloop", str(args.stored))
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

    # Off the loop: the privacy check and the table read, both synchronous, on a caller's own thread. The check
    # needs DuckDB, and ``require_legacy_range`` serialises it with an asyncio lock a thread cannot take, so this
    # uses a cursor of its own. DuckDB serialises statements on one connection itself; a cursor is the supported
    # way for a second thread to query while the loop is busy.
    def off_loop() -> int:
        from prme.storage.privacy import require_legacy_duck
        cursor = engine._event_store._conn.cursor()
        try:
            require_legacy_duck(cursor, common.USER, "personal")
        finally:
            cursor.close()
        with working._lock:
            facts = working._table.get((common.USER, "personal"), {}).get(assertion.address(), ())
        return len(facts)

    on_wait, off_wait = [], []
    stop = asyncio.Event()

    async def reader() -> None:
        while not stop.is_set():
            started = time.perf_counter()
            await working.lookup(assertion, user_id=common.USER)
            on_wait.append(1000 * (time.perf_counter() - started))
            started = time.perf_counter()
            await asyncio.to_thread(off_loop)
            off_wait.append(1000 * (time.perf_counter() - started))
            await asyncio.sleep(args.period_ms / 1000)

    probe = asyncio.create_task(reader())
    started = time.perf_counter()
    stats = await working.drain_once(user_id=common.USER, batch=args.facts, budget_ms=args.budget_ms)
    used = time.perf_counter() - started
    stop.set()
    await probe
    assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    working.close()
    await engine.close()
    return {"stored": args.stored, "facts": args.facts, "drain_seconds": round(used, 2),
            "drain_appended": stats.appended, "drain_dropped": stats.dropped,
            "read_on_loop_ms": harness.quantiles(on_wait), "read_off_loop_ms": harness.quantiles(off_wait),
            "on_loop_over_1s": sum(1 for v in on_wait if v > 1000),
            "off_loop_over_1s": sum(1 for v in off_wait if v > 1000),
            "reads": len(on_wait), "model_calls_missed": len(guard.misses)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--facts", type=int, default=20)
    parser.add_argument("--period-ms", type=float, default=20.0)
    parser.add_argument("--budget-ms", type=float, default=60000)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    print(json.dumps(result, indent=1), flush=True)
