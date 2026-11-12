"""P8 bar 2: what the graph candidate stage does with the edges, row by row.

    PYTHONPATH=. .venv/bin/python research/graph-read-cost-2026-11/probes/graph_cost.py \
        --pack /tmp/p8/m3-pack --label m3 --out research/graph-read-cost-2026-11/results/graph-cost-m3.json

``stages.py`` says the graph candidate branch is the whole difference between the two packs. This probe opens that
branch and reports, per question:

- how many seeds the question finds, and how many rows the walk returns;
- how many of those rows the Python filters drop, by reason, and how many reach the 150-candidate cut;
- how many of the kept nodes are hydrated with one more SQL read;
- the same read split into the SQL fetch, the row decode, the filter loop, the sort and the hydration.

The walk's SQL is run again here with the same seeds and the same parameters the read used, so its own three parts
can be timed separately. Nothing is re-implemented: the query text comes from ``prme.storage.seed_walk``, and the
decode is ``walked_nodes`` itself.

Every question is read through ``retrieve()`` once per round with the counters cleared, so the counts are per read.
The pack is read through a scratch copy.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import inspect
import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

USER = "robot-1"
QUESTIONS = [
    "Where is the red mug now?", "Where are the house keys?", "Where did I leave the blue notebook?",
    "Where is the phone charger?", "Where are the reading glasses?", "Where is the tv remote?",
    "Where is the black umbrella?", "Where is the dog leash?", "Where is the screwdriver set?",
    "Where is the first aid kit?",
    "What is on the kitchen counter?", "What is on the office desk?", "What is in the garage workbench area?",
    "Is the front porch clear?", "What is in the laundry room?", "What changed since the last pass?",
    "Summarize the last hour.", "What happened in the hallway this morning?", "Did a delivery arrive?",
    "Is there any leak anywhere?",
]
REFERENCE_TIME = "2026-11-05T00:00:00+00:00"

COUNT: dict[str, float] = {}
TIME: dict[str, float] = {}


def add(name: str, value: float = 1.0) -> None:
    COUNT[name] = COUNT.get(name, 0.0) + value


def timed(name: str, function):
    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return await function(*args, **kwargs)
            finally:
                TIME[name] = TIME.get(name, 0.0) + 1000 * (time.perf_counter() - started)
    else:
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                TIME[name] = TIME.get(name, 0.0) + 1000 * (time.perf_counter() - started)
    return wrapped


def install() -> None:
    import prme.retrieval.candidates as candidates
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.seed_walk import WalkedNode

    # One wrapper per store method, so the timing is not lost to a second assignment. Each counts the rows it
    # returns as well as its time.
    real_walk = DuckPGQGraphStore.walk_from_seeds
    real_links = DuckPGQGraphStore.walk_links
    real_nodes = DuckPGQGraphStore.get_nodes
    real_query = DuckPGQGraphStore.query_nodes

    @functools.wraps(real_walk)
    async def walk(self, seed_ids, **kwargs):
        started = time.perf_counter()
        result = await real_walk(self, seed_ids, **kwargs)
        TIME["walk_from_seeds.ms"] = TIME.get("walk_from_seeds.ms", 0.0) + 1000 * (time.perf_counter() - started)
        TIME["walk_calls"] = TIME.get("walk_calls", 0.0) + 1
        TIME["walk_seeds"] = TIME.get("walk_seeds", 0.0) + len(seed_ids)
        TIME["walk_rows"] = TIME.get("walk_rows", 0.0) + len(result)
        return result

    @functools.wraps(real_links)
    async def links(self, node_ids):
        started = time.perf_counter()
        result = await real_links(self, node_ids)
        TIME["walk_links.ms"] = TIME.get("walk_links.ms", 0.0) + 1000 * (time.perf_counter() - started)
        TIME["walk_links_rows"] = TIME.get("walk_links_rows", 0.0) + len(result)
        TIME["walk_links_nodes"] = TIME.get("walk_links_nodes", 0.0) + len(node_ids)
        return result

    @functools.wraps(real_nodes)
    async def nodes(self, node_ids, **kwargs):
        started = time.perf_counter()
        result = await real_nodes(self, node_ids, **kwargs)
        TIME["get_nodes.ms"] = TIME.get("get_nodes.ms", 0.0) + 1000 * (time.perf_counter() - started)
        TIME["get_nodes_rows"] = TIME.get("get_nodes_rows", 0.0) + len(result)
        return result

    @functools.wraps(real_query)
    async def query(self, **kwargs):
        started = time.perf_counter()
        result = await real_query(self, **kwargs)
        TIME["query_nodes.ms"] = TIME.get("query_nodes.ms", 0.0) + 1000 * (time.perf_counter() - started)
        TIME["seed_query_rows"] = TIME.get("seed_query_rows", 0.0) + len(result)
        TIME["seed_query_calls"] = TIME.get("seed_query_calls", 0.0) + 1
        return result

    DuckPGQGraphStore.walk_from_seeds = walk
    DuckPGQGraphStore.walk_links = links
    DuckPGQGraphStore.get_nodes = nodes
    DuckPGQGraphStore.query_nodes = query

    # The graph branch itself, so the Python part can be the branch less its SQL calls.
    real_graph = candidates._generate_graph_candidates
    candidates._generate_graph_candidates = timed("graph_branch.ms", real_graph)


async def replay_walk(engine, seed_ids, max_hops: int, valid_at) -> dict:
    """The walk's own SQL again, split into fetch, decode and count."""
    from prme.storage.seed_walk import seed_walk_sql, walked_nodes

    graph = engine._graph_store
    query = seed_walk_sql("duckdb", with_valid_at=valid_at is not None)
    params: list = [USER, max_hops, list(seed_ids)]
    if valid_at is not None:
        params.append(valid_at)
    started = time.perf_counter()
    rows = graph._conn.execute(query, params).fetchall()
    fetched = time.perf_counter() - started
    started = time.perf_counter()
    decoded = walked_nodes(rows, epoch=True)
    decode = time.perf_counter() - started
    return {"sql_ms": 1000 * fetched, "decode_ms": 1000 * decode, "rows": len(rows), "decoded": len(decoded)}


