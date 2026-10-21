"""The scale test run (``SCALE.md``): the baseline and the routed graph over padded stores.

For each distractor kind two stores are built from the same memories:

- a *text store* in DuckDB with real local embeddings (through an embedding cache that embeds each distinct
  text once and times the model) and no graph, where ``retrieve()`` gives the baseline's ranked list at the
  core size and after each padding step;
- *graph stores*, DuckDB and PostgreSQL, holding the memory nodes, the combined core gold graph and (for the
  look-alikes) the clones' gold graphs, and nothing else, where the one statement walk is run and timed.

The baseline is taken from a store with no graph in it, as in every earlier report, so the graph cannot reach the
baseline through ``retrieve()``'s own graph channel. A routed list is the walk's list ahead of the baseline's
list for a question the router sends to the graph, and the baseline's list otherwise.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
from unittest import mock
from uuid import uuid4

from pydantic import SecretStr

from benchmarks.diagnostics.extracted_packs import _drain_sources
from benchmarks.diagnostics.product_packing import gate_config
from benchmarks.graph_oracle import blind as blind_module
from benchmarks.graph_oracle import router
from benchmarks.graph_oracle.gold import Gold, Memory
from benchmarks.graph_oracle.graph import DIRECT, write_graph
from benchmarks.graph_oracle.run import _table, commit, first_rank, metrics, percentiles
from benchmarks.graph_oracle.scale import EmbeddingCache, conversation_pad, load_core, make_clones, shared_names
from benchmarks.graph_oracle.sqlwalk import SqlWalk
from benchmarks.graph_oracle.strategies import Params, graph_first
from prme import MemoryEngine
from prme.models.nodes import MemoryNode
from prme.types import NodeType, Scope

KIND = "prme-graph-oracle-scale-report"
SCHEMA_VERSION = 1
SIZES = (500, 2000, 10000)
KINDS = ("conversation", "lookalike")
TYPES = ("lookup", "list", "multi-hop", "temporal", "correction", "negation", "aggregation")
CONFIGS = {"derived": Params(cue_source="derived"), "hand": Params()}
STRATEGIES = ("baseline", "routed[derived]", "routed[hand]")
UNROUTED = ("lookup", "temporal", "correction")
OWNER = "scale"
GROWTH_POINTS = 0.08
LOSS = 1


def build_items(golds: dict[str, Gold]) -> list[dict]:
    """The 204 frozen questions with prefixed answer ids and the reference time of their fixture."""
    items = []
    for name, gold in golds.items():
        if name == "draw2-v1":
            continue
        for question in gold.questions:
            for number, text in enumerate((question.text, *question.variants)):
                items.append({"id": f"{name}:{question.id}" + ("" if number == 0 else f"#v{number}"), "set": "original",
                              "type": "multi-hop" if question.kind in ("two_hop", "three_hop") else question.kind, "text": text,
                              "answers": [f"{name}:{memory_id}" for memory_id in question.answers], "ref": gold.reference_time})
    for set_name, path in (("blind-v1", blind_module.BLIND_FILE), ("blind-v2", blind_module.BLIND2_FILE)):
        for question in blind_module.load_blind(path)["questions"]:
            name = question["corpus"]
            items.append({"id": f"{set_name}:{question['id']}", "set": set_name, "type": question["type"], "text": question["question"],
                          "answers": [f"{name}:{memory_id}" for memory_id in question["answers"]], "ref": golds[name].reference_time})
    return items


class Pads:
    """The padding memories in store order for a kind, and for look-alikes the clone each memory belongs to."""

    def __init__(self, memories: list[Memory], clone_of: list[int], clones: list[Gold], info: dict):
        self.memories, self.clone_of, self.clones, self.info = memories, clone_of, clones, info


def pad_memories(kind: str, core: Gold, count: int) -> Pads:
    if kind == "conversation":
        texts = conversation_pad(count)
        return Pads([Memory(f"pad{number:05d}", text, "user", None, NodeType.NOTE, Scope.PERSONAL, "pad") for number, text in enumerate(texts)],
                    [], [], {"locomo_turns": min(count, 5882), "longmemeval_user_turns": max(0, count - 5882)})
    pairs, clones, dropped = make_clones(core, count)
    return Pads([memory for memory, _ in pairs], [clone for _, clone in pairs], clones,
                {"clones": len(clones), "dropped_identical": dropped})


# --- The text store: baseline lists ---------------------------------------------------------------


async def baseline_lists(core: Gold, pads: Pads, items: list[dict], sizes: tuple[int, ...], cache: EmbeddingCache) -> dict:
    """Store the core, then each padding step, and take ``retrieve()``'s ranked memory ids for every question at every size."""
    from prme.storage import engine as engine_module

    original = engine_module.create_embedding_provider
    stages: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="graph-oracle-scale-text-") as work, \
            mock.patch.object(engine_module, "create_embedding_provider", lambda config: cache.wrap(original(config))):
        pack = Path(work) / "pack"
        (pack / "lexical_index").mkdir(parents=True)
        reverse: dict[str, str] = {}
        async with MemoryEngine.open(gate_config(pack)) as engine:
            async def store(memory: Memory) -> None:
                receipt = await engine.store_with_receipt(
                    memory.text, user_id=OWNER, role=memory.role, speaker=memory.speaker, node_type=memory.node_type,
                    scope=memory.scope, event_time=memory.event_time, extract=False, metadata={"source": "graph-oracle-scale"})
                reverse[str(receipt.node.id)] = memory.id

            async def measure(label: str, stored: int, store_seconds: float) -> None:
                await _drain_sources(engine, OWNER)
                lists, timings = {}, []
                for item in items:
                    began = time.perf_counter()
                    response = await engine.retrieve(item["text"], user_id=OWNER, reference_time=item["ref"])
                    timings.append((time.perf_counter() - began) * 1000)
                    lists[item["id"]] = [reverse[str(candidate.node.id)] for candidate in response.results
                                         if str(candidate.node.id) in reverse]
                stages[label] = {"memories": stored, "store_seconds": round(store_seconds, 1),
                                 "embedding_seconds_so_far": round(cache.seconds, 1), "retrieve_ms": percentiles(timings),
                                 "lists": lists}

            began = time.perf_counter()
            for memory in core.memories:
                await store(memory)
            await measure("core", len(core.memories), time.perf_counter() - began)
            done = 0
            for size in sizes:
                began = time.perf_counter()
                target = size - len(core.memories)
                for memory in pads.memories[done:target]:
                    await store(memory)
                done = target
                await measure(str(size), size, time.perf_counter() - began)
    return {"stages": stages, "embedding": {"seconds": round(cache.seconds, 1), "computed": cache.computed, "cached": cache.hits}}


