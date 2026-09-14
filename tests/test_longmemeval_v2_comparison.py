"""Paired comparison contracts for complete LongMemEval-V2 runs."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.integrations.compare_longmemeval_v2 import compare


def _write_run(root: Path, scores: list[bool], *, model: str = "reader") -> Path:
    root.mkdir()
    inputs = root.parent / "inputs"
    inputs.mkdir(exist_ok=True)
    for name in ("questions.json", "haystack.json", "trajectories.jsonl"):
        (inputs / name).write_text(name, encoding="utf-8")
    args = {
        "domain": "web",
        "model": model,
        "base_url": "http://reader.test/v1",
        "max_completion_tokens": 1024,
        "memory_context_max_tokens": 4096,
        "reader_max_concurrent_requests": 1,
        "prompt_build_max_workers": 1,
        "temperature": 0.0,
        "top_p": 1.0,
        "top_k": 20,
        "presence_penalty": None,
        "repetition_penalty": None,
        "reasoning_effort": "none",
        "reader_enable_thinking": False,
        "shuffle_questions_seed": None,
        "questions_path": str(inputs / "questions.json"),
        "haystack_path": str(inputs / "haystack.json"),
        "trajectories_path": str(inputs / "trajectories.jsonl"),
        "started_at_utc": "2026-09-14T00:00:00+00:00",
    }
    (root / "run_args.json").write_text(json.dumps(args), encoding="utf-8")
    rows = []
    for index, score in enumerate(scores):
        rows.append(
            {
                "question_id": f"q{index}",
                "question_type": "static-environment",
                "category": "static" if index < 2 else "dynamic",
                "is_abstention_problem": False,
                "eval_function": "exact",
                "question_text": f"Question {index}",
                "answer_gold": f"Answer {index}",
                "response_raw": "answer",
                "score_bool": score,
                "is_unknown": False,
                "usage": {
                    "prompt_tokens": 10 + index,
                    "completion_tokens": 2,
                    "total_tokens": 12 + index,
                },
                "memory_context_token_count": 5,
                "memory_query_duration_seconds": 0.1 + index,
            }
        )
    (root / "per_question.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    question_rows = "".join(
        json.dumps({"question_id": row["question_id"]}) + "\n" for row in rows
    )
    (root / "prompt_build_summary.json").write_text(json.dumps({
        "prompt_row_count": len(rows),
        "question_ids": [row["question_id"] for row in rows],
    }), encoding="utf-8")
    for name in ("prompt_rows.jsonl", "reader_outputs.checkpoint.jsonl"):
        (root / name).write_text(question_rows, encoding="utf-8")
    aggregate = {
        "overall": {
            "count_all_questions": len(rows),
            "overall_full_set": sum(scores) / len(scores),
        },
        "completed_at_utc": "2026-09-14T00:01:00+00:00",
    }
    (root / "aggregated_metrics.json").write_text(
        json.dumps(aggregate), encoding="utf-8"
    )
    return root


def _write_registration(path: Path, run: Path) -> Path:
    args = json.loads((run / "run_args.json").read_text())
    rows = [json.loads(line) for line in (run / "per_question.jsonl").read_text().splitlines()]
    ids = [row["question_id"] for row in rows]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["question_type"]] = counts.get(row["question_type"], 0) + 1
    def digest(value: str) -> str:
        return hashlib.sha256(Path(value).read_bytes()).hexdigest()
    value = {
        "selection": {
            "question_count": len(ids),
            "ordered_question_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
            "counts_by_type": counts,
            "questions_file_sha256": digest(args["questions_path"]),
            "haystack_file_sha256": digest(args["haystack_path"]),
        },
        "reader": {
            "model": args["model"],
            "reasoning_effort": args["reasoning_effort"],
            "reader_enable_thinking": args["reader_enable_thinking"],
            "temperature": args["temperature"],
            "top_p": args["top_p"],
            "top_k": args["top_k"],
            "max_completion_tokens": args["max_completion_tokens"],
            "max_concurrent_requests": args["reader_max_concurrent_requests"],
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_comparison_preserves_paired_direction_and_efficiency(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True, False, True])
    right = _write_run(tmp_path / "right", [False, True, False])
    result = compare(
        left, right, left_label="prme", right_label="no_memory", samples=100
    )
    assert result["overall"]["left_correct"] == 2
    assert result["overall"]["right_correct"] == 1
    paired = result["overall"]["paired_left_minus_right"]
    assert (paired["wins"], paired["losses"], paired["ties"]) == (2, 1, 0)
    assert paired["delta"] == pytest.approx(1 / 3)
    assert paired["exact_mcnemar_p_two_sided"] == 1.0
    assert result["efficiency"]["left"]["total_tokens"] == 39
    assert set(result["categories"]) == {"dynamic", "static"}


def test_comparison_rejects_reader_or_question_drift(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True])
    right = _write_run(tmp_path / "right", [True])
    args_path = right / "run_args.json"
    args = json.loads(args_path.read_text())
    args["model"] = "different-reader"
    args_path.write_text(json.dumps(args))
    with pytest.raises(ValueError, match="reader settings differ"):
        compare(left, right, left_label="left", right_label="right")

    args["model"] = "reader"
    args_path.write_text(json.dumps(args))
    rows_path = right / "per_question.jsonl"
    row = json.loads(rows_path.read_text())
    changed = deepcopy(row)
    changed["answer_gold"] = "changed"
    rows_path.write_text(json.dumps(changed) + "\n")
    with pytest.raises(ValueError, match="question inputs differ"):
        compare(left, right, left_label="left", right_label="right")


@pytest.mark.parametrize(
    ("artifact", "error"),
    [
        ("prompt_rows.jsonl", "artifact question identities are incomplete"),
        ("reader_outputs.checkpoint.jsonl", "artifact question identities are incomplete"),
    ],
)
def test_comparison_rejects_incomplete_intermediate_artifacts(
    tmp_path: Path, artifact: str, error: str
) -> None:
    left = _write_run(tmp_path / "left", [True, False])
    right = _write_run(tmp_path / "right", [True, False])
    (left / artifact).write_text('{"question_id": "q0"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        compare(left, right, left_label="left", right_label="right")


def test_comparison_rejects_aggregate_score_drift(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True, False])
    right = _write_run(tmp_path / "right", [True, False])
    aggregate_path = left / "aggregated_metrics.json"
    aggregate = json.loads(aggregate_path.read_text())
    aggregate["overall"]["overall_full_set"] = 1.0
    aggregate_path.write_text(json.dumps(aggregate))
    with pytest.raises(ValueError, match="aggregate score does not match rows"):
        compare(left, right, left_label="left", right_label="right")


def test_comparison_verifies_registered_cohort_and_reader(tmp_path: Path) -> None:
    left = _write_run(tmp_path / "left", [True, False])
    right = _write_run(tmp_path / "right", [True, False])
    registration = _write_registration(tmp_path / "registration.json", left)
    result = compare(
        left, right, left_label="left", right_label="right",
        registration=registration, samples=10,
    )
    assert result["registration_sha256"] == hashlib.sha256(registration.read_bytes()).hexdigest()

    registered = json.loads(registration.read_text())
    registered["selection"]["question_count"] = 1
    registration.write_text(json.dumps(registered))
    with pytest.raises(ValueError, match="cohort does not match"):
        compare(left, right, left_label="left", right_label="right",
                registration=registration, samples=10)
