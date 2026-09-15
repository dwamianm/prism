"""Fail-closed validation for artifacts from the pinned official BEAM runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from benchmarks.integrations.beam_service import UPSTREAM_COMMIT


QUESTION_TYPES = (
    "abstention",
    "contradiction_resolution",
    "event_ordering",
    "information_extraction",
    "instruction_following",
    "knowledge_update",
    "multi_session_reasoning",
    "preference_following",
    "summarization",
    "temporal_reasoning",
)
CHAT_SIZES = ("100K", "500K", "1M", "10M")


def _registered_validation(
    report: dict[str, Any],
    *,
    registration_path: Path,
    execution_root: Path,
    chat_sizes: tuple[str, ...],
    conversations: tuple[int, ...],
    question_types: tuple[str, ...],
    scored: bool,
    cutoffs: tuple[int, ...],
) -> None:
    from benchmarks.integrations.run_beam import (
        DATASET_FILENAME,
        MANIFEST_FILENAME,
        PREDICT_EXECUTION_KIND,
        PREDICT_REGISTRATION_KIND,
        SCORED_EXECUTION_KIND,
        SCORED_EXECUTION_KIND_V3,
        SCORED_REGISTRATION_KIND,
        SCORED_REGISTRATION_KIND_V3,
        _protocol,
    )

    errors = report["errors"]
    registration = _read(registration_path, errors)
    manifest_path = execution_root / MANIFEST_FILENAME
    manifest = _read(manifest_path, errors)
    adapter_manifest_path = execution_root / "prme-pack" / "beam_adapter_manifest.json"
    adapter_manifest = _read(adapter_manifest_path, errors)
    dataset_path = execution_root / "dataset" / DATASET_FILENAME
    if registration is None or manifest is None:
        return
    registration_schema = registration.get("schema_version")
    registration_kind = registration.get("kind")
    supported_registration = (
        (registration_schema, registration_kind)
        in {
            (1, PREDICT_REGISTRATION_KIND),
            (2, SCORED_REGISTRATION_KIND),
            (3, SCORED_REGISTRATION_KIND_V3),
        }
    )
    if not supported_registration:
        errors.append("unsupported BEAM registration schema")
    registered_protocol = registration.get("protocol")
    try:
        _protocol(registration)
    except RuntimeError as exc:
        errors.append(str(exc))
    if not isinstance(registered_protocol, dict):
        registered_protocol = {}
    expected_selection = {
        "chat_sizes": list(chat_sizes),
        "conversations": list(conversations),
        "question_types": list(question_types),
        "scored": scored,
        "cutoffs": list(cutoffs) if scored else [],
    }
    if expected_selection != {
        "chat_sizes": registered_protocol.get("chat_sizes"),
        "conversations": registered_protocol.get("conversations"),
        "question_types": registered_protocol.get("question_types"),
        "scored": not registered_protocol.get("predict_only", False),
        "cutoffs": (
            registered_protocol.get("top_k_cutoffs", []) if scored else []
        ),
    }:
        errors.append("validation selection differs from registration")
    expected_execution_kind = PREDICT_EXECUTION_KIND
    if scored:
        expected_execution_kind = (
            SCORED_EXECUTION_KIND_V3
            if registration_schema == 3
            else SCORED_EXECUTION_KIND
        )
    if manifest.get("kind") != expected_execution_kind:
        errors.append("unexpected BEAM execution manifest kind")
    expected_manifest_schema = registration_schema if scored else 1
    if manifest.get("schema_version") != expected_manifest_schema:
        errors.append("unsupported BEAM execution manifest schema")
    if manifest.get("registration_sha256") != _hash(registration_path):
        errors.append("BEAM execution manifest does not match registration")
    if scored:
        if manifest.get("protocol") != registered_protocol:
            errors.append("executed BEAM protocol differs from registration")
        if manifest.get("models") != registration.get("models"):
            errors.append("executed BEAM models differ from registration")
    source = manifest.get("source")
    registered_source = registration.get("source")
    registered_files: dict[str, Any] = {}
    if not isinstance(source, dict) or not isinstance(registered_source, dict):
        errors.append("BEAM source identity is missing")
    else:
        registered_files = registered_source.get("files", {})
        if source.get("prme_revision") != registered_source.get("prme_revision"):
            errors.append("executed PRME revision differs from registration")
        if source.get("upstream_revision") != UPSTREAM_COMMIT:
            errors.append("executed BEAM revision differs from the supported pin")
        if source.get("upstream_revision") != registered_source.get("upstream_revision"):
            errors.append("executed BEAM revision differs from registration")
        if source.get("files") != registered_source.get("files"):
            errors.append("executed BEAM source hashes differ from registration")
        if source.get("prme_worktree_changes") != []:
            errors.append("registered BEAM used a modified PRME worktree")
        if source.get("upstream_worktree_changes") != []:
            errors.append("registered BEAM used a modified upstream worktree")
    if not dataset_path.is_file():
        errors.append("frozen BEAM dataset cache is missing")
    else:
        dataset_hash = _hash(dataset_path)
        if manifest.get("dataset_sha256") != dataset_hash:
            errors.append("executed BEAM dataset hash differs")
        registered_dataset = registration.get("dataset")
        if (
            not isinstance(registered_dataset, dict)
            or registered_dataset.get("cache_sha256") != dataset_hash
        ):
            errors.append("BEAM dataset differs from registration")
        report["artifact_sha256"][str(dataset_path.relative_to(execution_root))] = dataset_hash
    if adapter_manifest is None:
        return
    system = registration.get("system")
    if not isinstance(system, dict):
        errors.append("registered BEAM system identity is missing")
    else:
        profile = system.get("profile")
        if adapter_manifest.get("profile") != profile:
            errors.append("BEAM adapter did not use the registered profile")
        if adapter_manifest.get("upstream_commit") != UPSTREAM_COMMIT:
            errors.append("BEAM adapter upstream revision differs")
        registered_extraction = system.get("extraction")
        executed_extraction = adapter_manifest.get("extraction")
        if profile == "raw" and executed_extraction is not None:
            errors.append("raw BEAM adapter unexpectedly configured extraction")
        if profile == "extracted":
            if isinstance(registered_extraction, dict):
                registered_extraction = {
                    key: value
                    for key, value in registered_extraction.items()
                    if key != "model_digest"
                }
            if executed_extraction != registered_extraction:
                errors.append("BEAM extraction configuration differs from registration")
        if adapter_manifest.get("prme_version") != system.get("version"):
            errors.append("BEAM adapter PRME version differs from registration")
        if adapter_manifest.get("adapter_source_sha256") != registered_files.get(
            "service_sha256"
        ):
            errors.append("BEAM adapter source differs from registration")
        embedding = adapter_manifest.get("embedding")
        if embedding != system.get("embedding"):
            errors.append("BEAM embedding configuration differs from registration")
        if registration_schema == 3:
            for field in (
                "adapter_schema",
                "duckdb_threads",
                "scoring_version",
                "packing",
            ):
                if adapter_manifest.get(field) != system.get(field):
                    errors.append(f"BEAM {field} differs from registration")
    for path in (manifest_path, adapter_manifest_path):
        if path.is_file():
            report["artifact_sha256"][str(path.relative_to(execution_root))] = _hash(path)
    report["registration_sha256"] = _hash(registration_path)
    report["execution_root"] = str(execution_root.resolve())


def parse_indices(spec: str) -> tuple[int, ...]:
    indices: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError("conversation selection contains an empty item")
        if "-" in part:
            lower_text, upper_text = part.split("-", 1)
            lower, upper = int(lower_text), int(upper_text)
            if lower < 0 or upper < lower:
                raise ValueError("conversation ranges must be nonnegative and ascending")
            indices.update(range(lower, upper + 1))
        else:
            value = int(part)
            if value < 0:
                raise ValueError("conversation indices must be nonnegative")
            indices.add(value)
    if not indices:
        raise ValueError("at least one conversation is required")
    return tuple(sorted(indices))


def _read(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: invalid JSON ({type(exc).__name__})")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path.name}: root must be an object")
        return None
    return value


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_run(
    prediction_dir: Path,
    *,
    chat_sizes: tuple[str, ...],
    conversations: tuple[int, ...],
    question_types: tuple[str, ...] = QUESTION_TYPES,
    scored: bool = False,
    cutoffs: tuple[int, ...] = (100,),
) -> dict[str, Any]:
    errors: list[str] = []
    expected_ingestion = {
        f"_ingestion_{size}_{index}.json"
        for size in chat_sizes
        for index in conversations
    }
    actual_ingestion = {path.name for path in prediction_dir.glob("_ingestion_*.json")}
    missing_ingestion = sorted(expected_ingestion - actual_ingestion)
    unexpected_ingestion = sorted(actual_ingestion - expected_ingestion)
    if missing_ingestion:
        errors.append(f"missing ingestion checkpoints: {missing_ingestion}")
    if unexpected_ingestion:
        errors.append(f"unexpected ingestion checkpoints: {unexpected_ingestion}")
    progress = sorted(path.name for path in prediction_dir.glob("_progress_*.json"))
    if progress:
        errors.append(f"partial ingestion checkpoints remain: {progress}")

    owners: dict[tuple[str, int], str] = {}
    run_ids: set[str] = set()
    artifact_paths: list[Path] = []
    for name in sorted(expected_ingestion & actual_ingestion):
        path = prediction_dir / name
        artifact_paths.append(path)
        row = _read(path, errors)
        if row is None:
            continue
        size, index = row.get("chat_size"), row.get("conversation_idx")
        if size not in chat_sizes or index not in conversations:
            errors.append(f"{name}: checkpoint identity is outside the selection")
            continue
        if row.get("chunk_size") != 2:
            errors.append(f"{name}: chunk_size must equal the pinned value 2")
        if row.get("total_chunks_failed") != 0:
            errors.append(f"{name}: ingestion contains failed chunks")
        processed = row.get("total_chunks_processed")
        if not isinstance(processed, int) or isinstance(processed, bool) or processed <= 0:
            errors.append(f"{name}: total_chunks_processed must be positive")
        owner = row.get("user_id")
        if not isinstance(owner, str) or not owner:
            errors.append(f"{name}: user_id must be nonempty")
        else:
            owners[(size, index)] = owner
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            errors.append(f"{name}: run_id must be nonempty")
        else:
            run_ids.add(run_id)
    if len(run_ids) > 1:
        errors.append(f"ingestion checkpoints mix run IDs: {sorted(run_ids)}")

    question_paths = sorted(
        path
        for path in prediction_dir.glob("*.json")
        if not path.name.startswith("_")
    )
    expected_questions = len(chat_sizes) * len(conversations) * len(question_types) * 2
    if len(question_paths) != expected_questions:
        errors.append(
            f"question coverage is {len(question_paths)}/{expected_questions}"
        )

    question_ids: set[str] = set()
    category_counts = {question_type: 0 for question_type in question_types}
    expected_cutoffs = {f"top_{cutoff}" for cutoff in cutoffs}
    retrieval_counts: list[int] = []
    for path in question_paths:
        artifact_paths.append(path)
        row = _read(path, errors)
        if row is None:
            continue
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            errors.append(f"{path.name}: question_id must be nonempty")
        elif question_id in question_ids:
            errors.append(f"{path.name}: duplicate question_id {question_id}")
        else:
            question_ids.add(question_id)
        if path.stem != question_id:
            errors.append(f"{path.name}: filename does not match question_id")

        size, index = row.get("chat_size"), row.get("conversation_idx")
        if size not in chat_sizes or index not in conversations:
            errors.append(f"{path.name}: question identity is outside the selection")
        if row.get("user_id") != owners.get((size, index)):
            errors.append(f"{path.name}: question owner differs from ingestion owner")
        question_type = row.get("question_type")
        if question_type not in category_counts:
            errors.append(f"{path.name}: unexpected question type {question_type!r}")
        else:
            category_counts[question_type] += 1
        question = row.get("question")
        if not isinstance(question, str) or not question.strip():
            errors.append(f"{path.name}: question must be nonempty")

        retrieval = row.get("retrieval")
        if not isinstance(retrieval, dict):
            errors.append(f"{path.name}: retrieval must be an object")
            continue
        if retrieval.get("search_query") != question:
            errors.append(f"{path.name}: search query differs from the question")
        results = retrieval.get("search_results")
        if not isinstance(results, list):
            errors.append(f"{path.name}: search_results must be a list")
            continue
        if retrieval.get("total_results") != len(results):
            errors.append(f"{path.name}: total_results does not match search_results")
        retrieval_counts.append(len(results))
        if not _number(retrieval.get("search_latency_ms")):
            errors.append(f"{path.name}: search latency must be finite")
        for result_index, result in enumerate(results):
            if not isinstance(result, dict):
                errors.append(f"{path.name}: result {result_index} must be an object")
                continue
            if not isinstance(result.get("id"), str) or not result.get("id"):
                errors.append(f"{path.name}: result {result_index} has no ID")
            if not isinstance(result.get("memory"), str) or not result.get("memory"):
                errors.append(f"{path.name}: result {result_index} has no memory text")
            if not _number(result.get("score")):
                errors.append(f"{path.name}: result {result_index} score is not finite")

        cutoff_results = row.get("cutoff_results")
        if not scored:
            if cutoff_results is not None:
                errors.append(f"{path.name}: predict-only artifact unexpectedly has cutoff results")
            continue
        if not isinstance(cutoff_results, dict):
            errors.append(f"{path.name}: scored artifact has no cutoff results")
            continue
        if set(cutoff_results) != expected_cutoffs:
            errors.append(f"{path.name}: cutoff identities differ from registration")
        rubric = row.get("rubric")
        rubric_count = len(rubric) if isinstance(rubric, list) else 0
        if rubric_count == 0:
            errors.append(f"{path.name}: scored artifact has no rubric nuggets")
        for cutoff, result in cutoff_results.items():
            if not isinstance(result, dict):
                errors.append(f"{path.name}: {cutoff} result must be an object")
                continue
            if result.get("judgment") not in {"PASS", "FAIL"}:
                errors.append(f"{path.name}: {cutoff} has invalid judgment")
            score = result.get("score")
            if not _number(score) or not 0 <= float(score) <= 1:
                errors.append(f"{path.name}: {cutoff} score is invalid")
            answer = result.get("generated_answer")
            if not isinstance(answer, str) or not answer.strip():
                errors.append(f"{path.name}: {cutoff} generated answer is empty")
            if result.get("error"):
                errors.append(f"{path.name}: {cutoff} records an error")
            nuggets = result.get("nugget_scores")
            if not isinstance(nuggets, list) or len(nuggets) != rubric_count:
                errors.append(f"{path.name}: {cutoff} nugget coverage is incomplete")
                continue
            for nugget in nuggets:
                reason = nugget.get("reason") if isinstance(nugget, dict) else None
                nugget_score = nugget.get("score") if isinstance(nugget, dict) else None
                if nugget_score not in {0.0, 0.5, 1.0}:
                    errors.append(f"{path.name}: {cutoff} has an invalid nugget score")
                if not isinstance(reason, str) or not reason.strip() or reason.startswith("Parse error:"):
                    errors.append(f"{path.name}: {cutoff} has an invalid nugget verdict")

    expected_per_category = len(chat_sizes) * len(conversations) * 2
    for question_type, count in category_counts.items():
        if count != expected_per_category:
            errors.append(
                f"question type {question_type} coverage is {count}/{expected_per_category}"
            )

    artifacts = {
        path.name: _hash(path)
        for path in sorted(set(artifact_paths))
        if path.is_file()
    }
    return {
        "schema_version": 1,
        "complete": not errors,
        "upstream_commit": UPSTREAM_COMMIT,
        "prediction_directory": str(prediction_dir.resolve()),
        "selection": {
            "chat_sizes": list(chat_sizes),
            "conversations": list(conversations),
            "question_types": list(question_types),
            "scored": scored,
            "cutoffs": list(cutoffs) if scored else [],
        },
        "coverage": {
            "ingestion_checkpoints": len(actual_ingestion),
            "expected_ingestion_checkpoints": len(expected_ingestion),
            "questions": len(question_paths),
            "expected_questions": expected_questions,
            "question_types": category_counts,
            "retrieval_result_min": min(retrieval_counts) if retrieval_counts else None,
            "retrieval_result_max": max(retrieval_counts) if retrieval_counts else None,
        },
        "run_id": next(iter(run_ids)) if len(run_ids) == 1 else None,
        "errors": errors,
        "artifact_sha256": artifacts,
    }


def _csv(value: str, *, allowed: tuple[str, ...], name: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items or len(set(items)) != len(items) or any(item not in allowed for item in items):
        raise ValueError(f"invalid {name}: {value!r}")
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prediction_dir", type=Path)
    parser.add_argument("--chat-sizes", default="100K")
    parser.add_argument("--conversations", required=True)
    parser.add_argument("--question-types", default=",".join(QUESTION_TYPES))
    parser.add_argument("--scored", action="store_true")
    parser.add_argument("--cutoffs", default="100")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--execution-root", type=Path)
    args = parser.parse_args()
    report = validate_run(
        args.prediction_dir,
        chat_sizes=_csv(args.chat_sizes, allowed=CHAT_SIZES, name="chat sizes"),
        conversations=parse_indices(args.conversations),
        question_types=_csv(
            args.question_types, allowed=QUESTION_TYPES, name="question types"
        ),
        scored=args.scored,
        cutoffs=tuple(int(value) for value in args.cutoffs.split(",")),
    )
    if (args.registration is None) != (args.execution_root is None):
        raise ValueError("--registration and --execution-root must be provided together")
    if args.registration is not None and args.execution_root is not None:
        _registered_validation(
            report,
            registration_path=args.registration.resolve(),
            execution_root=args.execution_root.resolve(),
            chat_sizes=_csv(args.chat_sizes, allowed=CHAT_SIZES, name="chat sizes"),
            conversations=parse_indices(args.conversations),
            question_types=_csv(
                args.question_types, allowed=QUESTION_TYPES, name="question types"
            ),
            scored=args.scored,
            cutoffs=tuple(int(value) for value in args.cutoffs.split(",")),
        )
        report["complete"] = not report["errors"]
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    if not report["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
