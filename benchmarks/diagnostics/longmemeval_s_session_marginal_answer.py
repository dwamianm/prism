"""Paired answer diagnostic for the lossless session-marginal source arm.

The cohort is fixed to every LongMemEval-S context changed by the preregistered
``free_slots=8, decay=0.90`` source arm. Reader inputs contain no references;
references become available only after the complete paired reader run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
from typing import Any

from benchmarks.diagnostics import paired_context_eval as paired
from benchmarks.diagnostics import reader_judge as judge_runtime
from benchmarks.diagnostics.longmemeval_s_compact import (
    _canonical,
    _case_checksum,
    _git_revision,
    _sha256,
    _sha256_file,
    _source_changes_since,
    _write,
)
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    _load_dataset,
)
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT


ARMS = ("control", "marginal")
SOURCE_ARM = "marginal_f8_d090"
SOURCE_CONFIG = {"free_slots": 8, "decay": 0.9}
READER_OPTIONS: dict[str, Any] = {
    "temperature": 0,
    "seed": 42,
    "num_ctx": 65536,
    "num_predict": 1024,
}


def _validate_source_result(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text())
    recorded = result.get("result_sha256")
    computed = _sha256(
        _canonical(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
    )
    control = result.get("arms", {}).get("control", {})
    candidate = result.get("arms", {}).get(SOURCE_ARM, {})
    comparison = candidate.get("comparison", {})
    gate = candidate.get("gate", {})
    if (
        result.get("kind") != "longmemeval-s-session-marginal-result"
        or result.get("cohort") != "full-development"
        or result.get("questions") != 500
        or result.get("decision") != "reject_grid"
        or result.get("selected_candidate") is not None
        or recorded != computed
        or candidate.get("config") != SOURCE_CONFIG
        or comparison.get("changed_contexts") != 31
        or comparison.get("turn_recall_wins") != 1
        or comparison.get("turn_recall_losses") != 0
        or comparison.get("session_recall_losses") != 0
        or gate.get("category_mean_turn_recall_losses") != 0
        or gate.get("passed") is not False
        or candidate.get("summary", {}).get("mean_turn_recall", 0)
        <= control.get("summary", {}).get("mean_turn_recall", 1)
    ):
        raise ValueError("session-marginal source result is not the fixed Pareto arm")
    return result


def _prepare(
    *,
    dataset_path: Path,
    cases_root: Path,
    source_result_path: Path,
    source_registration_path: Path,
    prepared_path: Path,
    references_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if prepared_path.exists() or references_path.exists():
        raise ValueError("fresh prepared and reference outputs are required")
    if _sha256_file(dataset_path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    source_result = _validate_source_result(source_result_path)
    source_registration = json.loads(source_registration_path.read_text())
    if source_registration.get(
        "kind"
    ) != "longmemeval-s-session-marginal-registration" or source_result.get(
        "registration_sha256"
    ) != _sha256_file(source_registration_path):
        raise ValueError("session-marginal registration identity differs")

    dataset = _load_dataset(dataset_path)
    by_id = {case["question_id"]: case for case in dataset}
    question_ids = source_registration.get("dataset", {}).get("question_ids", [])
    if (
        len(dataset) != 500
        or len(by_id) != 500
        or len(question_ids) != 500
        or len(set(question_ids)) != 500
        or set(question_ids) != set(by_id)
    ):
        raise ValueError("LongMemEval-S question identity differs")

    neutral_rows: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    full_manifest: list[list[str]] = []
    changed_manifest: list[list[str]] = []
    source_wins = source_losses = 0
    for question_id in question_ids:
        saved = json.loads((cases_root / f"{question_id}.json").read_text())
        if (
            saved.get("kind") != "longmemeval-s-session-marginal-case"
            or saved.get("question_id") != question_id
            or saved.get("registration_sha256") != source_result["registration_sha256"]
            or saved.get("case_sha256") != _case_checksum(saved)
            or set(saved.get("arms", {})) != set(source_result.get("arms", {}))
        ):
            raise ValueError(f"session-marginal case differs for {question_id}")
        full_manifest.append([question_id, saved["case_sha256"]])
        control = saved["arms"]["control"]
        candidate = saved["arms"][SOURCE_ARM]
        for arm in (control, candidate):
            context = arm.get("context")
            if (
                not isinstance(context, str)
                or arm.get("context_sha256") != _sha256(context.encode())
                or not isinstance(arm.get("tokens"), int)
                or arm["tokens"] > 3996
            ):
                raise ValueError(f"context integrity differs for {question_id}")
        control_evidence = control["evidence"]
        candidate_evidence = candidate["evidence"]
        if control_evidence.get("applicable"):
            delta = candidate_evidence["turn_recall"] - control_evidence["turn_recall"]
            source_wins += delta > 0
            source_losses += delta < 0
        if control["context_sha256"] == candidate["context_sha256"]:
            continue
        case = by_id[question_id]
        changed_manifest.append([question_id, saved["case_sha256"]])
        neutral_rows.append(
            {
                "question_id": question_id,
                "question": case["question"],
                "question_date": case["question_date"],
                "contexts": {
                    "control": {
                        "context": control["context"],
                        "sha256": control["context_sha256"],
                        "tokens": control["tokens"],
                    },
                    "marginal": {
                        "context": candidate["context"],
                        "sha256": candidate["context_sha256"],
                        "tokens": candidate["tokens"],
                    },
                },
            }
        )
        references.append(
            {
                key: case[key]
                for key in (
                    "question_id",
                    "question",
                    "question_date",
                    "question_type",
                    "answer",
                )
            }
        )

    if (
        len(neutral_rows) != 31
        or len(references) != 31
        or source_wins != 1
        or source_losses != 0
        or _sha256(_canonical(full_manifest)) != source_result["case_manifest_sha256"]
    ):
        raise ValueError("changed-context cohort differs from the fixed source result")
    prepared = {
        "schema_version": 1,
        "kind": "longmemeval-s-session-marginal-answer-inputs",
        "dataset_sha256": DATASET_SHA256,
        "cohort": "all-changed-contexts",
        "source_arm": SOURCE_ARM,
        "source_config": SOURCE_CONFIG,
        "source_result_sha256": _sha256_file(source_result_path),
        "source_registration_sha256": _sha256_file(source_registration_path),
        "questions": len(neutral_rows),
        "question_ids": [row["question_id"] for row in neutral_rows],
        "question_ids_sha256": _sha256(
            _canonical([row["question_id"] for row in neutral_rows])
        ),
        "changed_case_manifest_sha256": _sha256(_canonical(changed_manifest)),
        "generation_system_prompt": GENERATION_SYSTEM_PROMPT,
        "rows": neutral_rows,
    }
    if any("answer" in row for row in prepared["rows"]):
        raise ValueError("reader inputs contain reference answers")
    _write(prepared_path, prepared)
    _write(references_path, references)
    return prepared, references


def _validate_calibration(
    *, controls_path: Path, declaration_path: Path, calibration_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    controls = json.loads(controls_path.read_text())
    declaration = json.loads(declaration_path.read_text())
    calibration = json.loads(calibration_path.read_text())
    judge_runtime.validate_calibration(controls, calibration, declaration)
    return controls, declaration, calibration


def _protocol() -> dict[str, Any]:
    return {
        "arms": list(ARMS),
        "cohort": "all 31 contexts changed by the fixed mild source arm",
        "cohort_selected_without_answer_outcomes": True,
        "arm_order": "counterbalanced by question-ID hash parity",
        "reader_labels_hidden": True,
        "references_available_only_after_complete_reader": True,
        "one_generation_per distinct prompt": True,
        "no_selective_retries": True,
        "unchanged_contexts_excluded": (
            "their arm prompts are byte-identical and cannot identify the effect"
        ),
    }


def _evaluation() -> dict[str, Any]:
    return {
        "complete_reader_execution": True,
        "complete_judge_execution": True,
        "reader_failed_attempts": 0,
        "judge_failed_attempts": 0,
        "candidate_correct": ">= control_correct",
        "paired_wins": "> paired_losses",
        "category_regressions": 0,
        "decision": (
            "A pass supports retaining the private hook for separate-workload "
            "confirmation; it does not expose a public option or change defaults."
        ),
    }


def create_registration(
    *,
    dataset_path: Path,
    cases_root: Path,
    source_result_path: Path,
    source_registration_path: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    reader_model: str,
    base_url: str,
    project_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("registration output already exists")
    prepared, references = _prepare(
        dataset_path=dataset_path,
        cases_root=cases_root,
        source_result_path=source_result_path,
        source_registration_path=source_registration_path,
        prepared_path=prepared_path,
        references_path=references_path,
    )
    controls, declaration, calibration = _validate_calibration(
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
    )
    reader = paired.model_identity(
        reader_model, base_url, dict(READER_OPTIONS), GENERATION_SYSTEM_PROMPT
    )
    registration: dict[str, Any] = {
        "schema_version": 1,
        "kind": "longmemeval-s-session-marginal-answer-registration",
        "status": "registered_before_reader_or_judge_calls",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Paired answer diagnostic on all 31 contexts changed by a source-level "
            "Pareto arm; not an official LongMemEval score, a full-cohort accuracy "
            "estimate, independent confirmation, or a product promotion."
        ),
        "source": {
            "prme_revision": _git_revision(project_root),
            "runner_sha256": _sha256_file(Path(__file__).resolve()),
            "paired_runtime_sha256": _sha256_file(Path(paired.__file__).resolve()),
            "source_result_sha256": _sha256_file(source_result_path),
            "source_registration_sha256": _sha256_file(source_registration_path),
            "prepared_sha256": _sha256_file(prepared_path),
            "references_sha256": _sha256_file(references_path),
            "controls_sha256": _sha256_file(controls_path),
            "judge_declaration_sha256": _sha256_file(declaration_path),
            "judge_calibration_sha256": _sha256_file(calibration_path),
        },
        "dataset": {
            "name": "LongMemEval-S cleaned",
            "sha256": DATASET_SHA256,
            "cohort": prepared["cohort"],
            "questions": prepared["questions"],
            "question_ids": prepared["question_ids"],
            "question_ids_sha256": prepared["question_ids_sha256"],
            "changed_case_manifest_sha256": prepared["changed_case_manifest_sha256"],
        },
        "models": {"reader": reader, "judge": declaration},
        "calibration": {
            "metrics": calibration["metrics"],
            "cases": len(controls["cases"]),
        },
        "protocol": _protocol(),
        "evaluation": _evaluation(),
        "limitations": [
            "The cohort and source labels are development evidence from a previously inspected benchmark.",
            "Conditioning on changed contexts estimates the intervention effect, not full-cohort accuracy.",
            "Hosted Ollama aliases pin local manifests, not immutable remote weights.",
            "One reader generation per distinct prompt does not estimate provider variance.",
            "The calibrated custom judge is not the official LongMemEval judge.",
        ],
    }
    if len(references) != registration["dataset"]["questions"]:
        raise ValueError("reference coverage differs")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any],
    *,
    source_result_path: Path,
    source_registration_path: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    base_url: str,
    project_root: Path,
) -> None:
    source = registration.get("source", {})
    revision = source.get("prme_revision")
    prepared = json.loads(prepared_path.read_text())
    references = json.loads(references_path.read_text())
    controls, declaration, calibration = _validate_calibration(
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
    )
    reader = registration.get("models", {}).get("reader", {})
    runtime_reader = paired.model_identity(
        str(reader.get("model", "")),
        base_url,
        dict(READER_OPTIONS),
        GENERATION_SYSTEM_PROMPT,
    )
    expected_source = {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "paired_runtime_sha256": _sha256_file(Path(paired.__file__).resolve()),
        "source_result_sha256": _sha256_file(source_result_path),
        "source_registration_sha256": _sha256_file(source_registration_path),
        "prepared_sha256": _sha256_file(prepared_path),
        "references_sha256": _sha256_file(references_path),
        "controls_sha256": _sha256_file(controls_path),
        "judge_declaration_sha256": _sha256_file(declaration_path),
        "judge_calibration_sha256": _sha256_file(calibration_path),
    }
    expected_dataset = {
        "name": "LongMemEval-S cleaned",
        "sha256": DATASET_SHA256,
        "cohort": "all-changed-contexts",
        "questions": prepared.get("questions"),
        "question_ids": prepared.get("question_ids"),
        "question_ids_sha256": prepared.get("question_ids_sha256"),
        "changed_case_manifest_sha256": prepared.get("changed_case_manifest_sha256"),
    }
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "longmemeval-s-session-marginal-answer-registration"
        or registration.get("status") != "registered_before_reader_or_judge_calls"
        or not isinstance(revision, str)
        or source != expected_source
        or registration.get("dataset") != expected_dataset
        or registration.get("models")
        != {"reader": runtime_reader, "judge": declaration}
        or registration.get("calibration")
        != {"metrics": calibration["metrics"], "cases": len(controls["cases"])}
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _evaluation()
        or prepared.get("kind") != "longmemeval-s-session-marginal-answer-inputs"
        or prepared.get("source_arm") != SOURCE_ARM
        or prepared.get("source_config") != SOURCE_CONFIG
        or len(prepared.get("rows", [])) != 31
        or any("answer" in row for row in prepared.get("rows", []))
        or len(references) != 31
        or _sha256_file(source_result_path) != prepared.get("source_result_sha256")
        or _sha256_file(source_registration_path)
        != prepared.get("source_registration_sha256")
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered session-marginal answer inputs differ")
    _validate_source_result(source_result_path)


def _gate(metrics: dict[str, Any]) -> dict[str, Any]:
    overall = metrics["overall"]
    category_regressions = sum(
        row["candidate_correct"] < row["control_correct"]
        for row in metrics["categories"].values()
    )
    gate = {
        "complete_reader_execution": metrics["complete_reader_execution"],
        "complete_judge_execution": metrics["complete_judge_execution"],
        "reader_failed_attempts": metrics["reader_failed_attempts"],
        "judge_failed_attempts": metrics["judge_failed_attempts"],
        "candidate_noninferior": (
            overall["candidate_correct"] >= overall["control_correct"]
        ),
        "paired_wins_exceed_losses": (
            overall["paired_wins"] > overall["paired_losses"]
        ),
        "category_regressions": category_regressions,
    }
    gate["passed"] = (
        gate["complete_reader_execution"]
        and gate["complete_judge_execution"]
        and gate["reader_failed_attempts"] == 0
        and gate["judge_failed_attempts"] == 0
        and gate["candidate_noninferior"]
        and gate["paired_wins_exceed_losses"]
        and gate["category_regressions"] == 0
    )
    return gate


def evaluate(
    *,
    registration_path: Path,
    source_result_path: Path,
    source_registration_path: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    base_url: str,
    project_root: Path,
    output_dir: Path,
    summary_path: Path,
) -> dict[str, Any]:
    if summary_path.exists():
        raise ValueError("summary output already exists")
    registration = json.loads(registration_path.read_text())
    _validate_registration(
        registration,
        source_result_path=source_result_path,
        source_registration_path=source_registration_path,
        prepared_path=prepared_path,
        references_path=references_path,
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
        base_url=base_url,
        project_root=project_root,
    )
    prepared_raw = prepared_path.read_bytes()
    prepared = json.loads(prepared_raw)
    references = json.loads(references_path.read_text())
    output_dir.mkdir(parents=True, exist_ok=True)
    registration_sha256 = _sha256_file(registration_path)
    reader = registration["models"]["reader"]
    predictions = paired.run_reader(
        prepared,
        ARMS,
        output_dir / "reader-state.json",
        registration_sha256=registration_sha256,
        prepared_sha256=_sha256(prepared_raw),
        model=reader["model"],
        model_digest=reader["model_digest"],
        options=dict(READER_OPTIONS),
        system_prompt=GENERATION_SYSTEM_PROMPT,
        base_url=base_url,
    )
    _write(output_dir / "reader.json", predictions)
    if not predictions["complete"] or predictions["failed_attempts"]:
        raise ValueError("reader execution is incomplete")

    cases = paired.judge_cases(predictions, references, ARMS)
    declaration = json.loads(declaration_path.read_text())
    judgments = judge_runtime.run_cases(
        cases, declaration, output_dir / "judge-state.json", base_url
    )
    _write(output_dir / "judge.json", judgments)
    metrics = paired.paired_metrics(
        cases,
        judgments,
        control_arm="control",
        candidate_arm="marginal",
    )
    metrics["gate"] = _gate(metrics)
    execution = {
        "registration_sha256": registration_sha256,
        "reader": predictions,
        "judge": judgments,
        "metrics": metrics,
    }
    _write(output_dir / "execution.json", execution)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-session-marginal-answer-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": registration_sha256,
        "cohort": registration["dataset"]["cohort"],
        "questions": registration["dataset"]["questions"],
        "source_arm": SOURCE_ARM,
        "models": registration["models"],
        "metrics": metrics,
        "decision": (
            "advance_to_separate_workload_confirmation"
            if metrics["gate"]["passed"]
            else "reject_answer_effect"
        ),
        "execution_sha256": _sha256_file(output_dir / "execution.json"),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }
    result["result_sha256"] = _sha256(_canonical(result))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    run = commands.add_parser("evaluate")
    for command in (register, run):
        command.add_argument("--source-result", type=Path, required=True)
        command.add_argument("--source-registration", type=Path, required=True)
        command.add_argument("--prepared", type=Path, required=True)
        command.add_argument("--references", type=Path, required=True)
        command.add_argument("--controls", type=Path, required=True)
        command.add_argument("--judge-declaration", type=Path, required=True)
        command.add_argument("--judge-calibration", type=Path, required=True)
        command.add_argument("--base-url", default="http://127.0.0.1:11434")
        command.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--dataset", type=Path, required=True)
    register.add_argument("--cases-root", type=Path, required=True)
    register.add_argument("--reader-model", required=True)
    register.add_argument("--output", type=Path, required=True)
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--summary", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    shared = {
        "source_result_path": args.source_result.resolve(),
        "source_registration_path": args.source_registration.resolve(),
        "prepared_path": args.prepared.resolve(),
        "references_path": args.references.resolve(),
        "controls_path": args.controls.resolve(),
        "declaration_path": args.judge_declaration.resolve(),
        "calibration_path": args.judge_calibration.resolve(),
        "base_url": args.base_url,
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        value = create_registration(
            dataset_path=args.dataset.resolve(),
            cases_root=args.cases_root.resolve(),
            reader_model=args.reader_model,
            output_path=args.output.resolve(),
            **shared,
        )
        print(
            json.dumps(
                {
                    "dataset": value["dataset"],
                    "models": value["models"],
                    "evaluation": value["evaluation"],
                },
                indent=2,
            )
        )
        return
    value = evaluate(
        registration_path=args.registration.resolve(),
        output_dir=args.output_dir.resolve(),
        summary_path=args.summary.resolve(),
        **shared,
    )
    print(
        json.dumps(
            {
                "metrics": value["metrics"],
                "decision": value["decision"],
                "result_sha256": value["result_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
