from __future__ import annotations

import math

import pytest

from benchmarks.integrations import run_llm_aggrefact_structured_stack as subject


def test_alignment_features_retain_structured_mismatches() -> None:
    features = dict(
        zip(
            subject.FEATURE_NAMES,
            subject._alignment_features(
                "Alice moved to Paris in 2024. She did not move to Rome.",
                "Alice moved to Paris in 2025.",
                0.8,
            ),
            strict=True,
        )
    )

    assert features["factcg_logit"] == pytest.approx(math.log(4))
    assert features["alignment_min"] == pytest.approx(0.75)
    assert features["alignment_mean"] == pytest.approx(0.75)
    assert features["numeric_anchor_coverage"] == 0.0
    assert features["capitalized_anchor_coverage"] == 1.0
    assert features["negation_alignment_min"] == 1.0
    assert features["factcg_alignment_interaction"] == pytest.approx(math.log(4) * 0.75)


def test_group_fold_is_stable_and_document_scoped() -> None:
    first = subject._group_identity("dataset", "same document")
    second = subject._group_identity("dataset", "same document")
    other = subject._group_identity("dataset", "other document")

    assert first == second
    assert first != other
    assert subject._fold_for_group(first, seed="seed", folds=5) == subject._fold_for_group(
        second,
        seed="seed",
        folds=5,
    )


def test_decision_metrics_are_complete() -> None:
    metrics = subject._decision_metrics([1, 1, 0, 0], [True, False, True, False])

    assert metrics == {
        "tp": 1,
        "fp": 1,
        "tn": 1,
        "fn": 1,
        "accuracy": 0.5,
        "balanced_accuracy": 0.5,
        "supported_precision": 0.5,
        "supported_recall": 0.5,
        "supported_f1": 0.5,
        "false_support_rate": 0.5,
    }
