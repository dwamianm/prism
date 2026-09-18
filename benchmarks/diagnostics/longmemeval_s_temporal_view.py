"""Registered paired trial of PRME's query-aware temporal evidence view."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import tempfile
from typing import Any

from benchmarks.diagnostics import paired_context_eval as paired
from benchmarks.diagnostics import reader_judge as judge_runtime
from benchmarks.diagnostics.longmemeval_s_compact import (
    _case_checksum,
    _clone_pack,
    _sha256,
    _sha256_file,
    _source_changes_since,
    _write,
)
from benchmarks.diagnostics.longmemeval_s_compact_localization import _answer_result
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    USER_ID,
    _load_dataset,
    _pack_config,
    _parse_date,
)
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT
from prme import MemoryEngine
from prme.retrieval.context_formatter import format_for_llm
from prme.retrieval.tokenization import count_tokens


ARMS = ("auditable", "temporal_view")
READER_OPTIONS = {
    "temperature": 0,
    "seed": 42,
    "num_ctx": 65536,
    "num_predict": 1024,
}
MEMORY_TOKEN_BUDGET = 3996
TEMPORAL_QUESTIONS = 29


def _git_revision(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _validate_calibration(
    controls_path: Path, declaration_path: Path, calibration_path: Path
) -> dict[str, Any]:
    controls = json.loads(controls_path.read_text())
    declaration = json.loads(declaration_path.read_text())
    calibration = json.loads(calibration_path.read_text())
    judge_runtime.validate_calibration(controls, calibration, declaration)
    return declaration


def _question_ids(
    answer_registration: Path, dataset_path: Path
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    registration = json.loads(answer_registration.read_text())
    if (
        registration.get("kind") != "longmemeval-s-monotonic-answer-registration"
        or registration.get("dataset", {}).get("split") != "dev"
        or registration.get("dataset", {}).get("questions") != 119
        or registration.get("dataset", {}).get("sha256") != DATASET_SHA256
    ):
        raise ValueError("development answer registration differs")
    if _sha256_file(dataset_path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    cases = {case["question_id"]: case for case in _load_dataset(dataset_path)}
    selected = [
        question_id
        for question_id in registration["dataset"]["question_ids"]
        if cases[question_id]["question_type"] == "temporal-reasoning"
    ]
    if len(selected) != TEMPORAL_QUESTIONS:
        raise ValueError(
            f"expected {TEMPORAL_QUESTIONS} temporal development questions"
        )
    return selected, cases


def _numbered_records(context: str) -> int:
    return sum(
        line.startswith("[") and len(line) > 2 and line[1 : line.find("]")].isdigit()
        for line in context.splitlines()
    )


async def _prepare_case(
    case: dict[str, Any],
    *,
    baseline_root: Path,
    source_cases_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    question_id = case["question_id"]
    saved = json.loads((source_cases_root / f"{question_id}.json").read_text())
    if (
        saved.get("kind") != "longmemeval-s-monotonic-compact-case"
        or saved.get("question_id") != question_id
        or saved.get("case_sha256") != _case_checksum(saved)
    ):
        raise ValueError(f"saved source case differs for {question_id}")
    with tempfile.TemporaryDirectory(prefix="prme-lme-temporal-view-") as temporary:
        pack = Path(temporary) / question_id
        clone_method = await asyncio.to_thread(
            _clone_pack, baseline_root / "packs" / question_id, pack
        )
        config = _pack_config(pack)
        config = config.model_copy(
            update={
                "organizer": config.organizer.model_copy(
                    update={"opportunistic_enabled": False}
                )
            }
        )
        engine = await MemoryEngine.create(config)
        try:
            question_time = _parse_date(case["question_date"])
            response = await engine.retrieve(
                case["question"], user_id=USER_ID, reference_time=question_time
            )
            control = response.bundle.render()
            if control != saved["arms"]["control"]["context"]:
                raise ValueError(f"auditable control replay differs for {question_id}")
            control_ids = saved["arms"]["control"]["node_ids"]
            by_id = {
                str(candidate.node.id): candidate for candidate in response.results
            }
            if not set(control_ids) <= set(by_id):
                raise ValueError(
                    f"selected control nodes are unavailable for {question_id}"
                )
            selected = [by_id[node_id] for node_id in control_ids]
            candidate = format_for_llm(
                selected,
                case["question"],
                question_date=question_time,
                context_hint="temporal",
                include_profile=False,
                max_results=len(selected),
                token_budget=MEMORY_TOKEN_BUDGET,
                token_counter=lambda text: count_tokens(text, config.packing.tokenizer),
            )
            candidate_tokens = count_tokens(candidate, config.packing.tokenizer)
        finally:
            await engine.close()
    if (
        not candidate
        or _numbered_records(candidate) != len(control_ids)
        or candidate_tokens > MEMORY_TOKEN_BUDGET
    ):
        raise ValueError(
            f"temporal view did not retain the control set for {question_id}"
        )
    neutral = {
        "question_id": question_id,
        "question": case["question"],
        "question_date": case["question_date"],
        "contexts": {
            "auditable": {
                "context": control,
                "sha256": _sha256(control.encode()),
                "tokens": saved["arms"]["control"]["tokens"],
            },
            "temporal_view": {
                "context": candidate,
                "sha256": _sha256(candidate.encode()),
                "tokens": candidate_tokens,
            },
        },
    }
    reference = {
        key: case[key]
        for key in (
            "question_id",
            "question",
            "question_date",
            "question_type",
            "answer",
        )
    }
    audit = {
        "question_id": question_id,
        "clone_method": clone_method,
        "records": len(control_ids),
        "auditable_tokens": saved["arms"]["control"]["tokens"],
        "temporal_view_tokens": candidate_tokens,
        "node_ids_sha256": _sha256(paired.canonical(control_ids)),
    }
    return neutral, reference, audit


async def _prepare(
    *,
    question_ids: list[str],
    cases: dict[str, dict[str, Any]],
    baseline_root: Path,
    source_cases_root: Path,
    prepared_path: Path,
    references_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values = []
    for question_id in question_ids:
        values.append(
            await _prepare_case(
                cases[question_id],
                baseline_root=baseline_root,
                source_cases_root=source_cases_root,
            )
        )
        print(f"Prepared {len(values)}/{len(question_ids)}", flush=True)
    prepared = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-view-inputs",
        "dataset_sha256": DATASET_SHA256,
        "selection": (
            f"all {TEMPORAL_QUESTIONS} temporal-reasoning questions in the frozen "
            "development split"
        ),
        "question_ids": question_ids,
        "generation_system_prompt": GENERATION_SYSTEM_PROMPT,
        "arms": list(ARMS),
        "rows": [value[0] for value in values],
        "audits": [value[2] for value in values],
    }
    references = [value[1] for value in values]
    if any("answer" in row for row in prepared["rows"]):
        raise ValueError("neutral reader inputs contain reference answers")
    _write(prepared_path, prepared)
    _write(references_path, references)
    return prepared, references


def _protocol() -> dict[str, Any]:
    return {
        "status": "observed development cohort; cannot promote without confirmation",
        "arms": list(ARMS),
        "control": "the exact saved auditable MemoryBundle context",
        "candidate": (
            "the exact same selected records rendered by format_for_llm as a "
            "chronological temporal view with question-relative day offsets"
        ),
        "memory_token_budget": MEMORY_TOKEN_BUDGET,
        "record_set": "identical per question",
        "candidate_profile_preamble": False,
        "reader_order": "counterbalanced by question-ID hash parity",
        "one_generation_per_arm": True,
        "no_selective_retries": True,
        "gate": {
            "complete_reader_and_judge": True,
            "failed_calls": 0,
            "candidate_correct": "> control_correct",
            "paired_wins": "> paired_losses",
        },
    }


def _source(
    *,
    revision: str,
    answer_registration: Path,
    answer_result: Path,
    answer_execution: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
) -> dict[str, Any]:
    return {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "paired_runtime_sha256": _sha256_file(Path(paired.__file__).resolve()),
        "formatter_sha256": _sha256_file(Path(format_for_llm.__code__.co_filename)),
        "answer_registration_sha256": _sha256_file(answer_registration),
        "answer_result_sha256": _sha256_file(answer_result),
        "answer_execution_sha256": _sha256_file(answer_execution),
        "prepared_sha256": _sha256_file(prepared_path),
        "references_sha256": _sha256_file(references_path),
        "controls_sha256": _sha256_file(controls_path),
        "judge_declaration_sha256": _sha256_file(declaration_path),
        "judge_calibration_sha256": _sha256_file(calibration_path),
    }


async def create_registration(
    *,
    dataset_path: Path,
    baseline_root: Path,
    source_cases_root: Path,
    answer_registration: Path,
    answer_result: Path,
    answer_execution: Path,
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
            raise ValueError(f"fresh output required: {path}")
    failed_result, _execution = _answer_result(answer_result, answer_execution)
    question_ids, cases = _question_ids(answer_registration, dataset_path)
    prepared, references = await _prepare(
        question_ids=question_ids,
        cases=cases,
        baseline_root=baseline_root,
        source_cases_root=source_cases_root,
        prepared_path=prepared_path,
        references_path=references_path,
    )
    declaration = _validate_calibration(
        controls_path, declaration_path, calibration_path
    )
    revision = _git_revision(project_root)
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-view-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Paired temporal-format development evidence on an observed cohort; "
            "not an official LongMemEval score or product-promotion result."
        ),
        "source": _source(
            revision=revision,
            answer_registration=answer_registration,
            answer_result=answer_result,
            answer_execution=answer_execution,
            prepared_path=prepared_path,
            references_path=references_path,
            controls_path=controls_path,
            declaration_path=declaration_path,
            calibration_path=calibration_path,
        ),
        "dataset": {
            "name": "LongMemEval-S cleaned",
            "sha256": DATASET_SHA256,
            "questions": len(question_ids),
            "question_ids": question_ids,
            "question_ids_sha256": _sha256(paired.canonical(question_ids)),
        },
        "prior_failed_answer_result_sha256": failed_result["result_sha256"],
        "models": {
            "reader": paired.model_identity(
                reader_model, base_url, dict(READER_OPTIONS), GENERATION_SYSTEM_PROMPT
            ),
            "judge": declaration,
        },
        "protocol": _protocol(),
        "limitations": [
            "The development questions and prior answers were observed before registration.",
            "Ollama cloud manifests do not prove immutable remote weights.",
            "One generation per arm does not estimate model variance.",
            "The custom judge is not the official LongMemEval judge.",
            "This direct-turn cohort does not exercise mixed epistemic or validity states.",
        ],
    }
    if (
        len(prepared["rows"]) != len(references)
        or len(references) != TEMPORAL_QUESTIONS
    ):
        raise ValueError("prepared/reference coverage differs")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(value, indent=2) + "\n")
    return value


def _validate_registration(
    registration: dict[str, Any],
    *,
    answer_registration: Path,
    answer_result: Path,
    answer_execution: Path,
    prepared_path: Path,
    references_path: Path,
    controls_path: Path,
    declaration_path: Path,
    calibration_path: Path,
    base_url: str,
    project_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failed_result, _execution = _answer_result(answer_result, answer_execution)
    revision = registration.get("source", {}).get("prme_revision")
    expected_source = _source(
        revision=revision,
        answer_registration=answer_registration,
        answer_result=answer_result,
        answer_execution=answer_execution,
        prepared_path=prepared_path,
        references_path=references_path,
        controls_path=controls_path,
        declaration_path=declaration_path,
        calibration_path=calibration_path,
    )
    declaration = _validate_calibration(
        controls_path, declaration_path, calibration_path
    )
    reader = registration.get("models", {}).get("reader", {})
    runtime_reader = paired.model_identity(
        str(reader.get("model", "")),
        base_url,
        dict(READER_OPTIONS),
        GENERATION_SYSTEM_PROMPT,
    )
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "longmemeval-s-temporal-view-registration"
        or not isinstance(revision, str)
        or registration.get("source") != expected_source
        or registration.get("prior_failed_answer_result_sha256")
        != failed_result["result_sha256"]
        or registration.get("dataset", {}).get("questions") != TEMPORAL_QUESTIONS
        or registration.get("dataset", {}).get("question_ids_sha256")
        != _sha256(paired.canonical(registration["dataset"]["question_ids"]))
        or registration.get("models", {}).get("reader") != runtime_reader
        or registration.get("models", {}).get("judge") != declaration
        or registration.get("protocol") != _protocol()
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered temporal-view inputs differ")
    prepared = json.loads(prepared_path.read_text())
    references = json.loads(references_path.read_text())
    ids = registration["dataset"]["question_ids"]
    if (
        prepared.get("kind") != "longmemeval-s-temporal-view-inputs"
        or prepared.get("question_ids") != ids
        or prepared.get("arms") != list(ARMS)
        or [row.get("question_id") for row in prepared.get("rows", [])] != ids
        or [row.get("question_id") for row in references] != ids
        or any(row.get("question_type") != "temporal-reasoning" for row in references)
        or any("answer" in row for row in prepared["rows"])
        or any(set(row.get("contexts", {})) != set(ARMS) for row in prepared["rows"])
    ):
        raise ValueError("prepared temporal-view inputs differ")
    return prepared, references


def evaluate(
    *,
    registration_path: Path,
    answer_registration: Path,
    answer_result: Path,
    answer_execution: Path,
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
    prepared, references = _validate_registration(
        registration,
        answer_registration=answer_registration,
        answer_result=answer_result,
        answer_execution=answer_execution,
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
    predictions = paired.run_reader(
        prepared,
        ARMS,
        output_dir / "reader-state.json",
        registration_sha256=registration_sha256,
        prepared_sha256=_sha256_file(prepared_path),
        model=reader["model"],
        model_digest=reader["model_digest"],
        options=dict(READER_OPTIONS),
        system_prompt=GENERATION_SYSTEM_PROMPT,
        base_url=base_url,
    )
    _write(output_dir / "reader.json", predictions)
    if predictions["failed_attempts"]:
        raise ValueError("reader execution contains failed attempts")
    cases = paired.judge_cases(predictions, references, ARMS)
    judgments = judge_runtime.run_cases(
        cases,
        registration["models"]["judge"],
        output_dir / "judge-state.json",
        base_url,
    )
    _write(output_dir / "judge.json", judgments)
    metrics = paired.paired_metrics(
        cases,
        judgments,
        control_arm=ARMS[0],
        candidate_arm=ARMS[1],
    )
    gate = {
        "complete_reader_execution": predictions["complete"] is True,
        "complete_judge_execution": judgments["complete"] is True,
        "reader_failed_attempts": len(predictions["failed_attempts"]),
        "judge_failed_attempts": len(judgments["prior_failed_attempts"]),
        "candidate_improved": (
            metrics["overall"]["candidate_correct"]
            > metrics["overall"]["control_correct"]
        ),
        "wins_exceed_losses": (
            metrics["overall"]["paired_wins"] > metrics["overall"]["paired_losses"]
        ),
    }
    gate["passed"] = (
        gate["complete_reader_execution"]
        and gate["complete_judge_execution"]
        and gate["reader_failed_attempts"] == 0
        and gate["judge_failed_attempts"] == 0
        and gate["candidate_improved"]
        and gate["wins_exceed_losses"]
    )
    metrics["gate"] = gate
    execution_result = {
        "registration_sha256": registration_sha256,
        "reader": predictions,
        "judge": judgments,
        "metrics": metrics,
    }
    _write(output_dir / "execution.json", execution_result)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-view-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": registration_sha256,
        "questions": len(prepared["rows"]),
        "metrics": metrics,
        "context_tokens": {
            arm: {
                "total": sum(
                    row["contexts"][arm]["tokens"] for row in prepared["rows"]
                ),
                "mean": sum(row["contexts"][arm]["tokens"] for row in prepared["rows"])
                / len(prepared["rows"]),
                "maximum": max(
                    row["contexts"][arm]["tokens"] for row in prepared["rows"]
                ),
            }
            for arm in ARMS
        },
        "execution_sha256": _sha256_file(output_dir / "execution.json"),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "claim_boundary": registration["claim_boundary"],
    }
    result["result_sha256"] = _sha256(paired.canonical(result))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    run = commands.add_parser("evaluate")
    for command in (register, run):
        command.add_argument("--answer-registration", type=Path, required=True)
        command.add_argument("--answer-result", type=Path, required=True)
        command.add_argument("--answer-execution", type=Path, required=True)
        command.add_argument("--prepared", type=Path, required=True)
        command.add_argument("--references", type=Path, required=True)
        command.add_argument("--controls", type=Path, required=True)
        command.add_argument("--judge-declaration", type=Path, required=True)
        command.add_argument("--judge-calibration", type=Path, required=True)
        command.add_argument("--base-url", default="http://127.0.0.1:11434")
        command.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--dataset", type=Path, required=True)
    register.add_argument("--baseline-root", type=Path, required=True)
    register.add_argument("--source-cases-root", type=Path, required=True)
    register.add_argument("--reader-model", required=True)
    register.add_argument("--output", type=Path, required=True)
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--summary", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    shared = {
        "answer_registration": args.answer_registration.resolve(),
        "answer_result": args.answer_result.resolve(),
        "answer_execution": args.answer_execution.resolve(),
        "prepared_path": args.prepared.resolve(),
        "references_path": args.references.resolve(),
        "controls_path": args.controls.resolve(),
        "declaration_path": args.judge_declaration.resolve(),
        "calibration_path": args.judge_calibration.resolve(),
        "base_url": args.base_url,
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        value = asyncio.run(
            create_registration(
                dataset_path=args.dataset.resolve(),
                baseline_root=args.baseline_root.resolve(),
                source_cases_root=args.source_cases_root.resolve(),
                reader_model=args.reader_model,
                output_path=args.output.resolve(),
                **shared,
            )
        )
        print(
            json.dumps(
                {"dataset": value["dataset"], "protocol": value["protocol"]}, indent=2
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
