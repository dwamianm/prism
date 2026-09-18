"""Evaluate deterministic episode composition on frozen LongMemEval-S packs.

This is a full-development-cohort source-retention assay.  It compares the
current product packer with a fixed grid over PRME's existing candidate-backed
episode router.  It does not call a reader or judge and cannot promote a
configuration without a later answer-quality trial and untouched confirmation.
"""

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
from prme.retrieval.episode_context import expand_episode_context
from prme.retrieval.packing import pack_context
from prme.retrieval.query_analysis import analyze_query


WORKERS = 5
ARM_CONFIGS: dict[str, dict[str, int | float]] = {
    "control": {
        "episode_context_top_k": 0,
        "episode_context_local_k": 8,
        "episode_context_score_decay": 0.95,
    },
    "episode_1x4": {
        "episode_context_top_k": 1,
        "episode_context_local_k": 4,
        "episode_context_score_decay": 0.95,
    },
    "episode_2x4": {
        "episode_context_top_k": 2,
        "episode_context_local_k": 4,
        "episode_context_score_decay": 0.95,
    },
    "episode_3x4": {
        "episode_context_top_k": 3,
        "episode_context_local_k": 4,
        "episode_context_score_decay": 0.95,
    },
    "episode_2x8": {
        "episode_context_top_k": 2,
        "episode_context_local_k": 8,
        "episode_context_score_decay": 0.95,
    },
    "episode_3x8": {
        "episode_context_top_k": 3,
        "episode_context_local_k": 8,
        "episode_context_score_decay": 0.95,
    },
}


