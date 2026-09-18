"""Register, capture, read, and judge an unchanged-PRME LongMemEval-S baseline.

The capture stage ingests every history before exposing the question, stores no
answer/evidence labels, and saves replayable PRME receipts and contexts. Reader
and judge calls are separate so the expensive memory-side work survives provider
quota or availability failures.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import time
from typing import Any

import httpx
from openai import AsyncOpenAI

from prme import MemoryEngine, NodeType, PRMEConfig, Scope
from prme.retrieval.tokenization import count_tokens


DATASET_NAME = "LongMemEval-S cleaned"
DATASET_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
OFFICIAL_REVISION = "9e0b455f4ef0e2ab8f2e582289761153549043fc"
MODEL = "gpt-5.4-2026-03-05"
REASONING_EFFORT = "medium"
CAPTURE_WORKERS = 5
PROVIDER_CONCURRENCY = 4
MAX_PROVIDER_ATTEMPTS = 4
USER_ID = "longmemeval-s-baseline"
READER_PROMPT_VERSION = "prme_longmemeval_s_reader_v1"


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


def _git_is_ancestor(revision: str, root: Path) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _source_changes_since(revision: str, root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{revision}..HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text())
    required = {
        "question_id",
        "question_type",
        "question",
        "answer",
        "question_date",
        "haystack_session_ids",
        "haystack_dates",
        "haystack_sessions",
        "answer_session_ids",
    }
    allowed_types = {
        "single-session-user",
        "single-session-assistant",
        "single-session-preference",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    }
    if not isinstance(value, list) or len(value) != 500:
        raise ValueError("LongMemEval-S must contain exactly 500 questions")
    identifiers: set[str] = set()
    for case in value:
        if not isinstance(case, dict) or not required <= set(case):
            raise ValueError("LongMemEval-S case schema differs")
        identifier = case["question_id"]
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier in identifiers
            or case["question_type"] not in allowed_types
            or not isinstance(case["question"], str)
            or type(case["answer"]) not in {str, int}
            or not isinstance(case["question_date"], str)
        ):
            raise ValueError("LongMemEval-S case identity differs")
        identifiers.add(identifier)
        session_ids = case["haystack_session_ids"]
        dates = case["haystack_dates"]
        sessions = case["haystack_sessions"]
        if not (
            isinstance(session_ids, list)
            and isinstance(dates, list)
            and isinstance(sessions, list)
            and len(session_ids) == len(dates) == len(sessions)
            and session_ids
        ):
            raise ValueError(f"LongMemEval-S history differs for {identifier}")
        for session in sessions:
            if not isinstance(session, list) or not session:
                raise ValueError(f"LongMemEval-S session differs for {identifier}")
            for turn in session:
                if (
                    not isinstance(turn, dict)
                    or turn.get("role") not in {"user", "assistant"}
                    or not isinstance(turn.get("content"), str)
                ):
                    raise ValueError(f"LongMemEval-S turn differs for {identifier}")
    return value


def _parse_date(value: str) -> datetime:
    cleaned = value
    if "(" in value and ")" in value:
        cleaned = f"{value.split('(')[0].strip()} {value.split(')', 1)[1].strip()}"
    parsed = datetime.strptime(cleaned.strip(), "%Y/%m/%d %H:%M")
    return parsed.replace(tzinfo=timezone.utc)


def _dataset_identity(path: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    raw_sha = _sha256_file(path)
    if raw_sha != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    return {
        "name": DATASET_NAME,
        "source": "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned",
        "filename": path.name,
        "sha256": raw_sha,
        "questions": len(cases),
        "question_types": dict(
            sorted(Counter(case["question_type"] for case in cases).items())
        ),
        "abstention_questions": sum(
            case["question_id"].endswith("_abs") for case in cases
        ),
        "sessions": sum(len(case["haystack_sessions"]) for case in cases),
        "turns": sum(
            sum(len(session) for session in case["haystack_sessions"]) for case in cases
        ),
        "ordered_question_identity_sha256": _sha256(
            _canonical([case["question_id"] for case in cases])
        ),
    }


def _effective_defaults() -> dict[str, Any]:
    config = PRMEConfig(_env_file=None)  # type: ignore[call-arg]
    return {
        "embedding": config.embedding.model_dump(mode="json", exclude={"api_key"}),
        "scoring": config.scoring.model_dump(mode="json"),
        "packing": config.packing.model_dump(mode="json"),
        "organizer": config.organizer.model_dump(mode="json"),
        "enable_store_supersedence": config.enable_store_supersedence,
        "reinforce_similarity_threshold": config.reinforce_similarity_threshold,
        "enable_qa_pairing": config.enable_qa_pairing,
        "enable_surprise_gating": config.enable_surprise_gating,
        "enable_reranker": config.enable_reranker,
        "vector_exact_search": config.vector_exact_search,
        "duckdb_threads": config.duckdb_threads,
    }


def _reader_prompt(context: str, question_date: str, question: str) -> str:
    return (
        "I will give you several history chats between you and a user. Please "
        "answer the question based on the relevant chat history. Answer the "
        "question step by step: first extract all the relevant information, and "
        "then reason over the information to get the answer.\n\n\n"
        f"History Chats:\n\n{context}\n\nCurrent Date: {question_date}\n"
        f"Question: {question}\nAnswer (step by step):"
    )


def _protocol() -> dict[str, Any]:
    prompt_probe = _reader_prompt("{memory}", "{question_date}", "{question}")
    return {
        "ingestion": {
            "api": "MemoryEngine.store",
            "one_node_per_nonempty_turn": True,
            "empty_turn_policy": (
                "omit the 12 empty, unlabeled turns in the cleaned dataset while "
                "preserving original session and turn positions"
            ),
            "node_type": "fact",
            "scope": "personal",
            "roles": ["user", "assistant"],
            "session_identity": (
                "zero-based history position plus official haystack_session_id; "
                "the cleaned dataset repeats 13 IDs at distinct positions"
            ),
            "event_time": "official session timestamp as UTC",
            "question_visible_during_ingestion": False,
            "answers_or_evidence_labels_visible_during_ingestion": False,
            "generated_extraction": False,
        },
        "retrieval": {
            "query": "verbatim official question",
            "reference_time": "official question timestamp as UTC",
            "effective_prme_defaults": _effective_defaults(),
            "cold_definition": "first retrieve after closing and reopening the ingested pack; engine-open time excluded",
            "warm_definition": "immediate identical second retrieve on the same open engine",
            "context_equality_required": True,
            "receipt_persistence_required": True,
        },
        "reader": {
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "prompt_version": READER_PROMPT_VERSION,
            "prompt_template_sha256": _sha256(prompt_probe.encode()),
            "prompt_source": (
                "official LongMemEval run_generation.py CoT retrieval template; "
                "PRME context replaces the official history serialization"
            ),
            "max_output_tokens": 1024,
        },
        "judge": {
            "model": MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "prompt_source": "official LongMemEval evaluate_qa.py get_anscheck_prompt",
            "verdict_parser": "case-insensitive substring 'yes', matching official evaluator",
            "max_output_tokens": 64,
        },
    }


def _official_identity(root: Path) -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if revision != OFFICIAL_REVISION:
        raise ValueError("official LongMemEval revision differs")
    paths = {
        "evaluator": root / "src/evaluation/evaluate_qa.py",
        "generation": root / "src/generation/run_generation.py",
        "readme": root / "README.md",
    }
    return {
        "repository": "https://github.com/xiaowu0162/LongMemEval",
        "revision": revision,
        "files": {
            name: {"path": str(path.relative_to(root)), "sha256": _sha256_file(path)}
            for name, path in paths.items()
        },
    }


def _gates() -> dict[str, Any]:
    return {
        "capture": {
            "questions_completed": 500,
            "ingestion_failures": 0,
            "retrieval_failures": 0,
            "durable_receipts": 1000,
            "cold_warm_context_mismatches": 0,
            "pack_integrity_failures": 0,
        },
        "baseline_decision": (
            "This arm measures unchanged PRME and cannot promote a feature. "
            "Any later composition arm requires a separate registration and a "
            "positive paired interval without protected-category or evidence loss."
        ),
    }


def create_registration(
    *, dataset_path: Path, official_root: Path, project_root: Path, output_path: Path
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("registration output already exists")
    cases = _load_dataset(dataset_path)
    registration = {
        "schema_version": 1,
        "kind": "longmemeval-s-prme-baseline-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "This is a matched baseline of unchanged PRME on all 500 cleaned "
            "LongMemEval-S questions with a pinned GPT-5.4 reader and official-prompt "
            "judge. It does not establish a comparison with Zep unless disclosed "
            "reader, judge, budget, dataset, and latency boundaries match."
        ),
        "source": {
            "prme_revision": _git_revision(project_root),
            "runner_sha256": _sha256_file(Path(__file__).resolve()),
            "official": _official_identity(official_root),
        },
        "dataset": _dataset_identity(dataset_path, cases),
        "protocol": _protocol(),
        "evaluation": _gates(),
        "known_provider_preflight": {
            "model_visible": True,
            "generation_available": False,
            "failure_code": "credit_balance_exhausted",
            "meaning": "capture can proceed; reader and judge stages remain pending",
        },
        "limitations": [
            "PRME direct-turn FACT ingestion is the existing benchmark path; it does not reproduce Zep's proprietary write-time extraction.",
            "Thirteen questions contain one repeated official session ID; storage qualifies every session with its history position while evidence reporting retains the official ID.",
            "The cleaned dataset contains 12 empty, unlabeled turns; PRME omits them because empty memory records are invalid and reports the omission explicitly.",
            "Zep's exact GPT-5.4 prompts, judge prompt, dataset checksum and artifacts are not public.",
            "Hot and cold timings cover local PRME retrieval only and are reported separately from ingestion, reader and judge costs.",
            "The mutable GPT-5.4 alias is avoided by pinning the visible 2026-03-05 snapshot.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any],
    *,
    dataset_path: Path,
    official_root: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    cases = _load_dataset(dataset_path)
    revision = registration.get("source", {}).get("prme_revision")
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "longmemeval-s-prme-baseline-registration"
        or not isinstance(revision, str)
        or not _git_is_ancestor(revision, project_root)
        or any(
            not path.startswith("benchmarks/results/research/")
            for path in _source_changes_since(revision, project_root)
        )
        or registration.get("source", {}).get("runner_sha256")
        != _sha256_file(Path(__file__).resolve())
        or registration.get("source", {}).get("official")
        != _official_identity(official_root)
        or registration.get("dataset") != _dataset_identity(dataset_path, cases)
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _gates()
    ):
        raise ValueError("registered LongMemEval-S baseline inputs differ")
    return cases


def _case_identity(case: dict[str, Any]) -> str:
    return _sha256(
        _canonical(
            {
                "question_id": case["question_id"],
                "question_sha256": _sha256(case["question"].encode()),
                "question_date": case["question_date"],
                "session_ids": case["haystack_session_ids"],
                "history_sha256": _sha256(_canonical(case["haystack_sessions"])),
            }
        )
    )


def _safe_id(value: str) -> str:
    if not value or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for character in value
    ):
        raise ValueError("question ID is not filesystem-safe")
    return value


def _pack_config(pack: Path) -> PRMEConfig:
    return PRMEConfig(
        _env_file=None,  # type: ignore[call-arg]
        db_path=str(pack / "memory.duckdb"),
        vector_path=str(pack / "vectors.usearch"),
        lexical_path=str(pack / "lexical_index"),
    )


def _tree_identity(root: Path) -> dict[str, Any]:
    files = []
    for path in sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda item: str(item.relative_to(root)),
    ):
        relative = str(path.relative_to(root))
        if relative.endswith(".lock"):
            continue
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "files": files,
        "tree_sha256": _sha256(_canonical(files)),
        "bytes": sum(item["bytes"] for item in files),
    }


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * fraction) - 1]


def _response_sources(
    response: Any, receipt: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    packed = {
        candidate.node_id for candidate in receipt.candidates if candidate.in_context
    }
    returned: list[dict[str, Any]] = []
    packed_rows: list[dict[str, Any]] = []
    for result in response.results:
        metadata = result.node.metadata
        row = {
            "node_id": str(result.node.id),
            "source_session_id": metadata.get("source_session_id"),
            "source_session_position": metadata.get("source_session_position"),
            "source_turn_index": metadata.get("source_turn_index"),
            "source_role": metadata.get("source_role"),
            "content_sha256": _sha256(result.node.content.encode()),
            "composite_score": result.composite_score,
        }
        returned.append(row)
        if result.node.id in packed:
            packed_rows.append(row)
    return returned, packed_rows


def _evidence_metrics(
    case: dict[str, Any], packed: list[dict[str, Any]]
) -> dict[str, Any]:
    if case["question_id"].endswith("_abs"):
        return {"applicable": False}
    wanted_sessions = set(case["answer_session_ids"])
    packed_sessions = {row["source_session_id"] for row in packed}
    wanted_turns = {
        (session_id, session_position, index)
        for session_position, (session_id, session) in enumerate(
            zip(case["haystack_session_ids"], case["haystack_sessions"])
        )
        for index, turn in enumerate(session)
        if turn.get("has_answer") is True
    }
    packed_turns = {
        (
            row["source_session_id"],
            row["source_session_position"],
            row["source_turn_index"],
        )
        for row in packed
    }
    session_hits = wanted_sessions & packed_sessions
    turn_hits = wanted_turns & packed_turns
    return {
        "applicable": True,
        "required_sessions": len(wanted_sessions),
        "retrieved_required_sessions": len(session_hits),
        "any_session_recall": bool(session_hits),
        "complete_session_recall": wanted_sessions <= packed_sessions,
        "required_turns": len(wanted_turns),
        "retrieved_required_turns": len(turn_hits),
        "any_turn_recall": bool(turn_hits),
        "complete_turn_recall": wanted_turns <= packed_turns,
    }


def _validate_saved_capture(
    saved: dict[str, Any], *, identity: dict[str, str], pack_path: Path
) -> None:
    saved_checksum = saved.get("capture_sha256")
    checksum_input = {
        key: value for key, value in saved.items() if key != "capture_sha256"
    }
    if (
        saved.get("identity") != identity
        or saved.get("complete") is not True
        or not isinstance(saved_checksum, str)
        or saved_checksum != _sha256(_canonical(checksum_input))
        or saved.get("context_sha256")
        != _sha256(str(saved.get("context", "")).encode())
        or not pack_path.is_dir()
        or _tree_identity(pack_path) != saved.get("pack")
    ):
        raise ValueError(f"saved capture differs for {pack_path.name}")


async def _capture_case(
    case: dict[str, Any], *, output_dir: Path, registration_sha256: str
) -> dict[str, Any]:
    question_id = _safe_id(case["question_id"])
    capture_path = output_dir / "captures" / f"{question_id}.json"
    pack_path = output_dir / "packs" / question_id
    identity = {
        "registration_sha256": registration_sha256,
        "case_sha256": _case_identity(case),
    }
    if capture_path.exists():
        saved = json.loads(capture_path.read_text())
        _validate_saved_capture(saved, identity=identity, pack_path=pack_path)
        return saved
    if pack_path.exists():
        shutil.rmtree(pack_path)
    pack_path.mkdir(parents=True)
    config = _pack_config(pack_path)
    ingestion_started = time.perf_counter()
    engine = await MemoryEngine.create(config)
    try:
        stored = 0
        empty_turns = 0
        for session_position, (session_id, date_text, session) in enumerate(
            zip(
                case["haystack_session_ids"],
                case["haystack_dates"],
                case["haystack_sessions"],
            )
        ):
            event_time = _parse_date(date_text)
            for turn_index, turn in enumerate(session):
                if not turn["content"].strip():
                    empty_turns += 1
                    continue
                await engine.store(
                    turn["content"],
                    user_id=USER_ID,
                    session_id=f"{session_position:05d}:{session_id}",
                    role=turn["role"],
                    node_type=NodeType.FACT,
                    scope=Scope.PERSONAL,
                    metadata={
                        "benchmark": "longmemeval-s",
                        "source_session_id": session_id,
                        "source_session_position": session_position,
                        "source_turn_index": turn_index,
                        "source_role": turn["role"],
                    },
                    event_time=event_time,
                )
                stored += 1
    finally:
        await engine.close()
    ingestion_seconds = time.perf_counter() - ingestion_started

    open_started = time.perf_counter()
    engine = await MemoryEngine.create(config)
    open_seconds = time.perf_counter() - open_started
    reference_time = _parse_date(case["question_date"])
    try:
        cold_started = time.perf_counter()
        cold = await engine.retrieve(
            case["question"], user_id=USER_ID, reference_time=reference_time
        )
        cold_seconds = time.perf_counter() - cold_started
        warm_started = time.perf_counter()
        warm = await engine.retrieve(
            case["question"], user_id=USER_ID, reference_time=reference_time
        )
        warm_seconds = time.perf_counter() - warm_started
        cold_receipt = await engine.get_retrieval_receipt(
            str(cold.metadata.request_id), user_id=USER_ID
        )
        warm_receipt = await engine.get_retrieval_receipt(
            str(warm.metadata.request_id), user_id=USER_ID
        )
        if (
            not cold.metadata.receipt_persisted
            or not warm.metadata.receipt_persisted
            or cold_receipt is None
            or warm_receipt is None
            or cold.bundle.render() != warm.bundle.render()
            or [result.node.id for result in cold.results]
            != [result.node.id for result in warm.results]
            or cold_receipt.replay_ranking()
            != tuple(result.node.id for result in cold.results)
            or warm_receipt.replay_ranking()
            != tuple(result.node.id for result in warm.results)
        ):
            raise ValueError(f"retrieval replay differs for {question_id}")
        returned, packed = _response_sources(warm, warm_receipt)
        context = warm.bundle.render()
        capture = {
            "schema_version": 1,
            "kind": "longmemeval-s-prme-capture",
            "complete": True,
            "identity": identity,
            "question_id": question_id,
            "question_type": case["question_type"],
            "abstention": question_id.endswith("_abs"),
            "question_sha256": _sha256(case["question"].encode()),
            "question_date": case["question_date"],
            "history": {
                "sessions": len(case["haystack_sessions"]),
                "input_turns": sum(
                    len(session) for session in case["haystack_sessions"]
                ),
                "stored_turns": stored,
                "empty_turns_omitted": empty_turns,
            },
            "context": context,
            "context_sha256": _sha256(context.encode()),
            "context_tokens": count_tokens(context, config.packing.tokenizer),
            "bundle_tokens": warm.bundle.tokens_used,
            "returned": returned,
            "packed": packed,
            "evidence": _evidence_metrics(case, packed),
            "receipts": {
                "cold": cold_receipt.model_dump(mode="json"),
                "warm": warm_receipt.model_dump(mode="json"),
            },
            "timing": {
                "ingestion_seconds": ingestion_seconds,
                "open_seconds": open_seconds,
                "cold_retrieval_seconds": cold_seconds,
                "warm_retrieval_seconds": warm_seconds,
            },
        }
    finally:
        await engine.close()
    capture["pack"] = _tree_identity(pack_path)
    capture["capture_sha256"] = _sha256(_canonical(capture))
    _write(capture_path, capture)
    return capture


def _capture_summary(
    captures: list[dict[str, Any]], *, registration_path: Path, output_dir: Path
) -> dict[str, Any]:
    applicable = [
        capture["evidence"] for capture in captures if capture["evidence"]["applicable"]
    ]
    cold = [capture["timing"]["cold_retrieval_seconds"] for capture in captures]
    warm = [capture["timing"]["warm_retrieval_seconds"] for capture in captures]
    ingestion = [capture["timing"]["ingestion_seconds"] for capture in captures]
    contexts = [capture["context_tokens"] for capture in captures]
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "longmemeval-s-prme-baseline-capture-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": _sha256_file(registration_path),
        "questions": len(captures),
        "failures": 0,
        "receipts": len(captures) * 2,
        "cold_warm_context_mismatches": 0,
        "capture_manifest_sha256": _sha256(
            _canonical(
                [
                    [capture["question_id"], capture["capture_sha256"]]
                    for capture in captures
                ]
            )
        ),
        "pack_manifest_sha256": _sha256(
            _canonical(
                [
                    [capture["question_id"], capture["pack"]["tree_sha256"]]
                    for capture in captures
                ]
            )
        ),
        "contexts": {
            "mean_tokens": statistics.mean(contexts),
            "median_tokens": statistics.median(contexts),
            "p95_tokens": _percentile([float(value) for value in contexts], 0.95),
            "max_tokens": max(contexts),
        },
        "retrieval": {
            "cold_p50_seconds": statistics.median(cold),
            "cold_p95_seconds": _percentile(cold, 0.95),
            "warm_p50_seconds": statistics.median(warm),
            "warm_p95_seconds": _percentile(warm, 0.95),
        },
        "ingestion": {
            "sum_question_wall_seconds": sum(ingestion),
            "median_question_seconds": statistics.median(ingestion),
            "p95_question_seconds": _percentile(ingestion, 0.95),
            "input_turns": sum(
                capture["history"]["input_turns"] for capture in captures
            ),
            "stored_turns": sum(
                capture["history"]["stored_turns"] for capture in captures
            ),
            "empty_turns_omitted": sum(
                capture["history"]["empty_turns_omitted"] for capture in captures
            ),
        },
        "evidence": {
            "questions": len(applicable),
            "any_session_recall": sum(item["any_session_recall"] for item in applicable)
            / len(applicable),
            "complete_session_recall": sum(
                item["complete_session_recall"] for item in applicable
            )
            / len(applicable),
            "any_turn_recall": sum(item["any_turn_recall"] for item in applicable)
            / len(applicable),
            "complete_turn_recall": sum(
                item["complete_turn_recall"] for item in applicable
            )
            / len(applicable),
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "capture_workers": CAPTURE_WORKERS,
            "output_directory": str(output_dir),
        },
        "reader_status": "pending_provider_credit",
        "judge_status": "pending_reader",
    }
    result["result_sha256"] = _sha256(_canonical(result))
    return result


async def capture(
    *,
    registration_path: Path,
    dataset_path: Path,
    official_root: Path,
    project_root: Path,
    output_dir: Path,
    summary_path: Path,
) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    cases = _validate_registration(
        registration,
        dataset_path=dataset_path,
        official_root=official_root,
        project_root=project_root,
    )
    if summary_path.exists():
        raise ValueError("capture summary already exists")
    output_dir.mkdir(parents=True, exist_ok=True)
    identity_path = output_dir / "identity.json"
    identity = {
        "registration_sha256": _sha256_file(registration_path),
        "dataset_sha256": registration["dataset"]["sha256"],
        "questions": len(cases),
    }
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("capture directory belongs to another trial")
    _write(identity_path, identity)
    semaphore = asyncio.Semaphore(CAPTURE_WORKERS)
    completed = 0

    async def run_one(case: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed
        async with semaphore:
            result = await _capture_case(
                case,
                output_dir=output_dir,
                registration_sha256=identity["registration_sha256"],
            )
        completed += 1
        if completed % 10 == 0 or completed == len(cases):
            print(
                f"[longmemeval-s-capture] completed {completed}/{len(cases)}",
                flush=True,
            )
        return result

    values = await asyncio.gather(
        *(run_one(case) for case in cases), return_exceptions=True
    )
    errors = [value for value in values if isinstance(value, BaseException)]
    if errors:
        raise RuntimeError(
            f"LongMemEval-S capture failed for {len(errors)} case(s)"
        ) from errors[0]
    captures = [value for value in values if isinstance(value, dict)]
    captures.sort(key=lambda item: item["question_id"])
    result = _capture_summary(
        captures, registration_path=registration_path, output_dir=output_dir
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _load_official_prompt_function(official_root: Path):
    path = official_root / "src/evaluation/evaluate_qa.py"
    spec = importlib.util.spec_from_file_location("longmemeval_official_evaluate", path)
    if spec is None or spec.loader is None:
        raise ValueError("official evaluator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.get_anscheck_prompt


def _api_key(env_file: Path | None) -> str:
    value = os.environ.get("OPENAI_API_KEY")
    if value:
        return value
    if env_file is not None:
        from dotenv import dotenv_values

        loaded = dotenv_values(env_file)
        value = loaded.get("OPENAI_API_KEY")
        if isinstance(value, str) and value:
            return value
    raise RuntimeError("OPENAI_API_KEY is unavailable")


async def _provider_call(
    client: AsyncOpenAI, prompt: str, *, max_output_tokens: int
) -> dict[str, Any]:
    errors: list[str] = []
    started = time.perf_counter()
    for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
        try:
            request: dict[str, Any] = {
                "model": MODEL,
                "input": prompt,
                "reasoning": {"effort": REASONING_EFFORT},
                "max_output_tokens": max_output_tokens,
                "store": False,
            }
            response = await client.responses.create(**request)
            return {
                "response": response.model_dump(mode="json"),
                "text": response.output_text.strip(),
                "attempts": attempt,
                "transient_errors": errors,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
            }
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if (
                status not in {429, 500, 502, 503, 504, 529}
                or attempt == MAX_PROVIDER_ATTEMPTS
            ):
                raise
            errors.append(f"http_{status}")
            await asyncio.sleep(min(8.0, 0.5 * (2 ** (attempt - 1))))
    raise AssertionError("unreachable provider retry loop")


async def run_reader(
    *,
    registration_path: Path,
    dataset_path: Path,
    official_root: Path,
    project_root: Path,
    capture_dir: Path,
    state_path: Path,
    output_path: Path,
    env_file: Path | None,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("reader output already exists")
    registration = json.loads(registration_path.read_text())
    cases = _validate_registration(
        registration,
        dataset_path=dataset_path,
        official_root=official_root,
        project_root=project_root,
    )
    identity = {
        "kind": "longmemeval-s-prme-reader-v1",
        "registration_sha256": _sha256_file(registration_path),
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "prompt_template_sha256": registration["protocol"]["reader"][
            "prompt_template_sha256"
        ],
    }
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"identity": identity, "results": {}}
    )
    if state.get("identity") != identity:
        raise ValueError("reader state belongs to another trial")
    _write(state_path, state)
    key = _api_key(env_file)
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(PROVIDER_CONCURRENCY)
    completed = 0
    async with AsyncOpenAI(api_key=key, timeout=httpx.Timeout(180.0)) as client:

        async def run_one(case: dict[str, Any]) -> None:
            nonlocal completed
            question_id = case["question_id"]
            capture_value = json.loads(
                (capture_dir / "captures" / f"{_safe_id(question_id)}.json").read_text()
            )
            _validate_saved_capture(
                capture_value,
                identity={
                    "registration_sha256": identity["registration_sha256"],
                    "case_sha256": _case_identity(case),
                },
                pack_path=capture_dir / "packs" / _safe_id(question_id),
            )
            prompt = _reader_prompt(
                capture_value["context"], case["question_date"], case["question"]
            )
            prompt_sha256 = _sha256(prompt.encode())
            if question_id in state["results"]:
                if state["results"][question_id].get("prompt_sha256") != prompt_sha256:
                    raise ValueError(f"saved reader prompt differs for {question_id}")
                completed += 1
                return
            async with semaphore:
                result = await _provider_call(client, prompt, max_output_tokens=1024)
            async with lock:
                state["results"][question_id] = {
                    "prompt_sha256": prompt_sha256,
                    **result,
                }
                _write(state_path, state)
                completed += 1
                if completed % 10 == 0 or completed == len(cases):
                    print(
                        f"[longmemeval-s-reader] completed {completed}/{len(cases)}",
                        flush=True,
                    )

        await asyncio.gather(*(run_one(case) for case in cases))
    rows = [
        {
            "question_id": case["question_id"],
            "hypothesis": state["results"][case["question_id"]]["text"],
        }
        for case in cases
    ]
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-prme-reader-result",
        "identity": identity,
        "questions": len(rows),
        "rows": rows,
    }
    result["result_sha256"] = _sha256(_canonical(result))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


async def run_judge(
    *,
    registration_path: Path,
    dataset_path: Path,
    official_root: Path,
    project_root: Path,
    reader_path: Path,
    state_path: Path,
    output_path: Path,
    env_file: Path | None,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("judge output already exists")
    registration = json.loads(registration_path.read_text())
    cases = _validate_registration(
        registration,
        dataset_path=dataset_path,
        official_root=official_root,
        project_root=project_root,
    )
    reader = json.loads(reader_path.read_text())
    if (
        reader.get("kind") != "longmemeval-s-prme-reader-result"
        or reader.get("identity", {}).get("registration_sha256")
        != _sha256_file(registration_path)
        or reader.get("questions") != len(cases)
    ):
        raise ValueError("reader result belongs to another trial")
    hypotheses = {row["question_id"]: row["hypothesis"] for row in reader["rows"]}
    if set(hypotheses) != {case["question_id"] for case in cases}:
        raise ValueError("reader result question coverage differs")
    prompt_function = _load_official_prompt_function(official_root)
    identity = {
        "kind": "longmemeval-s-prme-judge-v1",
        "registration_sha256": _sha256_file(registration_path),
        "reader_file_sha256": _sha256_file(reader_path),
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "official_evaluator_sha256": registration["source"]["official"]["files"][
            "evaluator"
        ]["sha256"],
    }
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"identity": identity, "results": {}}
    )
    if state.get("identity") != identity:
        raise ValueError("judge state belongs to another trial")
    _write(state_path, state)
    key = _api_key(env_file)
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(PROVIDER_CONCURRENCY)
    completed = 0
    async with AsyncOpenAI(api_key=key, timeout=httpx.Timeout(180.0)) as client:

        async def run_one(case: dict[str, Any]) -> None:
            nonlocal completed
            question_id = case["question_id"]
            prompt = prompt_function(
                case["question_type"],
                case["question"],
                case["answer"],
                hypotheses[question_id],
                abstention=question_id.endswith("_abs"),
            )
            prompt_sha256 = _sha256(prompt.encode())
            if question_id in state["results"]:
                if state["results"][question_id].get("prompt_sha256") != prompt_sha256:
                    raise ValueError(f"saved judge prompt differs for {question_id}")
                completed += 1
                return
            async with semaphore:
                result = await _provider_call(client, prompt, max_output_tokens=64)
            correct = "yes" in result["text"].casefold()
            async with lock:
                state["results"][question_id] = {
                    "prompt_sha256": prompt_sha256,
                    "correct": correct,
                    **result,
                }
                _write(state_path, state)
                completed += 1
                if completed % 10 == 0 or completed == len(cases):
                    print(
                        f"[longmemeval-s-judge] completed {completed}/{len(cases)}",
                        flush=True,
                    )

        await asyncio.gather(*(run_one(case) for case in cases))
    by_type: dict[str, list[bool]] = {}
    rows = []
    for case in cases:
        value = state["results"][case["question_id"]]
        category = (
            "abstention"
            if case["question_id"].endswith("_abs")
            else case["question_type"]
        )
        by_type.setdefault(category, []).append(value["correct"])
        rows.append(
            {
                "question_id": case["question_id"],
                "question_type": category,
                "correct": value["correct"],
                "hypothesis": hypotheses[case["question_id"]],
            }
        )
    correct = sum(row["correct"] for row in rows)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-prme-judged-result",
        "identity": identity,
        "questions": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows),
        "category_accuracy": {
            name: sum(values) / len(values) for name, values in sorted(by_type.items())
        },
        "rows": rows,
    }
    result["result_sha256"] = _sha256(_canonical(result))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--official-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--dataset", type=Path, required=True)
    register.add_argument("--official-root", type=Path, required=True)
    register.add_argument("--project-root", type=Path, default=Path.cwd())
    register.add_argument("--output", type=Path, required=True)
    capture_parser = subparsers.add_parser("capture")
    _shared(capture_parser)
    capture_parser.add_argument("--output-dir", type=Path, required=True)
    capture_parser.add_argument("--summary", type=Path, required=True)
    reader = subparsers.add_parser("reader")
    _shared(reader)
    reader.add_argument("--capture-dir", type=Path, required=True)
    reader.add_argument("--state", type=Path, required=True)
    reader.add_argument("--output", type=Path, required=True)
    reader.add_argument("--env-file", type=Path)
    judge = subparsers.add_parser("judge")
    _shared(judge)
    judge.add_argument("--reader", type=Path, required=True)
    judge.add_argument("--state", type=Path, required=True)
    judge.add_argument("--output", type=Path, required=True)
    judge.add_argument("--env-file", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "register":
        value = create_registration(
            dataset_path=args.dataset.resolve(),
            official_root=args.official_root.resolve(),
            project_root=args.project_root.resolve(),
            output_path=args.output.resolve(),
        )
        print(
            json.dumps(
                {
                    "dataset": value["dataset"],
                    "protocol": value["protocol"],
                    "evaluation": value["evaluation"],
                },
                indent=2,
            )
        )
        return
    shared = {
        "registration_path": args.registration.resolve(),
        "dataset_path": args.dataset.resolve(),
        "official_root": args.official_root.resolve(),
        "project_root": args.project_root.resolve(),
    }
    if args.command == "capture":
        value = asyncio.run(
            capture(
                output_dir=args.output_dir.resolve(),
                summary_path=args.summary.resolve(),
                **shared,
            )
        )
    elif args.command == "reader":
        value = asyncio.run(
            run_reader(
                capture_dir=args.capture_dir.resolve(),
                state_path=args.state.resolve(),
                output_path=args.output.resolve(),
                env_file=args.env_file.resolve() if args.env_file else None,
                **shared,
            )
        )
    else:
        value = asyncio.run(
            run_judge(
                reader_path=args.reader.resolve(),
                state_path=args.state.resolve(),
                output_path=args.output.resolve(),
                env_file=args.env_file.resolve() if args.env_file else None,
                **shared,
            )
        )
    print(
        json.dumps(
            {
                key: value[key]
                for key in value
                if key in {"kind", "questions", "correct", "accuracy", "result_sha256"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
