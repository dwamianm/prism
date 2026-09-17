from __future__ import annotations

import pytest

from benchmarks.integrations import run_jev_entity_alignment as runner


def _response() -> dict:
    return {
        "model": runner.MODEL,
        "answers": {
            "link_state": {
                "type": "score",
                "score": 1.91,
                "legend": {str(index): value for index, value in enumerate(runner.LEVELS)},
                "probabilities": {"0": 0.01, "1": 0.07, "2": 0.92},
                "confidence": 0.88,
            },
            "same_name": {"type": "noul", "noul": 0.95},
            "same_brewery": {"type": "noul", "noul": 0.94},
            "same_style": {"type": "noul", "noul": 0.76},
        },
        "usage": {"input_tokens": 200, "output_tokens": 60},
    }


def test_response_validation_and_frozen_alignment_arms() -> None:
    response = runner._validate_response(_response())

    assert runner._decisions(response) == {
        "score_route": True,
        "confident_score": True,
        "field_guarded": True,
    }


def test_field_guard_fails_closed_without_brewery_agreement() -> None:
    response = _response()
    response["answers"]["same_brewery"]["noul"] = 0.3

    assert runner._decisions(runner._validate_response(response)) == {
        "score_route": True,
        "confident_score": True,
        "field_guarded": False,
    }


def test_response_rejects_wrong_score_legend() -> None:
    response = _response()
    response["answers"]["link_state"]["legend"]["2"] = "Anything"

    with pytest.raises(ValueError, match="distribution is invalid"):
        runner._validate_response(response)


def test_metrics_and_gates_require_safe_recall_gain() -> None:
    labels = [True] * 40 + [False] * 228
    predictions = [True] * 22 + [False] * 18 + [True] + [False] * 227
    metrics = runner._metrics(labels, predictions)
    registration = {"evaluation": runner._evaluation(len(labels))}

    assert metrics["merge_precision"] == pytest.approx(22 / 23)
    assert not runner._gate(
        metrics,
        valid=len(labels),
        baseline_recall=0.2,
        registration=registration,
    )["merge_precision_min"]

    safe_predictions = [True] * 22 + [False] * 18 + [False] * 228
    safe = runner._metrics(labels, safe_predictions)
    assert all(
        runner._gate(
            safe,
            valid=len(labels),
            baseline_recall=0.2,
            registration=registration,
        ).values()
    )
