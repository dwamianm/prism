"""Compare complete, matched evidence runs without merging partial results."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path


def paired_statistics(pairs: list[tuple[float, float]], *, samples: int = 2000, seed: int = 42) -> dict:
    """Paired query bootstrap; intervals describe this selected query sample."""
    if samples < 1:
        raise ValueError("Bootstrap samples must be positive")
    if not pairs:
        return {"queries": 0, "before": None, "after": None, "delta": None, "interval_95": None}
    differences = [after - before for before, after in pairs]
    rng = random.Random(seed)
    bootstraps = (
        [differences[0]] * samples if len(set(differences)) == 1 else
        sorted(statistics.mean(rng.choices(differences, k=len(pairs))) for _ in range(samples))
    )
    return {
        "queries": len(pairs), "before": statistics.mean(a for a, _ in pairs),
        "after": statistics.mean(b for _, b in pairs), "delta": statistics.mean(differences),
        "interval_95": [bootstraps[int(.025 * (samples - 1))], bootstraps[int(.975 * (samples - 1))]],
        "wins": sum(d > 1e-12 for d in differences),
        "losses": sum(d < -1e-12 for d in differences),
        "ties": sum(abs(d) <= 1e-12 for d in differences),
    }


def compare(before: dict, after: dict, *, samples: int = 2000) -> dict:
    for report in (before, after):
        if not report.get("complete") or report.get("errors"):
            raise ValueError("Comparison requires two complete runs without errors")
        selected = report["dataset"]["selected_question_ids"]
        details = report["details"]
        ids = [d["question_id"] for d in details]
        if len(ids) != len(set(ids)) or set(ids) != set(selected) or any("error" in d for d in details):
            raise ValueError("Every selected question must have exactly one successful result")
    for field in ("kind", "profile", "tokenizer", "budgets", "candidate_limit"):
        if before[field] != after[field]:
            raise ValueError(f"Cannot compare different {field}")
    for field in ("sha256", "variant", "split", "split_seed", "selected_question_ids"):
        if before["dataset"][field] != after["dataset"][field]:
            raise ValueError(f"Cannot compare different dataset {field}")
    old = {d["question_id"]: d for d in before["details"]}
    new = {d["question_id"]: d for d in after["details"]}
    methods = sorted(before["summary"])
    if methods != sorted(after["summary"]):
        raise ValueError("Runs must report the same methods")

    def metrics_for(ids):
        result = {}
        for method in methods:
            rows = [(old[q]["methods"][method], new[q]["methods"][method]) for q in ids]
            names = sorted(rows[0][0]["metrics"]) if rows else []
            metrics = {}
            for metric in names:
                pairs = [(a["metrics"][metric], b["metrics"][metric]) for a, b in rows]
                pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
                metrics[metric] = paired_statistics(pairs, samples=samples)
            packing = {}
            for budget in before["budgets"]:
                pairs = [(a["packing"][str(budget)]["evidence_recall"],
                          b["packing"][str(budget)]["evidence_recall"]) for a, b in rows]
                packing[str(budget)] = paired_statistics(
                    [(a, b) for a, b in pairs if a is not None and b is not None], samples=samples,
                )
            result[method] = {"metrics": metrics, "packed_evidence_recall": packing}
        return result

    ids = before["dataset"]["selected_question_ids"]
    if any(old[q]["category"] != new[q]["category"] for q in ids):
        raise ValueError("Category labels differ between runs")
    if any(old[q][field] != new[q][field] for q in ids for field in ("source_count", "evidence_source_ids")):
        raise ValueError("Source corpus or evidence labels differ between runs")
    protocol_changes = {}
    for field, default in (("query_clock", "wall"), ("concurrency", 1)):
        before_value, after_value = before.get(field, default), after.get(field, default)
        if before_value != after_value:
            protocol_changes[field] = {"before": before_value, "after": after_value}
    limitations = [
        "Evidence retrieval only; no generated-answer accuracy or cross-product superiority claim.",
        "Paired bootstrap resamples questions; shared source histories can make queries dependent.",
        "Development-set improvements require a frozen held-out confirmation.",
        "Timing is not compared because concurrent workloads and warm caches affect it.",
    ]
    if "query_clock" in protocol_changes:
        limitations.append(
            "Query clocks differ: this delta combines software and evaluation-clock changes; "
            "it is not an isolated algorithm comparison."
        )
    return {
        "kind": "paired-source-evidence-comparison", "selected_queries": len(ids),
        "before": {"run_id": before["run_id"], "provenance": before["provenance"],
                   "query_clock": before.get("query_clock", "wall"), "concurrency": before.get("concurrency", 1)},
        "after": {"run_id": after["run_id"], "provenance": after["provenance"],
                  "query_clock": after.get("query_clock", "wall"), "concurrency": after.get("concurrency", 1)},
        "dataset": before["dataset"], "bootstrap_samples": samples, "bootstrap_seed": 42,
        "protocol_changes": protocol_changes,
        "limitations": limitations,
        "methods": metrics_for(ids),
        "categories": {
            category: metrics_for([q for q in ids if old[q]["category"] == category])
            for category in sorted({old[q]["category"] for q in ids})
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