# --- The graph stores: the walk -------------------------------------------------------------------


async def graph_stage_runs(kind: str, backend: str, core: Gold, pads: Pads, items: list[dict], sizes: tuple[int, ...],
                           database_url: str | None) -> dict:
    """Build the graph store for a kind and run the walk at the core size and after each padding step."""
    out: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="graph-oracle-scale-graph-") as work:
        pack = Path(work) / "pack"
        (pack / "lexical_index").mkdir(parents=True)
        config = gate_config(pack)
        owner = OWNER
        if backend == "postgres":
            config = config.model_copy(update={"database_url": SecretStr(database_url)})
            owner = f"{OWNER}-{kind}-{uuid4().hex[:8]}"
        async with MemoryEngine.open(config) as engine:
            store = engine._graph_store

            async def node(memory: Memory) -> str:
                return await store.create_node(MemoryNode(user_id=owner, node_type=NodeType.NOTE, content=memory.text,
                                                          scope=Scope.PERSONAL, confidence=0.9))

            node_ids = {memory.id: await node(memory) for memory in core.memories}
            handles = await write_graph(engine, core, owner, node_ids)
            walks = {name: SqlWalk(engine, backend, owner, handles, core, params) for name, params in CONFIGS.items()}
            written = 0

            async def stage(label: str, count: int) -> None:
                scores: dict[str, dict[str, dict]] = {name: {} for name in CONFIGS}
                timings, routed_timings = [], []
                for item in items:
                    for name, walk in walks.items():
                        began = time.perf_counter()
                        result = await walk.scores(item["text"])
                        elapsed = (time.perf_counter() - began) * 1000
                        scores[name][item["id"]] = result.scores
                        if name == "derived" and result.seeds:
                            timings.append(elapsed)
                            if router.route(item["text"]) == "graph":
                                routed_timings.append(elapsed)
                out[label] = {"memories": count, "scores": scores, "walk_ms": percentiles(timings),
                              "walk_ms_routed_questions": percentiles(routed_timings), "walks": len(timings)}

            await stage("core", len(core.memories))
            for size in sizes:
                target = size - len(core.memories)
                new = pads.memories[written:target]
                new_ids = {memory.id: await node(memory) for memory in new}
                if pads.clones:
                    # A clone's graph is written for the memories of it that this step stored, so a clone that straddles a step
                    # gets its entities twice; the walk never reaches them either way.
                    by_clone: dict[int, list[Memory]] = {}
                    for index in range(written, target):
                        by_clone.setdefault(pads.clone_of[index], []).append(pads.memories[index])
                    for clone_index, group in by_clone.items():
                        clone = pads.clones[clone_index]
                        ids = {memory.id for memory in group}
                        sub = Gold(clone.name, clone.path, clone.owner, clone.owner_entity, clone.entities,
                                   [claim for claim in (_restrict(claim, ids) for claim in clone.claims) if claim], [], group,
                                   owner_entities=clone.owner_entities)
                        sub.patterns = clone.patterns
                        await write_graph(engine, sub, owner, {memory.id: new_ids[memory.id] for memory in group}, shapes=(DIRECT,))
                written = target
                await stage(str(size), size)
    return out


