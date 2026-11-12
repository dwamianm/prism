"""P8 bar 1: where one ``retrieve()`` goes on a real graph pack, stage by stage.

    PYTHONPATH=. .venv/bin/python research/graph-read-cost-2026-11/probes/stages.py \
        --pack /tmp/p8/m3-pack --label m3 --stored 20500 --rounds 3 \
        --out research/graph-read-cost-2026-11/results/stages-m3.json

Every stage the pipeline runs is wrapped, and each wrapped call reports its **inclusive** time, the wall time
between entering and leaving it. Inclusive times cannot be summed down a tree, because a parent's time holds its
children's. So the report also computes each label's **own** time as its inclusive time minus the inclusive
times of the children named for it in ``PARENT``. Every label a stage owns is in that map, so the own times of a
level add up to that level's time and the outermost level adds up to the total.

One part of the tree is not sequential: ``generate_candidates`` runs its graph, vector, lexical and pinned
branches at once with ``asyncio.gather``. Those four branches overlap, so their times cannot be subtracted. They
are reported as the gather's parts, each with its own inclusive time, and the gather's wall time is the stage's
own cost. Nothing below them is subtracted from anything above them.

Each question is read once per round, and the counters are cleared before each read, so every label has one
sample per read. The p50 and p95 are over those samples. One warm-up read runs first and is not counted.

The pack is read through a scratch copy, because every read stores a RETRIEVAL_REQUEST receipt in the pack's
operations table, and two runs would then time different stores.
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
    # The ten object questions both packs carry: the m3 pack is built from these statements, and the flat pack
    # holds the same ten objects among its 20,000 notes.
    "Where is the red mug now?", "Where are the house keys?", "Where did I leave the blue notebook?",
    "Where is the phone charger?", "Where are the reading glasses?", "Where is the tv remote?",
    "Where is the black umbrella?", "Where is the dog leash?", "Where is the screwdriver set?",
    "Where is the first aid kit?",
    # Ten more, from the read-cost diagnostic's question list: rooms, patrols, people, summaries.
    "What is on the kitchen counter?", "What is on the office desk?", "What is in the garage workbench area?",
    "Is the front porch clear?", "What is in the laundry room?", "What changed since the last pass?",
    "Summarize the last hour.", "What happened in the hallway this morning?", "Did a delivery arrive?",
    "Is there a leak anywhere?",
]
REFERENCE_TIME = "2026-11-05T00:00:00+00:00"

# Who owns whom. A label's own time is its inclusive time minus the inclusive times of the labels listed under
# it here. A label with no entry is a leaf. The graph, vector, lexical and pinned branches of
# ``generate_candidates`` overlap in one ``asyncio.gather``, so they are not in this map: they are reported apart,
# as the parts of the gather's wall time.
PARENT: dict[str, tuple[str, ...]] = {
    "engine.retrieve": ("retrieve: prepare_retrieval", "retrieve: ranking profile lookup", "retrieve: queue.debt",
                        "retrieve: pipeline.retrieve"),
    "retrieve: pipeline.retrieve": (
        "S1 analyze_query", "S1b current_state_observations", "S1c owner_profile",
        "S2-3 generate_candidates", "S3.5 filter_candidate_types", "S4 filter_epistemic",
        "S5 score_and_rank", "S5.4 select_candidates", "S5.5b session_context", "S5.5c episode_context",
        "S5.5d evidence projection", "S5.5d evidence augment", "S5.5e claim folding", "S6 pack_context",
    ),
    "S2-3 generate_candidates": ("  S2-3 merge_candidates",),
    "S5 score_and_rank": ("  S5 rrf relevance",),
}

# The four branches that run at once inside ``generate_candidates``'s ``asyncio.gather``.
OVERLAP_ROOTS = ("  S2-3 graph candidates", "  S2-3 vector candidates", "  S2-3 lexical candidates",
                 "  S2-3 pinned candidates")

# The four branches of the gather inside ``generate_candidates``. Their times overlap, so neither they nor
# anything they call is subtracted from a parent. Each is reported with its own inclusive time, and the gather's
# wall time is ``generate_candidates`` less ``merge_candidates``.
GATHER_PARTS = {
    "  S2-3 graph candidates": ("    graph: _find_seed_entities", "    graph: _seed_reach",
                                "    graph: walk_from_seeds", "    graph: get_nodes"),
    "    graph: _find_seed_entities": ("    graph: query_nodes",),
    "    graph: _seed_reach": ("    graph: walk_links",),
    "  S2-3 vector candidates": ("    vector: search",),
    "  S2-3 lexical candidates": ("    lexical: search",),
    "  S2-3 pinned candidates": (),
}

TIME: dict[str, float] = {}
CALLS: dict[str, int] = {}


def wrap(owner, name: str, label: str) -> None:
    """Time every call of ``owner.name`` inclusively."""
    function = getattr(owner, name, None)
    if function is None:
        return

    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def timed(*args, **kwargs):
            started = time.perf_counter()
            try:
                return await function(*args, **kwargs)
            finally:
                TIME[label] = TIME.get(label, 0.0) + time.perf_counter() - started
                CALLS[label] = CALLS.get(label, 0) + 1
    else:
        @functools.wraps(function)
        def timed(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                TIME[label] = TIME.get(label, 0.0) + time.perf_counter() - started
                CALLS[label] = CALLS.get(label, 0) + 1
    setattr(owner, name, timed)


def install() -> None:
    import prme.privacy.reads as privacy_reads
    import prme.retrieval.candidates as candidates
    import prme.retrieval.pipeline as pipeline
    import prme.retrieval.query_analysis as query_analysis
    import prme.storage.lexical_index as lexical
    import prme.storage.ranking_profiles as ranking_profiles
    import prme.storage.vector_index as vector
    from prme.storage.duckpgq_graph import DuckPGQGraphStore
    from prme.storage.durable_queue import DurableMaterializationQueue
    from prme.storage.engine import MemoryEngine

    # The outermost frame: what the caller's clock sees.
    wrap(MemoryEngine, "retrieve", "engine.retrieve")
    wrap(privacy_reads, "prepare_retrieval", "retrieve: prepare_retrieval")
    wrap(ranking_profiles.RankingProfileRepository, "active_application", "retrieve: ranking profile lookup")
    wrap(DurableMaterializationQueue, "debt", "retrieve: queue.debt")
    wrap(pipeline.RetrievalPipeline, "retrieve", "retrieve: pipeline.retrieve")

    # The pipeline's stages, in the order they run. ``pipeline`` binds most of these by name at import time, so
    # the name the call site reads is the one on the pipeline module; wrapping the module a function is defined
    # in would time nothing there.
    wrap(query_analysis, "analyze_query", "S1 analyze_query")
    wrap(pipeline, "analyze_query", "S1 analyze_query")
    wrap(pipeline.RetrievalPipeline, "_latest_observations", "S1b current_state_observations")
    wrap(pipeline.RetrievalPipeline, "_owner_profile", "S1c owner_profile")
    wrap(pipeline, "generate_candidates", "S2-3 generate_candidates")
    wrap(pipeline, "generate_summary_candidates", "S2-3b summary candidates")
    wrap(candidates, "_generate_graph_candidates", "  S2-3 graph candidates")
    wrap(candidates, "_generate_vector_candidates", "  S2-3 vector candidates")
    wrap(candidates, "_generate_lexical_candidates", "  S2-3 lexical candidates")
    wrap(candidates, "_generate_pinned_candidates", "  S2-3 pinned candidates")
    wrap(candidates, "merge_candidates", "  S2-3 merge_candidates")
    wrap(candidates, "_find_seed_entities", "    graph: _find_seed_entities")
    wrap(candidates, "_seed_reach", "    graph: _seed_reach")
    wrap(DuckPGQGraphStore, "walk_from_seeds", "    graph: walk_from_seeds")
    wrap(DuckPGQGraphStore, "walk_links", "    graph: walk_links")
    wrap(DuckPGQGraphStore, "get_nodes", "    graph: get_nodes")
    wrap(DuckPGQGraphStore, "query_nodes", "    graph: query_nodes")
    wrap(vector.VectorIndex, "search", "    vector: search")
    wrap(lexical.LexicalIndex, "search", "    lexical: search")
    wrap(pipeline, "filter_candidate_types", "S3.5 filter_candidate_types")
    wrap(pipeline, "filter_epistemic", "S4 filter_epistemic")
    wrap(pipeline, "score_and_rank", "S5 score_and_rank")
    wrap(pipeline, "with_rank_fusion_relevance", "  S5 rrf relevance")
    wrap(pipeline, "select_candidates", "S5.4 select_candidates")
    wrap(pipeline, "expand_session_context", "S5.5b session_context")
    wrap(pipeline, "expand_episode_context", "S5.5c episode_context")
    wrap(pipeline, "project_evidence_context", "S5.5d evidence projection")
    wrap(pipeline, "augment_evidence_context", "S5.5d evidence augment")
    wrap(pipeline, "fold_claim_statements", "S5.5e claim folding")
    wrap(pipeline, "pack_context", "S6 pack_context")


def overlap_labels() -> set[str]:
    """Every label of the gather: the four branches and every label below them."""
    labels = set(GATHER_PARTS)
    for children in GATHER_PARTS.values():
        labels.update(children)
    return labels


def gather_parts(rows: dict[str, dict]) -> dict:
    """The four branches of the gather, and the wall time they share.

    The gather's wall time is the time ``generate_candidates`` spent outside ``merge_candidates``, which runs
    after it. The branches overlap, so their inclusive times add to more than that wall time; the report says so
    and does not divide by their sum.
    """
    wall = (rows.get("S2-3 generate_candidates", {"p50": 0.0})["p50"]
            - rows.get("S2-3 merge_candidates", {"p50": 0.0})["p50"])
    labels: list[str] = []
    for branch in OVERLAP_ROOTS:
        labels.append(branch)
        for parent, children in GATHER_PARTS.items():
            if parent == branch or parent in labels:
                labels.extend(child for child in children if child not in labels)
    parts = {}
    for label in labels:
        value = rows.get(label)
        if value is not None:
            parts[label] = {**value, "share_of_gather": round(value["p50"] / wall, 4) if wall else None}
    branches = [label for label in OVERLAP_ROOTS if label in parts]
    return {"gather_wall_ms_p50": round(wall, 3),
            "branches_sum_ms_p50": round(sum(parts[label]["p50"] for label in branches), 3),
            "parts": parts}


def own_time(label: str, inclusive: dict[str, float]) -> float:
    """A label's inclusive time less the inclusive times of the children the map names for it."""
    value = inclusive.get(label, 0.0)
    for child in PARENT.get(label, ()):
        value -= inclusive.get(child, 0.0)
    return value


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
    work = Path(args.work or f"/tmp/p8/work/{args.label}")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    shutil.copytree(Path(args.pack), work / "pack")

    install()
    inclusive_samples: dict[str, list[float]] = {}
    own_samples: dict[str, list[float]] = {}
    call_samples: dict[str, list[float]] = {}
    totals: list[float] = []
    returned: list[int] = []
    candidate_counts: list[dict] = []
    overlap = overlap_labels()
    load_before = load()
    # ``gate_config`` appends the pack's own file names to the directory it is given, so it takes the pack
    # directory itself and not the scratch directory around it.
    async with MemoryEngine.open(gate_config(work / "pack")) as engine:
        await engine.retrieve("warm-up question about the house", user_id=USER, reference_time=when)
        for _ in range(args.rounds):
            for question in QUESTIONS:
                TIME.clear(); CALLS.clear()
                started = time.perf_counter()
                response = await engine.retrieve(question, user_id=USER, reference_time=when)
                totals.append(1000 * (time.perf_counter() - started))
                for label, seconds in TIME.items():
                    inclusive_samples.setdefault(label, []).append(1000 * seconds)
                    if label not in overlap:
                        own_samples.setdefault(label, []).append(1000 * own_time(label, TIME))
                for label, count in CALLS.items():
                    call_samples.setdefault(label, []).append(count)
                returned.append(len(response.results))
                candidate_counts.append(dict(response.metadata.candidates_generated))
    load_after = load()

    inclusive = {label: summary(values) for label, values in inclusive_samples.items()}
    own = {label: summary(values) for label, values in own_samples.items()}
    # The own times of the sequential frames add up: the outermost level sums to the read, and the pipeline's
    # stages sum to the pipeline. That total is what the shares divide by.
    own_total = round(sum(value["mean"] for value in own.values()), 3)
    return {
        "pack": args.pack, "label": args.label, "stored": args.stored,
        "questions": len(QUESTIONS), "rounds": args.rounds, "reads": len(totals), "backlog": 0,
        "reference_time": REFERENCE_TIME,
        "load_before": load_before, "load_after": load_after,
        "total_ms": {**summary(totals), "values": [round(v, 1) for v in totals]},
        "inclusive_ms_per_read": dict(sorted(inclusive.items(), key=lambda item: -item[1]["mean"])),
        "own_ms_per_read": dict(sorted(own.items(), key=lambda item: -item[1]["mean"])),
        "own_sum_ms_per_read": own_total,
        "own_share": {label: round(value["mean"] / own_total, 4) for label, value in own.items()},
        "overlap_labels": sorted(overlap),
        "gather": gather_parts(inclusive),
        "calls_per_read": {label: round(statistics.fmean(values), 2)
                           for label, values in sorted(call_samples.items())},
        "results_per_read": summary([float(v) for v in returned]),
        "candidates_generated": candidate_counts[:1],
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
    print(json.dumps({k: result[k] for k in ("total_ms", "own_sum_ms_per_read", "load_before", "load_after")},
                     indent=1))
    print("\n%8s %8s %8s %8s %7s  %s" % ("p50", "p95", "incl50", "incl95", "share", "stage (ms per read)"))
    for label, value in result["own_ms_per_read"].items():
        inc = result["inclusive_ms_per_read"].get(label, {"p50": 0.0, "p95": 0.0})
        print("%8.2f %8.2f %8.2f %8.2f %6.1f%%  %s" % (value["p50"], value["p95"], inc["p50"], inc["p95"],
                                                       100 * result["own_share"][label], label), flush=True)
    print("\ngather wall %.2f ms; parts (overlapping, inclusive):" % result["gather"]["gather_wall_ms_p50"])
    for label, value in result["gather"]["parts"].items():
        print("%8.2f %8.2f %6.1f%%  %s" % (value["p50"], value["p95"],
                                          100 * (value["share_of_gather"] or 0), label), flush=True)
