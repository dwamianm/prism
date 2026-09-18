"""Tests for the development-calibrated Jev product proposal rule."""

from __future__ import annotations

from benchmarks.integrations import (
    run_jev_candidate_calibrated_confirmation as runner,
)


def _answers(*, score: float, same_name: float, price: float) -> dict:
    return {
        "link_state": {"score": score},
        "same_name": {"noul": same_name},
        "compatible_price": {"noul": price},
    }


def test_calibrated_decision_requires_every_typed_signal() -> None:
    assert runner._decision(_answers(score=1.6, same_name=0.8, price=0.5))
    assert not runner._decision(_answers(score=1.59, same_name=0.8, price=0.5))
    assert not runner._decision(_answers(score=1.6, same_name=0.79, price=0.5))
    assert not runner._decision(_answers(score=1.6, same_name=0.8, price=0.49))


def test_calibrated_protocol_keeps_merge_forbidden() -> None:
    protocol = runner._protocol()

    assert protocol["proposal_rule"] == {
        "link_state_score_min": 1.6,
        "same_name_noul_min": 0.8,
        "compatible_price_noul_min": 0.5,
        "operator": "all",
    }
    assert protocol["mutation_authorized"] is False
    assert protocol["automatic_merge_authorized"] is False


def test_confirmation_gate_rejects_excess_false_positive_rate() -> None:
    registration = {"evaluation": runner._evaluation(100, 70)}
    gates = runner._gate(
        candidate_recall=0.98,
        pipeline={
            "proposal_precision": 0.94,
            "proposal_recall": 0.55,
            "false_positive_rate": 0.011,
        },
        baseline={"proposal_recall": 0.05},
        response_count=70,
        pair_count=100,
        reduction=0.995,
        p95=0.4,
        registration=registration,
    )

    assert gates["false_positive_rate_max"] is False
    assert not all(gates.values())
