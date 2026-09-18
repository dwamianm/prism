"""Contracts for the monotonic compact paired answer trial."""

from __future__ import annotations

from benchmarks.diagnostics import longmemeval_s_monotonic_answer as runner


def _case(question_id: str, arm: str, category: str = "multi-session") -> dict:
    return {
        "id": f"{question_id}:{arm}",
        "question_id": question_id,
        "arm": arm,
        "category": category,
    }


def _judgments(values: dict[str, bool]) -> dict:
    return {
        "complete": True,
        "prior_failed_attempts": [],
        "judgments": [
            {"id": identity, "correct": correct} for identity, correct in values.items()
        ],
    }


def test_metrics_accept_noninferior_candidate_without_category_loss() -> None:
    cases = [
        _case("q1", "control"),
        _case("q1", "monotonic"),
        _case("q2", "control", "temporal-reasoning"),
        _case("q2", "monotonic", "temporal-reasoning"),
    ]
    result = runner._metrics(
        cases,
        _judgments(
            {
                "q1:control": False,
                "q1:monotonic": True,
                "q2:control": True,
                "q2:monotonic": True,
            }
        ),
    )

    assert result["gate"]["passed"] is True
    assert result["overall"]["paired_wins"] == 1
    assert result["overall"]["paired_losses"] == 0


def test_metrics_reject_overall_and_category_regression() -> None:
    cases = [
        _case("q1", "control"),
        _case("q1", "monotonic"),
        _case("q2", "control", "temporal-reasoning"),
        _case("q2", "monotonic", "temporal-reasoning"),
    ]
    result = runner._metrics(
        cases,
        _judgments(
            {
                "q1:control": True,
                "q1:monotonic": False,
                "q2:control": True,
                "q2:monotonic": True,
            }
        ),
    )

    assert result["gate"]["passed"] is False
    assert result["overall"]["paired_losses"] == 1
    assert result["gate"]["category_regressions"] == 1


def test_reader_payload_contains_no_reference_answer() -> None:
    row = {
        "question_id": "q1",
        "question": "Which database?",
        "question_date": "2026-01-01",
        "contexts": {
            "control": {
                "context": "PostgreSQL was selected.",
                "sha256": runner._sha256(b"PostgreSQL was selected."),
                "tokens": 5,
            }
        },
    }

    payload = runner._reader_payload(row, "control", "reader")

    assert payload["model"] == "reader"
    assert "GOLD_CANARY" not in runner._canonical(payload).decode()
    assert "PostgreSQL was selected." in payload["messages"][1]["content"]
