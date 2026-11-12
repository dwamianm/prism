"""P6 bar 5: what the working tier adds to a read, at backlog 0 and at backlogs of 1,000 and 12,000.

    PYTHONPATH=. uv run python research/working-tier-2026-11/reads.py \
        --pack ~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack --stored 20500 \
        --backlogs 0 1000 12000 --out research/working-tier-2026-11/results/reads-20500.json

For each backlog size the tier accepts that many facts (the drainer is stopped, so they stay in the working table)
and then asks ``--questions`` questions through ``retrieve()`` and ``retrieve_with_working()``, alternating. The
difference between the two is the tier's added read cost: the working-table lookup and its merge. The base read is
the engine's own ``retrieve()`` and is reported beside it, because at 20,500 stored the base read is what the robot
read goal measures (P3: about 18.5 s).

The working table's memory is read before and after the accepts, and the tier's own address lookup is timed on its
own, because that read is the one the fast path in P3's bounded walk would use.
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

    directory = os.path.join(harness.SCRATCH, "reads", str(args.stored))
    if os.path.exists(directory):
        shutil.rmtree(directory)
    os.makedirs(directory)
    shutil.copytree(args.pack, os.path.join(directory, "pack"))
    engine, guard = await common.new_engine(directory, provider,
                                            organizer={"opportunistic_enabled": False})
    working = tier.WorkingTier(engine, directory)
    await working.replay()
    truth = common.truth_after(common.observations(args.stored + max(args.backlogs or [0])))
    rng = __import__("random").Random(common.SEED + args.stored)
    subjects = rng.sample(sorted(truth), args.questions)
    reference_time = common.BASE + common.STEP * args.stored
    messages = common.observations(args.stored + max(args.backlogs or [0]))[args.stored:]
    report = {"pack": args.pack, "stored": args.stored, "backlogs": {}}
    tried = 0
    for backlog in args.backlogs or [0]:
        while tried < backlog:
            await working.accept(messages[tried], user_id=common.USER)
            tried += 1
        points = {"backlog": working.backlog(user_id=common.USER),
                  "memory_mb": round(working.memory_bytes(user_id=common.USER) / (1 << 20), 2),
                  "facts": len(messages[:backlog])}
        plain, with_working, lookup = [], [], []
        for subject in subjects:
            question = f"Where is the {subject} now?"
            started = time.perf_counter()
            await engine.retrieve(question, user_id=common.USER, reference_time=reference_time)
            plain.append(1000 * (time.perf_counter() - started))
            started = time.perf_counter()
            await working.retrieve_with_working(question, user_id=common.USER, reference_time=reference_time,
                                                subject=subject, relation="located_in")
            with_working.append(1000 * (time.perf_counter() - started))
            assertion = tier.Assertion(subject=subject, subject_type="product", relation="located_in",
                                       object="x", object_type="location").normalized()
            started = time.perf_counter()
            await working.lookup(assertion, user_id=common.USER)
            lookup.append(1000 * (time.perf_counter() - started))
        points["retrieve_ms"] = harness.quantiles(plain)
        points["retrieve_with_working_ms"] = harness.quantiles(with_working)
        points["added_ms"] = harness.quantiles([b - a for a, b in zip(plain, with_working)])
        points["lookup_ms"] = harness.quantiles(lookup)
        report["backlogs"][str(backlog)] = points
        print(backlog, json.dumps(points), flush=True)
    assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    working.close()
    await engine.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--backlogs", type=int, nargs="+", default=[0, 1000, 12000])
    parser.add_argument("--questions", type=int, default=10)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=1)
