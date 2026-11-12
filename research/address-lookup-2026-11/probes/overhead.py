"""P7: why the inline page loop costs 805 ms and the real call costs 2,511 ms.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/overhead.py \
        --pack /tmp/p6/lookup/pack --out research/address-lookup-2026-11/results/overhead-20500.json

``stages.py`` repeats the page loop inline and totals 805 ms. The real ``get_assertion_state`` on the same pack
costs 2,511 ms. This probe accounts for the difference by timing the two calls the real page makes on every
iteration and the inline loop does not: ``engine._ordinary_graph`` (called once per page) and
``run_to_completion`` (the thread handoff plus the lock).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(HERE))
from probes.baseline import blocked_environment, build_engine  # noqa: E402

SCRATCH = os.environ.get("P7_SCRATCH", "/tmp/p7")
USER = "robot-1"


async def main(args) -> dict:
    blocked_environment()
    from prme.types import LifecycleState, NodeType, Scope

    engine = await build_engine(os.path.join(SCRATCH, "overhead"), args.pack)

    started = time.perf_counter()
    for _ in range(20):
        await engine._ordinary_graph(USER, scopes=[Scope.PERSONAL])
    ordinary = 1000 * (time.perf_counter() - started) / 20

    pages = 0
    page_ms = []
    cursor = None
    while True:
        started = time.perf_counter()
        page = await engine.scan_nodes(user_id=USER, scope=Scope.PERSONAL, node_type=NodeType.FACT,
                                      lifecycle_states=list(LifecycleState), after_id=cursor, limit=500)
        page_ms.append(1000 * (time.perf_counter() - started))
        pages += 1
        if len(page) < 500:
            break
        cursor = str(page[-1].id)

    await engine.close()
    ordered = sorted(page_ms)
    return {"pack": args.pack, "pages": pages,
            "ordinary_graph_ms": round(ordinary, 3),
            "scan_page_ms": {"p50": round(ordered[len(ordered) // 2], 3), "max": round(ordered[-1], 3),
                             "total": round(sum(page_ms), 3),
                             "mean": round(sum(page_ms) / len(page_ms), 3)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--out", default=None)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    text = json.dumps(result, indent=1)
    if arguments.out:
        with open(arguments.out, "w") as handle:
            handle.write(text)
    print(text, flush=True)
