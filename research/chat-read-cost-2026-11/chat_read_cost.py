"""P4: read cost of chat stores (ingest() packs), DuckDB backend, current defaults, organizer off.

For each pack: node, entity and edge counts and entity degree, then one warm-up read and 20 timed
retrieve() calls with the benchmark's own questions. Per read: total time, time in
_generate_graph_candidates, the neighborhood walk (SQL plus record building), _seed_reach, seeds,
rows the walks returned, distinct nodes they reached and graph candidates kept.

No engine change: the script wraps functions on a scratch copy's engine and only times them.

Questions. LoCoMo: the conversation's questions in the order of sha256(f"{SEED}:{question_id}")
(the mid tier's seeded hash); the first is the warm-up, the next 20 are timed. LongMemEval-S: each
pack holds one question's history, so the pack's own question is timed first, then 19 other
LongMemEval-S questions in the seeded order, with the 20th other question as the warm-up. All reads
use the pack's reference time.

Usage (providers blocked, see REPORT.md):
    .venv/bin/python research/chat-read-cost-2026-11/chat_read_cost.py --packs /tmp/p4/packs \
        --scratch /tmp/p4/scratch/run1 --out /tmp/p4/out/run1.json
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import functools
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SEED = 20261019  # benchmarks/mid_tier.py SEED
TIMED = 20


def _rank(question_id: str) -> str:
    return hashlib.sha256(f"{SEED}:{question_id}".encode()).hexdigest()


def questions_for(benchmark: str, unit_id: str, rows: list[dict]) -> tuple[str, list[str]]:
    if benchmark == "locomo":
        own = sorted((r for r in rows if r["conversation_id"] == unit_id), key=lambda r: _rank(r["question_id"]))
        return own[0]["question"], [r["question"] for r in own[1:TIMED + 1]]
    own = [r for r in rows if r["question_id"] == unit_id]
    others = sorted((r for r in rows if r["question_id"] != unit_id), key=lambda r: _rank(r["question_id"]))
    return others[TIMED - 1]["question"], [own[0]["question"]] + [r["question"] for r in others[:TIMED - 1]]


def shape(con) -> dict:
    nodes = con.execute("select count(*) from nodes").fetchone()[0]
    entities = con.execute("select count(*) from nodes where node_type = 'entity'").fetchone()[0]
    edges = con.execute("select count(*) from edges").fetchone()[0]
    # Degree counts every edge end; entities with no edge count as 0.
    mean, top, p90 = con.execute("""
        with d as (select id, count(*) c from (select source_id id from edges union all
                                               select target_id from edges) group by id)
        select avg(coalesce(d.c, 0)), max(coalesce(d.c, 0)), quantile_cont(coalesce(d.c, 0), 0.9)
        from nodes n left join d using (id) where n.node_type = 'entity'""").fetchone()
    hubs = con.execute("""
        with d as (select id, count(*) c from (select source_id id from edges union all
                                               select target_id from edges) group by id)
        select left(n.content, 40), d.c from d join nodes n using (id) where n.node_type = 'entity'
        order by d.c desc limit 3""").fetchall()
    return {"nodes": nodes, "entities": entities, "edges": edges, "entity_degree_mean": round(mean, 2),
            "entity_degree_p90": round(p90, 1), "entity_degree_max": top, "top_hubs": hubs}


def p95(values: list[float]) -> float:
    """Nearest rank: the ceil(0.95 n)-th smallest value (the 19th of 20)."""
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


class Probe:
    """Times the graph stages of one read. Wraps module functions and the store's sync walk."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.seconds = collections.defaultdict(float)
        self.seeds = 0
        self.walks = 0
        self.rows = 0
        self.reached: set[str] = set()
        self.kept = 0

    def install(self, store) -> None:
        import prme.retrieval.candidates as candidates

        probe = self
        real_graph, real_seeds, real_reach = (candidates._generate_graph_candidates, candidates._find_seed_entities,
                                              candidates._seed_reach)
        real_vector = candidates._generate_vector_candidates

        @functools.wraps(real_vector)
        async def vector(*args, **kwargs):
            started = time.perf_counter()
            try:
                return await real_vector(*args, **kwargs)
            finally:
                probe.seconds["vector_candidates"] += time.perf_counter() - started

        @functools.wraps(real_graph)
        async def graph(*args, **kwargs):
            started = time.perf_counter()
            try:
                found = await real_graph(*args, **kwargs)
                probe.kept = len(found)
                return found
            finally:
                probe.seconds["graph_candidates"] += time.perf_counter() - started

        @functools.wraps(real_seeds)
        async def seeds(*args, **kwargs):
            started = time.perf_counter()
            found = await real_seeds(*args, **kwargs)
            probe.seconds["seed_lookup"] += time.perf_counter() - started
            probe.seeds += len(found)
            return found

        @functools.wraps(real_reach)
        async def reach(*args, **kwargs):
            started = time.perf_counter()
            try:
                return await real_reach(*args, **kwargs)
            finally:
                probe.seconds["seed_reach"] += time.perf_counter() - started

        real_walk = store._get_neighborhood_with_depth_sync

        def walk(*args, **kwargs):
            started = time.perf_counter()
            rows = real_walk(*args, **kwargs)
            probe.seconds["walk"] += time.perf_counter() - started
            probe.walks += 1
            probe.rows += len(rows)
            probe.reached.update(str(node.id) for node, _depth in rows)
            return rows

        candidates._generate_graph_candidates = graph
        candidates._generate_vector_candidates = vector
        candidates._find_seed_entities = seeds
        candidates._seed_reach = reach
        store._get_neighborhood_with_depth_sync = walk


