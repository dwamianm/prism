"""P6 bar 4: offer facts, kill the process with ``os._exit`` mid-stream, reopen and replay.

    PYTHONPATH=. uv run python research/working-tier-2026-11/crash.py \
        --pack ~/Sites/prism-write-rate/data/write-rate-2026-11/m4/current-a/400/pack --stored 400 \
        --facts 120 --out research/working-tier-2026-11/results/crash-400.json

Two runs in one script, each its own process state:

1. Offer ``--facts`` and kill the process with ``os._exit(9)`` at ``--kill-at`` accepts, or while the drainer is
   mid-derivation at ``--kill-after-s`` seconds. The accepted ids are written to the log's sidecar before the kill
   only when ``--kill-after-s`` is used, so the second run can check them.
2. Reopen the same pack directory, replay the log, drain to empty, and compare what the store holds with every
   fact the first run accepted.

The accepted ids are also recoverable from the log itself (a record exists once ``accept`` returns), so the check
does not depend on the sidecar: the replay reads every record, and the second run verifies a stored event exists
for each one.
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


async def accept_run(args, directory: str) -> dict:
    provider = await common.embedding_provider()
    import tier

    engine, guard = await common.new_engine(directory, provider)
    working = tier.WorkingTier(engine, directory)
    replay = await working.replay()
    messages = common.observations(args.stored + args.facts)[args.stored:]
    await working.start_drainer(user_id=common.USER, every_ms=args.drain_every_ms, batch=args.drain_batch)
    started = time.perf_counter()
    accepted = 0
    for message in messages:
        await working.accept(message, user_id=common.USER)
        accepted += 1
        if args.kill_at and accepted >= args.kill_at:
            break
        if args.kill_after_s and time.perf_counter() - started >= args.kill_after_s:
            break
        if not args.kill_at and not args.kill_after_s and args.every_ms:
            await asyncio.sleep(args.every_ms / 1000)
    summary = {"replay": replay, "accepted": accepted, "logged": len(working.log),
               "backlog": working.backlog(user_id=common.USER), "model_calls_missed": len(guard.misses),
               "mode": "kill_at" if args.kill_at else "kill_after_s" if args.kill_after_s else "offer_only"}
    with open(os.path.join(directory, f"crash-{args.phase}.json"), "w") as handle:
        json.dump(summary, handle, indent=1)
    print(json.dumps(summary), flush=True)
    os._exit(9)


async def verify_run(args, directory: str) -> dict:
    """Replay the log, drain everything, and check that every logged fact is stored and derived."""
    provider = await common.embedding_provider()
    import tier

    engine, guard = await common.new_engine(directory, provider)
    working = tier.WorkingTier(engine, directory)
    replay = await working.replay()
    started = time.perf_counter()
    batches, stalls = 0, 0
    while working.backlog(user_id=common.USER):
        stats = await working.drain_once(user_id=common.USER, batch=args.drain_batch)
        batches += 1
        if stats.appended == 0 and stats.dropped == 0:
            stalls += 1
            if stalls > 1:
                break
    logged = len(working.log)
    records = [json.loads(working.log.read(sequence)) for sequence in range(logged)]
    missing_events, missing_records = [], []
    store = engine._event_store
    for record in records:
        event = await store.get(record["event_id"])
        if event is None:
            missing_events.append(record["event_id"])
            continue
        work = await store.extraction_work.status(record["event_id"], user_id=common.USER)
        status = await store.processing_status(record["event_id"], user_id=common.USER)
        if work is None or work.status != "complete" or status is None or status.status != "complete":
            missing_records.append(f"{record['event_id']}:{getattr(work, 'status', None)}")
    result = {
        "replay": replay, "logged": logged, "drain_batches": batches, "seconds": round(time.perf_counter() - started, 2),
        "facts_stored": logged - len(missing_events), "facts_derived": logged - len(missing_events) - len(missing_records),
        "missing_events": missing_events[:5], "missing_records": missing_records[:5],
        "model_calls_missed": len(guard.misses), "left_in_working_table": working.backlog(user_id=common.USER),
    }
    assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    working.close()
    await engine.close()
    return result


async def main(args) -> dict:
    directory = os.path.abspath(args.directory)
    if args.phase == "verify":
        # The accept phase left the log and the half-drained pack here. Reopening is the point, so do not reset.
        return await verify_run(args, directory)
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    shutil.copytree(args.pack, os.path.join(directory, "pack"))
    return await accept_run(args, directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--directory", required=True, help="the pack directory both runs use")
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--facts", type=int, required=True)
    parser.add_argument("--kill-at", type=int, default=0, help="kill after this many accepts")
    parser.add_argument("--kill-after-s", type=float, default=0.0, help="kill this long after the first accept")
    parser.add_argument("--every-ms", type=float, default=0.0, help="pace the offer")
    parser.add_argument("--drain-every-ms", type=float, default=25.0)
    parser.add_argument("--drain-batch", type=int, default=25)
    parser.add_argument("--phase", choices=("accept", "verify"), required=True)
    parser.add_argument("--out")
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    print(json.dumps(result, indent=1), flush=True)
    if arguments.out:
        with open(arguments.out, "w") as handle:
            json.dump(result, handle, indent=1)
