from __future__ import annotations

from collections import Counter

from benchmarks.integrations import run_llm_aggrefact_support_certificate as subject


def _selection_rows() -> list[dict]:
    return [
        {
            "contamination_identifier": f"{dataset}-{label}-{index}",
            "dataset": dataset,
            "label": label,
            "doc": "Evidence.",
            "claim": "Claim.",
        }
        for dataset in subject.ELIGIBLE_DATASETS
        for label in (0, 1)
        for index in range(subject.PER_LABEL_PER_DATASET + 2)
    ]


def test_fresh_selection_is_balanced_disjoint_and_deterministic() -> None:
    rows = _selection_rows()
    excluded = frozenset(
        {
            f"{dataset}-{label}-0"
            for dataset in subject.ELIGIBLE_DATASETS
            for label in (0, 1)
        }
    )

    first = subject._select_fresh_cohort(rows, excluded_ids=excluded)
    second = subject._select_fresh_cohort(rows, excluded_ids=excluded)

    assert first == second
    assert len(first) == 800
    assert not excluded & {row["contamination_identifier"] for row in first}
    assert Counter((row["dataset"], row["label"]) for row in first) == {
        (dataset, label): 50
        for dataset in subject.ELIGIBLE_DATASETS
        for label in (0, 1)
    }


def _item() -> dict:
    return {
        "claim": "Alice likes tea and Bob likes coffee.",
        "evidence_segments": [
            ("E0001", "Alice likes tea."),
            ("E0002", "Bob may prefer coffee."),
        ],
    }


def _valid_arguments() -> dict:
    return {
        "atoms": [
            {
                "token_ids": ["C0001", "C0002", "C0003"],
                "status": "supported",
                "support_kind": "direct",
                "evidence_ids": ["E0001"],
            },
            {
                "token_ids": ["C0005", "C0006", "C0007"],
                "status": "unknown",
                "support_kind": "none",
                "evidence_ids": ["E0002"],
            },
        ]
    }


def test_certificate_validation_requires_complete_source_bound_atoms() -> None:
    valid, errors = subject._strict_validate(_item(), _valid_arguments())

    assert valid
    assert errors == ()

    missing = _valid_arguments()
    missing["atoms"].pop()
    valid, errors = subject._strict_validate(_item(), missing)
    assert not valid
    assert "missing_claim_tokens:C0005,C0006,C0007" in errors

    invalid_composed = _valid_arguments()
    invalid_composed["atoms"][0]["support_kind"] = "composed"
    valid, errors = subject._strict_validate(_item(), invalid_composed)
    assert not valid
    assert "atom_1_composed_without_multiple_evidence" in errors


def test_cli_deliberately_has_no_test_dataset_argument() -> None:
    parser = subject._parser()
    destinations = {action.dest for action in parser._actions}
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                destinations.update(item.dest for item in subparser._actions)

    assert "dev" in destinations
    assert "test" not in destinations
    assert "state" in destinations
