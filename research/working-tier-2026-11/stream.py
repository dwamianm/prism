"""P6 bar 1: accept latency on the working tier, offered at 200 facts a second, and the drain behind it.

    PYTHONPATH=. uv run python research/working-tier-2026-11/stream.py \
        --pack ~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack --stored 20500 \
        --seconds 10 --out research/working-tier-2026-11/results/stream-working-20500.json

The offer is P5's: ``--batch`` facts every ``--every-ms`` (10 every 50 ms, 200 a second) for ``--seconds``, continuing
the observation list the pack was built from. Each fact goes through ``WorkingTier.accept`` in one call, and the
call's latency is what bar 1 measures. A poll task asks one fact's address every ``--poll-ms`` and records how long
after accept that address returns it.

Two clocks are kept apart, because the tier moves work off the caller's thread:

- ``accept_ms``, the caller's latency: validation, the dict put and the memory-mapped log write.
- ``durable_s``, from accept until the drainer has appended the source event, derived its records and dropped the
  fact. A kill before this point loses nothing that the log does not replay, but the fact is not in the store yet.

The process exits with ``os._exit`` when the offer and the settle end, so a backlog is left behind on purpose.
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
    import tier as tier_module

    name = f"working-{args.stored}"
    directory = os.path.join(harness.SCRATCH, "stream", name)
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    shutil.copytree(args.pack, os.path.join(directory, "pack"))
    engine, guard = await common.new_engine(directory, provider)
    working = tier_module.WorkingTier(engine, directory)
    replay = await working.replay()

    offered_count = int(args.seconds * 1000 / args.every_ms) * args.batch
    messages = common.observations(args.stored + offered_count)[args.stored:]

    accepted: list[dict] = []
    durable_at: dict[int, float] = {}
    backlog_curve: list[dict] = []
    drain_cycles: list[dict] = []
    samples: list[int] = []
    found: dict[int, dict] = {}
    started = time.perf_counter()

    def on_cycle(stats) -> None:
        drain_cycles.append({"t": round(time.perf_counter() - started, 3), "appended": stats.appended,
                             "dropped": stats.dropped, "seconds": round(stats.seconds, 3),
                             "backlog": working.backlog(user_id=common.USER)})
        if not stats.dropped:
            return
        still = {fact.sequence for fact in working._pending(user_id=common.USER)}
        now = time.perf_counter()
        for index, fact in enumerate(accepted):
            if index not in durable_at and fact["sequence"] is not None and fact["sequence"] not in still:
                durable_at[index] = now

    # Log sequence numbers continue after anything the replay found, so the accepted count starts at the log length.
    offset = len(working.log)
    await working.start_drainer(user_id=common.USER, every_ms=args.drain_every_ms, batch=args.drain_batch,
                                on_cycle=on_cycle)
    stop = asyncio.Event()

    async def poll() -> None:
        while not stop.is_set():
            backlog_curve.append({"t": round(time.perf_counter() - started, 2), "accepted": len(accepted),
                                  "backlog": working.backlog(user_id=common.USER),
                                  "memory_mb": round(working.memory_bytes(user_id=common.USER) / (1 << 20), 1)})
            for index in samples:
                if index in found or index >= len(accepted):
                    continue
                fact = accepted[index]
                message = messages[index]
                returned = await working.lookup(fact["assertion"], user_id=common.USER)
                if returned:
                    found[index] = {"after_s": round(time.perf_counter() - fact["accepted_at"], 4),
                                    "address_backlog": len(returned)}
            await asyncio.sleep(args.poll_ms / 1000)

    poller = asyncio.create_task(poll())
    try:
        samples[:] = sorted(set(range(0, 50, 5)) | set(range(0, offered_count, max(1, offered_count // 10))))[:20]
        ticks = int(args.seconds * 1000 / args.every_ms)
        for tick in range(ticks):
            planned = started + tick * args.every_ms / 1000
            delay = planned - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            for message in messages[tick * args.batch:(tick + 1) * args.batch]:
                call_started = time.perf_counter()
                await working.accept(message, user_id=common.USER)
                done = time.perf_counter()
                # The record's sequence is its position in the log, so it is the log length before this call.
                accepted.append({"accepted_at": done, "latency_ms": 1000 * (done - call_started),
                                 "sequence": offset + len(accepted),
                                 "assertion": tier_module.Assertion(
                                     **{key: value for key, value in message["assertions"][0].items()}).normalized()})
        offer_ended = time.perf_counter()
        deadline = offer_ended + args.settle
        while time.perf_counter() < deadline:
            if all(index in found for index in samples) and working.backlog(user_id=common.USER) == 0:
                break
            await asyncio.sleep(0.2)
        stop.set()
        await poller
        await working.stop_drainer()
        assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    except BaseException:
        stop.set()
        raise
    stopped = time.perf_counter()

    accept_times = [fact["latency_ms"] for fact in accepted]
    durable = [durable_at[index] - accepted[index]["accepted_at"] for index in durable_at]
    offer_seconds = offer_ended - started
    remaining = working.backlog(user_id=common.USER)
    return {
        "pack": args.pack, "stored_at_start": args.stored, "replay": replay,
        "offer": {"calls": f"accept({args.batch} facts) every {args.every_ms} ms",
                  "planned_facts_per_second": round(1000 * args.batch / args.every_ms, 1),
                  "planned_seconds": args.seconds, "facts": offered_count},
        "offer_seconds": round(offer_seconds, 3),
        "offered_facts_per_second": round(len(accepted) / offer_seconds, 2),
        "accept_ms": harness.quantiles(accept_times),
        "accepted_within_1ms": sum(1 for value in accept_times if value < 1.0),
        "facts_accepted": len(accepted),
        "drained": len(durable_at), "not_drained_at_stop": remaining,
        "accept_to_durable_s": harness.quantiles(durable),
        "drain_cycles": len(drain_cycles),
        "drain_seconds_per_cycle": harness.quantiles([cycle["seconds"] for cycle in drain_cycles]),
        "drain_facts_per_second": (round(len(durable_at) / max(1e-9, sum(cycle["seconds"] for cycle in drain_cycles)), 2)
                                   if drain_cycles else None),
        "samples": [{"offered_index": index, **(found.get(index) or {"found": False})} for index in samples],
        "found_within_1s": sum(1 for sample in samples if found.get(sample, {}).get("after_s", 99) < 1.0),
        "samples_asked": len(samples),
        "backlog_curve": backlog_curve[::max(1, len(backlog_curve) // 60)],
        "drain_tail": drain_cycles[-5:],
        "model_calls_missed": len(guard.misses),
        "run_seconds": round(stopped - started, 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True, help="a closed pack directory; it is copied first")
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--every-ms", type=float, default=50.0)
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--drain-every-ms", type=float, default=25.0)
    parser.add_argument("--drain-batch", type=int, default=25)
    parser.add_argument("--settle", type=float, default=180.0)
    parser.add_argument("--poll-ms", type=float, default=50.0)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()

    async def run_and_exit() -> None:
        result = await main(arguments)
        with open(arguments.out, "w") as handle:
            json.dump(result, handle, indent=2)
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ("backlog_curve", "drain_tail", "samples")}, indent=1), flush=True)
        os._exit(0)

    asyncio.run(run_and_exit())
