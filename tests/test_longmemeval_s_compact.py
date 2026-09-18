"""Fail-closed contracts for the LongMemEval-S compact-packing trial."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.diagnostics import longmemeval_s_compact as runner


def _case() -> dict:
    return {
        "question_id": "q1",
        "question_type": "multi-session",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "Where should we meet?"},
                {
                    "role": "assistant",
                    "content": "The library works.",
                    "has_answer": True,
                },
            ],
            [
                {
                    "role": "user",
                    "content": "Use the west entrance.",
                    "has_answer": True,
                }
            ],
        ],
        "answer_session_ids": ["s1", "s2"],
    }


def _source(session: str, position: int, turn: int) -> dict:
    return {
        "node_id": f"{session}-{turn}",
        "source_session_id": session,
        "source_session_position": position,
        "source_turn_index": turn,
    }


def _row(
    *,
    question_id: str = "q1",
    control: dict,
    compact: dict,
    control_tokens: int = 3900,
    compact_tokens: int = 3900,
) -> dict:
    return {
        "question_id": question_id,
        "question_type": "multi-session",
        "case_sha256": question_id * 8,
        "arms": {
            "control": {
                "evidence": control,
                "tokens": control_tokens,
                "records": 20,
            },
            "compact": {
                "evidence": compact,
                "tokens": compact_tokens,
                "records": 30,
            },
        },
    }


def _evidence(*, turn_recall: float, complete: bool) -> dict:
    return {
        "applicable": True,
        "required_sessions": 2,
        "retrieved_sessions": 2 if complete else 1,
        "session_recall": 1.0 if complete else 0.5,
        "complete_session_recall": complete,
        "required_turns": 2,
        "retrieved_turns": round(turn_recall * 2),
        "turn_recall": turn_recall,
        "complete_turn_recall": complete,
    }


def _registration(tmp_path: Path, split: str = "dev") -> dict:
    path = tmp_path / "registration.json"
    path.write_text(json.dumps({"split": split}))
    return {"dataset": {"split": split}, "_path": str(path)}


def test_evidence_requires_every_labeled_session_and_turn() -> None:
    case = _case()

    partial = runner._evidence(case, [_source("s1", 0, 1)])
    complete = runner._evidence(
        case,
        [_source("s1", 0, 1), _source("s2", 1, 0)],
    )

    assert partial == {
        "applicable": True,
        "required_sessions": 2,
        "retrieved_sessions": 1,
        "session_recall": 0.5,
        "complete_session_recall": False,
        "required_turns": 2,
        "retrieved_turns": 1,
        "turn_recall": 0.5,
        "complete_turn_recall": False,
    }
    assert complete["complete_session_recall"] is True
    assert complete["complete_turn_recall"] is True
    assert runner._evidence({**case, "question_id": "q1_abs"}, []) == {
        "applicable": False
    }


def test_development_gate_requires_a_strict_complete_turn_improvement(
    tmp_path: Path,
) -> None:
    partial = _evidence(turn_recall=0.5, complete=False)
    complete = _evidence(turn_recall=1.0, complete=True)

    improved = runner._summary(
        [_row(control=partial, compact=complete)],
        _registration(tmp_path),
    )
    tied = runner._summary(
        [_row(control=complete, compact=complete)],
        _registration(tmp_path),
    )

    assert improved["gate"]["passed"] is True
    assert improved["comparison"]["turn_recall_wins"] == 1
    assert tied["gate"]["passed"] is False
    assert tied["gate"]["complete_turn_questions_improved"] is False


def test_gate_rejects_any_source_loss_or_budget_violation(tmp_path: Path) -> None:
    partial = _evidence(turn_recall=0.5, complete=False)
    complete = _evidence(turn_recall=1.0, complete=True)

    source_loss = runner._summary(
        [_row(control=complete, compact=partial)],
        _registration(tmp_path),
    )
    over_budget = runner._summary(
        [_row(control=partial, compact=complete, compact_tokens=3997)],
        _registration(tmp_path),
    )

    assert source_loss["gate"]["passed"] is False
    assert source_loss["gate"]["per_question_turn_recall_losses"] == 1
    assert source_loss["gate"]["per_question_session_recall_losses"] == 1
    assert source_loss["gate"]["category_mean_turn_recall_losses"] == 1
    assert over_budget["gate"]["passed"] is False
    assert over_budget["gate"]["budget_violations"] == 1


def test_test_split_allows_a_no_loss_complete_turn_tie(tmp_path: Path) -> None:
    complete = _evidence(turn_recall=1.0, complete=True)

    result = runner._summary(
        [_row(control=complete, compact=complete)],
        _registration(tmp_path, split="test"),
    )

    assert result["gate"]["passed"] is True
    assert result["gate"]["complete_turn_questions_improved"] is False


def test_development_result_identity_requires_an_intact_passing_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "development.json"
    value = {
        "kind": "longmemeval-s-compact-packing-result",
        "split": "dev",
        "registration_sha256": "a" * 64,
        "gate": {"passed": True},
    }
    value["result_sha256"] = runner._sha256(runner._canonical(value))
    path.write_text(json.dumps(value))

    assert runner._development_result_identity(path) == runner._sha256_file(path)

    value["gate"]["passed"] = False
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="valid passing result"):
        runner._development_result_identity(path)
