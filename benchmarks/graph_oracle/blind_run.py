"""The blind validation run: the original 53 items, the blind questions and their paraphrases, once.

``python -m benchmarks.graph_oracle blind --out <dir>`` stores the three
memory sets with ``store()`` and real local embeddings, writes the gold graphs
into the graph store, and ranks every question by the baseline, by the generic
walk with the hand written cue words and with the cue words derived from
``DESIGN.md``, and by each of those fused with the text list at weights 1 and
2. A third configuration, derived cues with the owner seeded by I, me and my, is
exploratory and never part of the verdict. The settings are the first
report's; nothing is tuned between the first number and the last. The verdict
is computed from the rule fixed in ``BLIND.md`` before the run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

from benchmarks.diagnostics.product_packing import gate_config
from benchmarks.fast_cycle import store as fast_store
from benchmarks.graph_oracle import blind as blind_module
from benchmarks.graph_oracle.gold import GOLD_NAMES, load_gold, term_hits
from benchmarks.graph_oracle.graph import MemorySource, load_graph, write_graph
from benchmarks.graph_oracle.run import HELDOUT, Item, _table, build_corpus, commit, first_rank, metrics
from benchmarks.graph_oracle.strategies import FIRST_PERSON, Params, fuse, generic_scores, graph_first
from prme import MemoryEngine

KIND = "prme-graph-oracle-blind-report"
SCHEMA_VERSION = 1
CONFIGS = {"hand": Params(), "derived": Params(cue_source="derived"),
           "derived+I": Params(cue_source="derived", first_person_seed=True)}
WEIGHTS = (1.0, 2.0)
PRIMARY = "generic[derived]"
INFORMATION = ("fused@1[derived]", "fused@2[derived]", "generic[hand]")
EXPLORATORY = ("generic[derived+I]", "fused@1[derived+I]", "fused@2[derived+I]")
SETS = ("original", "blind", "paraphrase")
TYPES = ("lookup", "list", "multi-hop", "temporal", "correction", "negation", "aggregation")
STRATEGIES = ("baseline", *(f"{kind}[{name}]" for name in CONFIGS for kind in
                            ("generic", *(f"fused@{weight:g}" for weight in WEIGHTS))))
# Columns in the order the tables show them.
COLUMNS = ("baseline", "generic[hand]", "generic[derived]", "fused@1[hand]", "fused@1[derived]", "fused@2[hand]",
           "fused@2[derived]", "generic[derived+I]", "fused@1[derived+I]", "fused@2[derived+I]")
LIFT_MARGIN = 0.08
PARAPHRASE_MARGIN = 0.05


def _normal(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", "", text.lower().replace("’", "'")).split()


def _type(item: Item) -> str:
    return "multi-hop" if item.kind in ("two_hop", "three_hop") else item.kind


def _set(item: Item) -> str:
    return {"gold": "original", "blind": "blind", "paraphrase": "paraphrase"}[item.group]


def recall5(ranking: list[str], answers) -> float:
    return len(set(ranking[:5]) & set(answers)) / len(set(answers))


def cell(rows: list[dict]) -> dict:
    return {**metrics([row["rank"] for row in rows]),
            "recall5": round(sum(row["recall5"] for row in rows) / len(rows), 3) if rows else 0.0}


def verdict(table: dict, strategy: str, baseline: str = "baseline") -> dict:
    """The four conditions of BLIND.md for one strategy, from the by type table of the report."""
    blind = table["blind"]
    count = blind["all"][baseline]["n"]
    gain = (blind["all"][strategy]["top3"] - blind["all"][baseline]["top3"]) / count
    worst = max((blind[kind][baseline]["top3"] - blind[kind][strategy]["top3"] for kind in TYPES if kind in blind), default=0)
    lookups = blind["lookup"][strategy]["top3"] - blind["lookup"][baseline]["top3"]
    original_rate = blind["all"][strategy]["top3"] / count
    paraphrase = table["paraphrase"]["all"][strategy]
    paraphrase_rate = paraphrase["top3"] / paraphrase["n"]
    conditions = {
        "1 top 3 at least 8 points above the baseline": {"value": round(gain * 100, 1), "pass": gain >= LIFT_MARGIN - 1e-9},
        "2 loses on no type by more than 1 question": {"value": worst, "pass": worst <= 1},
        "3 ties or beats the baseline on lookups": {"value": lookups, "pass": lookups >= 0},
        "4 paraphrases lose at most 5 points": {"value": round((paraphrase_rate - original_rate) * 100, 1),
                                                "pass": paraphrase_rate - original_rate >= -PARAPHRASE_MARGIN - 1e-9},
    }
    return {"conditions": conditions, "passes": all(item["pass"] for item in conditions.values())}


async def run(out: Path, *, work: Path | None = None) -> dict:
    started = time.perf_counter()
    blind = blind_module.load_blind()
    paraphrases = blind_module.load_paraphrases()
    sha = blind_module.blind_sha256()
    golds = {name: load_gold(name) for name in GOLD_NAMES}
    cleanup = tempfile.TemporaryDirectory(prefix="graph-oracle-blind-") if work is None else None
    work = Path(cleanup.name) if cleanup else work
    pack = work / "pack"
    (pack / "lexical_index").mkdir(parents=True, exist_ok=True)
    unchanged = 0
    items: list[Item] = []
    graphs = {}
    async with MemoryEngine.open(gate_config(pack)) as engine:
        for name, gold in golds.items():
            extra = []
            for question in blind["questions"]:
                if question["corpus"] != name:
                    continue
                extra.append({"id": question["id"], "text": question["question"], "kind": question["type"],
                              "answers": question["answers"], "stale": [], "group": "blind"})
                for number, text in enumerate(paraphrases[question["id"]], start=1):
                    if _normal(text) == _normal(question["question"]):
                        unchanged += 1
                        continue
                    extra.append({"id": f"{question['id']}#p{number}", "text": text, "kind": question["type"],
                                  "answers": question["answers"], "stale": [], "group": "paraphrase"})
            node_ids, corpus_items = await build_corpus(engine, gold, False, extra)
            items += corpus_items
            handles = await write_graph(engine, gold, gold.owner, node_ids)
            graphs[name] = await load_graph(engine, gold.owner, handles)
    # Memories that answer an original question or a held-out question: facts the gold author had in view.
    heldout = fast_store.load_fixture(HELDOUT)["key"]["questions"]
    seen = {name: {memory_id for item in items if item.corpus == name and item.group == "gold" for memory_id in item.answers}
            for name in golds}
    for question in heldout:
        terms = {key: question[key] for key in ("all_of", "any_of") if question.get(key)}
        seen["store-v1"] |= {memory.id for memory in golds["store-v1"].memories if term_hits(terms, memory.text)}
    rows: list[dict] = []
    seedless = {"hand": 0, "derived": 0}
    for item in items:
        gold = golds[item.corpus]
        lists = {"baseline": item.base}
        for name, params in CONFIGS.items():
            result = await generic_scores(item.text, gold, MemorySource(graphs[item.corpus], params.shape), params)
            if name in seedless and item.group == "blind" and not result.seeds:
                seedless[name] += 1
            lists[f"generic[{name}]"] = graph_first(result.scores, item.base)
            for weight in WEIGHTS:
                lists[f"fused@{weight:g}[{name}]"] = fuse(result.scores, item.base, weight)
        base_id = item.id.split("#")[0]
        rows.append({"corpus": item.corpus, "id": item.id, "set": _set(item), "type": _type(item), "question": item.label,
                     "first_person": bool(FIRST_PERSON & set(_normal(item.text))),
                     "fresh": item.group == "gold" or not (set(item.answers) & seen[item.corpus]),
                     "answers": list(item.answers),
                     "ranks": {strategy: first_rank(ranking, item.answers) for strategy, ranking in lists.items()},
                     "recall5": {strategy: recall5(ranking, item.answers) for strategy, ranking in lists.items()},
                     "base_id": base_id})
    table: dict[str, dict] = {}
    for set_name in SETS:
        table[set_name] = {}
        chosen = [row for row in rows if row["set"] == set_name]
        for label in ("all", *TYPES):
            group = chosen if label == "all" else [row for row in chosen if row["type"] == label]
            if not group:
                continue
            table[set_name][label] = {strategy: cell([{"rank": row["ranks"][strategy], "recall5": row["recall5"][strategy]}
                                                      for row in group]) for strategy in STRATEGIES}
    subsets = {}
    for label, picker in (("blind, answer memory no original or held-out question asks about", lambda row: row["set"] == "blind" and row["fresh"]),
                          ("blind, first person questions", lambda row: row["set"] == "blind" and row["first_person"]),
                          ("blind, not first person", lambda row: row["set"] == "blind" and not row["first_person"])):
        group = [row for row in rows if picker(row)]
        subsets[label] = {"n": len(group), **{strategy: cell([{"rank": row["ranks"][strategy], "recall5": row["recall5"][strategy]}
                                                              for row in group]) for strategy in STRATEGIES}}
    report = {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "commit": commit(), "blind_sha256": sha,
        "blind_questions": len(blind["questions"]), "paraphrases_scored": sum(row["set"] == "paraphrase" for row in rows),
        "paraphrases_unchanged_and_left_out": unchanged, "seedless_blind_questions": seedless,
        "configs": {name: {"cue_source": params.cue_source, "first_person_seed": params.first_person_seed}
                    for name, params in CONFIGS.items()},
        "table": table, "subsets": subsets,
        "verdict": {strategy: verdict(table, strategy) for strategy in (PRIMARY, *INFORMATION)},
        "rows": rows, "seconds": round(time.perf_counter() - started, 1),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    (out / "report.md").write_text(report_markdown(report))
    if cleanup:
        cleanup.cleanup()
    return report


def _cell(entry: dict) -> str:
    return f"{entry['top3']}/{entry['top5']}/{entry['top1']}/{entry['mrr']:.2f}"


def report_markdown(report: dict) -> str:
    lines = [f"# Blind validation (commit `{report['commit']}`)", "",
             f"Blind set sha256 `{report['blind_sha256']}`, {report['blind_questions']} questions; "
             f"{report['paraphrases_scored']} paraphrases scored, {report['paraphrases_unchanged_and_left_out']} unchanged ones left out. "
             f"Blind questions with no entity to seed the walk: {report['seedless_blind_questions']}.", "",
             "Each cell is top 3 / top 5 / rank 1 / MRR, counts of questions.", ""]
    for set_name in SETS:
        lines += [f"## {set_name}", ""]
        rows = report["table"][set_name]
        lines += _table(["Type (n)", *COLUMNS], [[f"{label} ({rows[label]['baseline']['n']})",
                                                  *(_cell(rows[label][strategy]) for strategy in COLUMNS)]
                                                 for label in ("all", *TYPES) if label in rows])
        lines += [""]
    lines += ["## Blind subsets", ""]
    lines += _table(["Subset (n)", *COLUMNS], [[f"{label} ({entry['n']})", *(_cell(entry[strategy]) for strategy in COLUMNS)]
                                               for label, entry in report["subsets"].items()])
    lines += ["", "## Verdict by the rule in BLIND.md", ""]
    for strategy, result in report["verdict"].items():
        lines += [f"**{strategy}**: {'all four conditions hold' if result['passes'] else 'fails'}"
                  + (" (the rule's strategy)" if strategy == PRIMARY else " (information only)"), ""]
        lines += _table(["Condition", "Value", "Holds"], [[name, item["value"], "yes" if item["pass"] else "no"]
                                                         for name, item in result["conditions"].items()])
        lines += [""]
    lines += [f"Seconds: {report['seconds']}.", ""]
    return "\n".join(lines)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
