from __future__ import annotations

import pytest

from benchmarks.integrations import run_jev_claim_verification as runner


def _response() -> dict:
    return {
        "model": runner.MODEL,
        "answers": {
            "fully_supported": {"type": "noul", "noul": 0.91},
            "has_unsupported_content": {"type": "noul", "noul": 0.08},
            "relationship": {
                "type": "choice",
                "choice": "fully_supported",
                "confidence": 0.87,
                "probabilities": {
                    "fully_supported": 0.9,
                    "partially_supported": 0.07,
                    "contradicted": 0.02,
                    "not_addressed": 0.01,
                },
            },
        },
        "usage": {"input_tokens": 120, "output_tokens": 40},
    }


def test_response_validation_and_frozen_arms() -> None:
    response = runner._validate_response(_response())

    assert runner._arm_decisions(response) == {
        "choice_top": True,
        "direct_noul": True,
        "conservative_agreement": True,
    }


def test_conservative_arm_fails_closed_on_inverse_disagreement() -> None:
    response = _response()
    response["answers"]["has_unsupported_content"]["noul"] = 0.72

    assert runner._arm_decisions(runner._validate_response(response)) == {
        "choice_top": True,
        "direct_noul": True,
        "conservative_agreement": False,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(model="jev-latest"), "model differs"),
        (
            lambda value: value["answers"]["relationship"]["probabilities"].pop(
                "not_addressed"
            ),
            "probabilities are invalid",
        ),
        (
            lambda value: value["answers"]["fully_supported"].update(noul=1.2),
            "probability is invalid",
        ),
    ],
)
def test_response_validation_rejects_unbound_outputs(mutation, message: str) -> None:
    response = _response()
    mutation(response)

    with pytest.raises(ValueError, match=message):
        runner._validate_response(response)


def test_frozen_gate_requires_safety_and_utility() -> None:
    registration = runner._evaluation(400)
    metrics = {
        "tp": 120,
        "fp": 10,
        "tn": 190,
        "fn": 80,
        "supported_precision": 120 / 130,
        "supported_recall": 0.6,
        "balanced_accuracy": 0.775,
        "false_support_rate": 0.05,
    }

    gates = runner._gate_arm(
        metrics,
        400,
        {"evaluation": registration},
    )

    assert all(gates.values())

    metrics["supported_precision"] = 0.89
    assert not runner._gate_arm(
        metrics,
        400,
        {"evaluation": registration},
    )["supported_precision_min"]
