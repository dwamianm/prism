"""Authored fitting/gate controls, not an independent retrieval quality benchmark.

Uses repository test fixtures with deliberately constructed score features.
Run from a checkout with development dependencies, optionally against a wheel.
"""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys

from benchmarks.diagnostics._process import checked_report
from prme.models.learning import LearningConfig
from prme.retrieval.learning import evaluate_learning
from prme.types import Scope


def run():
    from tests.test_relevance_learning import evidence

    receipts, records = evidence()
    config = LearningConfig()
    baseline_bytes = [r.model_dump_json() for r in receipts]
    report = evaluate_learning(receipts, records, user_id="owner", scopes=[Scope.PROJECT], config=config)
    assert report.decision == "improved_on_observed_candidates"
    heldout = {rid for query in report.queries if query.split == "validation" for rid in query.request_ids}
    reversed_labels = [record.model_copy(update={"labels": {nid: not value for nid, value in record.labels.items()}})
                       if record.request_id in heldout else record for record in records]
    rejected = evaluate_learning(receipts, reversed_labels, user_id="owner", scopes=[Scope.PROJECT], config=config)
    assert rejected.multipliers == report.multipliers
    assert rejected.training_loss_after == report.training_loss_after
    assert rejected.decision == "no_improvement" and rejected.validation_ndcg_gain < 0
    assert [r.model_dump_json() for r in receipts] == baseline_bytes
    fields = {"algorithm", "input_checksum", "config", "coverage", "exclusions", "multipliers", "decision",
              "training_loss_before", "training_loss_after", "validation_ndcg_gain", "validation_pairwise_gain",
              "validation_gain_interval"}
    return {"passed": True, "positive_control": report.model_dump(mode="json", include=fields),
            "reversed_validation_control": rejected.model_dump(mode="json", include=fields),
            "package_path": str(Path(inspect.getfile(evaluate_learning)).resolve()),
            "learner_sha256": hashlib.sha256(Path(inspect.getfile(evaluate_learning)).read_bytes()).hexdigest(),
            "fixture_sha256": hashlib.sha256(Path(inspect.getfile(evidence)).read_bytes()).hexdigest(),
            "checks": ["learnable pairwise mechanism", "validation labels do not change fitted weights",
                       "negative holdout rejects proposal", "input receipt snapshots remain unchanged"],
            "limits": "100 authored query IDs share a constructed feature pattern. This tests fitting and rejection mechanics, not task generalization or product retrieval improvement."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    report = run() if args.worker else checked_report(
        [sys.executable, "-m", "benchmarks.diagnostics.learning_controls", "--worker"], timeout=120)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if not args.worker:
        print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
