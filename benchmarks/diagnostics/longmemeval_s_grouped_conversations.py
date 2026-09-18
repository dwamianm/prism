"""Registered source trial for named, grouped conversation context packing."""

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
from prme.retrieval.packing import (
    pack_context,
    pack_context_grouped_conversations,
)
from prme.retrieval.query_analysis import analyze_query


WORKERS = 5
ARMS = ("control", "grouped_same_set", "grouped_fill")


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


def _protocol() -> dict[str, Any]:
    return {
        "input": "all 500 frozen unchanged-PRME packs and scored candidate sets",
        "control": {
            "packing": "unchanged balanced auditable product behavior",
            "token_budget": 4096,
            "caller_reserve": 100,
        },
        "grouped_same_set": {
            "selection": "exact control node IDs and representation levels",
            "rendering": (
                "two or more records in one exact section, scope and session_id "
                "share named audit fields; turns retain UUID, validity, source type, "
                "representation and whole text, with explicit role and turn index "
                "when those stored metadata fields are valid"
            ),
            "singleton_behavior": "ordinary auditable JSON object",
        },
        "grouped_fill": {
            "selection": (
                "reserve the exact control set and included guidance, then spend "
                "only remaining grouped-context space on later balanced candidates"
            ),
            "rendering": "identical named grouped rendering",
        },
        "invariants": [
            "question, pack, candidate IDs, scores and order are identical",
            "current product retrieval reproduces the frozen context byte for byte",
            "both grouped arms retain every control node and included guidance",
            "same-set changes no node identity or representation",
            "whole-output token counting enforces the same 3996-token ceiling",
            "labels are unavailable until all three contexts are finalized",
            "all 500 questions remain in the denominator",
        ],
    }


def _gates() -> dict[str, Any]:
    return {
        "replay_failures": 0,
        "budget_violations": 0,
        "control_record_loss_questions": 0,
        "control_guidance_loss_questions": 0,
        "same_set_identity_mismatch_questions": 0,
        "grouped_rendering_questions": 500,
        "fill_per_question_turn_recall_losses": 0,
        "fill_per_question_session_recall_losses": 0,
        "fill_category_mean_turn_recall_losses": 0,
        "fill_complete_turn_questions": "candidate > control",
        "decision": (
            "A pass advances both frozen grouped arms to paired answer development. "
            "It cannot change product behavior or establish independent evidence."
        ),
    }


def _source(
    *,
    revision: str,
    baseline_registration: Path,
    baseline_capture: Path,
    baseline_identity: Path,
    methodology_review: Path,
    compact_localization: Path,
    session_marginal_result: Path,
) -> dict[str, Any]:
    return {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "packing_sha256": _sha256_file(Path(pack_context.__code__.co_filename)),
        "baseline_registration_sha256": _sha256_file(baseline_registration),
        "baseline_capture_sha256": _sha256_file(baseline_capture),
        "baseline_identity_sha256": _sha256_file(baseline_identity),
        "methodology_review_sha256": _sha256_file(methodology_review),
        "compact_localization_sha256": _sha256_file(compact_localization),
        "session_marginal_result_sha256": _sha256_file(session_marginal_result),
    }


