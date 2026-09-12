"""Replay development candidates through the product packer at fixed budgets.

This offline diagnostic changes only the multi-path tier's comparator. It never
runs retrieval concurrently with its temporary comparator substitution. Source
labels are used solely after packing, and pointer-only records earn no evidence
credit. No production defaults are changed.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import patch

from benchmarks.compare_evidence import paired_statistics
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens


def measure(bundle, gold: set[str], config: PackingConfig) -> dict:
    """Credit complete source text actually present in content-bearing JSON."""
    context = bundle.render()
    tokens = count_tokens(context, config.tokenizer)
    if tokens != bundle.tokens_used or tokens > max(0, config.token_budget - config.overhead_tokens):
        raise ValueError("Product context does not obey its measured budget")
    entries = {entry["id"]: entry for line in context.splitlines()
               if line.startswith("{") for entry in [json.loads(line)]}
    content_ids, pointer_ids, representations = [], [], {}
    for group in bundle.sections.values():
        for candidate in group:
            entry = entries[str(candidate.node.id)]
            representation = entry["representation"]
            representations[representation] = representations.get(representation, 0) + 1
            source_id = candidate.node.metadata["source_turn"]
            if representation in {"full", "prose", "structured"}:
                content = candidate.node.content
                if not content or content not in entry["text"]:
                    raise ValueError("Content-bearing representation lost source text")
                content_ids.append(source_id)
            else:
                pointer_ids.append(source_id)
    retained = set(content_ids)
    return {
        "tokens": tokens, "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "content_source_ids": content_ids, "pointer_source_ids": pointer_ids,
        "representations": representations,
        "evidence_recall": len(retained & gold) / len(gold) if gold else None,
        "all_evidence_retained": gold <= retained if gold else None,
    }


def compare(report: dict, snapshots: Path, *, samples: int = 2000) -> dict:
    if (not report.get("complete") or report.get("errors")
            or report.get("process_exit_code") != 0):
        raise ValueError("A complete, normally exited source run without errors is required")
    if report["dataset"]["split"] != "dev":
        raise ValueError("This exploratory comparator is restricted to the development split")
    selected = report["dataset"]["selected_question_ids"]
    rows = report["details"]
    ids = [row["question_id"] for row in rows]
    if (not selected or len(set(selected)) != len(selected) or len(set(ids)) != len(ids)
            or set(ids) != set(selected) or any("error" in row for row in rows)):
        raise ValueError("Every selected question must have one successful result")
    budgets = report["budgets"]
    if not budgets or any(not isinstance(b, int) or b < 1 for b in budgets):
        raise ValueError("Positive token budgets are required")
    config = PackingConfig.model_validate(report["provenance"]["engine_config"]["packing"])
    prepared = []
    # Reproduce every control before beginning the experimental comparison.
    for row in rows:
        ref = row["candidate_snapshot"]
        filename = hashlib.sha256(row["question_id"].encode()).hexdigest() + ".json"
        if ref["filename"] != filename:
            raise ValueError("Snapshot filename does not match question identity")
        raw = (snapshots / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("Candidate snapshot hash mismatch")
        saved = json.loads(raw)
        if saved["question_id"] != row["question_id"] or saved["packing_config"] != config.model_dump(mode="json"):
            raise ValueError("Snapshot identity or packing configuration mismatch")
        candidates = [RetrievalCandidate.model_validate(c) for c in saved["candidates"]]
        original = [c.model_dump(mode="json") for c in candidates]
        control = pack_context(candidates, config)
        if control.render() != saved["control"]["context"] or control.tokens_used != saved["control"]["tokens"]:
            raise ValueError("Baseline context does not reproduce; use the original product packer")
        if original != [c.model_dump(mode="json") for c in candidates]:
            raise ValueError("Control packing mutated the candidates")
        prepared.append((row, candidates, original))
    details = []
    for row, candidates, original in prepared:
        gold = set(row["evidence_source_ids"])
        variants = {"density": {}, "score": {}}
        for budget in budgets:
            current = config.model_copy(update={"token_budget": budget})
            variants["density"][str(budget)] = measure(pack_context(candidates, current), gold, current)
            with patch("prme.retrieval.packing.compute_str", lambda c: c.composite_score):
                bundle = pack_context(candidates, current)
            variants["score"][str(budget)] = measure(bundle, gold, current)
        if original != [c.model_dump(mode="json") for c in candidates]:
            raise ValueError("Experimental packing mutated frozen candidates")
        details.append({
            "question_id": row["question_id"], "category": row["category"],
            "evidence_source_ids": sorted(gold), "candidate_count": len(candidates),
            "candidate_snapshot_sha256": row["candidate_snapshot"]["sha256"],
            "variants": variants,
        })

    def summarize(items):
        result = {}
        for budget in budgets:
            metrics = {}
            for metric in ("evidence_recall", "all_evidence_retained"):
                pairs = [(row["variants"]["density"][str(budget)][metric],
                          row["variants"]["score"][str(budget)][metric]) for row in items]
                metrics[metric] = paired_statistics(
                    [(float(a), float(b)) for a, b in pairs if a is not None and b is not None], samples=samples,
                )
            result[str(budget)] = metrics
        return result

    return {
        "complete": True, "baseline_reproduction_passed": True,
        "kind": "development-product-packing-comparison", "dataset": report["dataset"],
        "source_provenance": report["provenance"], "packing_config": config.model_dump(mode="json"),
        "packing_module_sha256": hashlib.sha256(Path(inspect.getfile(pack_context)).read_bytes()).hexdigest(),
        "diagnostic_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "budgets": budgets, "bootstrap_samples": samples, "bootstrap_seed": 42,
        "comparator": "composite score within the multi-path tier; all other priorities and rendering unchanged",
        "limitations": [
            "Development source-support retention only; no answer accuracy or competitive quality claim.",
            "Both variants reuse identical public retrieval candidates, scores, identities and timestamps.",
            "All response candidates are packed; the shared evaluation ranking limit is not applied here.",
            "REFERENCE and KEY_VALUE pointers receive no supporting-evidence credit.",
            "Question bootstrap intervals are descriptive; shared histories can make questions dependent.",
            "Source snapshots contain benchmark text and are stored separately from this summary.",
        ],
        "summary": summarize(details),
        "categories": {category: summarize([row for row in details if row["category"] == category])
                       for category in sorted({row["category"] for row in details})},
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    result = compare(json.loads(raw), args.snapshots)
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"complete": result["complete"], "questions": len(result["details"]),
                      "summary": result["summary"]}))


if __name__ == "__main__":
    main()
