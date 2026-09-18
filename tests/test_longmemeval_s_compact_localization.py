from __future__ import annotations

import pytest

from benchmarks.diagnostics import longmemeval_s_compact_localization as diagnostic
from benchmarks.diagnostics.longmemeval_s_compact import _sha256


def execution_with_transitions(count: int = 20) -> dict:
    judgments = []
    for index in range(119):
        question_id = f"q{index:03d}"
        control = index < count
        monotonic = False
        for arm, correct in (("control", control), ("monotonic", monotonic)):
            judgments.append(
                {"id": f"{question_id}:{arm}", "correct": correct, "reason": "x"}
            )
    return {"judge": {"judgments": judgments}}


def test_transition_ids_require_the_observed_complete_cohort() -> None:
    assert diagnostic._transition_ids(execution_with_transitions()) == [
        f"q{index:03d}" for index in range(20)
    ]
    with pytest.raises(ValueError, match="20 observed"):
        diagnostic._transition_ids(execution_with_transitions(19))


def test_metrics_compare_exact_paired_coverage() -> None:
    cases = [
        {"id": "a:control", "question_id": "a", "arm": "control", "category": "multi-session"},
        {"id": "a:same_set_compact", "question_id": "a", "arm": "same_set_compact", "category": "multi-session"},
        {"id": "b:control", "question_id": "b", "arm": "control", "category": "temporal-reasoning"},
        {"id": "b:same_set_compact", "question_id": "b", "arm": "same_set_compact", "category": "temporal-reasoning"},
    ]
    judgments = {
        "complete": True,
        "prior_failed_attempts": [],
        "judgments": [
            {"id": "a:control", "correct": False},
            {"id": "a:same_set_compact", "correct": True},
            {"id": "b:control", "correct": True},
            {"id": "b:same_set_compact", "correct": False},
        ],
    }

    result = diagnostic._metrics(cases, judgments)

    assert result["overall"] == {
        "questions": 2,
        "control_correct": 1,
        "same_set_compact_correct": 1,
        "paired_wins": 1,
        "paired_losses": 1,
        "paired_ties": 0,
    }
    assert result["reader_failed_attempts"] == 0
    assert result["judge_failed_attempts"] == 0


def test_reader_payload_binds_only_the_selected_context() -> None:
    context = "compact memory"
    row = {
        "question": "What happened?",
        "question_date": "2023/01/01 (Sun) 00:00",
        "contexts": {
            "same_set_compact": {
                "context": context,
                "sha256": _sha256(context.encode()),
            }
        },
    }

    payload = diagnostic._reader_payload(row, "same_set_compact", "reader:cloud")

    assert payload["model"] == "reader:cloud"
    assert context in payload["messages"][1]["content"]
    assert "reference" not in payload["messages"][1]["content"].lower()
    assert payload["think"] is False


def test_protocol_cannot_be_used_for_promotion() -> None:
    protocol = diagnostic._protocol()

    assert "cannot promote" in protocol["status"]
    assert protocol["arms"] == ["control", "same_set_compact"]
