"""M2 and the M3 stream test (PROTOCOL.md): accept latency, and time from accept until a fact is committed and found.

    # M2: the current path, one ingest() a fact, not waiting, at 400 stored.
    PYTHONPATH=. uv run python research/write-rate-2026-11/stream.py --mode single --facts 100 \
        --pack data/write-rate-2026-11/m4/current-a/400/pack --stored 400 --out data/write-rate-2026-11/m2-400.json
    # M3 stream test: the prototype, 10 facts every 50 ms (200 a second) for 10 s, through ingest_batch(), not waiting.
    PYTHONPATH=. uv run python research/write-rate-2026-11/stream.py --mode stream --prototype \
        --pack data/write-rate-2026-11/m4/prototype/400/pack --stored 400 --out data/write-rate-2026-11/stream-400.json

The pack is copied to a scratch directory first, so the kept copy is never changed. The facts offered continue the
same observation list the pack was built from (observation ``stored`` onward), so the world stays consistent.

"Committed" is the moment the fact's plan is published: ``IngestionPipeline._publish_plan`` returns, after the graph
commit and the vector and lexical staging (the lexical stage commits and reloads its searcher). "Found" is the moment a
``retrieve("Where is the {object} now?")`` asked after the commit returns the fact's own claim (a fact node whose
evidence is the fact's source event); its rank is recorded too. A sample is asked once its commit is seen, and again
every poll until it is found.

The run stops ``--settle`` seconds after the last offer, whatever is still waiting; what is still not committed then is
the backlog, and its wait is reported as a lower bound. The process then exits without draining, because the scratch
copy is thrown away. ``--tag`` only marks the command line, so the window guard can pause this run during M3's timed
window at 20,000 stored.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SCRATCH = "/tmp/p2/stream"


def quantiles(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 2)

    return {"count": len(ordered), "p50": at(0.5), "p95": at(0.95), "max": round(ordered[-1], 2),
            "mean": round(statistics.fmean(ordered), 2)}


async def main(args) -> dict:
    common.blocked_environment()
    provider = await common.embedding_provider()

    from prme.ingestion import pipeline

    committed_at: dict[str, float] = {}
    real_publish = pipeline.IngestionPipeline._publish_plan

    async def publish(self, plan, *args_, **kwargs):
        result = await real_publish(self, plan, *args_, **kwargs)
        committed_at.setdefault(str(plan.event_id), time.perf_counter())
        return result

    pipeline.IngestionPipeline._publish_plan = publish

    name = f"{args.mode}-{'prototype' if args.prototype else 'current'}-{args.stored}"
    directory = os.path.join(SCRATCH, name)
    if os.path.exists(directory):
        shutil.rmtree(directory)
    shutil.copytree(args.pack, os.path.join(directory, "pack"))
    settings = common.PROTOTYPE if args.prototype else {}
    engine, guard = await common.new_engine(directory, provider, **settings)

    if args.mode == "single":
        offered_count = args.facts
    else:
        offered_count = int(args.seconds * 1000 / args.every_ms) * args.batch
    messages = common.observations(args.stored + offered_count)[args.stored:]

    accepted: list[dict] = []           # one per fact: event id, object, accept time, call latency
    calls: list[dict] = []              # one per call: latency, facts, lag behind its planned start
    backlog_curve: list[dict] = []
    samples: list[int] = []
    found: dict[int, dict] = {}

    async def poll(stop: asyncio.Event, started: float) -> None:
        """Every poll: the backlog, and one retrieve for each committed sample not yet found."""
        while not stop.is_set():
            backlog_curve.append({"t": round(time.perf_counter() - started, 2), "accepted": len(accepted),
                                  "committed": sum(1 for fact in accepted if fact["event_id"] in committed_at)})
            for index in samples:
                if index in found or index >= len(accepted):
                    continue
                fact = accepted[index]
                if fact["event_id"] not in committed_at:
                    continue
                response = await engine.retrieve(f"Where is the {fact['object']} now?", user_id=common.USER)
                rank = next((position for position, candidate in enumerate(response.results, 1)
                             if candidate.node.node_type.value == "fact"
                             and fact["event_id"] in {str(ref) for ref in candidate.node.evidence_refs}), None)
                if rank is not None:
                    now = time.perf_counter()
                    found[index] = {"rank": rank, "results": len(response.results),
                                    "found_after_s": round(now - fact["accepted_at"], 3),
                                    "committed_after_s": round(committed_at[fact["event_id"]] - fact["accepted_at"],
                                                               3)}
            await asyncio.sleep(args.poll_ms / 1000)

    stop = asyncio.Event()
    started = time.perf_counter()
    poller = asyncio.create_task(poll(stop, started))
    try:
        if args.mode == "single":
            samples[:] = list(range(0, offered_count, max(1, offered_count // 20)))[:20]
            for message in messages:
                call_started = time.perf_counter()
                event_id = await engine.ingest(message["content"], user_id=common.USER, role=message["role"],
                                               event_time=message["event_time"], assertions=message["assertions"],
                                               wait_for_extraction=False)
                done = time.perf_counter()
                calls.append({"latency_ms": 1000 * (done - call_started), "facts": 1, "lag_ms": 0.0})
                accepted.append({"event_id": str(event_id), "object": message["assertions"][0]["subject"],
                                 "accepted_at": done})
        else:
            # 10 from the first 50 offered, 10 spread over the whole offer.
            samples[:] = sorted(set(range(0, 50, 5)) | set(range(0, offered_count, offered_count // 10)))[:20]
            ticks = int(args.seconds * 1000 / args.every_ms)
            for tick in range(ticks):
                planned = started + tick * args.every_ms / 1000
                delay = planned - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                batch = messages[tick * args.batch:(tick + 1) * args.batch]
                call_started = time.perf_counter()
                event_ids = await engine.ingest_batch(batch, user_id=common.USER, wait_for_extraction=False)
                done = time.perf_counter()
                calls.append({"latency_ms": 1000 * (done - call_started), "facts": len(batch),
                              "lag_ms": 1000 * max(0.0, call_started - planned)})
                for event_id, message in zip(event_ids, batch):
                    accepted.append({"event_id": str(event_id), "object": message["assertions"][0]["subject"],
                                     "accepted_at": done})
        offer_ended = time.perf_counter()
        deadline = offer_ended + args.settle
        while time.perf_counter() < deadline:
            if all(fact["event_id"] in committed_at for fact in accepted) and all(index in found for index in samples):
                break
            await asyncio.sleep(0.2)
        stop.set()
        await poller
        assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    except BaseException:
        stop.set()
        raise
    stopped = time.perf_counter()

    commit_waits = [committed_at[fact["event_id"]] - fact["accepted_at"] for fact in accepted
                    if fact["event_id"] in committed_at]
    not_committed = [fact for fact in accepted if fact["event_id"] not in committed_at]
    offer_seconds = offer_ended - started
    committed_count = len(commit_waits)
    report = {
        "mode": args.mode, "path": "prototype" if args.prototype else "current", "settings": settings,
        "pack": args.pack, "stored_at_start": args.stored,
        "offer": ({"calls": "ingest(content, assertions=..., wait_for_extraction=False), one per fact, in sequence",
                   "facts": offered_count} if args.mode == "single" else
                  {"calls": f"ingest_batch({args.batch} facts, wait_for_extraction=False) every {args.every_ms} ms",
                   "planned_facts_per_second": round(1000 * args.batch / args.every_ms, 1),
                   "planned_seconds": args.seconds, "facts": offered_count}),
        "offer_seconds": round(offer_seconds, 3),
        "offered_facts_per_second": round(len(accepted) / offer_seconds, 2),
        "accept_latency_ms_per_call": quantiles([call["latency_ms"] for call in calls]),
        "accept_latency_ms_per_fact": quantiles([call["latency_ms"] / call["facts"] for call in calls]),
        "calls_late_ms": quantiles([call["lag_ms"] for call in calls]),
        "settle_seconds": args.settle,
        "run_seconds": round(stopped - started, 2),
        "committed": committed_count, "not_committed_at_stop": len(not_committed),
        "commit_wait_s": quantiles(commit_waits),
        "commit_wait_lower_bound_s_for_backlog": (round(stopped - min(fact["accepted_at"] for fact in not_committed), 2)
                                                  if not_committed else None),
        "committed_facts_per_second_after_offer_start": round(committed_count / (stopped - started), 2),
        "samples": [{"offered_index": index, "object": accepted[index]["object"],
                     **(found.get(index) or {"found": False,
                                            "committed": accepted[index]["event_id"] in committed_at})}
                    for index in samples if index < len(accepted)],
        "backlog_curve": backlog_curve[::max(1, len(backlog_curve) // 60)],
        "model_calls_missed": len(guard.misses),
    }
    report["searchable_within_1s"] = {
        "samples_found": sum(1 for sample in report["samples"] if "found_after_s" in sample),
        "samples_found_within_1s": sum(1 for sample in report["samples"] if sample.get("found_after_s", 99) < 1.0),
        "facts_committed_within_1s": sum(1 for wait in commit_waits if wait < 1.0),
        "facts_offered": len(accepted),
    }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("single", "stream"), required=True)
    parser.add_argument("--pack", required=True, help="a closed pack directory; it is copied first")
    parser.add_argument("--stored", type=int, required=True, help="observations the pack already holds")
    parser.add_argument("--prototype", action="store_true")
    parser.add_argument("--facts", type=int, default=100, help="single mode: facts offered")
    parser.add_argument("--seconds", type=float, default=10.0, help="stream mode: seconds of offer")
    parser.add_argument("--every-ms", type=float, default=50.0)
    parser.add_argument("--batch", type=int, default=10)
    parser.add_argument("--settle", type=float, default=120.0, help="seconds to wait after the last offer")
    parser.add_argument("--poll-ms", type=float, default=100.0)
    parser.add_argument("--out", required=True)
    parser.add_argument("--tag", default="p2_side_stream")
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key not in ("backlog_curve", "samples")},
                     indent=1), flush=True)
    os._exit(0)  # the scratch copy is thrown away; do not drain the backlog on close
