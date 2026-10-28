"""The tables of the embedding experiment and the checks of ``GATE.md`` lines 1, 2 and 6, from the result files.

    python -m benchmarks.embedding_models report --results <folder> --out <report.md>

The folder holds ``pack.json``, ``latency.json``, ``storage.json``, ``operations.json`` and one ``scale-<model>/report.json``
per model (whichever exist). Nothing is recomputed from the engine; the tables are the files' numbers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.embedding_models.models import MODELS
from benchmarks.graph_oracle.scale_run import TYPES

REFERENCE = "bge-small"
ORDER = ["bge-small", "bge-small+q", "minilm", "bge-base", "bge-base+q", "bge-large", "bge-large+q", "mxbai", "mxbai+q", "nomic"]
SETTINGS = (("lookalike", "core"), ("lookalike", "2000"), ("lookalike", "10000"), ("conversation", "2000"))
SPEED_LIMIT = 8.0


def table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" if index == 0 else "---:" for index in range(len(headers))) + "|"]
    return lines + ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]


def label_of(model: str, pass_name: str) -> str:
    if pass_name == "plain":
        return model
    return pass_name  # "bge-small+q", or the model's own key for nomic


def load(results: Path) -> dict:
    data: dict = {"scale": {}}
    for name in ("pack", "latency", "storage", "operations", "switch"):
        path = results / f"{name}.json"
        if path.exists():
            data[name] = json.loads(path.read_text())
    for model in MODELS:
        path = results / f"scale-{model}" / "report.json"
        if path.exists():
            report = json.loads(path.read_text())
            for kind, body in report["kinds"].items():
                for stage, stage_body in body["stages"].items():
                    for pass_name, pass_body in stage_body["passes"].items():
                        label = label_of(model, pass_name or "plain")
                        data["scale"].setdefault(label, {})[(kind, stage)] = {"summary": pass_body["summary"], "ranks": pass_body["ranks"],
                                                                            "retrieve_ms": pass_body["retrieve_ms"], "stage": stage_body}
            data["scale"].setdefault("_embedding", {})[model] = {kind: {"embedding": body["embedding"], "query_seconds": body["query_embedding_seconds"],
                                                                      "padding": body["padding"], "stages": {stage: {"store_seconds": s["store_seconds"],
                                                                      "embedding_seconds_so_far": s["embedding_seconds_so_far"]} for stage, s in body["stages"].items()}}
                                                                for kind, body in report["kinds"].items()}
    return data


def cell(entry: dict) -> str:
    return f"{entry['top3']}/{entry['top5']}/{entry['top1']}, {entry['mrr']:.3f}"


def pack_tables(pack: dict) -> list[str]:
    rows = []
    for label in [name for name in ORDER + ["nomic-bare"] if name in pack]:
        entry = pack[label]
        cities = entry["cities"]
        rows.append([label, ", ".join(str(rank) for rank in cities["ranks"]), cities["mrr"],
                     f"{entry['store']['top3']}/{entry['store']['questions']}", entry["store"]["top1"], entry["store"]["mrr"]])
    lines = table(["Recipe", "Cities rank, three spellings", "Cities MRR", "Store pack top 3", "Rank 1", "MRR (17)"], rows)
    lines += ["", "Cosine of the cities question to the memory that answers it, to the best memory that is not the answer, and to the best "
              "memory that says \"Japan trip\" (three spellings, in the order above), and the answer's rank by cosine alone among the 75:", ""]
    rows = []
    for label in [name for name in ORDER + ["nomic-bare"] if name in pack]:
        rows.append([label] + [f"{item['answer']:.2f} / {item['best_other']:.2f} / {max(item['japan_trip_memories']):.2f}, #{item['answer_rank_by_cosine']}"
                               for item in pack[label]["cities"]["cosines"]])
    return lines + table(["Recipe", "Spelling 1", "Spelling 2", "Spelling 3"], rows)


def scale_tables(data: dict) -> tuple[list[str], dict]:
    scale = data["scale"]
    labels = [label for label in ORDER if label in scale]
    reference = scale.get(REFERENCE, {})
    checks: dict = {}
    lines: list[str] = []
    for title, picker in (("Blind questions (151), answers in the top 3 / top 5 / rank 1, MRR", lambda summary: summary["blind"]),
                          ("All 204 questions, answers in the top 3 / top 5 / rank 1, MRR", lambda summary: summary["all"])):
        rows = []
        for label in labels:
            row = [label]
            for setting in SETTINGS:
                entry = scale[label].get(setting)
                row.append(cell(picker(entry["summary"])) if entry else "")
            rows.append(row)
        lines += [f"### {title}", ""] + table(["Recipe", "Core 189", "2,000 look-alike", "10,000 look-alike", "2,000 conversation"], rows) + [""]
    # Blind top 3 against the reference, the pass rule's first line.
    rows = []
    for label in labels:
        row = [label]
        for setting in SETTINGS:
            entry, base = scale[label].get(setting), reference.get(setting)
            if entry and base:
                gain = entry["summary"]["blind"]["top3"] - base["summary"]["blind"]["top3"]
                row.append(f"{gain:+d} ({gain / entry['summary']['blind']['n'] * 100:+.1f} pts)")
            else:
                row.append("")
        rows.append(row)
        base = reference.get(("lookalike", "2000"))
        entry = scale[label].get(("lookalike", "2000"))
        if entry and base:
            gain = entry["summary"]["blind"]["top3"] - base["summary"]["blind"]["top3"]
            checks.setdefault(label, {})["blind_gain_2000"] = gain
            checks[label]["line1"] = gain >= 7
    lines += ["### Blind top 3 against bge-small (answers, points of 151)", ""]
    lines += table(["Recipe", "Core 189", "2,000 look-alike", "10,000 look-alike", "2,000 conversation"], rows) + [""]
    rows = []
    for label in labels:
        row = [label]
        for setting in (("lookalike", "2000"), ("lookalike", "10000"), ("conversation", "2000")):
            entry = scale[label].get(setting)
            row.append(" / ".join(str(entry["summary"]["by_set"][name]["top3"]) for name in ("original", "blind-v1", "blind-v2")) if entry else "")
        rows.append(row)
    lines += ["### Top 3 by question set: original (53) / blind-v1 (86) / blind-v2 (65)", ""]
    lines += table(["Recipe", "2,000 look-alike", "10,000 look-alike", "2,000 conversation"], rows) + [""]
    lines += ["### Blind questions gained and lost in the top 3 against bge-small (paired, with a two-sided sign test)", ""]
    lines += paired_table(data) + [""]
    # Types: top 3 against the reference, 2,000 look-alike and 2,000 conversation, all 204 questions.
    for setting in (("lookalike", "2000"), ("conversation", "2000")):
        base = reference.get(setting)
        if not base:
            continue
        rows = []
        for label in labels:
            entry = scale[label].get(setting)
            if not entry:
                continue
            deltas = [entry["summary"]["by_type"][kind]["top3"] - base["summary"]["by_type"][kind]["top3"] for kind in TYPES]
            checks.setdefault(label, {})[f"worst_type_{setting[0]}"] = min(deltas)
            rows.append([label] + [f"{delta:+d}" for delta in deltas] + [f"{min(deltas):+d}"])
        counts = [base["summary"]["by_type"][kind]["n"] for kind in TYPES]
        lines += [f"### Top 3 by question type against bge-small, {setting[1]} {setting[0]} memories (all 204 questions)", ""]
        lines += table(["Recipe"] + [f"{kind} ({count})" for kind, count in zip(TYPES, counts)] + ["Worst"], rows) + [""]
    for label in labels:
        worst = [checks.get(label, {}).get(f"worst_type_{kind}") for kind in ("lookalike", "conversation")]
        if all(value is not None for value in worst):
            checks[label]["line2"] = min(worst) >= -1
    return lines, checks


def sign_test(gained: int, lost: int) -> float:
    """Two-sided exact p value of a paired sign test: gained and lost questions, ties dropped."""
    from math import comb

    n = gained + lost
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(0, min(gained, lost) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_table(data: dict) -> list[str]:
    """For each recipe against bge-small, the blind questions gained and lost in the top 3 and a sign test."""
    scale = data["scale"]
    reference = scale.get(REFERENCE, {})
    rows = []
    for label in [name for name in ORDER if name in scale and name != REFERENCE]:
        row = [label]
        for setting in SETTINGS:
            entry, base = scale[label].get(setting), reference.get(setting)
            if not (entry and base):
                row.append("")
                continue
            blind = [question for question in entry["ranks"] if question.startswith("blind-")]
            def top3(ranks: dict, question: str) -> bool:
                return ranks[question] is not None and ranks[question] <= 3

            gained = sum(top3(entry["ranks"], q) and not top3(base["ranks"], q) for q in blind)
            lost = sum(top3(base["ranks"], q) and not top3(entry["ranks"], q) for q in blind)
            row.append(f"+{gained} / -{lost}, p {sign_test(gained, lost):.2f}")
        rows.append(row)
    return table(["Recipe", "Core 189", "2,000 look-alike", "10,000 look-alike", "2,000 conversation"], rows)


def cost_tables(data: dict) -> list[str]:
    latency, storage, operations = data.get("latency", {}), data.get("storage", {}), data.get("operations", {})
    embedding = data["scale"].get("_embedding", {})
    lines = []
    if latency:
        base = latency[REFERENCE]["ms"]["p50"]
        rows = []
        for key in MODELS:
            if key not in latency:
                continue
            item = latency[key]
            seconds = embedding.get(key, {}).get("lookalike", {}).get("stages", {}).get("10000", {}).get("embedding_seconds_so_far")
            rows.append([key, item["dimension"], item["ms"]["p50"], item["ms"]["p95"], f"{item['ms']['p50'] / base:.1f}x", item["turns"]["p50"],
                         f"{seconds / 60:.1f} min" if seconds else "", f"{item['download_mb']:.0f} MB", f"{item['rss_mb_added']} MB",
                         "yes" if item["ms"]["p50"] / base <= SPEED_LIMIT else "no"])
        lines += ["### Embedding cost on one thread", ""]
        lines += table(["Model", "Dim", "p50 ms/text", "p95 ms/text", "p50 vs bge-small", "p50 ms, conversation turn", "Embed the 10,000 store",
                        "Download", "Loaded model memory", "Within 8x"], rows) + [""]
    if storage:
        rows = [[key, item["dimension"], item["vector_column_bytes_per_row"], item["vector_table_mb"], item["hnsw_index_mb"], item["vector_and_index_mb"],
                 item["hnsw_build_seconds"], f"{item['update_ms']['p50']} / {item['update_ms']['p95']}",
                 f"{item['exact_search_ms']['p50']} / {item['exact_search_ms']['p95']}"] for key, item in storage.items()]
        lines += ["### PostgreSQL pgvector per 10,000 memories", ""]
        lines += table(["Model", "Dim", "Bytes per vector", "Vector column MB", "HNSW index MB", "Vector and index MB", "HNSW build s",
                        "Vector UPDATE p50 / p95 ms", "Exact search p50 / p95 ms"], rows) + [""]
    if operations:
        rows = [[key, f"{item['store_ms']['p50']} / {item['store_ms']['p95']}", item["store_embedding_ms"]["p50"],
                 f"{item['retrieve_ms']['p50']} / {item['retrieve_ms']['p95']}", item["retrieve_embedding_ms"]["p50"]] for key, item in operations.items()]
        lines += ["### One store() and one retrieve() call on PostgreSQL, one thread (the 75-memory pack)", ""]
        lines += table(["Model", "store() p50 / p95 ms", "of which embedding p50 ms", "retrieve() p50 / p95 ms", "of which question embedding p50 ms"], rows) + [""]
    return lines


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.embedding_models report")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    data = load(args.results)
    lines = ["# Embedding models: tables", ""]
    if "pack" in data:
        lines += ["## The store pack and the cities question", ""] + pack_tables(data["pack"]) + [""]
    checks: dict = {}
    if data["scale"]:
        scale_lines, checks = scale_tables(data)
        lines += ["## The scale harness", ""] + scale_lines
    lines += ["## Cost", ""] + cost_tables(data)
    latency = data.get("latency", {})
    if latency and checks:
        base = latency[REFERENCE]["ms"]["p50"]
        rows = []
        for label, item in checks.items():
            model = label.split("+")[0].split("-bare")[0]
            speed = latency[model]["ms"]["p50"] / base if model in latency else None
            rows.append([label, item.get("blind_gain_2000", ""), item.get("line1", ""), item.get("worst_type_lookalike", ""),
                         item.get("worst_type_conversation", ""), item.get("line2", ""), f"{speed:.1f}x" if speed else "",
                         speed <= SPEED_LIMIT if speed else ""])
        lines += ["## Pass rule lines 1, 2 and 6", ""]
        lines += table(["Recipe", "Blind gain at 2,000 look-alike (questions)", "Line 1 (at least 7)", "Worst type, look-alike", "Worst type, conversation",
                        "Line 2 (not below -1)", "p50 vs bge-small", "Line 6 (within 8x)"], rows) + [""]
    args.out.write_text("\n".join(lines) + "\n")
    (args.out.with_suffix(".checks.json")).write_text(json.dumps(checks, indent=1) + "\n")
    print(args.out)
