"""Compare two complete, matched LongMemEval-V2 harness runs."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from benchmarks.compare_evidence import paired_statistics


_READER_FIELDS = (
    "domain",
    "model",
    "base_url",
    "max_completion_tokens",
    "memory_context_max_tokens",
    "reader_max_concurrent_requests",
    "prompt_build_max_workers",
    "temperature",
    "top_p",
    "top_k",
    "presence_penalty",
    "repetition_penalty",
    "reasoning_effort",
    "reader_enable_thinking",
    "shuffle_questions_seed",
)
_INPUT_FIELDS = ("questions_path", "haystack_path", "trajectories_path")
_QUESTION_FIELDS = (
    "question_id",
    "question_type",
    "category",
    "is_abstention_problem",
    "eval_function",
    "question_text",
    "answer_gold",
)
_ARTIFACT_NAMES = (
    "run_args.json",
    "prompt_build_summary.json",
    "prompt_rows.jsonl",
    "reader_outputs.checkpoint.jsonl",
    "per_question.jsonl",
    "aggregated_metrics.json",
)


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {line_number} is not an object: {path}")
        records.append(value)
    return records


def _require_exact_question_ids(
    records: list[dict[str, Any]], expected: set[str], *, path: Path
) -> None:
    ids = [row.get("question_id") for row in records]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError(f"artifact has invalid question identities: {path}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"artifact has duplicate question identities: {path}")
    if set(ids) != expected:
        raise ValueError(f"artifact question identities are incomplete: {path}")


def _load_run(directory: Path) -> dict[str, Any]:
    required = [directory / name for name in _ARTIFACT_NAMES]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"incomplete LongMemEval-V2 run {directory}: missing {missing}")

    records = _load_jsonl(directory / "per_question.jsonl")
    ids = [row.get("question_id") for row in records]
    if not records or any(not isinstance(value, str) or not value for value in ids):
        raise ValueError(f"run has invalid question identities: {directory}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"run has duplicate question identities: {directory}")
    for row in records:
        if type(row.get("score_bool")) is not bool or type(row.get("is_unknown")) is not bool:
            raise ValueError(f"run has invalid binary outcomes: {directory}")
        usage = row.get("usage")
        if not isinstance(usage, dict) or any(
            type(usage.get(field)) is not int or usage[field] < 0
            for field in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            raise ValueError(f"run has invalid token usage: {directory}")

    aggregated = _load_json(directory / "aggregated_metrics.json")
    observed_count = aggregated.get("overall", {}).get("count_all_questions")
    if observed_count != len(records):
        raise ValueError(f"aggregate question count does not match rows: {directory}")
    expected_score = sum(row["score_bool"] for row in records) / len(records)
    if not math.isclose(
        float(aggregated.get("overall", {}).get("overall_full_set", math.nan)),
        expected_score,
        abs_tol=1e-12,
    ):
        raise ValueError(f"aggregate score does not match rows: {directory}")
    expected_ids = set(ids)
    for name in ("prompt_rows.jsonl", "reader_outputs.checkpoint.jsonl"):
        _require_exact_question_ids(
            _load_jsonl(directory / name), expected_ids, path=directory / name
        )
    return {
        "directory": directory,
        "args": _load_json(directory / "run_args.json"),
        "aggregated": aggregated,
        "records": records,
        "by_id": {row["question_id"]: row for row in records},
        "artifacts": {name: _digest(directory / name) for name in _ARTIFACT_NAMES},
    }


def _exact_mcnemar(wins: int, losses: int) -> float:
    """Two-sided exact McNemar p-value over discordant binary pairs."""
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, index) for index in range(min(wins, losses) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def _outcomes(
    ids: list[str],
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
    *,
    samples: int,
) -> dict[str, Any]:
    pairs = [
        (float(right[question_id]["score_bool"]), float(left[question_id]["score_bool"]))
        for question_id in ids
    ]
    paired = paired_statistics(pairs, samples=samples)
    wins = int(paired["wins"])
    losses = int(paired["losses"])
    paired["exact_mcnemar_p_two_sided"] = _exact_mcnemar(wins, losses)
    return {
        "questions": len(ids),
        "left_correct": sum(left[question_id]["score_bool"] for question_id in ids),
        "right_correct": sum(right[question_id]["score_bool"] for question_id in ids),
        "left_unknown": sum(left[question_id]["is_unknown"] for question_id in ids),
        "right_unknown": sum(right[question_id]["is_unknown"] for question_id in ids),
        "left_empty_responses": sum(
            not str(left[question_id].get("response_raw") or "").strip()
            for question_id in ids
        ),
        "right_empty_responses": sum(
            not str(right[question_id].get("response_raw") or "").strip()
            for question_id in ids
        ),
        "paired_left_minus_right": paired,
    }


def _efficiency(run: dict[str, Any]) -> dict[str, Any]:
    rows = run["records"]
    started = datetime.fromisoformat(run["args"]["started_at_utc"])
    completed = datetime.fromisoformat(run["aggregated"]["completed_at_utc"])
    if started.utcoffset() is None or completed.utcoffset() is None or completed < started:
        raise ValueError(f"invalid run clock: {run['directory']}")
    return {
        "wall_seconds": (completed - started).total_seconds(),
        "prompt_tokens": sum(row["usage"]["prompt_tokens"] for row in rows),
        "completion_tokens": sum(row["usage"]["completion_tokens"] for row in rows),
        "total_tokens": sum(row["usage"]["total_tokens"] for row in rows),
        "memory_context_tokens_mean": sum(
            int(row["memory_context_token_count"]) for row in rows
        ) / len(rows),
        "memory_query_seconds_mean": sum(
            float(row["memory_query_duration_seconds"]) for row in rows
        ) / len(rows),
        "memory_query_seconds_max": max(
            float(row["memory_query_duration_seconds"]) for row in rows
        ),
    }


def compare(
    left_directory: Path,
    right_directory: Path,
    *,
    left_label: str,
    right_label: str,
    samples: int = 10_000,
    registration: Path | None = None,
) -> dict[str, Any]:
    if not left_label.strip() or not right_label.strip() or left_label == right_label:
        raise ValueError("comparison labels must be distinct and non-empty")
    if samples < 1:
        raise ValueError("bootstrap samples must be positive")
    left = _load_run(left_directory)
    right = _load_run(right_directory)

    left_settings = {field: left["args"].get(field) for field in _READER_FIELDS}
    right_settings = {field: right["args"].get(field) for field in _READER_FIELDS}
    if left_settings != right_settings:
        raise ValueError("reader settings differ between runs")

    left_ids = [row["question_id"] for row in left["records"]]
    if set(left_ids) != set(right["by_id"]):
        raise ValueError("question identity sets differ between runs")
    for question_id in left_ids:
        left_row = left["by_id"][question_id]
        right_row = right["by_id"][question_id]
        if any(left_row.get(field) != right_row.get(field) for field in _QUESTION_FIELDS):
            raise ValueError(f"question inputs differ between runs: {question_id}")

    input_hashes: dict[str, str] = {}
    for field in _INPUT_FIELDS:
        left_path = Path(str(left["args"].get(field) or ""))
        right_path = Path(str(right["args"].get(field) or ""))
        if not left_path.is_file() or not right_path.is_file():
            raise ValueError(f"run input is unavailable for hashing: {field}")
        left_hash = _digest(left_path)
        if left_hash != _digest(right_path):
            raise ValueError(f"run input differs: {field}")
        input_hashes[field] = left_hash

    categories = sorted({str(row["category"]) for row in left["records"]})
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "longmemeval-v2-paired-comparison",
        "labels": {"left": left_label, "right": right_label},
        "reader_settings": left_settings,
        "input_sha256": input_hashes,
        "bootstrap": {"samples": samples, "seed": 42, "unit": "question"},
        "overall": _outcomes(
            left_ids, left["by_id"], right["by_id"], samples=samples
        ),
        "categories": {
            category: _outcomes(
                [
                    question_id
                    for question_id in left_ids
                    if left["by_id"][question_id]["category"] == category
                ],
                left["by_id"],
                right["by_id"],
                samples=samples,
            )
            for category in categories
        },
        "efficiency": {
            "left": _efficiency(left),
            "right": _efficiency(right),
        },
        "artifacts": {
            "left": left["artifacts"],
            "right": right["artifacts"],
        },
        "limitations": [
            "Question bootstrap intervals condition on this selected cohort; shared haystacks can make questions dependent.",
            "Results compare the recorded reader and execution settings only and do not establish universal system leadership.",
        ],
    }
    if registration is not None:
        if not registration.is_file():
            raise ValueError("registration file does not exist")
        report["registration_sha256"] = _digest(registration)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left_directory", type=Path)
    parser.add_argument("right_directory", type=Path)
    parser.add_argument("--left-label", required=True)
    parser.add_argument("--right-label", required=True)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(
        args.left_directory,
        args.right_directory,
        left_label=args.left_label,
        right_label=args.right_label,
        samples=args.bootstrap_samples,
        registration=args.registration,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
