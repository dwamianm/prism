from __future__ import annotations

import copy

import pytest

from benchmarks.integrations import run_jev_product_alignment as runner


def _response() -> dict:
    return {
        "model": runner.MODEL,
        "answers": {
            "link_state": {
                "type": "score",
                "score": 1.91,
                "legend": {
                    str(index): value for index, value in enumerate(runner.LEVELS)
                },
                "probabilities": {"0": 0.01, "1": 0.07, "2": 0.92},
                "confidence": 0.88,
            },
            "same_name": {"type": "noul", "noul": 0.95},
            "same_manufacturer": {"type": "noul", "noul": 0.94},
            "compatible_price": {"type": "noul", "noul": 0.76},
        },
        "usage": {"input_tokens": 200, "output_tokens": 60},
    }


def test_response_validation_accepts_bound_generic_product_output() -> None:
    response = runner._validate_response(_response())

    assert response["answers"]["link_state"]["score"] >= 1.5


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(model="jev-latest"), "model differs"),
        (
            lambda value: value["answers"]["link_state"]["legend"].update(
                {"2": "similar"}
            ),
            "distribution is invalid",
        ),
        (
            lambda value: value["answers"]["same_manufacturer"].update(noul=1.2),
            "same_manufacturer answer is invalid",
        ),
    ],
)
def test_response_validation_rejects_unbound_outputs(mutation, message: str) -> None:
    response = copy.deepcopy(_response())
    mutation(response)

    with pytest.raises(ValueError, match=message):
        runner._validate_response(response)


def test_proposal_metrics_and_gates_measure_additive_value() -> None:
    labels = [True] * 200 + [False] * 200
    baseline = runner._metrics(labels, [True] * 20 + [False] * 380)
    candidate = runner._metrics(
        labels,
        [True] * 120 + [False] * 80 + [True] * 10 + [False] * 190,
    )
    registration = {"evaluation": runner._evaluation(400)}

    gates = runner._gate(
        candidate=candidate,
        baseline=baseline,
        valid=400,
        p95=0.25,
        registration=registration,
    )

    assert candidate["proposal_precision"] == pytest.approx(120 / 130)
    assert candidate["proposal_recall"] == 0.6
    assert all(gates.values())


def test_exact_name_baseline_normalizes_case_and_whitespace_only() -> None:
    pair = {
        "entity_a": {"name": "  Product   Pro  "},
        "entity_b": {"name": "product pro"},
    }

    assert runner._baseline_decision(pair)
    pair["entity_b"]["name"] = "Product Pro 2"
    assert not runner._baseline_decision(pair)
