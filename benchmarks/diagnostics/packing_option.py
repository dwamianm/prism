"""Verify the public ordering option reproduces frozen experimental contexts."""

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys

from benchmarks.diagnostics._process import checked_report
from benchmarks.diagnostics.product_packing import measure
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(source_path, comparison_path, snapshots):
    source = json.loads(source_path.read_bytes())
    comparison = json.loads(comparison_path.read_bytes())
    if (
        not source.get("complete")
        or source.get("errors")
        or source.get("process_exit_code") != 0
        or not comparison.get("complete")
        or not comparison.get("baseline_reproduction_passed")
        or comparison["input_sha256"] != sha(source_path)
    ):
        raise ValueError("Complete frozen source and comparison required")
    selected = source["dataset"]["selected_question_ids"]
    if selected != comparison["dataset"]["selected_question_ids"]:
        raise ValueError("Frozen cohort mismatch")
    captured = {row["question_id"]: row for row in source["details"]}
    expected = {row["question_id"]: row for row in comparison["details"]}
    if (
        len(captured) != len(source["details"])
        or len(expected) != len(comparison["details"])
        or set(captured) != set(selected)
        or set(expected) != set(selected)
    ):
        raise ValueError("Complete unique paired coverage required")
    config = PackingConfig.model_validate(
        source["provenance"]["engine_config"]["packing"]
    )
    verified = 0
    for qid in selected:
        ref = captured[qid]["candidate_snapshot"]
        filename = hashlib.sha256(qid.encode()).hexdigest() + ".json"
        if ref["filename"] != filename:
            raise ValueError("Snapshot identity mismatch")
        path = snapshots / filename
        if (
            sha(path) != ref["sha256"]
            or ref["sha256"] != expected[qid]["candidate_snapshot_sha256"]
        ):
            raise ValueError("Snapshot hash mismatch")
        saved = json.loads(path.read_bytes())
        if saved["question_id"] != qid:
            raise ValueError("Snapshot question mismatch")
        candidates = [
            RetrievalCandidate.model_validate(item) for item in saved["candidates"]
        ]
        before = [item.model_dump(mode="json") for item in candidates]
        for budget in comparison["budgets"]:
            for ordering in ["density", "score"]:
                cfg = config.model_copy(
                    update={"token_budget": budget, "multipath_ordering": ordering}
                )
                result = measure(
                    pack_context(candidates, cfg),
                    set(captured[qid]["evidence_source_ids"]),
                    cfg,
                )
                if result != expected[qid]["variants"][ordering][str(budget)]:
                    raise ValueError(
                        f"Public packing differs: {qid}, {budget}, {ordering}"
                    )
                verified += 1
        if before != [item.model_dump(mode="json") for item in candidates]:
            raise ValueError("Public packing mutated source candidates")
    return {
        "passed": True,
        "questions": len(selected),
        "contexts_verified": verified,
        "budgets": comparison["budgets"],
        "default_ordering": PackingConfig().multipath_ordering,
        "source_sha256": sha(source_path),
        "comparison_sha256": sha(comparison_path),
        "package_path": inspect.getfile(pack_context),
        "packing_module_sha256": sha(inspect.getfile(pack_context)),
        "config_module_sha256": sha(inspect.getfile(PackingConfig)),
        "diagnostic_sha256": sha(__file__),
        "limits": "Exact reproduction of frozen experimental contexts and measurements, not new quality evidence. "
        "No provider calls or changes to the confirmation study.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["source", "comparison", "snapshots", "output"]:
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = (
            run(args.source, args.comparison, args.snapshots)
            if args.worker
            else checked_report(
                [
                    sys.executable,
                    "-m",
                    "benchmarks.diagnostics.packing_option",
                    "--worker",
                    "--source",
                    str(args.source),
                    "--comparison",
                    str(args.comparison),
                    "--snapshots",
                    str(args.snapshots),
                ],
                timeout=600,
            )
        )
    except Exception as exc:
        result = {"passed": False, "error_type": type(exc).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
