"""Complete paired answer scores, with independently verified reader/judge chains."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.diagnostics.compare_public_captures import (
    cluster_statistics,
    groups_for,
)
from benchmarks.diagnostics.hindsight_capture import digest, write
from benchmarks.diagnostics.public_context_judging import prepare_judgments
from benchmarks.diagnostics.public_context_judge import verify_result

COMPARISONS = {
    "prme_vs_hindsight": ("memory_b", "memory_a"),
    "prme_vs_empty": ("empty", "memory_a"),
    "hindsight_vs_empty": ("empty", "memory_b"),
}


def summarize(details, groups, readers, *, comparisons=None):
    """Each reader gets its own comparisons, including every registered category."""
    comparisons = COMPARISONS if comparisons is None else comparisons

    def metrics(rows, model):
        values = {}
        for name, (before, after) in comparisons.items():
            pairs = [
                (int(row["answers"][model][before]), int(row["answers"][model][after]))
                for row in rows
            ]
            values[name] = {
                "before_arm": before,
                "after_arm": after,
                "before_correct": sum(a for a, _ in pairs),
                "after_correct": sum(b for _, b in pairs),
                "query_bootstrap": cluster_statistics(
                    pairs, [row["case_id"] for row in rows]
                ),
                "group_bootstrap": cluster_statistics(
                    pairs, [groups[row["case_id"]] for row in rows]
                ),
            }
        return values

    return {
        model: {
            "overall": metrics(details, model),
            "categories": {
                category: metrics(
                    [r for r in details if r["category"] == category], model
                )
                for category in sorted({r["category"] for r in details})
            },
        }
        for model in readers
    }


def analyze(args):
    readers = {
        model: {name: Path(path) for name, path in paths.items()}
        for model, paths in json.loads(args.readers.read_bytes()).items()
    }
    expected_inputs, expected_mapping = prepare_judgments(
        readers, args.references, args.registration
    )
    if (
        json.loads(args.inputs.read_bytes()) != expected_inputs
        or json.loads(args.mapping.read_bytes()) != expected_mapping
    ):
        raise ValueError(
            "Judge inputs or mapping do not reproduce verified reader artifacts"
        )
    registration = json.loads(args.registration.read_bytes())
    capture_raw = args.capture_analysis.read_bytes()
    capture = json.loads(capture_raw)
    capture_completion = json.loads(args.capture_completion.read_bytes())
    if (
        capture_completion["native_exit_code"] != 0
        or capture_completion["report_sha256"] != digest(capture_raw)
        or not capture["complete"]
        or not capture["verification_passed"]
        or capture["inputs_sha256"] != registration["neutral_inputs_sha256"]
        or any(
            capture["source_artifacts"][product + "_plan"] != expected
            for product, expected in registration["capture_plan_sha256"].items()
        )
    ):
        raise ValueError("Capture analysis differs from the registered native study")
    for paths in readers.values():
        prepared = json.loads(paths["prepared_path"].read_bytes())
        if prepared["analysis_sha256"] != digest(capture_raw) or prepared[
            "analysis_completion_sha256"
        ] != digest(args.capture_completion.read_bytes()):
            raise ValueError(
                "Reader contexts are bound to a different capture analysis"
            )
    if registration["scorer_sha256"] != digest(Path(__file__).read_bytes()):
        raise ValueError("Scoring implementation differs from registration")
    if any(
        declared["model_digest"] == registration["judge_declaration"]["model_digest"]
        for declared in registration["reader_declarations"].values()
    ):
        raise ValueError("A separate model must judge the readers")
    plan = json.loads(args.plan.read_bytes())
    if (
        plan["registration_sha256"] != digest(args.registration.read_bytes())
        or plan["mapping_sha256"] != digest(args.mapping.read_bytes())
        or plan["judge"] != registration["judge_declaration"]
        or plan["worker_sha256"] != registration["judge_worker_sha256"]
    ):
        raise ValueError("Judge plan differs from registered study")
    _, result = verify_result(
        args.inputs,
        args.plan,
        args.controls,
        args.calibration,
        args.state,
        args.report,
        args.completion,
    )
    raw = args.neutral_inputs.read_bytes()
    if digest(raw) != registration["neutral_inputs_sha256"]:
        raise ValueError("Neutral cohort differs from registration")
    cases = json.loads(raw)["cases"]
    refs = {
        r["case_id"]: r for r in json.loads(args.references.read_bytes())["references"]
    }
    groups = groups_for(cases, refs)
    verdicts = {row["id"]: row["correct"] for row in result["judgments"]}
    answers = {}
    for mapped in expected_mapping["mapping"]:
        answers.setdefault(mapped["case_id"], {}).setdefault(mapped["reader"], {})[
            mapped["arm"]
        ] = verdicts[mapped["id"]]
    details = [
        {
            "case_id": c["case_id"],
            "question_id": refs[c["case_id"]]["question_id"],
            "category": "abstention"
            if refs[c["case_id"]]["question_id"].endswith("_abs")
            else refs[c["case_id"]]["category"],
            "answers": answers[c["case_id"]],
        }
        for c in cases
    ]
    return {
        "complete": True,
        "verified": True,
        "questions": len(details),
        "history_or_question_groups": len(set(groups.values())),
        "readers": summarize(details, groups, sorted(readers)),
        "details": details,
        "scorer_sha256": digest(Path(__file__).read_bytes()),
        "artifacts": {
            name: digest(getattr(args, name).read_bytes())
            for name in (
                "readers",
                "references",
                "registration",
                "neutral_inputs",
                "inputs",
                "mapping",
                "plan",
                "controls",
                "calibration",
                "state",
                "report",
                "completion",
                "capture_analysis",
                "capture_completion",
            )
        },
        "limits": [
            "Development cohort; not an independent holdout or superiority gate.",
            "Custom calibrated local judge, not the official benchmark judge.",
            "Gemma reader and judge share a model family; calibration does not remove judge bias.",
            "Raw-memory profiles and adapter-rendered Hindsight context; full extraction/consolidation not evaluated.",
            "Groups cover identical histories and abstention pairs, not all partial history dependence.",
            "Reader families remain separate; no pooled headline score or default-promotion decision.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "readers",
        "references",
        "registration",
        "neutral-inputs",
        "inputs",
        "mapping",
        "plan",
        "controls",
        "calibration",
        "state",
        "report",
        "completion",
        "capture-analysis",
        "capture-completion",
        "output",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite a prior score report")
    write(args.output, analyze(args))


if __name__ == "__main__":
    main()
