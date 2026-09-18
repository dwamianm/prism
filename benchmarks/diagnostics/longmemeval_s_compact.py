"""Registered fixed-candidate LongMemEval-S context-format comparison.

The runner reopens clone-on-write copies of the captured PRME packs, proves the
current product path reproduces each frozen baseline context, then repacks the
same scored candidates with the public compact renderer. Dataset labels are
used only after both contexts are complete.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tempfile
from typing import Any

from benchmarks.evidence import select_questions
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    USER_ID,
    _load_dataset,
    _pack_config,
    _parse_date,
)
from prme import MemoryEngine
from prme.retrieval.context_formatter import build_context_guidance
from prme.retrieval.packing import pack_context
from prme.retrieval.query_analysis import analyze_query


SPLIT_SEED = "prme-evidence-v1"
WORKERS = 5
PILOT_IDS = (
    "1b9b7252",
    "gpt4_8279ba02",
    "e982271f",
    "ef66a6e5",
    "6aeb4375",
    "6aeb4375_abs",
    "5d3d2817",
    "d905b33f",
    "e56a43b9",
    "d23cf73b",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(_canonical(value) + b"\n")
    os.replace(temporary, path)


def _git_revision(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_changes_since(revision: str, root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{revision}..HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _dataset_identity(
    path: Path, cases: list[dict[str, Any]], split: str
) -> dict[str, Any]:
    if _sha256_file(path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    selected = select_questions(cases, split=split, seed=SPLIT_SEED)
    return {
        "name": "LongMemEval-S cleaned",
        "sha256": DATASET_SHA256,
        "split": split,
        "split_seed": SPLIT_SEED,
        "questions": len(selected),
        "question_ids": [case["question_id"] for case in selected],
        "question_ids_sha256": _sha256(
            _canonical([case["question_id"] for case in selected])
        ),
    }


def _protocol() -> dict[str, Any]:
    return {
        "input": (
            "frozen unchanged-PRME packs and scored candidates from the complete "
            "LongMemEval-S baseline capture"
        ),
        "control": {
            "multipath_ordering": "balanced",
            "context_format": "auditable",
            "token_budget": 4096,
        },
        "candidate": {
            "multipath_ordering": "balanced",
            "context_format": "compact",
            "token_budget": 4096,
        },
        "invariants": [
            "question, pack, candidate IDs, scores and order are identical",
            "current product retrieval must reproduce the frozen auditable context byte for byte",
            "only PackingConfig.context_format changes",
            "both arms use the same exact tokenizer and whole-output budget",
            "labels are unavailable until both contexts are finalized",
            "all split questions remain in the denominator",
        ],
    }


def _gates(split: str) -> dict[str, Any]:
    return {
        "replay_failures": 0,
        "budget_violations": 0,
        "per_question_turn_recall_losses": 0,
        "per_question_session_recall_losses": 0,
        "category_mean_turn_recall_losses": 0,
        "complete_turn_questions": "candidate > control"
        if split == "dev"
        else "candidate >= control",
        "decision": (
            "A passing development result may register the frozen test split. "
            "It cannot change the default without answer-quality evidence and "
            "a separate workload confirmation."
        ),
    }


def _development_result_identity(path: Path) -> str:
    result = json.loads(path.read_text())
    recorded_checksum = result.get("result_sha256")
    computed_checksum = _sha256(
        _canonical(
            {key: value for key, value in result.items() if key != "result_sha256"}
        )
    )
    if (
        result.get("kind") != "longmemeval-s-compact-packing-result"
        or result.get("split") != "dev"
        or result.get("gate", {}).get("passed") is not True
        or not isinstance(result.get("registration_sha256"), str)
        or len(result["registration_sha256"]) != 64
        or recorded_checksum != computed_checksum
    ):
        raise ValueError("development result is not a valid passing result")
    return _sha256_file(path)


def create_registration(
    *,
    dataset_path: Path,
    split: str,
    baseline_registration: Path,
    baseline_capture: Path,
    baseline_manifest: Path,
    development_result: Path | None,
    project_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("registration output already exists")
    cases = _load_dataset(dataset_path)
    source = {
        "prme_revision": _git_revision(project_root),
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "baseline_registration_sha256": _sha256_file(baseline_registration),
        "baseline_capture_sha256": _sha256_file(baseline_capture),
        "baseline_manifest_sha256": _sha256_file(baseline_manifest),
    }
    if split == "test":
        if development_result is None:
            raise ValueError("test registration requires a passing development result")
        source["development_result_sha256"] = _development_result_identity(
            development_result
        )
    elif development_result is not None:
        raise ValueError("development registration cannot bind a development result")
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-compact-packing-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Fixed-candidate source-retention comparison of PRME's public "
            "auditable and compact renderers; not answer accuracy or a Zep score."
        ),
        "source": source,
        "dataset": _dataset_identity(dataset_path, cases, split),
        "protocol": _protocol(),
        "evaluation": _gates(split),
        "pilot_disclosure": {
            "question_ids": list(PILOT_IDS),
            "arms_examined": [
                "balanced/auditable",
                "balanced/compact",
                "score/compact",
                "density/compact",
                "episode top-k 1,2,4,8 with compact rendering",
            ],
            "selection": (
                "Balanced/compact was the only examined arm with no labeled-turn "
                "loss in the ten-question pilot and is the sole candidate arm."
            ),
        },
        "limitations": [
            "The development and test partitions have appeared in earlier PRME studies and are not independent benchmark holdouts.",
            "Compact rendering changes serialization overhead, not retrieval scores or memory representation.",
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
    development_result: Path | None,
    project_root: Path,
) -> list[dict[str, Any]]:
    cases = _load_dataset(dataset_path)
    split = registration.get("dataset", {}).get("split")
    revision = registration.get("source", {}).get("prme_revision")
    expected_source = {
        "prme_revision": revision,
        "runner_sha256": _sha256_file(Path(__file__).resolve()),
        "baseline_registration_sha256": _sha256_file(baseline_registration),
        "baseline_capture_sha256": _sha256_file(baseline_capture),
        "baseline_manifest_sha256": _sha256_file(baseline_manifest),
    }
    if split == "test":
        if development_result is None:
            raise ValueError("test evaluation requires its development result")
        expected_source["development_result_sha256"] = _development_result_identity(
            development_result
        )
    elif development_result is not None:
        raise ValueError("development evaluation cannot bind a development result")
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "longmemeval-s-compact-packing-registration"
        or split not in {"dev", "test"}
        or not isinstance(revision, str)
        or registration.get("source") != expected_source
        or registration.get("dataset") != _dataset_identity(dataset_path, cases, split)
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _gates(split)
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
    ):
        raise ValueError("registered compact-packing inputs differ")
    selected_by_id = {case["question_id"]: case for case in cases}
    return [
        selected_by_id[question_id]
        for question_id in registration["dataset"]["question_ids"]
    ]


def _clone_pack(source: Path, destination: Path) -> str:
    try:
        subprocess.run(
            ["cp", "-cR", str(source), str(destination)],
            check=True,
            capture_output=True,
        )
        return "clonefile"
    except subprocess.CalledProcessError:
        shutil.copytree(source, destination)
        return "copytree"


def _bundle_sources(bundle: Any) -> list[dict[str, Any]]:
    rows = []
    for values in bundle.sections.values():
        for candidate in values:
            metadata = candidate.node.metadata or {}
            rows.append(
                {
                    "node_id": str(candidate.node.id),
                    "source_session_id": metadata.get("source_session_id"),
                    "source_session_position": metadata.get("source_session_position"),
                    "source_turn_index": metadata.get("source_turn_index"),
                }
            )
    return rows


def _evidence(case: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    if case["question_id"].endswith("_abs"):
        return {"applicable": False}
    wanted_sessions = set(case["answer_session_ids"])
    wanted_turns = {
        (session_id, session_position, turn_index)
        for session_position, (session_id, session) in enumerate(
            zip(case["haystack_session_ids"], case["haystack_sessions"])
        )
        for turn_index, turn in enumerate(session)
        if turn.get("has_answer") is True
    }
    packed_sessions = {row["source_session_id"] for row in rows}
    packed_turns = {
        (
            row["source_session_id"],
            row["source_session_position"],
            row["source_turn_index"],
        )
        for row in rows
    }
    session_hits = wanted_sessions & packed_sessions
    turn_hits = wanted_turns & packed_turns
    return {
        "applicable": True,
        "required_sessions": len(wanted_sessions),
        "retrieved_sessions": len(session_hits),
        "session_recall": len(session_hits) / len(wanted_sessions),
        "complete_session_recall": wanted_sessions <= packed_sessions,
        "required_turns": len(wanted_turns),
        "retrieved_turns": len(turn_hits),
        "turn_recall": len(turn_hits) / len(wanted_turns),
        "complete_turn_recall": wanted_turns <= packed_turns,
    }


def _case_checksum(value: dict[str, Any]) -> str:
    return _sha256(
        _canonical({key: item for key, item in value.items() if key != "case_sha256"})
    )


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
            raise ValueError(f"saved compact case differs for {question_id}")
        return saved

    baseline_capture = json.loads(
        (baseline_root / "captures" / f"{question_id}.json").read_text()
    )
    with tempfile.TemporaryDirectory(prefix="prme-lme-compact-") as temporary:
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
            reproduced = pack_context(
                response.results,
                config.packing,
                coverage_notice=response.bundle.coverage_notice,
                context_guidance=guidance,
            )
            if reproduced.render() != response.bundle.render():
                raise ValueError(f"baseline repack differs for {question_id}")
            compact_config = config.packing.model_copy(
                update={"context_format": "compact"}
            )
            compact = pack_context(
                response.results,
                compact_config,
                coverage_notice=response.bundle.coverage_notice,
                context_guidance=guidance,
            )
            arms = {}
            for name, bundle in (("control", reproduced), ("compact", compact)):
                context = bundle.render()
                rows = _bundle_sources(bundle)
                arms[name] = {
                    "context": context,
                    "context_sha256": _sha256(context.encode()),
                    "tokens": bundle.tokens_used,
                    "records": bundle.included_count,
                    "node_ids": [row["node_id"] for row in rows],
                    "evidence": _evidence(case, rows),
                }
        finally:
            await engine.close()

    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-compact-packing-case",
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
            row["arms"]["compact"]["evidence"][field]
            - row["arms"]["control"]["evidence"][field]
        )

    category_results = {}
    for name, values in sorted(categories.items()):
        category_results[name] = {
            "questions": len(values),
            "control_mean_turn_recall": statistics.mean(
                row["arms"]["control"]["evidence"]["turn_recall"] for row in values
            ),
            "compact_mean_turn_recall": statistics.mean(
                row["arms"]["compact"]["evidence"]["turn_recall"] for row in values
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


def _summary(
    rows: list[dict[str, Any]], registration: dict[str, Any]
) -> dict[str, Any]:
    control = _arm_summary(rows, "control")
    compact = _arm_summary(rows, "compact")
    comparison = _comparison(rows)
    category_losses = sum(
        value["compact_mean_turn_recall"] < value["control_mean_turn_recall"]
        for value in comparison["categories"].values()
    )
    gate = {
        "replay_failures": 0,
        "budget_violations": sum(
            row["arms"][arm]["tokens"] > 3996
            for row in rows
            for arm in ("control", "compact")
        ),
        "per_question_turn_recall_losses": comparison["turn_recall_losses"],
        "per_question_session_recall_losses": comparison["session_recall_losses"],
        "category_mean_turn_recall_losses": category_losses,
        "complete_turn_questions_improved": (
            compact["complete_turn_questions"] > control["complete_turn_questions"]
        ),
    }
    split = registration["dataset"]["split"]
    gate["passed"] = (
        gate["replay_failures"] == 0
        and gate["budget_violations"] == 0
        and gate["per_question_turn_recall_losses"] == 0
        and gate["per_question_session_recall_losses"] == 0
        and gate["category_mean_turn_recall_losses"] == 0
        and (
            gate["complete_turn_questions_improved"]
            if split == "dev"
            else compact["complete_turn_questions"]
            >= control["complete_turn_questions"]
        )
    )
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-compact-packing-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": _sha256_file(Path(registration["_path"])),
        "split": split,
        "arms": {"control": control, "compact": compact},
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
    development_result: Path | None,
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
        development_result=development_result,
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
                f"[longmemeval-s-compact] completed {completed}/{len(cases)}",
                flush=True,
            )
        return result

    values = await asyncio.gather(
        *(run_one(case) for case in cases), return_exceptions=True
    )
    errors = [value for value in values if isinstance(value, BaseException)]
    if errors:
        raise RuntimeError(
            f"compact packing failed for {len(errors)} case(s)"
        ) from errors[0]
    rows = [value for value in values if isinstance(value, dict)]
    rows.sort(
        key=lambda item: registration["dataset"]["question_ids"].index(
            item["question_id"]
        )
    )
    registration["_path"] = str(registration_path)
    result = _summary(rows, registration)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--dataset", type=Path, required=True)
    register.add_argument("--split", choices=("dev", "test"), required=True)
    register.add_argument("--baseline-registration", type=Path, required=True)
    register.add_argument("--baseline-capture", type=Path, required=True)
    register.add_argument("--baseline-manifest", type=Path, required=True)
    register.add_argument("--development-result", type=Path)
    register.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("evaluate")
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--baseline-registration", type=Path, required=True)
    run.add_argument("--baseline-capture", type=Path, required=True)
    run.add_argument("--baseline-manifest", type=Path, required=True)
    run.add_argument("--development-result", type=Path)
    run.add_argument("--baseline-root", type=Path, required=True)
    run.add_argument("--project-root", type=Path, default=Path.cwd())
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
        "development_result": (
            args.development_result.resolve() if args.development_result else None
        ),
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        value = create_registration(
            split=args.split,
            output_path=args.output.resolve(),
            **shared,
        )
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
            {key: value[key] for key in ("split", "gate", "result_sha256")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
