"""The routing gate's run: the baseline and the routed graph on the new blind draw, once.

``python -m benchmarks.graph_oracle routed --out <dir> --latency <json>`` stores
the new memory set (``draw2-v1``) with ``store()`` and real local embeddings,
writes its gold graph, and for each frozen question (``blind-v2.json``) takes:

- ``baseline``: ``retrieve()`` as it is;
- ``routed[derived]`` and ``routed[hand]``: the walk's list ahead of the text list
  when the router sends the question to the graph, and the baseline's list
  unchanged otherwise;
- ``graph[derived]`` and ``graph[hand]``, for information: the walk's list ahead of
  the text list for every question, routed or not.

The walk is ``sqlwalk.SqlWalk`` (one statement, DuckDB here; the equivalence with
the Python walk is a test). The six conditions of ``GATE.md`` are computed from the
run, the router's accuracy on the spent 86 question blind set and on the new
draw, and the PostgreSQL latency file the latency command wrote.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time

from benchmarks.diagnostics.product_packing import gate_config
from benchmarks.graph_oracle import blind as blind_module
from benchmarks.graph_oracle import router
from benchmarks.graph_oracle.gold import load_gold
from benchmarks.graph_oracle.graph import write_graph
from benchmarks.graph_oracle.run import _table, build_corpus, commit, first_rank, metrics
from benchmarks.graph_oracle.sqlwalk import SqlWalk
from benchmarks.graph_oracle.strategies import Params, graph_first
from prme import MemoryEngine

KIND = "prme-graph-oracle-routed-report"
SCHEMA_VERSION = 1
TYPES = ("lookup", "list", "multi-hop", "temporal", "correction", "negation", "aggregation")
CONFIGS = {"derived": Params(cue_source="derived"), "hand": Params()}
COLUMNS = ("baseline", "routed[derived]", "routed[hand]", "graph[derived]", "graph[hand]")
UNROUTED = ("lookup", "temporal", "correction")
GRAPH_TYPES = ("list", "multi-hop", "aggregation")
LIFT_POINTS = 0.05
GAIN_QUESTIONS = 6
ACCURACY = 0.90
LOOKUP_ROUTED = 0.02
BUDGET_MS = 50.0


def conditions(rows: list[dict], routing: dict, latency_p95: float | None, strategy: str = "routed[derived]") -> dict:
    """The six conditions of GATE.md for one routed strategy, from the new draw's per question rows."""
    def count(kind: str | None, name: str) -> int:
        return sum(1 for row in rows if (kind is None or row["type"] == kind) and row["ranks"][name] is not None
                   and row["ranks"][name] <= 3)

    total = len(rows)
    lift = (count(None, strategy) - count(None, "baseline")) / total
    worst = max((count(kind, "baseline") - count(kind, strategy) for kind in TYPES if any(r["type"] == kind for r in rows)),
                default=0)
    different = sum(1 for row in rows if row["type"] in UNROUTED and row["ranks"][strategy] != row["ranks"]["baseline"])
    gain = sum(count(kind, strategy) - count(kind, "baseline") for kind in GRAPH_TYPES)
    return {
        "1 overall top 3 at least 5 points above the baseline": {"value": round(lift * 100, 1), "pass": lift >= LIFT_POINTS - 1e-9},
        "2 no type loses more than 1 question": {"value": worst, "pass": worst <= 1},
        "3 lookups, temporal and correction equal the baseline question by question": {"value": different, "pass": different == 0},
        "4 list, multi-hop and aggregation together gain at least 6": {"value": gain, "pass": gain >= GAIN_QUESTIONS},
        "5 router accuracy at least 90 percent and at most 2 percent of lookups routed": {
            "value": f"{routing['accuracy'] * 100:.1f}% accurate, {routing['lookup_routed_share'] * 100:.1f}% of lookups routed",
            "pass": routing["accuracy"] >= ACCURACY and routing["lookup_routed_share"] <= LOOKUP_ROUTED + 1e-9},
        "6 the SQL walk at 10,000 memories at most 50 ms p95 on PostgreSQL": {
            "value": latency_p95, "pass": latency_p95 is not None and latency_p95 <= BUDGET_MS},
    }


def sql_p95(path: Path | None) -> float | None:
    """The PostgreSQL SQL walk's 95th percentile at 10,000 memories, from the latency command's file."""
    if path is None or not path.exists():
        return None
    report = json.loads(path.read_text())
    if report.get("backend") != "postgres":
        return None
    return next((result["sql_reads"]["all"]["p95"] for result in report["results"] if result["turns"] == 10000), None)


