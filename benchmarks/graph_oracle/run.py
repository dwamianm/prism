"""Run the graph oracle: store the memories, write the gold graph, ask every question of every strategy.

One engine and one pack hold the corpora (a user each). Memories go in with
``store()`` and real local embeddings, no extraction. The baseline is
``retrieve()`` before any gold graph exists. Then the gold graph is written
into the graph store and each question is answered by:

- ``oracle``: the gold query spec followed exactly (the upper bound of the shape);
- ``generic``: seeds from the question text, a typed walk with hop decay and
  relation weights, the memories that name or evidence what it reached, ahead
  of the text list;
- ``fused@w``: reciprocal rank fusion of the text list with the generic graph
  list, the graph list weighted ``w``.

Ranks are the first memory that states the answer (the gold answers) in the
strategy's list. No model is called. The ablations sweep the generic walk's
settings over the in-memory copy of the graph read back from the store.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

from benchmarks.diagnostics.extracted_packs import _drain_sources
from benchmarks.diagnostics.product_packing import _quiet_offline_cli, gate_config
from benchmarks.fast_cycle import store as fast_store
from benchmarks.graph_oracle import gold as gold_module
from benchmarks.graph_oracle.gold import GOLD_NAMES, RELATIONS, Gold, load_gold, target_cues, term_hits
from benchmarks.graph_oracle.graph import CLAIM, GraphData, MemorySource, StoreSource, leaf_intent, load_graph, write_graph
from benchmarks.graph_oracle.strategies import HUBS, Params, fuse, generic_scores, graph_first, run_oracle
from prme import MemoryEngine

KIND = "prme-graph-oracle-report"
SCHEMA_VERSION = 1
FUSION_WEIGHTS = (0.5, 1.0, 2.0, 4.0)
HELDOUT = fast_store.FIXTURES / "store-heldout-v1.json"
CITIES = ("cities", "japan-cities")
GATED = (0.5, 4.0)  # fusion weight without and with a subtype cue ("which cities"); chosen after the first table
STRATEGIES = ("baseline", "oracle", "generic", *(f"fused@{weight:g}" for weight in FUSION_WEIGHTS), "fused-gated")
HEADLINE = "fused@1"


@dataclass
class Item:
    corpus: str
    id: str
    label: str
    text: str
    kind: str
    answers: tuple[str, ...]
    terms: dict
    stale: tuple[str, ...]
    spec: dict | None
    group: str          # gold or heldout
    base: list[str]
    base_ms: float


def first_rank(ranking: list[str], answers) -> int | None:
    wanted = set(answers)
    return next((place for place, memory_id in enumerate(ranking, start=1) if memory_id in wanted), None)


def stale_above(ranking: list[str], answers, stale, texts: dict[str, str]) -> bool:
    """Whether a memory that is not an answer but holds a stale value ranks above the first answer."""
    rank = first_rank(ranking, answers)
    above = ranking if rank is None else ranking[:rank - 1]
    return bool(stale) and any(memory_id not in set(answers) and any(term.casefold() in texts[memory_id].casefold()
                                                                       for term in stale) for memory_id in above)


def metrics(ranks: list[int | None]) -> dict:
    count = len(ranks)
    return {"n": count, "top1": sum(rank == 1 for rank in ranks), "top3": sum(rank is not None and rank <= 3 for rank in ranks),
            "top5": sum(rank is not None and rank <= 5 for rank in ranks),
            "mrr": round(sum(1 / rank for rank in ranks if rank) / count, 3) if count else 0.0,
            "missing": sum(rank is None for rank in ranks)}


def percentiles(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"p50": round(statistics.median(ordered), 2), "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 2),
            "max": round(ordered[-1], 2), "mean": round(statistics.fmean(ordered), 2)} if ordered else {}


# --- Building the pack -----------------------------------------------------------------------------


async def build_corpus(engine, gold: Gold, heldout: bool, extra: list[dict] | None = None) -> tuple[dict[str, str], list[Item]]:
    """Store a corpus, then run the baseline over its questions (and the held-out ones for the store corpus).

    ``extra`` adds questions with no query spec: dicts with ``id``, ``text``, ``kind``, ``answers``, ``stale``, ``group``.
    """
    owner = gold.owner
    node_ids: dict[str, str] = {}
    for memory in gold.memories:
        receipt = await engine.store_with_receipt(
            memory.text, user_id=owner, role=memory.role, speaker=memory.speaker, node_type=memory.node_type,
            scope=memory.scope, event_time=memory.event_time, extract=False,
            metadata={"source": "graph-oracle", "session": memory.session, "memory": memory.id})
        node_ids[memory.id] = str(receipt.node.id)
    await _drain_sources(engine, owner)
    reverse = {node_id: memory_id for memory_id, node_id in node_ids.items()}
    items: list[Item] = []

    async def baseline(text: str) -> tuple[list[str], float]:
        started = time.perf_counter()
        response = await engine.retrieve(text, user_id=owner, reference_time=gold.reference_time)
        elapsed = (time.perf_counter() - started) * 1000
        return [reverse[str(candidate.node.id)] for candidate in response.results if str(candidate.node.id) in reverse], elapsed

    for question in gold.questions:
        for number, text in enumerate((question.text, *question.variants)):
            base, elapsed = await baseline(text)
            items.append(Item(gold.name, question.id if number == 0 else f"{question.id}#v{number}", text, text, question.kind,
                              question.answers, question.terms, question.stale, question.spec, "gold", base, elapsed))
    if heldout and gold.name == "store-v1":
        fixture = fast_store.load_fixture(HELDOUT)
        for question in fixture["key"]["questions"]:
            terms = {key: question[key] for key in ("all_of", "any_of") if question.get(key)}
            answers = tuple(memory.id for memory in gold.memories if term_hits(terms, memory.text))
            base, elapsed = await baseline(question["question"])
            items.append(Item(gold.name, question["id"], question["question"], question["question"], "heldout", answers,
                              terms, tuple(question.get("stale", ())), None, "heldout", base, elapsed))
    for question in extra or []:
        base, elapsed = await baseline(question["text"])
        items.append(Item(gold.name, question["id"], question["text"], question["text"], question["kind"],
                          tuple(question["answers"]), {}, tuple(question.get("stale", ())), None, question["group"], base,
                          elapsed))
    return node_ids, items


# --- Evaluating strategies ------------------------------------------------------------------------


async def graph_lists(item: Item, gold: Gold, source, params: Params) -> tuple[dict[str, float], int, int]:
    result = await generic_scores(item.text, gold, source, params)
    return result.scores, result.calls, result.edges_read


def rankings(item: Item, gold: Gold, scores: dict[str, float], oracle: list[str] | None) -> dict[str, list[str]]:
    lists = {"baseline": item.base, "generic": graph_first(scores, item.base)}
    if oracle is not None:
        lists["oracle"] = oracle
    for weight in FUSION_WEIGHTS:
        lists[f"fused@{weight:g}"] = fuse(scores, item.base, weight)
    lists["fused-gated"] = fuse(scores, item.base, GATED[1] if target_cues(gold, item.text)["subtype"] else GATED[0])
    return lists


def summarize(items: list[Item], lists: dict[str, dict[str, list[str]]], texts: dict[str, dict[str, str]]) -> dict:
    """Per strategy: all items, then by kind and corpus."""
    summary: dict[str, dict] = {}
    for strategy in STRATEGIES:
        chosen = [item for item in items if strategy in lists[item.id + "|" + item.corpus]]
        ranks = [first_rank(lists[item.id + "|" + item.corpus][strategy], item.answers) for item in chosen]
        by_kind: dict[str, list] = {}
        by_corpus: dict[str, list] = {}
        for item, rank in zip(chosen, ranks):
            by_kind.setdefault(item.kind, []).append(rank)
            by_corpus.setdefault(item.corpus, []).append(rank)
        stale = [stale_above(lists[item.id + "|" + item.corpus][strategy], item.answers, item.stale, texts[item.corpus])
                 for item in chosen if item.stale]
        summary[strategy] = {**metrics(ranks), "stale_above_answer": f"{sum(stale)}/{len(stale)}",
                             "by_kind": {kind: metrics(values) for kind, values in sorted(by_kind.items())},
                             "by_corpus": {corpus: metrics(values) for corpus, values in sorted(by_corpus.items())}}
    return summary


def ablation_grid(golds: list[Gold], base: Params) -> list[tuple[str, str, Params, bool]]:
    """(factor, setting, walk settings, whether to hide the claims that containment already implies)."""
    used = sorted({claim.r for gold in golds for claim in gold.claims})
    everything = frozenset(RELATIONS)
    containment = frozenset({"located_in", "part_of"})
    rows = [("default", "default", base)]
    rows += [("hop limit", f"hops={hops}", base.with_(hops=hops)) for hops in (1, 2, 3, 4)]
    rows += [("hub damping of the owner", f"hub={hub}", base.with_(hub=hub)) for hub in HUBS if hub != base.hub]
    rows += [("hub damping of the owner", f"hub={base.hub}, the owner's own score counts", base.with_(owner_mentions=True)),
             ("hub damping of the owner", f"hub={base.hub}, factor 1.0", base.with_(hub_factor=1.0))]
    rows += [("intent edges or containment", "containment only", base.with_(relations=containment)),
             ("intent edges or containment", "intent only, no containment", base.with_(relations=everything - containment)),
             ("intent edges or containment", "no inferred containment", base.with_(inferred=False))]
    rows += [("claim nodes or direct edges", f"claim nodes, hops={hops}", base.with_(shape=CLAIM, hops=hops))
             for hops in (2, 4, 6)]
    rows += [("what a memory scores on", "memory names reached entities only", base.with_(evidence=0.0)),
             ("what a memory scores on", "memory evidences reached claims only", base.with_(mention=0.0))]
    rows += [("question cues", "relation cues only boost, they do not restrict", base.with_(cue_filter=False)),
             ("question cues", "no cues", base.with_(cue_filter=False, target_cues=False, relation_cues=False)),
             ("question cues", "subtype and type cues only", base.with_(cue_filter=False, relation_cues=False)),
             ("question cues", "relation and role cues only (boost)", base.with_(cue_filter=False, target_cues=False)),
             ("question cues", "restrict, without the subtype and type boost", base.with_(target_cues=False))]
    rows += [("hop decay", f"decay={decay}", base.with_(decay=decay)) for decay in (0.4, 0.8)]
    rows += [("claim status and polarity", "superseded claims count 0.3", base.with_(superseded=0.3)),
             ("claim status and polarity", "negative claims count 0.5", base.with_(negative=0.5)),
             ("claim status and polarity", "containment weighs 0.5", base.with_(weights=(("located_in", 0.5), ("part_of", 0.5))))]
    rows += [("drop one relation", f"without {relation}", base.with_(relations=everything - {relation})) for relation in used
             if relation != "has"]
    rows = [(*row, False) for row in rows]
    rows += [("intent edges or containment", "intent to the country only, cities by containment", base, True),
             ("intent edges or containment", "intent to the country only, no inferred containment", base.with_(inferred=False), True)]
    return rows


async def evaluate_grid(items: list[Item], golds: dict[str, Gold], sources: dict[str, GraphData], base: Params) -> list[dict]:
    rows = []
    hidden = {name: leaf_intent(data) for name, data in sources.items()}
    for group, name, params, hide in ablation_grid(list(golds.values()), base):
        ranks = {"generic": [], HEADLINE: []}
        for item in items:
            gold = golds[item.corpus]
            source = MemorySource(sources[item.corpus], params.shape, hidden[item.corpus] if hide else frozenset())
            result = await generic_scores(item.text, gold, source, params)
            ranks["generic"].append(first_rank(graph_first(result.scores, item.base), item.answers))
            ranks[HEADLINE].append(first_rank(fuse(result.scores, item.base, float(HEADLINE.split("@")[1])), item.answers))
        rows.append({"group": group, "name": name, "generic": metrics(ranks["generic"]), HEADLINE: metrics(ranks[HEADLINE])})
    return rows


def commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True, check=True,
                              cwd=gold_module.ROOT).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def run(out: Path, *, corpora: tuple[str, ...] = GOLD_NAMES, heldout: bool = True, ablations: bool = True,
              params: Params | None = None, work: Path | None = None) -> dict:
    params = params or Params()
    started = time.perf_counter()
    golds = {name: load_gold(name) for name in corpora}
    cleanup = tempfile.TemporaryDirectory(prefix="graph-oracle-") if work is None else None
    work = Path(cleanup.name) if cleanup else work
    pack = work / "pack"
    (pack / "lexical_index").mkdir(parents=True, exist_ok=True)
    timing: dict[str, float] = {}
    items: list[Item] = []
    graphs, handles, lists, texts = {}, {}, {}, {}
    store_ms: dict[str, list[float]] = {"generic": [], "oracle": []}
    store_calls: list[int] = []
    edges_read: list[int] = []
    mismatches = 0
    async with MemoryEngine.open(gate_config(pack)) as engine:
        for name, gold in golds.items():
            began = time.perf_counter()
            node_ids, corpus_items = await build_corpus(engine, gold, heldout)
            timing[f"store_and_baseline:{name}"] = time.perf_counter() - began
            items += corpus_items
            began = time.perf_counter()
            handles[name] = await write_graph(engine, gold, gold.owner, node_ids)
            timing[f"write_graph:{name}"] = time.perf_counter() - began
            began = time.perf_counter()
            graphs[name] = await load_graph(engine, gold.owner, handles[name])
            timing[f"load_graph:{name}"] = time.perf_counter() - began
            texts[name] = {memory.id: memory.text for memory in gold.memories}
        for item in items:
            gold = golds[item.corpus]
            source = StoreSource(engine, gold.owner, handles[item.corpus])
            began = time.perf_counter()
            result = await generic_scores(item.text, gold, source, params)
            store_ms["generic"].append((time.perf_counter() - began) * 1000)
            store_calls.append(result.calls)
            edges_read.append(result.edges_read)
            memory_result = await generic_scores(item.text, gold, MemorySource(graphs[item.corpus], params.shape), params)
            mismatches += result.scores != memory_result.scores
            oracle = None
            if item.spec is not None:
                began = time.perf_counter()
                oracle = run_oracle(gold, graphs[item.corpus], item.spec, item.base).ranked
                store_ms["oracle"].append((time.perf_counter() - began) * 1000)
            lists[item.id + "|" + item.corpus] = rankings(item, gold, result.scores, oracle)
    gold_items = [item for item in items if item.group == "gold"]
    held = [item for item in items if item.group == "heldout"]
    report = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "commit": commit(), "corpora": list(corpora),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "params": _params_dict(params),
        "graph": {name: {"memories": len(gold.memories), "entities": len(gold.entities), "claims": len(gold.claims),
                         "edges_decoded": len(graphs[name].edges)} for name, gold in golds.items()},
        "summary": summarize(gold_items, lists, texts),
        "heldout_summary": _only(summarize(held, lists, texts)) if held else {},
        "items": [{"corpus": item.corpus, "id": item.id, "question": item.label, "kind": item.kind, "group": item.group,
                   "ranks": {strategy: first_rank(ranking, item.answers) for strategy, ranking in lists[item.id + "|" + item.corpus].items()},
                   "top": {strategy: ranking[:3] for strategy, ranking in lists[item.id + "|" + item.corpus].items()
                           if strategy in ("baseline", "oracle", HEADLINE)}} for item in items],
        "store_and_memory_traversals_differ": mismatches,
        "milliseconds": {"baseline_retrieve": percentiles([item.base_ms for item in items]),
                         "generic_traversal_from_the_store": percentiles(store_ms["generic"]),
                         "oracle_in_memory": percentiles(store_ms["oracle"]),
                         "store_reads_per_question": percentiles([float(calls) for calls in store_calls]),
                         "edges_read_per_question": percentiles([float(count) for count in edges_read])},
        "seconds": {name: round(value, 2) for name, value in timing.items()},
    }
    if ablations:
        began = time.perf_counter()
        report["ablations"] = await evaluate_grid(gold_items, golds, graphs, params)
        report["seconds"]["ablations"] = round(time.perf_counter() - began, 2)
    report["seconds"]["total"] = round(time.perf_counter() - started, 2)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "report.md").write_text(report_markdown(report))
    if cleanup:
        cleanup.cleanup()
    return report


def parse_params(settings: list[str]) -> Params:
    """``--set name=value`` for the generic walk: the value is JSON; relations is a list, weights a mapping."""
    changes = {}
    for setting in settings:
        name, _, raw = setting.partition("=")
        if name not in Params.__dataclass_fields__:
            raise ValueError(f"{name} is not a setting of the generic walk")
        value = json.loads(raw)
        changes[name] = (None if value is None else frozenset(value)) if name == "relations" else (
            tuple(value.items()) if name == "weights" else value)
    return Params().with_(**changes)


def _only(summary: dict) -> dict:
    """The rows a held-out table shows: no oracle, since held-out questions have no query spec."""
    return {name: row for name, row in summary.items() if name != "oracle" and row["n"]}


def _params_dict(params: Params) -> dict:
    values = {name: getattr(params, name) for name in Params.__dataclass_fields__}
    values["relations"] = None if params.relations is None else sorted(params.relations)
    values["weights"] = dict(params.weights)
    return values


# --- Reports ---------------------------------------------------------------------------------------


def _table(header: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    return lines + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]


def _cell(rank) -> str:
    return "none" if rank is None else str(rank)


def report_markdown(report: dict) -> str:
    lines = [f"# Graph oracle (commit `{report['commit']}`)", "", f"Corpora: {', '.join(report['corpora'])}. "
             f"Settings: `{json.dumps(report['params'])}`", "", "## Strategies over all gold questions", ""]
    header = ["Strategy", "Questions", "Rank 1", "Top 3", "Top 5", "MRR", "Missing", "Stale above answer"]
    lines += _table(header, [[name, row["n"], row["top1"], row["top3"], row["top5"], row["mrr"], row["missing"],
                              row["stale_above_answer"]] for name, row in report["summary"].items()])
    lines += ["", "## By question kind (top 3 of questions, MRR)", ""]
    kinds = sorted({kind for row in report["summary"].values() for kind in row["by_kind"]})
    lines += _table(["Strategy", *kinds], [[name, *(f"{row['by_kind'][kind]['top3']}/{row['by_kind'][kind]['n']}, "
                                                    f"{row['by_kind'][kind]['mrr']}" if kind in row["by_kind"] else "" for kind in kinds)]
                                           for name, row in report["summary"].items()])
    lines += ["", "## The cities question, three spellings (rank of the answer memory)", ""]
    cities = [item for item in report["items"] if item["id"].split("#")[0] in CITIES]
    lines += _table(["Corpus", "Spelling", *STRATEGIES], [[item["corpus"], item["question"], *(_cell(item["ranks"].get(name))
                                                                                               for name in STRATEGIES)] for item in cities])
    if report.get("heldout_summary"):
        lines += ["", "## Held-out store questions (28 single fact lookups, never used to choose a setting; the answer is "
                  "any memory holding the answer terms)", ""]
        lines += _table(header, [[name, row["n"], row["top1"], row["top3"], row["top5"], row["mrr"], row["missing"],
                                  row["stale_above_answer"]] for name, row in report["heldout_summary"].items()])
    lines += ["", "## Each question (rank of the answer; none when it is not ranked)", ""]
    lines += _table(["Corpus", "Question", "Kind", *STRATEGIES], [[item["corpus"], item["question"], item["kind"],
                                                                  *(_cell(item["ranks"].get(name)) for name in STRATEGIES)]
                                                                 for item in report["items"] if item["group"] == "gold"])
    if report.get("ablations"):
        lines += ["", "## Ablations of the generic walk (gold questions; graph list first, and fused@1)", ""]
        lines += _table(["Factor", "Setting", "Top 3", "Top 5", "MRR", "Fused top 3", "Fused top 5", "Fused MRR"], [
            [row["group"], row["name"], row["generic"]["top3"], row["generic"]["top5"], row["generic"]["mrr"],
             row[HEADLINE]["top3"], row[HEADLINE]["top5"], row[HEADLINE]["mrr"]] for row in report["ablations"]])
    lines += ["", "## Cost", ""]
    lines += _table(["Step", "p50", "p95", "max", "mean"], [[name, *(values.get(key, "") for key in ("p50", "p95", "max", "mean"))]
                                                           for name, values in report["milliseconds"].items()])
    lines += ["", f"Seconds: `{json.dumps(report['seconds'])}`. Traversals from the store and from memory differ on "
              f"{report['store_and_memory_traversals_differ']} questions.", ""]
    return "\n".join(lines)


def compare_markdown(first: dict, second: dict) -> str:
    lines = [f"# Graph oracle: `{first['commit']}` against `{second['commit']}`", ""]
    rows = []
    for name in STRATEGIES:
        for key in ("top3", "top5", "mrr"):
            before, after = first["summary"][name][key], second["summary"][name][key]
            rows.append([name, key, before, after, round(after - before, 3)])
    lines += _table(["Strategy", "Measure", "Before", "After", "Change"], rows)
    before_items = {(item["corpus"], item["id"]): item for item in first["items"]}
    moved = [[item["corpus"], item["question"], name, _cell(before_items[(item["corpus"], item["id"])]["ranks"].get(name)),
              _cell(item["ranks"].get(name))] for item in second["items"] if (item["corpus"], item["id"]) in before_items
             for name in STRATEGIES if item["ranks"].get(name) != before_items[(item["corpus"], item["id"])]["ranks"].get(name)]
    lines += ["", "## Questions whose rank changed", ""] + _table(["Corpus", "Question", "Strategy", "Before", "After"], moved)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.graph_oracle",
                                     description="Probe the ideal memory graph: oracle, generic traversal and fusion.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="Store the corpora, write the gold graphs and rank every question")
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--corpora", nargs="+", choices=GOLD_NAMES, default=list(GOLD_NAMES))
    command.add_argument("--no-heldout", action="store_true")
    command.add_argument("--no-ablations", action="store_true")
    command.add_argument("--set", dest="settings", action="append", default=[], metavar="NAME=VALUE",
                         help="Change a setting of the generic walk, for example cue_filter=true or hops=3")
    command = commands.add_parser("compare", help="Put two reports side by side")
    command.add_argument("before", type=Path)
    command.add_argument("after", type=Path)
    command = commands.add_parser("fidelity", help="Score an extracted graph (the gold file format) against a gold graph")
    command.add_argument("--gold", required=True)
    command.add_argument("--graph", type=Path, required=True)
    command = commands.add_parser("blind", help="The blind validation run (BLIND.md): original items, blind questions, paraphrases")
    command.add_argument("--out", type=Path, required=True)
    command = commands.add_parser("routed", help="The routing gate's run on the new blind draw (GATE.md)")
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--latency", type=Path, default=None, help="The PostgreSQL latency file for condition 6")
    command = commands.add_parser("latency", help="Time the typed walk on synthetic packs")
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--backend", choices=("duckdb", "postgres"), default="duckdb")
    command.add_argument("--database-url", default=None)
    command.add_argument("--turns", type=int, nargs="+", default=[2000, 10000])
    for name in ("blind-generate", "blind-paraphrase"):
        command = commands.add_parser(name, help="Ask the local model for candidate questions or paraphrases (blind.py)")
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "compare":
        print(compare_markdown(json.loads(args.before.read_text()), json.loads(args.after.read_text())))
        return
    if args.command == "fidelity":
        from benchmarks.graph_oracle.fidelity import fidelity, fidelity_markdown

        print(fidelity_markdown(fidelity(load_gold(args.gold), json.loads(args.graph.read_text()))))
        return
    _quiet_offline_cli()
    if args.command == "blind":
        from benchmarks.graph_oracle import blind_run

        report = asyncio.run(blind_run.run(args.out))
        print((args.out / "report.md").read_text())
        print(f"graph oracle blind: {report['seconds']}s, report in {args.out}", file=sys.stderr)
        return
    if args.command == "routed":
        from benchmarks.graph_oracle import routed_run

        report = asyncio.run(routed_run.run(args.out, latency=args.latency))
        print((args.out / "report.md").read_text())
        print(f"graph oracle routed: {report['seconds']}s, report in {args.out}", file=sys.stderr)
        return
    if args.command == "latency":
        from benchmarks.graph_oracle import latency

        report = asyncio.run(latency.run(args.out, backend=args.backend, turns=args.turns, database_url=args.database_url))
        print(latency.markdown([report]))
        return
    if args.command in ("blind-generate", "blind-paraphrase"):
        from benchmarks.graph_oracle import blind

        result = asyncio.run(blind.generate(args.out) if args.command == "blind-generate" else blind.paraphrase(args.out))
        print(f"{result['calls']} requests, written under {args.out}", file=sys.stderr)
        return
    try:
        params = parse_params(args.settings)
    except ValueError as exc:
        parser.error(str(exc))
    report = asyncio.run(run(args.out, corpora=tuple(args.corpora), heldout=not args.no_heldout,
                             ablations=not args.no_ablations, params=params))
    print((args.out / "report.md").read_text())
    print(f"graph oracle: {report['seconds']['total']}s, report in {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
