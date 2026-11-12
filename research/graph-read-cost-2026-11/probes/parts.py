"""P8 bars 2 and 3: what each part of the graph candidate stage costs, on the current path and on the cheap one.

    PYTHONPATH=. .venv/bin/python research/graph-read-cost-2026-11/probes/parts.py \
        --pack /tmp/p8/m3-pack --out research/graph-read-cost-2026-11/results/parts-m3.json

Eight questions that find seeds, three repeats each, on one pack. Both paths run over the same engine and the
same store, so the two part lists are directly comparable:

| part            | current path                             | cheap path                              |
|-----------------|------------------------------------------|-----------------------------------------|
| walk SQL        | ``cur_sql``                              | ``cheap_sql``                           |
| walk decode     | ``cur_decode``, every reached row        | ``cheap_split``, five narrow columns     |
| order and cut   | ``cur_filter_sort``, in Python           | inside ``cheap_sql``                    |
| hydrate         | ``hydrate``                              | ``hydrate``                             |
| reach count     | ``reach``                                | ``reach``                               |
| reach records   | ``cur_decode`` also builds them          | ``reach_objs``, one per reached node     |

The reach count is the same call on both sides, so the difference between the two rows is the noise floor of this
probe, not a change.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

USER = "robot-1"
QUESTIONS = [
    "Where is the red mug now?", "Where did I leave the blue notebook?", "Where is the phone charger?",
    "Where is the tv remote?", "Where is the black umbrella?", "Where is the dog leash?",
    "Where is the screwdriver set?", "What is in the laundry room?",
]


def summary(values: list[float]) -> dict | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(ordered), "p50": round(statistics.median(ordered), 2),
            "mean": round(statistics.fmean(ordered), 2)}


async def main(args) -> dict:
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
                       "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                       "PRME_EXTRACTION_API_KEY": ""})
    from benchmarks.diagnostics.product_packing import gate_config
    from prme import MemoryEngine
    from prme.retrieval.candidates import _find_seed_entities, _seed_reach
    from prme.retrieval.query_analysis import analyze_query
    from prme.storage.seed_walk import seed_walk_sql, walked_nodes
    from prme.types import Scope

    import cheap_graph as cheap

    load_before = [round(value, 2) for value in os.getloadavg()]
    parts: dict[str, list[float]] = {name: [] for name in
                                     ("cur_sql", "cur_decode", "cur_filter_sort", "cheap_sql", "cheap_split",
                                      "reach_objects", "hydrate", "reach")}
    async with MemoryEngine.open(gate_config(Path(args.pack))) as engine:
        graph = engine._graph_store
        config = engine._config.packing
        for question in QUESTIONS:
            analysis = await analyze_query(question)
            seeds = await _find_seed_entities(analysis, graph, USER, [Scope.PERSONAL], config.graph_seed_words)
            seed_ids = [str(node.id) for node in seeds]
            if not seed_ids:
                continue
            params = [USER, 3, seed_ids]
            cheap_sql, extra = cheap.cheap_walk_sql(
                seed_walk_sql("duckdb", with_valid_at=False), limit=config.graph_max_candidates,
                scopes=[Scope.PERSONAL.value], first_parameter=4)
            for _ in range(args.repeats):
                started = time.perf_counter()
                rows = graph._conn.execute(seed_walk_sql("duckdb", with_valid_at=False), params).fetchall()
                parts["cur_sql"].append(1000 * (time.perf_counter() - started))

                started = time.perf_counter()
                decoded = walked_nodes(rows, epoch=True)
                parts["cur_decode"].append(1000 * (time.perf_counter() - started))

                started = time.perf_counter()
                node_map: dict = {}
                for node, _depth in decoded:
                    node_map.setdefault(str(node.id), node)
                node_map = {key: node for key, node in node_map.items() if node.user_id == USER}
                depths = {str(node.id): depth for node, depth in decoded}
                kept = sorted(node_map.values(),
                              key=lambda node: (-node.confidence, -node.created_at.timestamp(), str(node.id)))
                kept = kept[:config.graph_max_candidates]
                parts["cur_filter_sort"].append(1000 * (time.perf_counter() - started))

                started = time.perf_counter()
                await graph.get_nodes([str(node.id) for node in kept], user_id=USER)
                parts["hydrate"].append(1000 * (time.perf_counter() - started))

                started = time.perf_counter()
                await _seed_reach(graph, seeds, node_map, 3, depths)
                parts["reach"].append(1000 * (time.perf_counter() - started))

                started = time.perf_counter()
                cheap_rows = graph._conn.execute(cheap_sql, params + extra).fetchall()
                parts["cheap_sql"].append(1000 * (time.perf_counter() - started))

                started = time.perf_counter()
                cheap_depths, cheap_kept = cheap.split_rows(cheap_rows)
                reached: dict = {}
                plain = cheap._PlainNode("fact")
                for node_id, _depth, _is_kept, node_type, metadata in cheap_rows:
                    if node_id in reached:
                        continue
                    reached[node_id] = (cheap._ReachNode(node_id, node_type, metadata)
                                        if node_type == "entity" else plain)
                parts["cheap_split"].append(1000 * (time.perf_counter() - started))

                started = time.perf_counter()
                await graph.get_nodes([node_id for node_id, _depth in cheap_kept], user_id=USER)
                parts["hydrate"].append(1000 * (time.perf_counter() - started))

                started = time.perf_counter()
                await _seed_reach(graph, seeds, reached, 3, cheap_depths)
                parts["reach"].append(1000 * (time.perf_counter() - started))
                parts["reach_objects"].append(0.0)  # counted inside cheap_split; kept for a stable key set
    return {"pack": args.pack, "stored": args.stored, "questions": len(QUESTIONS), "repeats": args.repeats,
            "load_before": load_before, "load_after": [round(value, 2) for value in os.getloadavg()],
            "parts": {name: summary(values) for name, values in parts.items() if name != "reach_objects"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--stored", type=int, default=20500)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({key: result[key] for key in ("parts", "load_before", "load_after")}, indent=1))