async def run(out: Path, *, latency: Path | None = None, work: Path | None = None) -> dict:
    started = time.perf_counter()
    blind = blind_module.load_blind(blind_module.BLIND2_FILE)
    sha = blind_module.blind_sha256(blind_module.BLIND2_FILE)
    gold = load_gold("draw2-v1")
    cleanup = tempfile.TemporaryDirectory(prefix="graph-oracle-routed-") if work is None else None
    work = Path(cleanup.name) if cleanup else work
    pack = work / "pack"
    (pack / "lexical_index").mkdir(parents=True, exist_ok=True)
    extra = [{"id": question["id"], "text": question["question"], "kind": question["type"], "answers": question["answers"],
              "stale": [], "group": "blind"} for question in blind["questions"]]
    rows: list[dict] = []
    async with MemoryEngine.open(gate_config(pack)) as engine:
        node_ids, items = await build_corpus(engine, gold, False, extra)
        handles = await write_graph(engine, gold, gold.owner, node_ids)
        walks = {name: SqlWalk(engine, "duckdb", gold.owner, handles, gold, params) for name, params in CONFIGS.items()}
        for item in items:
            routed = router.route(item.text)
            ranks = {"baseline": first_rank(item.base, item.answers)}
            for name, walk in walks.items():
                graph = graph_first((await walk.scores(item.text)).scores, item.base)
                ranks[f"graph[{name}]"] = first_rank(graph, item.answers)
                ranks[f"routed[{name}]"] = ranks[f"graph[{name}]"] if routed == "graph" else ranks["baseline"]
            rows.append({"id": item.id, "type": item.kind, "question": item.text, "shape": router.shape(item.text),
                         "route": routed, "ranks": ranks})
    spent = blind_module.load_blind()["questions"]
    new_draw = [{"question": row["question"], "type": row["type"]} for row in rows]
    routing = {"spent_blind_set": router.accuracy(spent), "new_draw": router.accuracy(new_draw)}
    table: dict[str, dict] = {}
    for label in ("all", *TYPES):
        group = rows if label == "all" else [row for row in rows if row["type"] == label]
        if group:
            table[label] = {name: metrics([row["ranks"][name] for row in group]) for name in COLUMNS}
    p95 = sql_p95(latency)
    verdict = {strategy: conditions(rows, routing["new_draw"], p95, strategy) for strategy in ("routed[derived]", "routed[hand]")}
    report = {"kind": KIND, "schema_version": SCHEMA_VERSION, "commit": commit(), "blind_sha256": sha,
              "questions": len(rows), "routing": routing, "table": table, "sql_walk_p95_ms_postgres_10000": p95,
              "verdict": {name: {"conditions": value, "passes": all(item["pass"] for item in value.values())}
                          for name, value in verdict.items()},
              "rows": rows, "seconds": round(time.perf_counter() - started, 1)}
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    (out / "report.md").write_text(report_markdown(report))
    if cleanup:
        cleanup.cleanup()
    return report


def _cell(entry: dict) -> str:
    return f"{entry['top3']}/{entry['top5']}/{entry['top1']}/{entry['mrr']:.2f}"


def report_markdown(report: dict) -> str:
    lines = [f"# Routing gate run (commit `{report['commit']}`)", "",
             f"New draw `blind-v2.json` sha256 `{report['blind_sha256']}`, {report['questions']} questions. "
             "Each cell is top 3 / top 5 / rank 1 / MRR, counts of questions.", "", "## By question type", ""]
    lines += _table(["Type (n)", *COLUMNS], [[f"{label} ({entry['baseline']['n']})", *(_cell(entry[name]) for name in COLUMNS)]
                                             for label, entry in report["table"].items()])
    lines += ["", "## Router", ""]
    for name, result in report["routing"].items():
        lines.append(f"- {name.replace('_', ' ')}: {result['n']} questions, accuracy {result['accuracy'] * 100:.1f}%, "
                     f"{result['lookups_routed_to_graph']} of {result['lookups']} lookups routed to the graph; routed "
                     + ", ".join(f"{kind} {entry['routed_to_graph']}/{entry['n']}" for kind, entry in result["by_type"].items()))
    lines += ["", f"SQL walk at 10,000 memories on PostgreSQL, p95: {report['sql_walk_p95_ms_postgres_10000']} ms.", "",
              "## Verdict against GATE.md", ""]
    for strategy, result in report["verdict"].items():
        lines += [f"**{strategy}**: {'pass' if result['passes'] else 'fail'}", ""]
        lines += _table(["Condition", "Value", "Holds"], [[name, item["value"], "yes" if item["pass"] else "no"]
                                                         for name, item in result["conditions"].items()])
        lines += [""]
    lines += [f"Seconds: {report['seconds']}.", ""]
    return "\n".join(lines)