def _restrict(claim, ids: set):
    from dataclasses import replace

    evidence = tuple(memory_id for memory_id in claim.ev if memory_id in ids)
    if not evidence and not claim.inferred:
        return None
    return replace(claim, ev=evidence)


# --- Tables and conditions ------------------------------------------------------------------------


def rows_for(items: list[dict], baseline: dict[str, list[str]], scores: dict[str, dict[str, dict]]) -> list[dict]:
    rows = []
    for item in items:
        base = baseline[item["id"]]
        route = router.route(item["text"])
        ranks = {"baseline": first_rank(base, item["answers"])}
        for name in CONFIGS:
            graph = graph_first(scores[name][item["id"]], base)
            ranks[f"routed[{name}]"] = first_rank(graph, item["answers"]) if route == "graph" else ranks["baseline"]
        rows.append({"id": item["id"], "set": item["set"], "type": item["type"], "route": route, "ranks": ranks})
    return rows


def summarize(rows: list[dict]) -> dict:
    out = {}
    for label in ("all", *TYPES):
        group = rows if label == "all" else [row for row in rows if row["type"] == label]
        if group:
            out[label] = {name: metrics([row["ranks"][name] for row in group]) for name in STRATEGIES}
    return out


def advantage(summary: dict, strategy: str = "routed[derived]") -> float:
    """The routed graph's pooled top 3 minus the baseline's, in points of the pooled questions."""
    pooled = summary["all"]
    return round((pooled[strategy]["top3"] - pooled["baseline"]["top3"]) / pooled["baseline"]["n"] * 100, 1)


def conditions(by_size: dict[str, list[dict]], strategy: str = "routed[derived]") -> dict:
    """The three conditions of SCALE.md for one distractor kind, from its rows at each size (``core`` first)."""
    summaries = {label: summarize(rows) for label, rows in by_size.items()}
    grown = "2000" if "2000" in summaries else list(summaries)[-1]
    growth = advantage(summaries[grown], strategy) - advantage(summaries["core"], strategy)
    worst = max((summaries[label][kind]["baseline"]["top3"] - summaries[label][kind][strategy]["top3"]
                 for label in summaries for kind in TYPES if kind in summaries[label]), default=0)
    unequal = sum(1 for rows in by_size.values() for row in rows if row["type"] in UNROUTED and row["route"] == "text"
                  and row["ranks"][strategy] != row["ranks"]["baseline"])
    misrouted = {label: sum(1 for row in rows if row["type"] in UNROUTED and row["route"] == "graph") for label, rows in by_size.items()}
    return {
        "1 advantage at 2,000 at least 8 points larger than at the core size": {"value": round(growth, 1), "pass": growth >= GROWTH_POINTS * 100 - 1e-9},
        "2 no type loses more than 1 question at any size": {"value": worst, "pass": worst <= LOSS},
        "3 routed-to-text lookups, temporal and corrections equal the baseline": {"value": unequal, "pass": unequal == 0},
        "misrouted lookups, temporal and corrections (counted, in condition 2)": {"value": misrouted["core"], "pass": True},
    }