def create_registration(
    *,
    dataset_path: Path,
    baseline_registration: Path,
    baseline_capture: Path,
    baseline_identity: Path,
    methodology_review: Path,
    compact_localization: Path,
    session_marginal_result: Path,
    project_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("registration output already exists")
    cases = _load_dataset(dataset_path)
    revision = _git_revision(project_root)
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-grouped-conversations-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Full observed-cohort source trial of named conversation grouping; "
            "not answer accuracy, an independent holdout, or a Zep comparison."
        ),
        "source": _source(
            revision=revision,
            baseline_registration=baseline_registration,
            baseline_capture=baseline_capture,
            baseline_identity=baseline_identity,
            methodology_review=methodology_review,
            compact_localization=compact_localization,
            session_marginal_result=session_marginal_result,
        ),
        "dataset": _dataset_identity(dataset_path, cases),
        "protocol": _protocol(),
        "evaluation": _gates(),
        "limitations": [
            "All 500 questions are already inspected development evidence.",
            "The direct-turn benchmark stores exact role and turn index metadata.",
            "Source retention and token savings cannot establish answer improvement.",
            "A successful answer trial still requires confirmation on another workload.",
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
    baseline_identity: Path,
    methodology_review: Path,
    compact_localization: Path,
    session_marginal_result: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    cases = _load_dataset(dataset_path)
    revision = registration.get("source", {}).get("prme_revision")
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "longmemeval-s-grouped-conversations-registration"
        or not isinstance(revision, str)
        or registration.get("source")
        != _source(
            revision=revision,
            baseline_registration=baseline_registration,
            baseline_capture=baseline_capture,
            baseline_identity=baseline_identity,
            methodology_review=methodology_review,
            compact_localization=compact_localization,
            session_marginal_result=session_marginal_result,
        )
        or registration.get("dataset") != _dataset_identity(dataset_path, cases)
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _gates()
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered grouped-conversation inputs differ")
    by_id = {case["question_id"]: case for case in cases}
    return [by_id[value] for value in registration["dataset"]["question_ids"]]


def _node_ids(bundle: Any) -> list[str]:
    return [
        str(candidate.node.id)
        for values in bundle.sections.values()
        for candidate in values
    ]


def _representation_map(bundle: Any) -> dict[str, str]:
    return {
        str(candidate.node.id): candidate.representation.value
        for values in bundle.sections.values()
        for candidate in values
        if candidate.representation is not None
    }


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
            raise ValueError(f"saved grouped-conversation case differs for {question_id}")
        return saved

    baseline = json.loads(
        (baseline_root / "captures" / f"{question_id}.json").read_text()
    )
    with tempfile.TemporaryDirectory(prefix="prme-lme-grouped-") as temporary:
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
            candidate_scores = [candidate.composite_score for candidate in response.results]
            if (
                response.bundle.render() != baseline["context"]
                or candidate_ids != [row["node_id"] for row in baseline["returned"]]
                or candidate_scores
                != [row["composite_score"] for row in baseline["returned"]]
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
            same_set = pack_context_grouped_conversations(
                response.results,
                config.packing,
                coverage_notice=response.bundle.coverage_notice,
                context_guidance=guidance,
            )
            fill = pack_context_grouped_conversations(
                response.results,
                config.packing,
                coverage_notice=response.bundle.coverage_notice,
                context_guidance=guidance,
                admit_additional=True,
            )
            bundles = {
                "control": control,
                "grouped_same_set": same_set,
                "grouped_fill": fill,
            }
            arms: dict[str, dict[str, Any]] = {}
            for name, bundle in bundles.items():
                context = bundle.render()
                sources = _bundle_sources(bundle)
                arms[name] = {
                    "context": context,
                    "context_sha256": _sha256(context.encode()),
                    "tokens": bundle.tokens_used,
                    "records": bundle.included_count,
                    "node_ids": _node_ids(bundle),
                    "representations": _representation_map(bundle),
                    "guidance_included": bundle.context_guidance is not None,
                    "conversation_groups": context.count('\"conversation\":'),
                    "evidence": _evidence(case, sources),
                }
        finally:
            await engine.close()

    control_ids = set(arms["control"]["node_ids"])
    losses = {
        name: sorted(control_ids - set(arms[name]["node_ids"]))
        for name in ARMS[1:]
    }
    guidance_losses = {
        name: (
            arms["control"]["guidance_included"]
            and not arms[name]["guidance_included"]
        )
        for name in ARMS[1:]
    }
    same_set_identity_matches = (
        arms["grouped_same_set"]["node_ids"] == arms["control"]["node_ids"]
        and arms["grouped_same_set"]["representations"]
        == arms["control"]["representations"]
    )
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-grouped-conversations-case",
        "registration_sha256": registration_sha256,
        "question_id": question_id,
        "question_type": (
            "abstention" if question_id.endswith("_abs") else case["question_type"]
        ),
        "clone_method": clone_method,
        "candidate_count": len(candidate_ids),
        "candidate_ids_sha256": _sha256(_canonical(candidate_ids)),
        "candidate_scores_sha256": _sha256(_canonical(candidate_scores)),
        "control_record_losses": losses,
        "control_guidance_losses": guidance_losses,
        "same_set_identity_matches": same_set_identity_matches,
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
    tokens = [int(row["arms"][arm]["tokens"]) for row in rows]
    records = [int(row["arms"][arm]["records"]) for row in rows]
    groups = [int(row["arms"][arm]["conversation_groups"]) for row in rows]
    return {
        "questions": len(rows),
        "evidence_questions": len(applicable),
        "mean_session_recall": statistics.mean(
            item["session_recall"] for item in applicable
        ),
        "complete_session_questions": sum(
            item["complete_session_recall"] for item in applicable
        ),
        "mean_turn_recall": statistics.mean(
            item["turn_recall"] for item in applicable
        ),
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
        "conversation_groups": {
            "questions": sum(value > 0 for value in groups),
            "mean": statistics.mean(groups),
            "max": max(groups),
        },
    }


def _comparison(
    rows: list[dict[str, Any]], candidate: str
) -> dict[str, Any]:
    applicable = [
        row for row in rows if row["arms"]["control"]["evidence"]["applicable"]
    ]

    def delta(row: dict[str, Any], field: str) -> float:
        return (
            row["arms"][candidate]["evidence"][field]
            - row["arms"]["control"]["evidence"][field]
        )

    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in applicable:
        categories[row["question_type"]].append(row)
    category_results = {
        name: {
            "questions": len(values),
            "control_mean_turn_recall": statistics.mean(
                row["arms"]["control"]["evidence"]["turn_recall"]
                for row in values
            ),
            "candidate_mean_turn_recall": statistics.mean(
                row["arms"][candidate]["evidence"]["turn_recall"]
                for row in values
            ),
        }
        for name, values in sorted(categories.items())
    }
    return {
        "turn_recall_wins": sum(delta(row, "turn_recall") > 0 for row in applicable),
        "turn_recall_losses": sum(
            delta(row, "turn_recall") < 0 for row in applicable
        ),
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
        "token_savings": {
            "mean": statistics.mean(
                row["arms"]["control"]["tokens"]
                - row["arms"][candidate]["tokens"]
                for row in rows
            ),
            "questions": sum(
                row["arms"][candidate]["tokens"]
                < row["arms"]["control"]["tokens"]
                for row in rows
            ),
        },
        "categories": category_results,
    }


def _summary(
    rows: list[dict[str, Any]], registration_path: Path
) -> dict[str, Any]:
    summaries = {arm: _arm_summary(rows, arm) for arm in ARMS}
    comparisons = {
        arm: _comparison(rows, arm) for arm in ("grouped_same_set", "grouped_fill")
    }
    fill_comparison = comparisons["grouped_fill"]
    category_losses = sum(
        value["candidate_mean_turn_recall"] < value["control_mean_turn_recall"]
        for value in fill_comparison["categories"].values()
    )
    gate = {
        "replay_failures": 0,
        "budget_violations": sum(
            row["arms"][arm]["tokens"] > 3996
            for row in rows
            for arm in ARMS
        ),
        "control_record_loss_questions": sum(
            any(row["control_record_losses"][arm] for arm in ARMS[1:])
            for row in rows
        ),
        "control_guidance_loss_questions": sum(
            any(row["control_guidance_losses"][arm] for arm in ARMS[1:])
            for row in rows
        ),
        "same_set_identity_mismatch_questions": sum(
            not row["same_set_identity_matches"] for row in rows
        ),
        "grouped_rendering_questions": summaries["grouped_same_set"][
            "conversation_groups"
        ]["questions"],
        "fill_per_question_turn_recall_losses": fill_comparison[
            "turn_recall_losses"
        ],
        "fill_per_question_session_recall_losses": fill_comparison[
            "session_recall_losses"
        ],
        "fill_category_mean_turn_recall_losses": category_losses,
        "fill_complete_turn_questions_improved": (
            summaries["grouped_fill"]["complete_turn_questions"]
            > summaries["control"]["complete_turn_questions"]
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
                "same_set_identity_mismatch_questions",
                "fill_per_question_turn_recall_losses",
                "fill_per_question_session_recall_losses",
                "fill_category_mean_turn_recall_losses",
            )
        )
        and gate["grouped_rendering_questions"] == len(rows)
        and gate["fill_complete_turn_questions_improved"]
    )
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-grouped-conversations-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": _sha256_file(registration_path),
        "cohort": "full-development",
        "questions": len(rows),
        "arms": summaries,
        "comparisons": comparisons,
        "gate": gate,
        "decision": (
            "advance_to_paired_answer_development"
            if gate["passed"]
            else "reject_source_composition"
        ),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "workers": WORKERS,
        },
        "case_manifest_sha256": _sha256(
            _canonical(
                [[row["question_id"], row["case_sha256"]] for row in rows]
            )
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
    baseline_identity: Path,
    methodology_review: Path,
    compact_localization: Path,
    session_marginal_result: Path,
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
        baseline_identity=baseline_identity,
        methodology_review=methodology_review,
        compact_localization=compact_localization,
        session_marginal_result=session_marginal_result,
        project_root=project_root,
    )
    registration_sha256 = _sha256_file(registration_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    identity_path = output_dir / "identity.json"
    identity = {"registration_sha256": registration_sha256, "questions": len(cases)}
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("output directory belongs to another trial")
    _write(identity_path, identity)
    semaphore = asyncio.Semaphore(WORKERS)
    completed = 0

    async def run_one(case: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed
        async with semaphore:
            value = await _evaluate_case(
                case,
                registration_sha256=registration_sha256,
                baseline_root=baseline_root,
                output_dir=output_dir,
            )
        completed += 1
        if completed % 10 == 0 or completed == len(cases):
            print(
                f"[longmemeval-s-grouped] completed {completed}/{len(cases)}",
                flush=True,
            )
        return value

    values = await asyncio.gather(
        *(run_one(case) for case in cases), return_exceptions=True
    )
    errors = [value for value in values if isinstance(value, BaseException)]
    if errors:
        raise RuntimeError(
            f"grouped-conversation packing failed for {len(errors)} case(s)"
        ) from errors[0]
    rows = [value for value in values if isinstance(value, dict)]
    order = registration["dataset"]["question_ids"]
    index = {question_id: position for position, question_id in enumerate(order)}
    rows.sort(key=lambda item: index[item["question_id"]])
    result = _summary(rows, registration_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    run = commands.add_parser("evaluate")
    for command in (register, run):
        command.add_argument("--dataset", type=Path, required=True)
        command.add_argument("--baseline-registration", type=Path, required=True)
        command.add_argument("--baseline-capture", type=Path, required=True)
        command.add_argument("--baseline-identity", type=Path, required=True)
        command.add_argument("--methodology-review", type=Path, required=True)
        command.add_argument("--compact-localization", type=Path, required=True)
        command.add_argument("--session-marginal-result", type=Path, required=True)
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
        "baseline_identity": args.baseline_identity.resolve(),
        "methodology_review": args.methodology_review.resolve(),
        "compact_localization": args.compact_localization.resolve(),
        "session_marginal_result": args.session_marginal_result.resolve(),
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
    print(
        json.dumps(
            {
                "decision": value["decision"],
                "gate": value["gate"],
                "result_sha256": value["result_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
