"""P7 stage breakdown: split one get_assertion_state call into SQL, hydration and filter.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/stages.py \
        --pack /tmp/p6/lookup/pack --out research/address-lookup-2026-11/results/stages-20500.json

The current lookup pages every node of each queried node type through ``engine.scan_nodes`` and filters in Python.
This probe repeats that page loop inline with a timer around each part, so the report can name the largest cost
before any design is chosen:

1. ``sql_fetch_ms``  the statement and ``fetchall()``.
2. ``hydrate_ms``    ``_row_to_node`` for each row, which parses the metadata JSON and the evidence refs.
3. ``view_ms``       ``_owned_view_sync``, the owner and privacy view of the page.
4. ``filter_ms``     ``_assertion_values`` plus the two ``normalize_assertion_value`` calls per node.

Nothing in ``src/`` changes. The store is copied for each run because every ``retrieve()`` writes a receipt.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

sys.path.insert(0, os.path.dirname(HERE))
from probes.baseline import blocked_environment, build_engine, pick_pairs  # noqa: E402

SCRATCH = os.environ.get("P7_SCRATCH", "/tmp/p7")
USER = "robot-1"


async def main(args) -> dict:
    blocked_environment()
    from prme.retrieval.aggregation import _assertion_values, normalize_assertion_value
    from prme.types import LifecycleState, NodeType, Scope

    engine = await build_engine(os.path.join(SCRATCH, "stages"), args.pack)
    subject, predicate = pick_pairs(args.pack, 1)[0]
    target_subject = normalize_assertion_value("subject", subject)
    target_predicate = normalize_assertion_value("predicate", predicate)
    graph = await engine._ordinary_graph(USER, scopes=[Scope.PERSONAL])
    batch_size = 500
    totals = {"sql_fetch_ms": 0.0, "hydrate_ms": 0.0, "view_ms": 0.0, "filter_ms": 0.0}
    scanned = 0
    pages = 0
    matched = 0

    for node_type in (NodeType.FACT, NodeType.DECISION, NodeType.PREFERENCE):
        states = list(LifecycleState)
        conditions = ["lifecycle_state IN (" + ",".join("?" for _ in states) + ")"]
        params: list = [state.value for state in states]
        conditions += ["user_id = ?", "node_type = ?", "scope = ?"]
        params += [USER, node_type.value, Scope.PERSONAL.value]
        after = None
        while True:
            sql_params = list(params)
            where = " AND ".join(conditions + (["id > ?::UUID"] if after else []))
            if after:
                sql_params.append(after)
            sql_params.append(batch_size)
            sql = f"SELECT * FROM nodes WHERE {where} ORDER BY id ASC LIMIT ?"

            started = time.perf_counter()
            rows = await asyncio.to_thread(lambda s=sql, p=sql_params: graph._conn.execute(s, p).fetchall())
            totals["sql_fetch_ms"] += 1000 * (time.perf_counter() - started)

            started = time.perf_counter()
            nodes = [graph._row_to_node(row) for row in rows]
            totals["hydrate_ms"] += 1000 * (time.perf_counter() - started)

            started = time.perf_counter()
            page = graph._owned_view_sync(nodes, USER)
            totals["view_ms"] += 1000 * (time.perf_counter() - started)

            started = time.perf_counter()
            for node in page:
                values = _assertion_values(node)
                if values is None:
                    continue
                if (normalize_assertion_value("subject", values["subject"]) == target_subject
                        and normalize_assertion_value("predicate", values["predicate"]) == target_predicate):
                    matched += 1
            totals["filter_ms"] += 1000 * (time.perf_counter() - started)

            pages += 1
            scanned += len(page)
            if len(page) < batch_size:
                break
            after = str(page[-1].id)

    await engine.close()
    return {"pack": args.pack, "pair": f"{subject} / {predicate}", "pages": pages, "scanned_nodes": scanned,
            "matched": matched,
            **{name: round(value, 3) for name, value in totals.items()},
            "sum_ms": round(sum(totals.values()), 3)}


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
