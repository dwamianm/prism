"""Read latency beside the write numbers (REPORT.md, "Bar 2"): how long one ``retrieve()`` takes on an idle store.

    PYTHONPATH=. uv run python research/write-rate-2026-11/read_latency.py \
        --out data/write-rate-2026-11/read-latency.json

Not one of PROTOCOL.md's measurements. The M3 stream test at 20,500 stored found each sample about 19 s after its
commit, so this times ``retrieve()`` alone: the kept packs at 400 and 4,000 stored (current path) and the M3 pack at
20,500 stored (prototype path, whose setting does nothing when reading), each on a scratch copy, with no write
running and the opportunistic organizer off. Five "Where is the {object} now?" questions per size, from
``m4_compare.questions``, asked once each after one warm-up question.
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
import m4_compare  # noqa: E402

PACKS = {400: "data/write-rate-2026-11/m4/current-a/400/pack",
         4000: "data/write-rate-2026-11/m4/current-a/4000/pack",
         20500: "data/write-rate-2026-11/m3-pack/pack"}
SCRATCH = "/tmp/p2/read-latency"


async def main(args) -> dict:
    common.blocked_environment()
    provider = await common.embedding_provider()
    report = {"organizer": "opportunistic off", "sizes": {}}
    for stored, pack in PACKS.items():
        directory = os.path.join(SCRATCH, str(stored))
        if os.path.exists(directory):
            shutil.rmtree(directory)
        shutil.copytree(pack, os.path.join(directory, "pack"))
        engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
        asked = [question for question, expected in m4_compare.questions(min(stored, 20000)) if expected][:6]
        seconds, results = [], []
        try:
            await engine.retrieve(asked[0], user_id=common.USER)
            for question in asked[1:]:
                started = time.perf_counter()
                response = await engine.retrieve(question, user_id=common.USER)
                seconds.append(time.perf_counter() - started)
                results.append(len(response.results))
            assert not guard.misses, f"a model was called: {guard.misses[:3]}"
        finally:
            await engine.close()
        report["sizes"][str(stored)] = {"questions": len(seconds), "seconds": [round(s, 3) for s in seconds],
                                        "median_s": round(statistics.median(seconds), 3),
                                        "results_returned": results}
        print(stored, json.dumps(report["sizes"][str(stored)]), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=1)
