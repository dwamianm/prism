"""P7: attribute one ``scan_nodes`` page across SQL, row conversion and the owner view.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/page_parts.py \
        --pack /tmp/p6/lookup/pack --out research/address-lookup-2026-11/results/page-parts-20500.json

``overhead.py`` shows one page of 500 nodes costing 56.7 ms, while the same SQL plus row conversion measured
inline in ``stages.py`` costs about 12 ms. This probe times the pieces inside ``scan_nodes`` itself by wrapping
the graph's own ``_row_to_node`` and ``_owned_view_sync``, so the difference is attributed rather than inferred.
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

    engine = await build_engine(os.path.join(SCRATCH, "page-parts"), args.pack)
    graph = await engine._ordinary_graph(USER, scopes=[Scope.PERSONAL])

    totals = {"row_to_node_ms": 0.0, "owned_view_ms": 0.0}
    calls = {"row_to_node": 0, "owned_view": 0}
    original_row = graph._row_to_node
    original_view = graph._owned_view_sync

    def timed_row(row):
        started = time.perf_counter()
        try:
            return original_row(row)
        finally:
            totals["row_to_node_ms"] += 1000 * (time.perf_counter() - started)
            calls["row_to_node"] += 1

    def timed_view(nodes, user_id):
        started = time.perf_counter()
        try:
            return original_view(nodes, user_id)
        finally:
            totals["owned_view_ms"] += 1000 * (time.perf_counter() - started)
            calls["owned_view"] += 1

    graph._row_to_node = timed_row
    graph._owned_view_sync = timed_view

    # One page, repeated, so the split is stable and unrelated to paging order.
    states = list(LifecycleState)
    sql = ("SELECT * FROM nodes WHERE lifecycle_state IN (" + ",".join("?" for _ in states) + ") "
           "AND user_id = ? AND node_type = ? AND scope = ? ORDER BY id ASC LIMIT 500")
    params = [state.value for state in states] + [USER, NodeType.FACT.value, Scope.PERSONAL.value]

    def read():
        rows = graph._conn.execute(sql, params).fetchall()
        nodes = [graph._row_to_node(row) for row in rows]
        return graph._owned_view_sync(nodes, USER)

    from prme.storage._threading import run_to_completion

    wall = []
    for _ in range(10):
        totals["row_to_node_ms"] = 0.0
        totals["owned_view_ms"] = 0.0
        calls["row_to_node"] = 0
        calls["owned_view"] = 0
        started = time.perf_counter()
        page = await run_to_completion(read)
        wall.append(1000 * (time.perf_counter() - started))
        parts = {name: round(value, 3) for name, value in totals.items()}
        calls_now = dict(calls)
    rows_in_page = len(page)
    await engine.close()
    ordered = sorted(wall)
    return {"pack": args.pack, "rows": rows_in_page, "reads": len(wall),
            "wall_ms": {"p50": round(ordered[len(ordered) // 2], 3), "min": round(ordered[0], 3),
                        "max": round(ordered[-1], 3)},
            "last_read_parts_ms": parts,
            "last_read_calls": calls_now,
            "row_to_node_ms_per_call": round(parts["row_to_node_ms"] / max(1, calls_now["row_to_node"]), 4),
            "owned_view_ms_per_call": round(parts["owned_view_ms"] / max(1, calls_now["owned_view"]), 4),
            "sql_and_overhead_ms": round(ordered[len(ordered) // 2] - parts["row_to_node_ms"] - parts["owned_view_ms"], 3)}


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
