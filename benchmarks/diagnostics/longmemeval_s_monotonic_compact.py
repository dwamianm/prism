"""Registered full-cohort validation of monotonic compact context packing."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import statistics
import tempfile
from typing import Any

from benchmarks.diagnostics.longmemeval_s_compact import (
    _bundle_sources,
    _canonical,
    _case_checksum,
    _clone_pack,
    _evidence,
    _git_revision,
    _sha256,
    _sha256_file,
    _source_changes_since,
    _write,
)
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    USER_ID,
    _load_dataset,
    _pack_config,
    _parse_date,
)
from prme import MemoryEngine
from prme.retrieval.context_formatter import build_context_guidance
from prme.retrieval.packing import pack_context, pack_context_monotonic_compact
from prme.retrieval.query_analysis import analyze_query


WORKERS = 5


def _dataset_identity(path: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    if _sha256_file(path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    question_ids = [case["question_id"] for case in cases]
    if len(question_ids) != 500 or len(set(question_ids)) != 500:
        raise ValueError("LongMemEval-S full cohort identity differs")
    return {
        "name": "LongMemEval-S cleaned",
        "sha256": DATASET_SHA256,
        "cohort": "full-development",
        "questions": len(cases),
        "question_ids": question_ids,
        "question_ids_sha256": _sha256(_canonical(question_ids)),
    }


def _prior_result_identity(path: Path) -> str:
    result = json.loads(path.read_text())
    recorded = result.get("result_sha256")
    computed = _sha256(
        _canonical(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
    )
    if (
        result.get("kind") != "longmemeval-s-compact-packing-result"
        or result.get("split") != "test"
        or result.get("gate", {}).get("passed") is not False
        or result.get("gate", {}).get("per_question_turn_recall_losses", 0) <= 0
        or recorded != computed
    ):
        raise ValueError("prior result is not the intact rejected compact trial")
    return _sha256_file(path)


def _protocol() -> dict[str, Any]:
    return {
        "input": "all 500 frozen unchanged-PRME packs and scored candidate sets",
        "control": {
            "multipath_ordering": "balanced",
            "context_format": "auditable",
            "token_budget": 4096,
        },
        "candidate": {
            "multipath_ordering": "balanced",
            "context_format": "compact",
            "composition": (
                "reserve every control candidate at its control representation and "
                "any included control guidance, then admit additional candidates"
            ),
            "token_budget": 4096,
        },
        "invariants": [
            "question, pack, candidate IDs, scores and order are identical",
            "current product retrieval reproduces the frozen auditable context byte for byte",
            "every control memory and included guidance must survive in the candidate",
            "both arms use the same exact tokenizer and whole-output budget",
            "labels are unavailable until both contexts are finalized",
            "all 500 questions remain in the denominator",
        ],
    }


def _gates() -> dict[str, Any]:
    return {
        "replay_failures": 0,
        "budget_violations": 0,
        "control_record_loss_questions": 0,
        "control_guidance_loss_questions": 0,
        "compact_fallback_questions": 0,
        "per_question_turn_recall_losses": 0,
        "per_question_session_recall_losses": 0,
        "category_mean_turn_recall_losses": 0,
        "complete_turn_questions": "candidate > control",
        "decision": (
            "A pass establishes full-cohort monotonic source retention. It cannot "
            "change the default without answer-quality evidence on another workload."
        ),
    }


def create_registration(
    *,
    dataset_path: Path,
    baseline_registration: Path,
    baseline_capture: Path,
    baseline_manifest: Path,
    prior_result: Path,
    project_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("registration output already exists")
    cases = _load_dataset(dataset_path)
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-monotonic-compact-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Full-development-cohort validation of control-preserving compact "
            "packing; not answer accuracy, an independent holdout, or a Zep score."
        ),
        "source": {
            "prme_revision": _git_revision(project_root),
            "runner_sha256": _sha256_file(Path(__file__).resolve()),
            "baseline_registration_sha256": _sha256_file(baseline_registration),
            "baseline_capture_sha256": _sha256_file(baseline_capture),
            "baseline_manifest_sha256": _sha256_file(baseline_manifest),
            "rejected_compact_result_sha256": _prior_result_identity(prior_result),
        },
        "dataset": _dataset_identity(dataset_path, cases),
        "protocol": _protocol(),
        "evaluation": _gates(),
        "limitations": [
            "All 500 questions have now been inspected and constitute development evidence.",
            "The composition is motivated by the rejected compact confirmation losses.",
            "Source retention is necessary but does not prove downstream answer improvement.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(value, indent=2) + "\n")
    return value


def _validate_registration(
    registration: dict[str, Any],
    *,
    dataset_path: Path,
    baseline_registration: Path,
    baseline_capture: Path,
    baseline_manifest: Path,
    prior_result: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    cases = _load_dataset(dataset_path)
    revision = registration.get("source", {}).get("prme_revision")
    expected_source = {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "baseline_registration_sha256": _sha256_file(baseline_registration),
        "baseline_capture_sha256": _sha256_file(baseline_capture),
        "baseline_manifest_sha256": _sha256_file(baseline_manifest),
        "rejected_compact_result_sha256": _prior_result_identity(prior_result),
    }
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "longmemeval-s-monotonic-compact-registration"
        or not isinstance(revision, str)
        or registration.get("source") != expected_source
        or registration.get("dataset") != _dataset_identity(dataset_path, cases)
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _gates()
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered monotonic-compact inputs differ")
    selected_by_id = {case["question_id"]: case for case in cases}
    return [
        selected_by_id[question_id]
        for question_id in registration["dataset"]["question_ids"]
    ]


async def _evaluate_case(
    case: dict[str, Any],
    *,
    registration_sha256: str,
    baseline_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    question_id = case["question_id"]
    output_path = output_dir / "cases" / f"{question_id}.json"
    if output_path.exists():
        saved = json.loads(output_path.read_text())
        if saved.get("registration_sha256") != registration_sha256 or saved.get(
            "case_sha256"
        ) != _case_checksum(saved):
            raise ValueError(f"saved monotonic case differs for {question_id}")
        return saved

    baseline_capture = json.loads(
        (baseline_root / "captures" / f"{question_id}.json").read_text()
    )
    with tempfile.TemporaryDirectory(prefix="prme-lme-monotonic-") as temporary:
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
            reference_time = _parse_date(case["question_date"])
            response = await engine.retrieve(
                case["question"], user_id=USER_ID, reference_time=reference_time
            )
            candidate_ids = [str(candidate.node.id) for candidate in response.results]
            candidate_scores = [
                candidate.composite_score for candidate in response.results
            ]
            if (
                response.bundle.render() != baseline_capture["context"]
                or candidate_ids
                != [row["node_id"] for row in baseline_capture["returned"]]
                or candidate_scores
                != [row["composite_score"] for row in baseline_capture["returned"]]
            ):
                raise ValueError(f"baseline product replay differs for {question_id}")

            analysis = await analyze_query(
                case["question"], reference_time=reference_time
            )
            guidance = build_context_guidance(
                case["question"],
                query_analysis=analysis,
                reference_time=reference_time,
                mode=config.packing.context_guidance_mode,
            )
            control = pack_context(
                response.results,
                config.packing,
                coverage_notice=response.bundle.coverage_notice,
                context_guidance=guidance,
            )
            if control.render() != response.bundle.render():
                raise ValueError(f"baseline repack differs for {question_id}")
            monotonic = pack_context_monotonic_compact(
                response.results,
                config.packing.model_copy(update={"context_format": "compact"}),
                coverage_notice=response.bundle.coverage_notice,
                context_guidance=guidance,
            )
            arms = {}
            for name, bundle in (("control", control), ("monotonic", monotonic)):
                context = bundle.render()
                rows = _bundle_sources(bundle)
                arms[name] = {
                    "context": context,
                    "context_sha256": _sha256(context.encode()),
                    "context_format": bundle.context_format,
                    "guidance_included": bundle.context_guidance is not None,
                    "tokens": bundle.tokens_used,
                    "records": bundle.included_count,
                    "node_ids": [row["node_id"] for row in rows],
                    "evidence": _evidence(case, rows),
                }
        finally:
            await engine.close()

    control_ids = set(arms["control"]["node_ids"])
    monotonic_ids = set(arms["monotonic"]["node_ids"])
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-monotonic-compact-case",
        "registration_sha256": registration_sha256,
        "question_id": question_id,
        "question_type": (
            "abstention" if question_id.endswith("_abs") else case["question_type"]
        ),
        "clone_method": clone_method,
        "candidate_count": len(candidate_ids),
        "candidate_ids_sha256": _sha256(_canonical(candidate_ids)),
        "candidate_scores_sha256": _sha256(_canonical(candidate_scores)),
        "control_record_losses": sorted(control_ids - monotonic_ids),
        "control_guidance_lost": (
            arms["control"]["guidance_included"]
            and not arms["monotonic"]["guidance_included"]
        ),
        "arms": arms,
    }
    result["case_sha256"] = _case_checksum(result)
    _write(output_path, result)
    return result


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * fraction) - 1]


def _arm_summary(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    applicable = [
        row["arms"][arm]["evidence"]
        for row in rows
        if row["arms"][arm]["evidence"]["applicable"]
    ]
    tokens = [row["arms"][arm]["tokens"] for row in rows]
    records = [row["arms"][arm]["records"] for row in rows]
    return {
        "questions": len(rows),
        "evidence_questions": len(applicable),
        "mean_session_recall": statistics.mean(
            item["session_recall"] for item in applicable
        ),
        "complete_session_questions": sum(
            item["complete_session_recall"] for item in applicable
        ),
        "mean_turn_recall": statistics.mean(item["turn_recall"] for item in applicable),
        "complete_turn_questions": sum(
            item["complete_turn_recall"] for item in applicable
        ),
        "context_tokens": {
            "mean": statistics.mean(tokens),
            "median": statistics.median(tokens),
            "p95": _percentile([float(value) for value in tokens], 0.95),
            "max": max(tokens),
        },
        "records": {
            "mean": statistics.mean(records),
            "median": statistics.median(records),
            "p95": _percentile([float(value) for value in records], 0.95),
            "max": max(records),
        },
    }


def _comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [
        row for row in rows if row["arms"]["control"]["evidence"]["applicable"]
    ]
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in applicable:
        categories[row["question_type"]].append(row)

    def delta(row: dict[str, Any], field: str) -> float:
        return (
            row["arms"]["monotonic"]["evidence"][field]
            - row["arms"]["control"]["evidence"][field]
        )

    category_results = {}
    for name, values in sorted(categories.items()):
        category_results[name] = {
            "questions": len(values),
            "control_mean_turn_recall": statistics.mean(
                row["arms"]["control"]["evidence"]["turn_recall"] for row in values
            ),
            "monotonic_mean_turn_recall": statistics.mean(
                row["arms"]["monotonic"]["evidence"]["turn_recall"] for row in values
            ),
            "turn_recall_losses": sum(delta(row, "turn_recall") < 0 for row in values),
        }
    return {
        "questions": len(applicable),
        "turn_recall_wins": sum(delta(row, "turn_recall") > 0 for row in applicable),
        "turn_recall_losses": sum(delta(row, "turn_recall") < 0 for row in applicable),
        "turn_recall_ties": sum(delta(row, "turn_recall") == 0 for row in applicable),
        "session_recall_wins": sum(
            delta(row, "session_recall") > 0 for row in applicable
        ),
        "session_recall_losses": sum(
            delta(row, "session_recall") < 0 for row in applicable
        ),
        "session_recall_ties": sum(
            delta(row, "session_recall") == 0 for row in applicable
        ),
        "categories": category_results,
    }


def _summary(rows: list[dict[str, Any]], registration_path: Path) -> dict[str, Any]:
    control = _arm_summary(rows, "control")
    monotonic = _arm_summary(rows, "monotonic")
    comparison = _comparison(rows)
    category_losses = sum(
        value["monotonic_mean_turn_recall"] < value["control_mean_turn_recall"]
        for value in comparison["categories"].values()
    )
    gate = {
        "replay_failures": 0,
        "budget_violations": sum(
            row["arms"][arm]["tokens"] > 3996
            for row in rows
            for arm in ("control", "monotonic")
        ),
        "control_record_loss_questions": sum(
            bool(row["control_record_losses"]) for row in rows
        ),
        "control_guidance_loss_questions": sum(
            row["control_guidance_lost"] for row in rows
        ),
        "compact_fallback_questions": sum(
            row["arms"]["monotonic"]["context_format"] != "compact" for row in rows
        ),
        "per_question_turn_recall_losses": comparison["turn_recall_losses"],
        "per_question_session_recall_losses": comparison["session_recall_losses"],
        "category_mean_turn_recall_losses": category_losses,
        "complete_turn_questions_improved": (
            monotonic["complete_turn_questions"] > control["complete_turn_questions"]
        ),
    }
    gate["passed"] = (
        all(
            gate[key] == 0
            for key in (
                "replay_failures",
                "budget_violations",
                "control_record_loss_questions",
                "control_guidance_loss_questions",
                "compact_fallback_questions",
                "per_question_turn_recall_losses",
                "per_question_session_recall_losses",
                "category_mean_turn_recall_losses",
            )
        )
        and gate["complete_turn_questions_improved"]
    )
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-monotonic-compact-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": _sha256_file(registration_path),
        "cohort": "full-development",
        "arms": {"control": control, "monotonic": monotonic},
        "comparison": comparison,
        "gate": gate,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "workers": WORKERS,
        },
        "case_manifest_sha256": _sha256(
            _canonical([[row["question_id"], row["case_sha256"]] for row in rows])
        ),
    }
    result["result_sha256"] = _sha256(_canonical(result))
    return result


async def evaluate(
    *,
    registration_path: Path,
    dataset_path: Path,
    baseline_registration: Path,
    baseline_capture: Path,
    baseline_manifest: Path,
    prior_result: Path,
    baseline_root: Path,
    project_root: Path,
    output_dir: Path,
    summary_path: Path,
) -> dict[str, Any]:
    if summary_path.exists():
        raise ValueError("summary output already exists")
    registration = json.loads(registration_path.read_text())
    cases = _validate_registration(
        registration,
        dataset_path=dataset_path,
        baseline_registration=baseline_registration,
        baseline_capture=baseline_capture,
        baseline_manifest=baseline_manifest,
        prior_result=prior_result,
        project_root=project_root,
    )
    registration_sha256 = _sha256_file(registration_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    identity_path = output_dir / "identity.json"
    identity = {
        "registration_sha256": registration_sha256,
        "questions": len(cases),
    }
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("output directory belongs to another trial")
    _write(identity_path, identity)
    semaphore = asyncio.Semaphore(WORKERS)
    completed = 0

    async def run_one(case: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed
        async with semaphore:
            result = await _evaluate_case(
                case,
                registration_sha256=registration_sha256,
                baseline_root=baseline_root,
                output_dir=output_dir,
            )
        completed += 1
        if completed % 10 == 0 or completed == len(cases):
            print(
                f"[longmemeval-s-monotonic] completed {completed}/{len(cases)}",
                flush=True,
            )
        return result

    values = await asyncio.gather(
        *(run_one(case) for case in cases), return_exceptions=True
    )
    errors = [value for value in values if isinstance(value, BaseException)]
    if errors:
        raise RuntimeError(
            f"monotonic compact packing failed for {len(errors)} case(s)"
        ) from errors[0]
    rows = [value for value in values if isinstance(value, dict)]
    order = registration["dataset"]["question_ids"]
    rows.sort(key=lambda item: order.index(item["question_id"]))
    result = _summary(rows, registration_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    run = subparsers.add_parser("evaluate")
    for command in (register, run):
        command.add_argument("--dataset", type=Path, required=True)
        command.add_argument("--baseline-registration", type=Path, required=True)
        command.add_argument("--baseline-capture", type=Path, required=True)
        command.add_argument("--baseline-manifest", type=Path, required=True)
        command.add_argument("--prior-result", type=Path, required=True)
        command.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--output", type=Path, required=True)
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--baseline-root", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--summary", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    shared = {
        "dataset_path": args.dataset.resolve(),
        "baseline_registration": args.baseline_registration.resolve(),
        "baseline_capture": args.baseline_capture.resolve(),
        "baseline_manifest": args.baseline_manifest.resolve(),
        "prior_result": args.prior_result.resolve(),
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        value = create_registration(output_path=args.output.resolve(), **shared)
        print(
            json.dumps(
                {"dataset": value["dataset"], "evaluation": value["evaluation"]},
                indent=2,
            )
        )
        return
    value = asyncio.run(
        evaluate(
            registration_path=args.registration.resolve(),
            baseline_root=args.baseline_root.resolve(),
            output_dir=args.output_dir.resolve(),
            summary_path=args.summary.resolve(),
            **shared,
        )
    )
    print(json.dumps({key: value[key] for key in ("gate", "result_sha256")}, indent=2))


if __name__ == "__main__":
    main()
