"""Evaluate task-specific context guidance with a fixed local reader.

This development study selects every previously examined question for which
``build_context_guidance`` emits text. It changes only the balanced 4K context:
the guidance is token-counted inside the same bundle budget. Baseline outcomes
come from the complete balanced study; the guided arm receives no outcome retry.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from benchmarks.diagnostics import balanced_packing_answer as base
from benchmarks.diagnostics import context_ablation_answer as prior
from benchmarks.diagnostics import reader_judge
from benchmarks.diagnostics.hindsight_capture import digest, write
from benchmarks.diagnostics.product_packing import measure
from prme.retrieval.config import PackingConfig
from prme.retrieval.context_formatter import build_context_guidance
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context


EXPECTED_CASES = 46


def source_paths(root: Path) -> dict[str, Path]:
    return prior.source_paths(root)


def source_identity(paths: dict[str, Path]) -> dict[str, str]:
    return prior.source_identity(paths)


def _guidance_kind(guidance: str) -> str:
    marker = guidance.split(" TASK:", maxsplit=1)[0].splitlines()[-1]
    if marker not in {"TEMPORAL", "CURRENT-STATE", "PERSONALIZATION"}:
        raise ValueError("Unknown context-guidance kind")
    return marker.lower().replace("-", "_")


def prepare(root: Path) -> dict:
    """Reproduce balanced contexts and add guidance without outcome access."""
    paths = source_paths(root)
    baseline = base.prepare(root)
    baseline_by_id = {row["case_id"]: row for row in baseline["rows"]}
    cases = {
        row["case_id"]: row
        for row in json.loads(paths["inputs"].read_bytes())["cases"]
    }
    references = {
        row["question_id"]: row
        for row in json.loads(paths["references"].read_bytes())["references"]
    }
    composition = json.loads(paths["composition"].read_bytes())
    rows = []
    for result in composition["details"]:
        reference = references[result["question_id"]]
        case = cases[reference["case_id"]]
        reference_time = datetime.fromisoformat(case["question_date"])
        guidance = build_context_guidance(
            case["question"], reference_time=reference_time
        )
        if guidance is None:
            continue

        snapshot_ref = result["snapshot"]
        snapshot_raw = (paths["snapshots"] / snapshot_ref["filename"]).read_bytes()
        if digest(snapshot_raw) != snapshot_ref["sha256"]:
            raise ValueError("Candidate snapshot checksum mismatch")
        snapshot = json.loads(snapshot_raw)
        candidates = [
            RetrievalCandidate.model_validate(candidate)
            for candidate in snapshot["candidates"]["parser"]
        ]
        config = PackingConfig.model_validate(
            snapshot["arms"]["parser:density:4096"]["packing"]
        ).model_copy(update={"multipath_ordering": "balanced"})
        control = pack_context(candidates, config)
        expected = baseline_by_id[reference["case_id"]]["contexts"]["balanced"]
        if (
            control.render() != expected["context"]
            or digest(control.render().encode()) != expected["sha256"]
            or control.tokens_used != expected["tokens"]
        ):
            raise ValueError("Balanced baseline does not reproduce")

        guided = pack_context(candidates, config, coverage_notice=guidance)
        gold = set(reference["evidence_source_ids"])
        control_measure = measure(control, gold, config)
        guided_measure = measure(guided, gold, config)
        if (
            control_measure["evidence_recall"] != guided_measure["evidence_recall"]
            or control_measure["all_evidence_retained"]
            != guided_measure["all_evidence_retained"]
        ):
            raise ValueError("Guidance changed annotated-source retention")
        context = guided.render()
        rows.append(
            {
                "case_id": result["question_id"],
                "question": case["question"],
                "question_date": case["question_date"],
                "category": (
                    "abstention"
                    if result["question_id"].endswith("_abs")
                    else reference["category"]
                ),
                "guidance_kind": _guidance_kind(guidance),
                "guidance": guidance,
                "baseline_context_sha256": expected["sha256"],
                "context": context,
                "context_sha256": digest(context.encode()),
                "tokens": guided.tokens_used,
                "source_retention": {
                    "baseline": control_measure["evidence_recall"],
                    "guided": guided_measure["evidence_recall"],
                },
            }
        )
    rows.sort(key=lambda row: row["case_id"])
    if (
        len(rows) != EXPECTED_CASES
        or len({row["case_id"] for row in rows}) != EXPECTED_CASES
    ):
        raise ValueError(f"Expected exactly {EXPECTED_CASES} guided questions")
    return {
        "kind": "balanced-context-guidance-development",
        "schema_version": 1,
        "selection": "all development questions receiving deterministic guidance",
        "source_identity": source_identity(paths),
        "rows": rows,
    }


def register(root: Path, directory: Path, registration: Path, base_url: str) -> None:
    if directory.exists() or registration.exists():
        raise ValueError("Fresh registration and study directory required")
    prepared = prepare(root)
    paths = source_paths(root)
    baseline_registration = json.loads(paths["prior_registration"].read_bytes())
    reader = base.reader_declaration(base_url)
    judge = reader_judge.declaration(
        base.base.JUDGE_MODEL,
        base_url,
        baseline_registration["judge"]["controls_sha256"],
    )
    if reader != baseline_registration["reader"] or judge != baseline_registration["judge"]:
        raise ValueError("Reader or judge differs from the complete baseline study")
    reader_judge.validate_calibration(
        json.loads(paths["controls"].read_bytes()),
        json.loads(paths["calibration"].read_bytes()),
        judge,
    )
    directory.mkdir(parents=True)
    prepared_path = directory / "prepared.json"
    write(prepared_path, prepared)
    write(
        registration,
        {
            "kind": "balanced-context-guidance-development",
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "implementation_sha256": digest(Path(__file__).read_bytes()),
            "guidance_implementation_sha256": digest(
                Path(build_context_guidance.__code__.co_filename).read_bytes()
            ),
            "reader_implementation_sha256": digest(Path(base.__file__).read_bytes()),
            "prepared_sha256": digest(prepared_path.read_bytes()),
            "source_identity": prepared["source_identity"],
            "case_ids": [row["case_id"] for row in prepared["rows"]],
            "guidance_kinds": dict(
                sorted(Counter(row["guidance_kind"] for row in prepared["rows"]).items())
            ),
            "reader": reader,
            "judge": judge,
            "gate": {
                "overall_gains_exceed_losses": True,
                "temporal_gains_exceed_losses": True,
                "no_personalization_accuracy_loss": True,
                "no_current_state_accuracy_loss": True,
                "unchanged_annotated_source_recall": True,
            },
            "limits": [
                "All questions and baseline outcomes were examined previously; this is development evidence.",
                "Selection is deterministic and outcome-blind; every question receiving guidance is retained.",
                "Guidance is counted within the same balanced 4K memory budget.",
                "Annotated-source recall must remain unchanged before any reader call.",
                "One local reader and one calibrated local judge are used, with no outcome retries.",
                "A passing development gate requires confirmation on the separate 381-question partition.",
            ],
        },
    )


def verify_registration(
    root: Path, directory: Path, registration: Path, base_url: str
) -> tuple[dict, dict]:
    declared = json.loads(registration.read_bytes())
    prepared_path = directory / "prepared.json"
    prepared = json.loads(prepared_path.read_bytes())
    paths = source_paths(root)
    if (
        declared["implementation_sha256"] != digest(Path(__file__).read_bytes())
        or declared["guidance_implementation_sha256"]
        != digest(Path(build_context_guidance.__code__.co_filename).read_bytes())
        or declared["reader_implementation_sha256"] != digest(Path(base.__file__).read_bytes())
        or declared["prepared_sha256"] != digest(prepared_path.read_bytes())
        or declared["source_identity"] != source_identity(paths)
        or prepared != prepare(root)
        or declared["case_ids"] != [row["case_id"] for row in prepared["rows"]]
        or declared["reader"] != base.reader_declaration(base_url)
        or declared["judge"]
        != reader_judge.declaration(
            base.base.JUDGE_MODEL,
            base_url,
            declared["judge"]["controls_sha256"],
        )
    ):
        raise ValueError("Registration, inputs, implementation, or models changed")
    return declared, prepared


def _summary(rows: list[dict]) -> dict:
    gains = sum(not row["baseline_correct"] and row["guided_correct"] for row in rows)
    losses = sum(row["baseline_correct"] and not row["guided_correct"] for row in rows)
    return {
        "questions": len(rows),
        "baseline_correct": sum(row["baseline_correct"] for row in rows),
        "guided_correct": sum(row["guided_correct"] for row in rows),
        "gains": gains,
        "losses": losses,
        "ties": len(rows) - gains - losses,
    }


def summarize(
    prepared: dict,
    reader: dict,
    judge: dict,
    baseline: dict[str, bool],
    registration: dict,
) -> dict:
    verdicts = {row["id"]: row["correct"] for row in judge["judgments"]}
    details = [
        {
            "case_id": row["case_id"],
            "category": row["category"],
            "guidance_kind": row["guidance_kind"],
            "baseline_correct": baseline[row["case_id"]],
            "guided_correct": verdicts[row["case_id"]],
        }
        for row in prepared["rows"]
    ]
    overall = _summary(details)
    by_kind = {
        kind: _summary([row for row in details if row["guidance_kind"] == kind])
        for kind in sorted({row["guidance_kind"] for row in details})
    }
    categories = {
        category: _summary([row for row in details if row["category"] == category])
        for category in sorted({row["category"] for row in details})
    }
    observed_gate = {
        "overall_gains_exceed_losses": overall["gains"] > overall["losses"],
        "temporal_gains_exceed_losses": (
            by_kind["temporal"]["gains"] > by_kind["temporal"]["losses"]
        ),
        "no_personalization_accuracy_loss": (
            by_kind["personalization"]["guided_correct"]
            >= by_kind["personalization"]["baseline_correct"]
        ),
        "no_current_state_accuracy_loss": (
            by_kind["current_state"]["guided_correct"]
            >= by_kind["current_state"]["baseline_correct"]
        ),
        "unchanged_annotated_source_recall": all(
            row["source_retention"]["baseline"] == row["source_retention"]["guided"]
            for row in prepared["rows"]
        ),
    }
    if set(observed_gate) != set(registration["gate"]):
        raise ValueError("Observed gate differs from registration")
    return {
        "complete": True,
        "quality_gate_passed": all(observed_gate.values()),
        "gate": observed_gate,
        "overall": overall,
        "guidance_kinds": by_kind,
        "categories": categories,
        "details": details,
        "reader_state_sha256": reader["state_sha256"],
        "judge_unique_calls": judge["unique_calls"],
        "limits": registration["limits"],
    }


def run(root: Path, directory: Path, registration_path: Path, base_url: str) -> dict:
    registration, prepared = verify_registration(
        root, directory, registration_path, base_url
    )
    for name in ("reader.json", "judge.json", "results.json"):
        if (directory / name).exists():
            raise ValueError("Fresh study outcomes required")
    reader = prior.run_reader(
        prepared,
        registration["reader"],
        directory / "reader-state.json",
        base_url,
    )
    write(directory / "reader.json", reader)
    references = {
        row["question_id"]: row
        for row in json.loads(source_paths(root)["references"].read_bytes())["references"]
    }
    answers = {row["case_id"]: row["answer"] for row in reader["rows"]}
    judge_cases = [
        {
            "id": row["case_id"],
            "question": row["question"],
            "category": row["category"],
            "reference": str(references[row["case_id"]]["answer"]),
            "hypothesis": answers[row["case_id"]],
        }
        for row in prepared["rows"]
    ]
    judge_cases.sort(key=lambda row: row["id"])
    write(directory / "judge-inputs.json", {"cases": judge_cases})
    judge = reader_judge.run_cases(
        judge_cases,
        registration["judge"],
        directory / "judge-state.json",
        base_url,
    )
    if judge["prior_failed_attempts"]:
        raise ValueError("Judge study contains failed calls")
    write(directory / "judge.json", judge)
    result = summarize(
        prepared,
        reader,
        judge,
        prior.baseline_outcomes(source_paths(root), registration),
        registration,
    )
    result.update(
        registration_sha256=digest(registration_path.read_bytes()),
        reader_report_sha256=digest((directory / "reader.json").read_bytes()),
        judge_report_sha256=digest((directory / "judge.json").read_bytes()),
    )
    write(directory / "results.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "run"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    root = args.root.resolve()
    directory = args.directory.resolve()
    registration = args.registration.resolve()
    if args.mode == "register":
        register(root, directory, registration, args.base_url)
    else:
        result = run(root, directory, registration, args.base_url)
        print(
            json.dumps(
                {
                    "complete": result["complete"],
                    "quality_gate_passed": result["quality_gate_passed"],
                    "overall": result["overall"],
                    "guidance_kinds": result["guidance_kinds"],
                }
            )
        )


if __name__ == "__main__":
    main()
