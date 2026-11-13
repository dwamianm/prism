"""Task 26 Part A: where the seed reach count (``_seed_reach``) spends its time, and how often its limit turns it off.

    cd ~/Sites/prism-seed-reach && PYTHONPATH=src:. .venv/bin/python /tmp/task26/probes/parts.py \
        --pack /tmp/task26/packs/m3 --kind m3 --timed 20 --repeats 3 --out /tmp/task26/results/parts-m3.json

The engine opens a scratch copy of the pack with the evidence gate's configuration (current defaults on ``main``,
address-first on, seed count on, environment ignored, organizer off). ``candidates._seed_reach`` is replaced by
an instrumented copy that runs the same steps with a timer between them and returns the same value; on every call
the original is also run (untimed) and the two results must be equal. Parts:

- ``setup``: merge the reached nodes and the seeds, find the owner entities, build the expandable set.
- ``links_await``: the whole ``walk_links`` await (lock, thread hop, SQL, conversion).
- ``links_sql``: inside the worker thread, ``execute`` plus ``fetchall`` of the ``walk_links`` query.
- ``links_convert``: inside the worker thread, the list of (source, target) tuples.
- ``adjacency``: the adjacency sets built from the links.
- ``walk``: the per-seed breadth-first walks in Python.
- ``result``: the returned dict.

When more than ``SEED_REACH_MAX_NODES`` nodes are expandable the original returns None; the copy then also times
the read and the walk it skipped (``limit_*``), so the cost of raising the limit is measured, and still returns
None. Every question of a LoCoMo pack is read once untimed (``--all``) to count how many hit the limit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                   "TOKENIZERS_PARALLELISM": "false"})
sys.path.insert(0, os.getcwd())

M3_USER = "robot-1"
M3_TIME = datetime.fromisoformat("2026-11-05T00:00:00+00:00")
M3_QUESTIONS = [
    "Where is the red mug now?", "Where are the house keys?", "Where did I leave the blue notebook?",
    "Where is the phone charger?", "Where are the reading glasses?", "Where is the tv remote?",
    "Where is the black umbrella?", "Where is the dog leash?", "Where is the screwdriver set?",
    "Where is the first aid kit?",
    "What is on the kitchen counter?", "What is on the office desk?", "What is in the garage workbench area?",
    "Is the front porch clear?", "What is in the laundry room?", "What changed since the last pass?",
    "Summarize the last hour.", "What happened in the hallway this morning?", "Did a delivery arrive?",
    "Is there a leak anywhere?",
]
SEED = 20261019  # the question order of the #565 read cost check (/tmp/flip/latscript/lat.py)
PARTS = ("setup", "links_await", "links_sql", "links_convert", "adjacency", "walk", "result")


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


class Probe:
    """The instrumented ``_seed_reach`` and its per-call records."""

    def __init__(self, candidates, original):
        self.candidates = candidates
        self.original = original
        self.calls: list[dict] = []
        self.active = True
        self.verify = True

    async def links(self, graph_store, expandable, record, prefix):
        from prme.storage.seed_walk import walk_links_sql
        from prme.storage.duckpgq_graph import _uuid_literals
        from prme.storage._threading import run_to_completion

        ids = list(expandable)
        literals = _uuid_literals(ids)
        if literals is not None:
            query, params = walk_links_sql("duckdb", literals), []
        else:
            query, params = walk_links_sql("duckdb", ", ".join("?" for _ in ids)), ids + ids

        def read():
            started = time.perf_counter()
            rows = graph_store._conn.execute(query, params).fetchall()
            middle = time.perf_counter()
            links = [(source, target) for source, target in rows]
            record[f"{prefix}links_sql"] = 1000 * (middle - started)
            record[f"{prefix}links_convert"] = 1000 * (time.perf_counter() - middle)
            return links

        started = time.perf_counter()
        async with graph_store._conn_lock:
            links = await run_to_completion(read)
        record[f"{prefix}links_await"] = 1000 * (time.perf_counter() - started)
        return links

    def walk(self, seeds, known, owners, links, max_hops, record, prefix):
        started = time.perf_counter()
        adjacency: dict[str, set[str]] = defaultdict(set)
        for source, target in links:
            if source in known and target in known:
                adjacency[source].add(target)
                adjacency[target].add(source)
        middle = time.perf_counter()
        hops: dict[str, dict[str, int]] = defaultdict(dict)
        visits = 0
        for seed in seeds:
            seed_id = str(seed.id)
            seen, frontier = {seed_id}, [seed_id]
            for depth in range(1, max_hops + 1):
                reached = []
                for node_id in frontier:
                    if node_id in owners and node_id != seed_id:
                        continue
                    neighbors = adjacency[node_id]
                    visits += len(neighbors)
                    for other in neighbors:
                        if other not in seen:
                            seen.add(other)
                            reached.append(other)
                            hops[other][seed_id] = depth
                frontier = reached
        after_walk = time.perf_counter()
        result = {
            node_id: (len(by_seed), sum(by_seed.values()), min(by_seed.values()))
            for node_id, by_seed in hops.items()
        }
        record[f"{prefix}adjacency"] = 1000 * (middle - started)
        record[f"{prefix}walk"] = 1000 * (after_walk - middle)
        record[f"{prefix}result"] = 1000 * (time.perf_counter() - after_walk)
        record[f"{prefix}adjacency_nodes"] = len(adjacency)
        record[f"{prefix}edge_visits"] = visits
        record[f"{prefix}reached"] = len(result)
        return result

    async def __call__(self, graph_store, seeds, nodes, max_hops, depths=None):
        if not self.active:
            return await self.original(graph_store, seeds, nodes, max_hops, depths)
        result = await self.timed(graph_store, seeds, nodes, max_hops, depths)
        if self.verify:
            # The original runs after the timed copy, so the timed read is never the second of two.
            expected = await self.original(graph_store, seeds, nodes, max_hops, depths)
            assert result == expected, "instrumented seed reach differs from the original"
            self.calls[-1]["verified"] = True
        return result

    async def timed(self, graph_store, seeds, nodes, max_hops, depths):
        record: dict = {"seeds": len(seeds), "reached_nodes": len(nodes)}
        total_started = time.perf_counter()
        if len(seeds) < 2:
            record.update(total=1000 * (time.perf_counter() - total_started), outcome="fewer_than_two_seeds")
            self.calls.append(record)
            return {}
        from prme.ingestion.owner_name import OWNER_NAME_KEY

        started = time.perf_counter()
        known = {**nodes, **{str(seed.id): seed for seed in seeds}}
        owners = {node_id for node_id, node in known.items()
                  if self.candidates._is_owner_entity(node, OWNER_NAME_KEY)}
        seed_ids = {str(seed.id) for seed in seeds}
        expandable = (
            set(known) if depths is None
            else seed_ids | {node_id for node_id, depth in depths.items() if depth < max_hops}
        ) - (owners - seed_ids)
        record["setup"] = 1000 * (time.perf_counter() - started)
        record.update(known=len(known), owners=len(owners), expandable=len(expandable))
        if len(expandable) > self.candidates.SEED_REACH_MAX_NODES:
            record["total"] = 1000 * (time.perf_counter() - total_started)
            record["outcome"] = "limit"
            # What the limit saves: the read and the walk it skips.
            links = await self.links(graph_store, expandable, record, "limit_")
            record["limit_links"] = len(links)
            self.walk(seeds, known, owners, links, max_hops, record, "limit_")
            self.calls.append(record)
            return None
        links = await self.links(graph_store, expandable, record, "")
        record["links"] = len(links)
        result = self.walk(seeds, known, owners, links, max_hops, record, "")
        record["total"] = 1000 * (time.perf_counter() - total_started)
        record["outcome"] = "walked"
        self.calls.append(record)
        return result


def rank(question_id: str) -> str:
    return hashlib.sha256(f"{SEED}:{question_id}".encode()).hexdigest()


async def main(args) -> dict:
    from benchmarks.diagnostics.product_packing import _quiet_offline_cli, _warm_up, gate_config
    from prme import MemoryEngine
    import prme
    from prme.retrieval import candidates

    assert Path(prme.__file__).resolve().is_relative_to(Path.cwd().resolve()), prme.__file__
    _quiet_offline_cli()
    pack = Path(args.pack)
    if args.kind == "m3":
        user_id, reference_time = M3_USER, M3_TIME
        timed, every = M3_QUESTIONS[:args.timed], []
    else:
        from benchmarks.diagnostics.product_packing import _harness

        manifest = json.loads(Path(f"{args.pack}-manifest.json").read_text())
        user_id, reference_time = manifest["user_id"], datetime.fromisoformat(manifest["reference_time"])
        rows = sorted((r for r in _harness().question_rows("locomo") if r["conversation_id"] == manifest["unit_id"]),
                      key=lambda r: rank(r["question_id"]))
        timed = [r["question"] for r in rows[:args.timed]]
        every = [r["question"] for r in rows] if args.all else []

    copy = Path(args.scratch) / f"{pack.name}-parts"
    shutil.rmtree(copy, ignore_errors=True)
    subprocess.run(["cp", "-cR", str(pack), str(copy)], check=True)
    probe = Probe(candidates, candidates._seed_reach)
    candidates._seed_reach = probe
    stage_ms: list[float] = []
    original_stage = candidates._generate_graph_candidates

    async def timed_stage(*a, **k):
        started = time.perf_counter()
        try:
            return await original_stage(*a, **k)
        finally:
            stage_ms.append(1000 * (time.perf_counter() - started))

    candidates._generate_graph_candidates = timed_stage
    config = gate_config(copy, json.loads(args.set))
    settings = {"memory_mode": config.memory_mode, "fusion": config.scoring.fusion,
                "rrf_graph_seed_count": config.scoring.rrf_graph_seed_count,
                "rrf_graph_weight": config.scoring.rrf_graph_weight, "graph_max_hops": config.packing.graph_max_hops,
                "graph_max_candidates": config.packing.graph_max_candidates,
                "graph_seed_words": config.packing.graph_seed_words}
    load_before = [round(v, 2) for v in os.getloadavg()]
    per_question: list[dict] = []
    limit_count = {"questions": 0, "limit": 0, "walked": 0, "fewer_than_two_seeds": 0, "no_seed_reach_call": 0,
                   "limit_expandable": [], "metadata_neighborhood_limit": 0}
    try:
        async with MemoryEngine.open(config) as engine:
            await _warm_up(engine, user_id)
            probe.active = False
            await engine.retrieve(timed[0], user_id=user_id, reference_time=reference_time)
            probe.active = True
            for repeat in range(args.repeats):
                # Repeat 0 also runs the original and checks the result; later repeats time the read alone.
                probe.verify = repeat == 0
                for question in timed:
                    before = len(probe.calls)
                    stage_before = len(stage_ms)
                    started = time.perf_counter()
                    response = await engine.retrieve(question, user_id=user_id, reference_time=reference_time)
                    read_ms = 1000 * (time.perf_counter() - started)
                    calls = probe.calls[before:]
                    per_question.append({
                        "repeat": repeat, "question": question, "read_ms": read_ms,
                        "graph_stage_ms": sum(stage_ms[stage_before:]),
                        "graph_seed_reach_metadata": response.metadata.model_dump(mode="json").get("graph_seed_reach"),
                        "calls": calls,
                    })
            probe.verify = True
            for question in every:
                before = len(probe.calls)
                response = await engine.retrieve(question, user_id=user_id, reference_time=reference_time)
                calls = probe.calls[before:]
                limit_count["questions"] += 1
                if not calls:
                    limit_count["no_seed_reach_call"] += 1
                for call in calls:
                    limit_count[call["outcome"]] += 1
                    if call["outcome"] == "limit":
                        limit_count["limit_expandable"].append(call["expandable"])
                if response.metadata.model_dump(mode="json").get("graph_seed_reach") == "neighborhood_limit":
                    limit_count["metadata_neighborhood_limit"] += 1
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    load_after = [round(v, 2) for v in os.getloadavg()]

    calls = [call for q in per_question for call in q["calls"]]
    summary = {"reads": len(per_question), "seed_reach_calls": len(calls),
               "outcomes": {o: sum(c["outcome"] == o for c in calls) for o in ("walked", "limit",
                                                                               "fewer_than_two_seeds")}}
    walked = [c for c in calls if c["outcome"] == "walked"]
    for name in ("total",) + PARTS:
        values = [c[name] for c in walked if name in c]
        if values:
            summary[name] = {"p50": round(percentile(values, 0.5), 2), "p95": round(percentile(values, 0.95), 2),
                             "mean": round(statistics.fmean(values), 2), "sum": round(sum(values), 1)}
    for name in ("seeds", "known", "owners", "expandable", "links", "adjacency_nodes", "edge_visits", "reached"):
        values = [c[name] for c in walked if name in c]
        if values:
            summary[f"{name}_range"] = [min(values), round(statistics.median(values), 1), max(values)]
    limited = [c for c in calls if c["outcome"] == "limit"]
    if limited:
        summary["limit"] = {
            "expandable": sorted(c["expandable"] for c in limited),
            **{name: round(statistics.median(c[f"limit_{name}"] for c in limited), 2)
               for name in ("links_await", "links_sql", "links_convert", "adjacency", "walk", "result")},
            "links_median": statistics.median(c["limit_links"] for c in limited),
        }
    summary["verified_calls"] = sum(bool(c.get("verified")) for c in calls)
    clean = [q for q in per_question if q["repeat"] > 0] or per_question
    reads = [q["read_ms"] for q in clean]
    stages = [q["graph_stage_ms"] for q in clean]
    summary["read_ms"] = {"p50": round(percentile(reads, 0.5), 1), "p95": round(percentile(reads, 0.95), 1)}
    summary["graph_stage_ms"] = {"p50": round(percentile(stages, 0.5), 1), "p95": round(percentile(stages, 0.95), 1)}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return {"pack": args.pack, "kind": args.kind, "commit": commit, "settings": settings,
            "timed_questions": len(timed), "repeats": args.repeats, "load_before": load_before,
            "load_after": load_after, "summary": summary, "limit_count_all_questions": limit_count,
            "per_question": per_question}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--kind", choices=("m3", "locomo"), required=True)
    parser.add_argument("--timed", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--set", default="{}")
    parser.add_argument("--scratch", default="/tmp/task26/scratch")
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    Path(arguments.scratch).mkdir(parents=True, exist_ok=True)
    result = asyncio.run(main(arguments))
    Path(arguments.out).write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("pack", "settings", "load_before", "load_after", "summary")}, indent=1))
    print(json.dumps({k: v for k, v in result["limit_count_all_questions"].items()}))
