from __future__ import annotations

from benchmarks.diagnostics import llm_aggrefact_partition_scoring as subject


def _prepared() -> list[dict]:
    return [
        {
            "id": "case",
            "dataset": "d",
            "label": 1,
            "claim": "Maya moved to Rome and works as a surgeon.",
            "evidence_segments": [("E0001", "Maya moved to Rome.")],
        }
    ]


def _state() -> dict:
    return {
        "jobs": {
            "case": {
                "status": "complete",
                "semantic_attempts": [
                    {
                        "validation_errors": [],
                        "arguments": {
                            "atoms": [
                                {
                                    "token_ids": [
                                        "C0001",
                                        "C0002",
                                        "C0003",
                                        "C0004",
                                    ]
                                },
                                {
                                    "token_ids": [
                                        "C0001",
                                        "C0006",
                                        "C0007",
                                        "C0009",
                                    ]
                                },
                            ]
                        },
                    }
                ],
            }
        }
    }


def test_atomic_pairs_use_reconstructed_source_and_all_ranked_evidence() -> None:
    pairs, owners, metadata = subject._build_atomic_pairs(_prepared(), _state())

    assert owners == ["case", "case"]
    assert [pair["claim"] for pair in pairs] == [
        "Maya moved to Rome",
        "Maya works as a surgeon",
    ]
    assert all(pair["doc"] == "Maya moved to Rome." for pair in pairs)
    assert metadata["case"]["atom_count"] == 2
    assert metadata["case"]["partition_status"] == "complete"
    assert metadata["case"]["partition_sha256"]
    assert metadata["case"]["atom_source_sha256"]


def test_atomic_pairs_preserve_safe_abstention_without_fallback() -> None:
    state = _state()
    state["jobs"]["case"] = {
        "status": "semantic_exhausted",
        "semantic_attempts": [],
    }

    pairs, owners, metadata = subject._build_atomic_pairs(_prepared(), state)

    assert pairs == []
    assert owners == []
    assert metadata["case"] == {
        "partition_status": "safe_abstention",
        "atom_count": 0,
        "partition_sha256": None,
        "atom_source_sha256": None,
    }


def test_diagnostics_report_required_operating_points() -> None:
    samples = [
        {"label": 1, "support_probability": 0.9},
        {"label": 1, "support_probability": 0.8},
        {"label": 0, "support_probability": 0.7},
        {"label": 0, "support_probability": 0.1},
    ]

    result = subject._diagnostics(samples)

    assert result["best_balanced_accuracy"]["balanced_accuracy"] == 1.0
    assert result["best_recall_at_precision_min"]["supported_precision"] >= 0.9
    assert result["best_precision_at_recall_min"]["supported_recall"] >= 0.6


def test_unpartitioned_pairs_use_original_claim_and_ranked_evidence() -> None:
    pairs = subject._build_unpartitioned_pairs(_prepared())

    assert pairs == [
        {
            "dataset": "d",
            "doc": "Maya moved to Rome.",
            "claim": "Maya moved to Rome and works as a surgeon.",
            "label": 1,
            "contamination_identifier": "case:unpartitioned",
        }
    ]


def test_cli_deliberately_has_no_test_dataset_argument() -> None:
    actions = {action.dest: action for action in subject._parser()._actions}

    assert "dev" in actions
    assert "test" not in actions
    assert actions["cohort"].default == "failures"