async def run(out: Path, *, kinds=KINDS, sizes=SIZES, cache_path: Path | None = None, database_url: str | None = None,
              backends=("duckdb", "postgres"), resume: bool = True, every: int = 1) -> dict:
    """Run the scale test. ``every`` keeps each n-th question, for a quick check; the report uses all 204."""
    started = time.perf_counter()
    golds, core = load_core()
    items = build_items(golds)[::every]
    out.mkdir(parents=True, exist_ok=True)
    from prme.config import EmbeddingConfig

    cache = EmbeddingCache(None, cache_path)
    result: dict = {"kind": KIND, "schema_version": SCHEMA_VERSION, "commit": commit(), "questions": len(items), "every": every,
                    "core_memories": len(core.memories), "sizes": ["core", *map(str, sizes)], "shared_names": shared_names(golds),
                    "kinds": {}, "embedding_model": EmbeddingConfig.model_fields["model_name"].default}
    for kind in kinds:
        pads = pad_memories(kind, core, max(sizes) - len(core.memories))
        text_path = out / f"text-{kind}.json"
        if resume and text_path.exists():
            text = json.loads(text_path.read_text())
        else:
            text = await baseline_lists(core, pads, items, sizes, cache)
            cache.save()
            text_path.write_text(json.dumps(text) + "\n")
        graphs: dict[str, dict] = {}
        for backend in backends:
            graphs[backend] = await graph_stage_runs(kind, backend, core, pads, items, sizes, database_url)
        by_size = {label: rows_for(items, text["stages"][label]["lists"], graphs["duckdb"][label]["scores"]) for label in text["stages"]}
        result["kinds"][kind] = {
            "padding": pads.info, "embedding": text["embedding"],
            "stages": {label: {"memories": text["stages"][label]["memories"], "store_seconds": text["stages"][label]["store_seconds"],
                               "retrieve_ms": text["stages"][label]["retrieve_ms"],
                               "walk_ms": {backend: {"all": graphs[backend][label]["walk_ms"],
                                                     "routed": graphs[backend][label]["walk_ms_routed_questions"]} for backend in backends},
                               "summary": summarize(by_size[label]), "advantage_points": advantage(summarize(by_size[label])),
                               "advantage_points_hand": advantage(summarize(by_size[label]), "routed[hand]")}
                       for label in text["stages"]},
            "conditions": conditions(by_size), "conditions_hand": conditions(by_size, "routed[hand]"),
            "rows": by_size}
        (out / "report.json").write_text(json.dumps(result, indent=1) + "\n")
    result["seconds"] = round(time.perf_counter() - started, 1)
    (out / "report.json").write_text(json.dumps(result, indent=1) + "\n")
    (out / "report.md").write_text(report_markdown(result))
    return result


def _cell(entry: dict) -> str:
    return f"{entry['top3']}/{entry['top5']}/{entry['top1']}/{entry['mrr']:.2f}"


def report_markdown(result: dict) -> str:
    lines = [f"# Scale test (commit `{result['commit']}`)", "", f"{result['questions']} questions; core {result['core_memories']} memories; "
             f"embedding model {result['embedding_model']}. Each cell is top 3 / top 5 / rank 1 / MRR, counts of questions.", ""]
    lines += ["Names shared by more than one fixture in the core (natural distractors): "
              + ", ".join(f"{name} ({'/'.join(fixtures)})" for name, fixtures in result["shared_names"].items()), ""]
    for kind, report in result["kinds"].items():
        lines += [f"## {kind}", "", f"Padding: {json.dumps(report['padding'])}; embedding: {json.dumps(report['embedding'])}", ""]
        lines += _table(["Size", "Advantage (derived, points)", "Advantage (hand, points)", "Baseline top 3", "Routed top 3", "Baseline MRR",
                         "Retrieve p50 / p95 ms", "Walk p50 / p95 ms, DuckDB", "Walk p50 / p95 ms, PostgreSQL"], [
            [label, stage["advantage_points"], stage["advantage_points_hand"], stage["summary"]["all"]["baseline"]["top3"],
             stage["summary"]["all"]["routed[derived]"]["top3"], stage["summary"]["all"]["baseline"]["mrr"],
             f"{stage['retrieve_ms'].get('p50')} / {stage['retrieve_ms'].get('p95')}",
             f"{stage['walk_ms']['duckdb']['all'].get('p50')} / {stage['walk_ms']['duckdb']['all'].get('p95')}",
             f"{stage['walk_ms'].get('postgres', {}).get('all', {}).get('p50')} / {stage['walk_ms'].get('postgres', {}).get('all', {}).get('p95')}"]
            for label, stage in report["stages"].items()])
        for label, stage in report["stages"].items():
            lines += ["", f"### {kind}, {label} memories", ""]
            lines += _table(["Type (n)", *STRATEGIES], [[f"{row} ({entry['baseline']['n']})", *(_cell(entry[name]) for name in STRATEGIES)]
                                                       for row, entry in stage["summary"].items()])
        lines += ["", "### Conditions", ""]
        for name, item in report["conditions"].items():
            lines.append(f"- {name}: {item['value']} ({'holds' if item['pass'] else 'fails'})")
        lines.append("")
    lines += [f"Seconds: {result.get('seconds')}.", ""]
    return "\n".join(lines)


__all__ = ["run", "conditions", "advantage", "summarize", "rows_for", "build_items", "report_markdown", "_table"]
