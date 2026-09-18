"""Tests for the frozen candidate-routing plus Jev evaluation protocol."""

from __future__ import annotations

import pytest

from benchmarks.integrations import run_jev_candidate_pipeline as runner


def test_metrics_measure_precision_recall_and_false_positive_rate() -> None:
    metrics = runner._metrics(
        [True, True, False, False, False],
        [True, False, True, False, False],
    )

    assert metrics == {
        "tp": 1,
        "fp": 1,
        "tn": 2,
        "fn": 1,
        "accuracy": 0.6,
        "proposal_precision": 0.5,
        "proposal_recall": 0.5,
        "f1": 0.5,
        "false_positive_rate": pytest.approx(1 / 3),
    }


def test_pipeline_gate_requires_additive_recall_and_safe_proposals() -> None:
    registration = {"evaluation": runner._evaluation(100, 70)}
    baseline = {
        "proposal_recall": 0.25,
    }
    pipeline = {
        "proposal_precision": 0.95,
        "proposal_recall": 0.60,
        "false_positive_rate": 0.02,
    }

    gates = runner._gate(
        candidate_recall=0.98,
        pipeline=pipeline,
        baseline=baseline,
        response_count=70,
        pair_count=100,
        reduction=0.995,
        p95=0.4,
        registration=registration,
    )

    assert all(gates.values())


def test_pipeline_gate_rejects_low_precision_even_with_high_recall() -> None:
    registration = {"evaluation": runner._evaluation(100, 70)}

    gates = runner._gate(
        candidate_recall=1.0,
        pipeline={
            "proposal_precision": 0.89,
            "proposal_recall": 0.90,
            "false_positive_rate": 0.02,
        },
        baseline={"proposal_recall": 0.10},
        response_count=70,
        pair_count=100,
        reduction=0.995,
        p95=0.4,
        registration=registration,
    )

    assert gates["proposal_precision_min"] is False
    assert not all(gates.values())


def test_protocol_keeps_automatic_merge_forbidden() -> None:
    protocol = runner._protocol()

    assert protocol["mutation_authorized"] is False
    assert protocol["automatic_merge_authorized"] is False
    assert protocol["proposal_rule"] == "link_state.score >= 1.5"