async def measure_pack(benchmark: str, folder: Path, scratch: Path, rows: list[dict]) -> dict:
    from benchmarks.diagnostics.product_packing import _warm_up, gate_config
    from prme import MemoryEngine

    manifest = json.loads((folder / "manifest.json").read_text())
    unit_id, user_id = manifest["unit_id"], manifest["user_id"]
    reference_time = datetime.fromisoformat(manifest["reference_time"])
    copy = scratch / f"{benchmark}-{folder.name}"
    if copy.exists():
        raise SystemExit(f"Scratch path already exists: {copy}")
    copy.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-cR", str(folder / "pack"), str(copy)], check=True)
    warm, timed = questions_for(benchmark, unit_id, rows)
    config = gate_config(copy)
    assert config.organizer.opportunistic_enabled is False
    reads = []
    async with MemoryEngine.open(config) as engine:
        store = engine._graph_store
        pack_shape = shape(store._conn)
        probe = Probe()
        probe.install(store)
        await _warm_up(engine, user_id)
        await engine.retrieve(warm, user_id=user_id, reference_time=reference_time)
        for question in timed:
            probe.reset()
            started = time.perf_counter()
            response = await engine.retrieve(question, user_id=user_id, reference_time=reference_time)
            total = time.perf_counter() - started
            reads.append({
                "question": question, "retrieve_ms": round(1000 * total, 2),
                **{f"{name}_ms": round(1000 * value, 2) for name, value in probe.seconds.items()},
                "seeds": probe.seeds, "walks": probe.walks, "rows_returned": probe.rows,
                "nodes_reached": len(probe.reached), "graph_kept": probe.kept,
                "results": len(response.results),
            })
    totals = [r["retrieve_ms"] for r in reads]
    graph = [r.get("graph_candidates_ms", 0.0) for r in reads]
    return {
        "benchmark": benchmark, "unit_id": unit_id, "scale": manifest.get("scale", 1), "turns": manifest["turns"],
        **pack_shape,
        "retrieve_ms_median": round(statistics.median(totals), 1), "retrieve_ms_p95": round(p95(totals), 1),
        "graph_ms_median": round(statistics.median(graph), 1), "graph_ms_p95": round(p95(graph), 1),
        "graph_share_of_total": round(sum(graph) / sum(totals), 3),
        "walk_ms_median": round(statistics.median(r.get("walk_ms", 0.0) for r in reads), 1),
        "walk_ms_p95": round(p95([r.get("walk_ms", 0.0) for r in reads]), 1),
        "vector_ms_median": round(statistics.median(r.get("vector_candidates_ms", 0.0) for r in reads), 1),
        "seed_reach_ms_median": round(statistics.median(r.get("seed_reach_ms", 0.0) for r in reads), 1),
        "seeds_median": statistics.median(r["seeds"] for r in reads), "seeds_max": max(r["seeds"] for r in reads),
        "rows_returned_median": statistics.median(r["rows_returned"] for r in reads),
        "rows_returned_max": max(r["rows_returned"] for r in reads),
        "nodes_reached_median": statistics.median(r["nodes_reached"] for r in reads),
        "graph_kept_median": statistics.median(r["graph_kept"] for r in reads),
        "reads": reads,
    }


async def main(args) -> None:
    from benchmarks.diagnostics.product_packing import _harness

    harness = _harness()
    results = []
    for benchmark in args.benchmarks:
        rows = harness.question_rows(benchmark)
        for folder in sorted((args.packs / benchmark).iterdir()):
            if args.units and folder.name not in args.units:
                continue
            result = await measure_pack(benchmark, folder, args.scratch, rows)
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k not in ("reads", "top_hubs")}), flush=True)
    import duckdb
    import prme

    report = {"provenance": {"engine": prme.__version__, "duckdb": duckdb.__version__, "python": sys.version.split()[0],
                             "platform": platform.platform(), "load_1m_at_end": round(__import__("os").getloadavg()[0], 2),
                             "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                                      text=True).stdout.strip()},
              "packs": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--benchmarks", nargs="+", default=["locomo", "longmemeval"])
    parser.add_argument("--units", nargs="*", default=[])
    asyncio.run(main(parser.parse_args()))