def load() -> list[float]:
    return [round(value, 2) for value in os.getloadavg()]


def summary(values: list[float]) -> dict:
    ordered = sorted(values)
    def at(fraction: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))], 3)
    return {"count": len(ordered), "p50": at(0.50), "p95": at(0.95), "mean": round(statistics.fmean(ordered), 3)}


async def main(args) -> dict:
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
                       "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                       "PRME_EXTRACTION_API_KEY": ""})
    from datetime import datetime

    from benchmarks.diagnostics.product_packing import gate_config
    from prme import MemoryEngine

    when = datetime.fromisoformat(REFERENCE_TIME)
    work = Path(args.work or f"/tmp/p8/work/{args.label}-cost")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    shutil.copytree(Path(args.pack), work / "pack")

    install()
    per_read: list[dict] = []
    walk_replays: list[dict] = []
    totals: list[float] = []
    load_before = load()
    async with MemoryEngine.open(gate_config(work / "pack")) as engine:
        await engine.retrieve("warm-up question about the house", user_id=USER, reference_time=when)
        from prme.retrieval.query_analysis import analyze_query
        for _ in range(args.rounds):
            for question in QUESTIONS:
                COUNT.clear(); TIME.clear()
                started = time.perf_counter()
                await engine.retrieve(question, user_id=USER, reference_time=when)
                totals.append(1000 * (time.perf_counter() - started))
                per_read.append({"question": question, **{k: v for k, v in COUNT.items()},
                                 **{k: v for k, v in TIME.items()}})
                # The same walk, run again from the same seeds, split into its parts.
                from prme.retrieval.candidates import _find_seed_entities
                analysis = await analyze_query(question)
                seeds = await _find_seed_entities(analysis, engine._graph_store, USER, None, True)
                if seeds:
                    walk_replays.append({"question": question,
                                         **await replay_walk(engine, [str(s.id) for s in seeds], 3, None)})
    load_after = load()

    def column(name: str) -> dict:
        values = [row.get(name, 0.0) for row in per_read]
        return summary(values) if values else {"count": 0}

    return {
        "pack": args.pack, "label": args.label, "stored": args.stored, "rounds": args.rounds,
        "reads": len(totals), "load_before": load_before, "load_after": load_after,
        "total_ms": summary(totals),
        "per_read": per_read,
        "columns": {name: column(name) for name in
                    ("walk_seeds", "walk_rows", "walk_links_rows", "walk_links_nodes", "get_nodes_rows",
                     "seed_query_rows", "seed_query_calls", "walk_from_seeds.ms", "walk_links.ms",
                     "get_nodes.ms", "query_nodes.ms", "graph_branch.ms")},
        "walk_replay": {
            "questions": len(walk_replays),
            "sql_ms": summary([row["sql_ms"] for row in walk_replays]) if walk_replays else {"count": 0},
            "decode_ms": summary([row["decode_ms"] for row in walk_replays]) if walk_replays else {"count": 0},
            "rows": summary([float(row["rows"]) for row in walk_replays]) if walk_replays else {"count": 0},
            "detail": walk_replays,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--stored", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--work", default=None)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps({"total_ms": result["total_ms"], "columns": result["columns"],
                      "walk_replay": {k: v for k, v in result["walk_replay"].items() if k != "detail"}},
                     indent=1), flush=True)
