from __future__ import annotations

import pytest

from benchmarks.diagnostics import llm_aggrefact_evidence_lattice as subject


def _prepared() -> list[dict]:
    return [
        {
            "id": "case",
            "dataset": "d",
            "label": 1,
            "claim": "Maya moved to Rome and works as a surgeon.",
            "evidence_segments": [
                ("E0001", "Maya moved to Rome."),
                ("E0002", "She works as a surgeon."),
            ],
        }
    ]


def test_lattice_pairs_preserve_whole_claim_and_evidence_orders() -> None:
    pairs, keys = subject._build_lattice_pairs(_prepared())

    assert keys == [
        ("case", "single_0"),
        ("case", "single_1"),
        ("case", "group_ordered"),
        ("case", "group_reversed"),
    ]
    assert all(
        pair["claim"] == "Maya moved to Rome and works as a surgeon." for pair in pairs
    )
    assert [pair["doc"] for pair in pairs] == [
        "Maya moved to Rome.",
        "She works as a surgeon.",
        "Maya moved to Rome.\nShe works as a surgeon.",
        "She works as a surgeon.\nMaya moved to Rome.",
    ]


def test_collect_samples_builds_static_lattice_projections() -> None:
    _pairs, keys = subject._build_lattice_pairs(_prepared())
    scored = [{"support_probability": value} for value in (0.6, 0.4, 0.8, 0.7)]

    samples = subject._collect_samples(_prepared(), keys, scored)

    assert samples == [
        {
            "id": "case",
            "dataset": "d",
            "label": 1,
            "evidence_count": 2,
            "single_support_probabilities": [0.6, 0.4],
            "group_ordered_support_probability": 0.8,
            "group_reversed_support_probability": 0.7,
            "projections": {
                "ordered_group": 0.8,
                "order_robust_group": 0.7,
                "max_single": 0.6,
                "max_subset": 0.8,
                "order_robust_max_subset": 0.7,
                "single_group_consensus": 0.6,
            },
        }
    ]


def test_single_evidence_reuses_ordered_group_score() -> None:
    prepared = _prepared()
    prepared[0]["evidence_segments"] = prepared[0]["evidence_segments"][:1]
    _pairs, keys = subject._build_lattice_pairs(prepared)
    samples = subject._collect_samples(
        prepared,
        keys,
        [{"support_probability": 0.4}, {"support_probability": 0.6}],
    )

    assert keys == [("case", "single_0"), ("case", "group_ordered")]
    assert samples[0]["group_reversed_support_probability"] == 0.6
    assert samples[0]["projections"]["max_subset"] == 0.6


def test_control_validation_allows_bounded_mps_variation() -> None:
    samples = [
        {
            "id": "case",
            "label": 1,
            "projections": {"ordered_group": 0.600001},
        }
    ]
    control = {
        "scoring": {
            "samples": [
                {
                    "id": "case",
                    "label": 1,
                    "unpartitioned_support_probability": 0.6,
                }
            ]
        }
    }

    difference = subject._validate_control_scores(samples, control)

    assert 0 < difference < subject.CONTROL_SCORE_TOLERANCE

    samples[0]["projections"]["ordered_group"] = 0.7
    with pytest.raises(ValueError, match="max absolute difference"):
        subject._validate_control_scores(samples, control)


def test_cli_deliberately_has_no_test_dataset_argument() -> None:
    actions = {action.dest for action in subject._parser()._actions}

    assert "dev" in actions
    assert "test" not in actions