def _dataset_identity(path: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    if _sha256_file(path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset differs")
    question_ids = [case["question_id"] for case in cases]
    if len(question_ids) != 500 or len(set(question_ids)) != 500:
        raise ValueError("LongMemEval-S question identity differs")
    return {
        "name": "LongMemEval-S cleaned",
        "sha256": DATASET_SHA256,
        "cohort": "full-development",
        "questions": 500,
        "question_ids": question_ids,
        "question_ids_sha256": _sha256(_canonical(question_ids)),
    }


def _protocol() -> dict[str, Any]:
    return {
        "input": "all 500 frozen unchanged-PRME packs and scored candidate sets",
        "control": {
            "multipath_ordering": "balanced",
            "context_format": "auditable",
            "token_budget": 4096,
            "episode_context_top_k": 0,
        },
        "candidate_grid": ARM_CONFIGS,
        "episode_boundary": "exact (scope, session_id) among authorized candidates",
        "episode_routing": "deterministic BM25 over complete candidate-backed episodes",
        "local_routing": "deterministic BM25 within selected episodes",
        "invariants": [
            "question, pack, candidate IDs, scores and order reproduce the frozen baseline",
            "all arms use the same candidates, tokenizer, serializer and whole-output budget",
            "the router sees no references, evidence labels, answers or question types",
            "labels are read only after every arm context is finalized",
            "all 500 questions remain in the denominator",
        ],
    }


def _gates() -> dict[str, Any]:
    return {
        "per_arm": {
            "replay_failures": 0,
            "budget_violations": 0,
            "per_question_turn_recall_losses": 0,
            "per_question_session_recall_losses": 0,
            "category_mean_turn_recall_losses": 0,
            "complete_turn_questions": "candidate > control",
        },
        "selection": (
            "Among passing candidates, maximize complete-turn questions, mean turn "
            "recall, complete-session questions and mean session recall in that "
            "order; then minimize changed contexts and use registration order."
        ),
        "decision": (
            "A selected arm advances only to paired answer-quality development. "
            "No source result changes a public default or establishes Zep parity."
        ),
    }


def create_registration(
    *,
    dataset_path: Path,
    baseline_registration: Path,
    baseline_capture: Path,
    baseline_identity: Path,
    methodology_review: Path,
    project_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("registration output already exists")
    cases = _load_dataset(dataset_path)
    registration = {
        "schema_version": 1,
        "kind": "longmemeval-s-episode-composition-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "registered_before_episode_grid_evaluation",
        "claim_boundary": (
            "Full-development source-retention assay for existing deterministic "
            "episode composition; not answer accuracy, an independent holdout, a "
            "multi-scope representation test, or a Zep comparison."
        ),
        "source": {
            "prme_revision": _git_revision(project_root),
            "runner_sha256": _sha256_file(Path(__file__).resolve()),
            "baseline_registration_sha256": _sha256_file(baseline_registration),
            "baseline_capture_sha256": _sha256_file(baseline_capture),
            "baseline_identity_sha256": _sha256_file(baseline_identity),
            "methodology_review_sha256": _sha256_file(methodology_review),
        },
        "dataset": _dataset_identity(dataset_path, cases),
        "protocol": _protocol(),
        "evaluation": _gates(),
        "limitations": [
            "All 500 labels have been inspected and constitute development evidence.",
            "Direct-turn FACT ingestion provides one representation rather than Zep's five write-time scopes.",
            "Gold labels score finalized contexts but never route or pack candidates.",
            "Source retention is necessary but does not imply answer improvement.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any],
    *,
    dataset_path: Path,
    baseline_registration: Path,
    baseline_capture: Path,
    baseline_identity: Path,
    methodology_review: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    cases = _load_dataset(dataset_path)
    revision = registration.get("source", {}).get("prme_revision")
    source = {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "baseline_registration_sha256": _sha256_file(baseline_registration),
        "baseline_capture_sha256": _sha256_file(baseline_capture),
        "baseline_identity_sha256": _sha256_file(baseline_identity),
        "methodology_review_sha256": _sha256_file(methodology_review),
    }
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "longmemeval-s-episode-composition-registration"
        or registration.get("status")
        != "registered_before_episode_grid_evaluation"
        or not isinstance(revision, str)
        or registration.get("source") != source
        or registration.get("dataset") != _dataset_identity(dataset_path, cases)
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _gates()
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered episode-composition inputs differ")
    by_id = {case["question_id"]: case for case in cases}
    return [by_id[question_id] for question_id in registration["dataset"]["question_ids"]]


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
        if (
            saved.get("registration_sha256") != registration_sha256
            or saved.get("case_sha256") != _case_checksum(saved)
        ):
            raise ValueError(f"saved episode case differs for {question_id}")
        return saved

    baseline = json.loads(
        (baseline_root / "captures" / f"{question_id}.json").read_text()
    )
    with tempfile.TemporaryDirectory(prefix="prme-lme-episode-") as temporary:
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
            arms: dict[str, Any] = {}
            for name, updates in ARM_CONFIGS.items():
                arm_config = config.packing.model_copy(update=updates)
                candidates = (
                    response.results
                    if name == "control"
                    else expand_episode_context(
                        response.results, case["question"], arm_config
                    )
                )
                bundle = pack_context(
                    candidates,
                    arm_config,
                    coverage_notice=response.bundle.coverage_notice,
                    context_guidance=guidance,
                )
                if name == "control" and bundle.render() != response.bundle.render():
                    raise ValueError(f"control repack differs for {question_id}")
                context = bundle.render()
                sources = _bundle_sources(bundle)
                arms[name] = {
                    "context": context,
                    "context_sha256": _sha256(context.encode()),
                    "tokens": bundle.tokens_used,
                    "records": bundle.included_count,
                    "node_ids": [row["node_id"] for row in sources],
                    "episode_records": sum(
                        "EPISODE_CONTEXT" in candidate.paths
                        for values in bundle.sections.values()
                        for candidate in values
                    ),
                    "evidence": _evidence(case, sources),
                }
        finally:
            await engine.close()

    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-episode-composition-case",
        "registration_sha256": registration_sha256,
        "question_id": question_id,
        "question_type": (
            "abstention" if question_id.endswith("_abs") else case["question_type"]
        ),
        "clone_method": clone_method,
        "candidate_count": len(candidate_ids),
        "candidate_ids_sha256": _sha256(_canonical(candidate_ids)),
        "candidate_scores_sha256": _sha256(_canonical(candidate_scores)),
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
        "episode_records": sum(row["arms"][arm]["episode_records"] for row in rows),
    }


def _comparison(
    rows: list[dict[str, Any]], arm: str
) -> dict[str, Any]:
    applicable = [
        row for row in rows if row["arms"]["control"]["evidence"]["applicable"]
    ]

    def delta(row: dict[str, Any], field: str) -> float:
        return (
            row["arms"][arm]["evidence"][field]
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
                row["arms"][arm]["evidence"]["turn_recall"] for row in values
            ),
        }
        for name, values in sorted(categories.items())
    }
    return {
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
        "changed_contexts": sum(
            row["arms"][arm]["context_sha256"]
            != row["arms"]["control"]["context_sha256"]
            for row in rows
        ),
        "categories": category_results,
    }


def _candidate_gate(
    *,
    control: dict[str, Any],
    candidate: dict[str, Any],
    comparison: dict[str, Any],
    rows: list[dict[str, Any]],
    arm: str,
) -> dict[str, Any]:
    category_losses = sum(
        value["candidate_mean_turn_recall"] < value["control_mean_turn_recall"]
        for value in comparison["categories"].values()
    )
    gate = {
        "budget_violations": sum(
            row["arms"][arm]["tokens"] > 3996 for row in rows
        ),
        "per_question_turn_recall_losses": comparison["turn_recall_losses"],
        "per_question_session_recall_losses": comparison["session_recall_losses"],
        "category_mean_turn_recall_losses": category_losses,
        "complete_turn_questions_improved": (
            candidate["complete_turn_questions"] > control["complete_turn_questions"]
        ),
    }
    gate["passed"] = (
        gate["budget_violations"] == 0
        and gate["per_question_turn_recall_losses"] == 0
        and gate["per_question_session_recall_losses"] == 0
        and gate["category_mean_turn_recall_losses"] == 0
        and gate["complete_turn_questions_improved"]
    )
    return gate


def _select_candidate(results: dict[str, dict[str, Any]]) -> str | None:
    eligible = [
        name
        for name in ARM_CONFIGS
        if name != "control" and results[name]["gate"]["passed"]
    ]
    if not eligible:
        return None
    order = {name: index for index, name in enumerate(ARM_CONFIGS)}
    return min(
        eligible,
        key=lambda name: (
            -results[name]["summary"]["complete_turn_questions"],
            -results[name]["summary"]["mean_turn_recall"],
            -results[name]["summary"]["complete_session_questions"],
            -results[name]["summary"]["mean_session_recall"],
            results[name]["comparison"]["changed_contexts"],
            order[name],
        ),
    )


def _summary(
    rows: list[dict[str, Any]], registration_path: Path
) -> dict[str, Any]:
    control = _arm_summary(rows, "control")
    arms: dict[str, dict[str, Any]] = {
        "control": {"config": ARM_CONFIGS["control"], "summary": control}
    }
    for name in list(ARM_CONFIGS)[1:]:
        candidate = _arm_summary(rows, name)
        comparison = _comparison(rows, name)
        arms[name] = {
            "config": ARM_CONFIGS[name],
            "summary": candidate,
            "comparison": comparison,
            "gate": _candidate_gate(
                control=control,
                candidate=candidate,
                comparison=comparison,
                rows=rows,
                arm=name,
            ),
        }
    selected = _select_candidate(arms)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-episode-composition-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": _sha256_file(registration_path),
        "cohort": "full-development",
        "questions": len(rows),
        "replay_failures": 0,
        "arms": arms,
        "selected_candidate": selected,
        "decision": (
            "advance_to_answer_development" if selected else "reject_grid"
        ),
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
    baseline_identity: Path,
    methodology_review: Path,
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
        project_root=project_root,
    )
    registration_sha256 = _sha256_file(registration_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    identity = {"registration_sha256": registration_sha256, "questions": 500}
    identity_path = output_dir / "identity.json"
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
                f"[longmemeval-s-episode] completed {completed}/{len(cases)}",
                flush=True,
            )
        return value

    values = await asyncio.gather(
        *(run_one(case) for case in cases), return_exceptions=True
    )
    errors = [value for value in values if isinstance(value, BaseException)]
    if errors:
        raise RuntimeError(
            f"episode composition failed for {len(errors)} case(s)"
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
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        registration = create_registration(
            output_path=args.output.resolve(), **shared
        )
        print(
            json.dumps(
                {
                    "dataset": registration["dataset"],
                    "protocol": registration["protocol"],
                    "evaluation": registration["evaluation"],
                },
                indent=2,
            )
        )
        return
    result = asyncio.run(
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
                "selected_candidate": result["selected_candidate"],
                "decision": result["decision"],
                "result_sha256": result["result_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
