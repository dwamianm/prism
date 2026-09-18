"""Registered paired reader trial for frozen monotonic compact contexts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
from typing import Any

from benchmarks.diagnostics import packing_reader as reader_runtime
from benchmarks.diagnostics import reader_judge as judge_runtime
from benchmarks.diagnostics.longmemeval_s_compact import (
    _canonical,
    _case_checksum,
    _sha256,
    _sha256_file,
    _source_changes_since,
    _write,
)
from benchmarks.evidence import select_questions
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    _load_dataset,
)
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT


ARMS = ("control", "monotonic")
SPLIT_SEED = "prme-evidence-v1"
READER_OPTIONS = {
    "temperature": 0,
    "seed": 42,
    "num_ctx": 65536,
    "num_predict": 1024,
}


def _git_revision(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _result_identity(path: Path) -> str:
    result = json.loads(path.read_text())
    recorded = result.get("result_sha256")
    computed = _sha256(
        _canonical(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
    )
    if (
        result.get("kind") != "longmemeval-s-monotonic-compact-result"
        or result.get("cohort") != "full-development"
        or result.get("gate", {}).get("passed") is not True
        or recorded != computed
    ):
        raise ValueError("monotonic source result is not an intact passing result")
    return _sha256_file(path)


def _model_identity(model: str, base_url: str) -> dict[str, Any]:
    return {
        "provider": "ollama",
        "model": model,
        "model_digest": reader_runtime.model_digest(base_url, model),
        "ollama_version": reader_runtime.request(base_url, "/api/version")["version"],
        "accepted_response_models": sorted(
            {
                model,
                model.removesuffix(":cloud")
                if model.endswith(":cloud")
                else model.removesuffix("-cloud")
                if model.endswith("-cloud")
                else model,
            }
        ),
    }


def _prepare(
    *,
    dataset_path: Path,
    cases_root: Path,
    split: str,
    prepared_path: Path,
    references_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if _sha256_file(dataset_path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    cases = _load_dataset(dataset_path)
    selected = select_questions(cases, split=split, seed=SPLIT_SEED)
    neutral_rows = []
    references = []
    case_manifest = []
    for case in selected:
        question_id = case["question_id"]
        path = cases_root / f"{question_id}.json"
        saved = json.loads(path.read_text())
        if (
            saved.get("question_id") != question_id
            or saved.get("kind") != "longmemeval-s-monotonic-compact-case"
            or saved.get("case_sha256") != _case_checksum(saved)
            or saved.get("control_record_losses")
            or saved.get("control_guidance_lost")
            or saved.get("arms", {}).get("monotonic", {}).get("context_format")
            != "compact"
        ):
            raise ValueError(f"monotonic saved case differs for {question_id}")
        contexts = {}
        for arm in ARMS:
            source = saved["arms"][arm]
            context = source["context"]
            if source["context_sha256"] != _sha256(context.encode()):
                raise ValueError(f"context checksum differs for {question_id}:{arm}")
            contexts[arm] = {
                "context": context,
                "sha256": source["context_sha256"],
                "tokens": source["tokens"],
            }
        neutral_rows.append(
            {
                "question_id": question_id,
                "question": case["question"],
                "question_date": case["question_date"],
                "contexts": contexts,
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
        case_manifest.append([question_id, saved["case_sha256"]])
    prepared = {
        "schema_version": 1,
        "kind": "longmemeval-s-monotonic-answer-inputs",
        "dataset_sha256": DATASET_SHA256,
        "split": split,
        "split_seed": SPLIT_SEED,
        "question_ids": [row["question_id"] for row in neutral_rows],
        "case_manifest_sha256": _sha256(_canonical(case_manifest)),
        "generation_system_prompt": GENERATION_SYSTEM_PROMPT,
        "rows": neutral_rows,
    }
    if any("answer" in row for row in prepared["rows"]):
        raise ValueError("reader inputs contain reference answers")
    _write(prepared_path, prepared)
    _write(references_path, references)
    return prepared, references


def _validate_calibration(
    *,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    controls = json.loads(controls_path.read_text())
    declaration = json.loads(declaration_path.read_text())
    calibration = json.loads(calibration_path.read_text())
    judge_runtime.validate_calibration(controls, calibration, declaration)
    return controls, declaration, calibration


def _protocol() -> dict[str, Any]:
    return {
        "arms": list(ARMS),
        "arm_order": "counterbalanced by question-ID hash parity",
        "reader_temperature": 0,
        "reader_seed": 42,
        "reader_thinking": False,
        "reader_labels_hidden": True,
        "judge_labels_available_only_after_all_reader_answers_complete": True,
        "judge_calibration_required": True,
        "one_generation_per_arm": True,
        "no_selective_retries": True,
    }


def _gates() -> dict[str, Any]:
    return {
        "complete_reader_execution": True,
        "complete_judge_execution": True,
        "reader_failed_attempts": 0,
        "judge_failed_attempts": 0,
        "monotonic_correct": ">= control_correct",
        "paired_wins": ">= paired_losses",
        "category_regressions": 0,
        "decision": (
            "A pass advances the policy to a separately frozen confirmation. "
            "It does not change the product default."
        ),
    }


def create_registration(
    *,
    dataset_path: Path,
    cases_root: Path,
    monotonic_result: Path,
    split: str,
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
    for path in (prepared_path, references_path, output_path):
        if path.exists():
            raise ValueError(f"fresh registration output required: {path}")
    prepared, references = _prepare(
        dataset_path=dataset_path,
        cases_root=cases_root,
        split=split,
        prepared_path=prepared_path,
        references_path=references_path,
    )
    controls, declaration, calibration = _validate_calibration(
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
    )
    reader = _model_identity(reader_model, base_url)
    reader["options"] = dict(READER_OPTIONS)
    reader["system_prompt_sha256"] = _sha256(GENERATION_SYSTEM_PROMPT.encode())
    value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "longmemeval-s-monotonic-answer-registration",
        "status": "preregistered",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Paired answer-quality development evidence for two frozen PRME "
            "contexts; not an official LongMemEval score or independent confirmation."
        ),
        "source": {
            "prme_revision": _git_revision(project_root),
            "runner_sha256": _sha256_file(Path(__file__).resolve()),
            "monotonic_result_sha256": _result_identity(monotonic_result),
            "prepared_sha256": _sha256_file(prepared_path),
            "references_sha256": _sha256_file(references_path),
            "controls_sha256": _sha256_file(controls_path),
            "judge_declaration_sha256": _sha256_file(declaration_path),
            "judge_calibration_sha256": _sha256_file(calibration_path),
        },
        "dataset": {
            "name": "LongMemEval-S cleaned",
            "sha256": DATASET_SHA256,
            "split": split,
            "split_seed": SPLIT_SEED,
            "questions": len(prepared["rows"]),
            "question_ids": prepared["question_ids"],
            "question_ids_sha256": _sha256(_canonical(prepared["question_ids"])),
            "case_manifest_sha256": prepared["case_manifest_sha256"],
        },
        "models": {
            "reader": reader,
            "judge": declaration,
        },
        "calibration": {
            "metrics": calibration["metrics"],
            "cases": len(controls["cases"]),
        },
        "protocol": _protocol(),
        "evaluation": _gates(),
        "limitations": [
            "The development split and its labels have appeared in prior PRME studies.",
            "Ollama cloud manifests pin local aliases, not immutable remote weights.",
            "One generation per arm does not estimate hosted-model variance.",
            "The custom calibrated judge is not the official LongMemEval judge.",
        ],
    }
    if len(references) != value["dataset"]["questions"]:
        raise ValueError("reference coverage differs")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(value, indent=2) + "\n")
    return value


def _validate_registration(
    registration: dict[str, Any],
    *,
    monotonic_result: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    base_url: str,
    project_root: Path,
) -> None:
    revision = registration.get("source", {}).get("prme_revision")
    expected_source = {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "monotonic_result_sha256": _result_identity(monotonic_result),
        "prepared_sha256": _sha256_file(prepared_path),
        "references_sha256": _sha256_file(references_path),
        "controls_sha256": _sha256_file(controls_path),
        "judge_declaration_sha256": _sha256_file(declaration_path),
        "judge_calibration_sha256": _sha256_file(calibration_path),
    }
    reader = registration.get("models", {}).get("reader", {})
    runtime_reader = _model_identity(reader.get("model", ""), base_url)
    runtime_reader["options"] = dict(READER_OPTIONS)
    runtime_reader["system_prompt_sha256"] = _sha256(GENERATION_SYSTEM_PROMPT.encode())
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "longmemeval-s-monotonic-answer-registration"
        or registration.get("status") != "preregistered"
        or not isinstance(revision, str)
        or registration.get("source") != expected_source
        or registration.get("models", {}).get("reader") != runtime_reader
        or registration.get("models", {}).get("judge")
        != json.loads(declaration_path.read_text())
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _gates()
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered monotonic answer inputs differ")
    _validate_calibration(
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
    )


def _reader_payload(row: dict[str, Any], arm: str, model: str) -> dict[str, Any]:
    context = row["contexts"][arm]
    if context["sha256"] != _sha256(context["context"].encode()):
        raise ValueError("prepared context checksum differs")
    messages = [
        {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "QUESTION DATE:\n"
                + row["question_date"]
                + "\n\nMEMORY:\n"
                + context["context"]
                + "\n\nQUESTION:\n"
                + row["question"]
            ),
        },
    ]
    if (
        sum(len(message["content"].encode()) for message in messages)
        + 4096
        + READER_OPTIONS["num_predict"]
        > READER_OPTIONS["num_ctx"]
    ):
        raise ValueError("reader prompt exceeds conservative context headroom")
    return {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": dict(READER_OPTIONS),
    }


def _reader_run(
    prepared_path: Path,
    state_path: Path,
    *,
    registration_sha256: str,
    model: str,
    model_digest: str,
    base_url: str,
) -> dict[str, Any]:
    prepared_raw = prepared_path.read_bytes()
    prepared = json.loads(prepared_raw)
    identity = {
        "registration_sha256": registration_sha256,
        "prepared_sha256": _sha256(prepared_raw),
        "model": model,
        "model_digest": model_digest,
        "options": dict(READER_OPTIONS),
    }
    jobs = []
    for row in prepared["rows"]:
        order = (
            ARMS
            if int(_sha256(row["question_id"].encode()), 16) % 2
            else tuple(reversed(ARMS))
        )
        for arm in order:
            body = _reader_payload(row, arm, model)
            jobs.append((row, arm, body, _sha256(_canonical(body))))
    with reader_runtime.exclusive_state(state_path):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "identity": identity,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "generations": {},
                "failed_attempts": [],
                "complete": False,
            }
        )
        if state["identity"] != identity:
            raise ValueError("reader resume identity differs")
        wanted = {key for _row, _arm, _body, key in jobs}
        if not set(state["generations"]) <= wanted:
            raise ValueError("reader state contains unrelated generations")
        for saved in state["generations"].values():
            if _sha256(_canonical(saved["response"])) != saved["response_sha256"]:
                raise ValueError("saved reader response checksum differs")
            reader_runtime.validate_response(saved["response"])
        _write(state_path, state)
        for index, (_row, _arm, body, key) in enumerate(jobs):
            if key not in state["generations"]:
                response = None
                try:
                    if reader_runtime.model_digest(base_url, model) != model_digest:
                        raise ValueError("reader model changed")
                    response = reader_runtime.request(base_url, "/api/chat", body)
                    answer = reader_runtime.validate_response(response)
                    if (
                        not judge_runtime.matches_response_model(
                            model, response.get("model")
                        )
                        or not answer
                        or reader_runtime.model_digest(base_url, model) != model_digest
                    ):
                        raise ValueError("reader response model identity changed")
                    state["generations"][key] = {
                        "response": response,
                        "response_sha256": _sha256(_canonical(response)),
                    }
                except Exception as exc:
                    state["failed_attempts"].append(
                        {
                            "prompt_sha256": key,
                            "error_type": type(exc).__name__,
                            "at": datetime.now(timezone.utc).isoformat(),
                            "response": response,
                            "response_sha256": (
                                _sha256(_canonical(response))
                                if response is not None
                                else None
                            ),
                        }
                    )
                    _write(state_path, state)
                    raise
                _write(state_path, state)
            print(
                f"[monotonic-answer-reader] completed {index + 1}/{len(jobs)}",
                flush=True,
            )
        state["complete"] = True
        _write(state_path, state)
        rows = [
            {
                "question_id": row["question_id"],
                "arm": arm,
                "context_sha256": row["contexts"][arm]["sha256"],
                "prompt_sha256": key,
                "hypothesis": reader_runtime.validate_response(
                    state["generations"][key]["response"]
                ),
            }
            for row, arm, _body, key in jobs
        ]
        return {
            "complete": True,
            "identity": identity,
            "started_at": state["started_at"],
            "questions": len(prepared["rows"]),
            "logical_predictions": len(jobs),
            "unique_generations": len(state["generations"]),
            "failed_attempts": state["failed_attempts"],
            "rows": rows,
        }


def _judge_cases(
    predictions: dict[str, Any], references_path: Path
) -> list[dict[str, Any]]:
    references = {
        row["question_id"]: row for row in json.loads(references_path.read_text())
    }
    rows = predictions["rows"]
    if (
        not predictions.get("complete")
        or len(rows) != 2 * len(references)
        or {(row["question_id"], row["arm"]) for row in rows}
        != {(question_id, arm) for question_id in references for arm in ARMS}
    ):
        raise ValueError("reader predictions are incomplete")
    cases = []
    for row in rows:
        reference = references[row["question_id"]]
        cases.append(
            {
                "id": row["question_id"] + ":" + row["arm"],
                "question_id": row["question_id"],
                "arm": row["arm"],
                "category": (
                    "abstention"
                    if row["question_id"].endswith("_abs")
                    else reference["question_type"]
                ),
                "question": reference["question"],
                "reference": str(reference["answer"]),
                "hypothesis": row["hypothesis"],
            }
        )
    return cases


def _metrics(cases: list[dict[str, Any]], judgments: dict[str, Any]) -> dict[str, Any]:
    verdicts = {row["id"]: row["correct"] for row in judgments["judgments"]}
    if set(verdicts) != {case["id"] for case in cases}:
        raise ValueError("judge verdict coverage differs")
    grouped: dict[str, dict[str, Any]] = {}
    for case in cases:
        group = grouped.setdefault(case["question_id"], {"category": case["category"]})
        group[case["arm"]] = verdicts[case["id"]]
    if any(set(group) != {"category", *ARMS} for group in grouped.values()):
        raise ValueError("paired judge coverage differs")

    def summarize(values: list[dict[str, Any]]) -> dict[str, Any]:
        control = sum(row["control"] for row in values)
        monotonic = sum(row["monotonic"] for row in values)
        wins = sum(row["monotonic"] and not row["control"] for row in values)
        losses = sum(row["control"] and not row["monotonic"] for row in values)
        return {
            "questions": len(values),
            "control_correct": control,
            "monotonic_correct": monotonic,
            "paired_wins": wins,
            "paired_losses": losses,
            "paired_ties": len(values) - wins - losses,
            "accuracy_delta": (monotonic - control) / len(values),
        }

    values = list(grouped.values())
    categories = {
        category: summarize([row for row in values if row["category"] == category])
        for category in sorted({row["category"] for row in values})
    }
    overall = summarize(values)
    category_regressions = sum(
        value["monotonic_correct"] < value["control_correct"]
        for value in categories.values()
    )
    gate = {
        "complete_reader_execution": True,
        "complete_judge_execution": judgments.get("complete") is True,
        "reader_failed_attempts": 0,
        "judge_failed_attempts": len(judgments.get("prior_failed_attempts", [])),
        "monotonic_noninferior": (
            overall["monotonic_correct"] >= overall["control_correct"]
        ),
        "paired_wins_at_least_losses": (
            overall["paired_wins"] >= overall["paired_losses"]
        ),
        "category_regressions": category_regressions,
    }
    gate["passed"] = (
        gate["complete_reader_execution"]
        and gate["complete_judge_execution"]
        and gate["reader_failed_attempts"] == 0
        and gate["judge_failed_attempts"] == 0
        and gate["monotonic_noninferior"]
        and gate["paired_wins_at_least_losses"]
        and gate["category_regressions"] == 0
    )
    return {"overall": overall, "categories": categories, "gate": gate}


def evaluate(
    *,
    registration_path: Path,
    monotonic_result: Path,
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
        monotonic_result=monotonic_result,
        prepared_path=prepared_path,
        references_path=references_path,
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
        base_url=base_url,
        project_root=project_root,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    registration_sha256 = _sha256_file(registration_path)
    reader = registration["models"]["reader"]
    predictions = _reader_run(
        prepared_path,
        output_dir / "reader-state.json",
        registration_sha256=registration_sha256,
        model=reader["model"],
        model_digest=reader["model_digest"],
        base_url=base_url,
    )
    _write(output_dir / "reader.json", predictions)
    if predictions["failed_attempts"]:
        raise ValueError("reader execution contains failed attempts")

    judge_cases = _judge_cases(predictions, references_path)
    declaration = json.loads(declaration_path.read_text())
    judgments = judge_runtime.run_cases(
        judge_cases,
        declaration,
        output_dir / "judge-state.json",
        base_url,
    )
    _write(output_dir / "judge.json", judgments)
    metrics = _metrics(judge_cases, judgments)
    execution = {
        "registration_sha256": registration_sha256,
        "reader": predictions,
        "judge": judgments,
        "metrics": metrics,
    }
    _write(output_dir / "execution.json", execution)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-monotonic-answer-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": registration_sha256,
        "split": registration["dataset"]["split"],
        "questions": registration["dataset"]["questions"],
        "models": registration["models"],
        "metrics": metrics,
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
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    run = subparsers.add_parser("evaluate")
    for command in (register, run):
        command.add_argument("--monotonic-result", type=Path, required=True)
        command.add_argument("--prepared", type=Path, required=True)
        command.add_argument("--references", type=Path, required=True)
        command.add_argument("--controls", type=Path, required=True)
        command.add_argument("--judge-declaration", type=Path, required=True)
        command.add_argument("--judge-calibration", type=Path, required=True)
        command.add_argument("--base-url", default="http://127.0.0.1:11434")
        command.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--dataset", type=Path, required=True)
    register.add_argument("--cases-root", type=Path, required=True)
    register.add_argument("--split", choices=("dev", "test"), required=True)
    register.add_argument("--reader-model", required=True)
    register.add_argument("--output", type=Path, required=True)
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--summary", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    shared = {
        "monotonic_result": args.monotonic_result.resolve(),
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
            split=args.split,
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
            {"metrics": value["metrics"], "result_sha256": value["result_sha256"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
