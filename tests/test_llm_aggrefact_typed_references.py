from __future__ import annotations

from collections import Counter

from benchmarks.integrations import run_llm_aggrefact_typed_references as subject


def _item(claim: str = "Maya moved to Rome and works as a surgeon."):
    return {
        "id": "case",
        "dataset": "dataset",
        "label": 0,
        "claim": claim,
        "source_chunks": 1,
        "selected_chunk_indices": [0],
        "selected_chunk_scores": [0.9],
        "evidence_segments": [
            ("E0001", "Maya moved to Rome. She works as an architect."),
        ],
    }


def _range(start: int, end: int) -> dict[str, str]:
    return {"start": f"C{start:04d}", "end": f"C{end:04d}"}


def _supported_atom(**overrides):
    values = {
        "claim": _range(1, 4),
        "subject": _range(1, 1),
        "relation": _range(2, 3),
        "object": _range(4, 4),
        "qualifiers": [],
        "status": "supported",
        "evidence": [
            {
                "evidence_id": "E0001",
                "subject": "aligned",
                "relation": "aligned",
                "object": "aligned",
                "qualifiers": "aligned",
            }
        ],
    }
    values.update(overrides)
    return subject._ClaimAtom.model_validate(values)


def test_new_cohort_excludes_base_and_prior_observed_identities() -> None:
    rows = []
    for label in (0, 1):
        for index in range(10):
            rows.append(
                {
                    "dataset": "d",
                    "label": label,
                    "doc": f"doc {label} {index}",
                    "claim": f"claim {label} {index}",
                    "contamination_identifier": f"{label}-{index}",
                }
            )
    excluded, initially_selected = subject._select_new_cohort(
        rows,
        excluded_seed="old",
        selection_seed="new",
        per_label_per_dataset=3,
    )
    prior_ids = {initially_selected[0]["contamination_identifier"]}

    _, selected = subject._select_new_cohort(
        rows,
        excluded_seed="old",
        selection_seed="new",
        per_label_per_dataset=3,
        prior_observed_ids=prior_ids,
    )

    assert len(excluded) == len(selected) == 6
    assert {row["contamination_identifier"] for row in excluded}.isdisjoint(
        row["contamination_identifier"] for row in selected
    )
    assert prior_ids.isdisjoint(row["contamination_identifier"] for row in selected)


def test_new_cohort_redistributes_a_group_capacity_shortfall() -> None:
    rows = []
    for label in (0, 1):
        for dataset, cases in (("small", 4), ("large", 10)):
            for index in range(cases):
                rows.append(
                    {
                        "dataset": dataset,
                        "label": label,
                        "doc": f"doc {dataset} {label} {index}",
                        "claim": f"claim {dataset} {label} {index}",
                        "contamination_identifier": f"{dataset}-{label}-{index}",
                    }
                )

    excluded, selected = subject._select_new_cohort(
        rows,
        excluded_seed="old",
        selection_seed="new",
        per_label_per_dataset=3,
    )

    assert len(excluded) == len(selected) == 12
    assert Counter((row["dataset"], row["label"]) for row in selected) == {
        ("small", 0): 1,
        ("large", 0): 5,
        ("small", 1): 1,
        ("large", 1): 5,
    }


def test_token_ranges_reconstruct_authoritative_source_text() -> None:
    claim = "Maya didn't move to Rome."
    tokens = subject._claim_tokens(claim)

    assert [token["text"] for token in tokens] == [
        "Maya",
        "didn't",
        "move",
        "to",
        "Rome",
        ".",
    ]
    assert (
        subject._range_text(
            claim, tokens, subject._TokenRange.model_validate(_range(2, 5))
        )
        == "didn't move to Rome"
    )


def test_reference_validator_accepts_complete_atomic_decomposition() -> None:
    verdict = subject._TypedVerdict(
        atoms=[
            _supported_atom(),
            _supported_atom(
                claim=_range(6, 9),
                subject=_range(1, 1),
                relation=_range(6, 7),
                object=_range(8, 9),
                status="unsupported",
                evidence=[
                    {
                        "evidence_id": "E0001",
                        "subject": "aligned",
                        "relation": "aligned",
                        "object": "conflict",
                        "qualifiers": "aligned",
                    }
                ],
            ),
        ]
    )

    valid, errors = subject._validate_typed_verdict(_item(), verdict)

    assert valid is True
    assert errors == ()


def test_validator_rejects_dropped_conjunct_and_unknown_evidence() -> None:
    verdict = subject._TypedVerdict(
        atoms=[
            _supported_atom(
                evidence=[
                    {
                        "evidence_id": "E9999",
                        "subject": "aligned",
                        "relation": "aligned",
                        "object": "aligned",
                        "qualifiers": "aligned",
                    }
                ]
            )
        ]
    )

    valid, errors = subject._validate_typed_verdict(_item(), verdict)

    assert valid is False
    assert "atom_0_unknown_evidence" in errors
    assert "incomplete_claim_word_coverage" in errors


def test_validator_rejects_supported_atom_with_conflicting_dimension() -> None:
    verdict = subject._TypedVerdict(
        atoms=[
            _supported_atom(
                evidence=[
                    {
                        "evidence_id": "E0001",
                        "subject": "aligned",
                        "relation": "aligned",
                        "object": "conflict",
                        "qualifiers": "aligned",
                    }
                ]
            )
        ]
    )

    valid, errors = subject._validate_typed_verdict(
        _item("Maya moved to Rome"), verdict
    )

    assert valid is False
    assert "atom_0_object_not_aligned" in errors


def test_validator_binds_relation_and_object_ranges_to_atom() -> None:
    verdict = subject._TypedVerdict(
        atoms=[
            _supported_atom(
                claim=_range(6, 9),
                subject=_range(1, 1),
                relation=_range(2, 3),
                object=_range(8, 9),
                status="unsupported",
            ),
            _supported_atom(),
        ]
    )

    valid, errors = subject._validate_typed_verdict(_item(), verdict)

    assert valid is False
    assert "atom_0_relation_outside_atom" in errors


def test_validator_requires_typed_ranges_to_cover_modifiers() -> None:
    item = _item("Maya possibly moved to Rome")
    verdict = subject._TypedVerdict(
        atoms=[_supported_atom(claim=_range(1, 5), relation=_range(3, 4))]
    )

    valid, errors = subject._validate_typed_verdict(item, verdict)

    assert valid is False
    assert "atom_0_incomplete_typed_coverage" in errors


def test_render_request_exposes_only_source_bound_identifiers() -> None:
    rendered = subject._render_request(_item("Maya moved."))

    assert "[C0001] Maya [C0002] moved [C0003] ." in rendered
    assert "[E0001] Maya moved to Rome." in rendered


def test_public_sample_contains_hashes_and_scores_without_source_text() -> None:
    item = _item("Maya moved to Rome")
    verdict = subject._TypedVerdict(atoms=[_supported_atom()])
    record = {
        "schema_success": True,
        "reference_integrity": True,
        "validation_errors": [],
        "verdict": verdict.model_dump(mode="json"),
        "attempts": 1,
        "elapsed_seconds": 1.0,
    }

    sample = subject._public_sample(item, record, [0.91])

    assert sample["support_probability"] == 0.91
    assert sample["response_sha256"]
    serialized = subject.json.dumps(sample)
    assert "Maya moved" not in serialized
    assert "architect" not in serialized


def test_schema_failure_is_a_complete_fail_closed_sample() -> None:
    record = {
        "schema_success": False,
        "reference_integrity": False,
        "validation_errors": ["provider_or_schema_failure"],
        "error_type": "ValidationError",
        "attempts": 3,
        "elapsed_seconds": 1.0,
    }

    sample = subject._public_sample(_item(), record, [])

    assert sample["schema_success"] is False
    assert sample["support_probability"] == 0.0
    assert sample["response_sha256"] is None


def test_calibration_uses_minimum_atomic_support_score() -> None:
    samples = [
        {
            "label": 1,
            "support_probability": 0.9,
            "schema_success": True,
            "reference_integrity": True,
        },
        {
            "label": 1,
            "support_probability": 0.8,
            "schema_success": True,
            "reference_integrity": True,
        },
        {
            "label": 0,
            "support_probability": 0.7,
            "schema_success": True,
            "reference_integrity": True,
        },
        {
            "label": 0,
            "support_probability": 0.0,
            "schema_success": True,
            "reference_integrity": True,
        },
    ]
    evaluation = {
        "calibration": {"supported_precision_min": 1.0, "supported_recall_min": 1.0},
        "gates": {"schema_success_rate_min": 1.0, "reference_integrity_rate_min": 1.0},
    }

    threshold, metrics = subject._calibrate_threshold(samples, evaluation)

    assert threshold == 0.7
    assert metrics is not None
    assert metrics["supported_precision"] == 1.0
    assert metrics["supported_recall"] == 1.0


def test_cli_deliberately_has_no_test_dataset_argument() -> None:
    actions = {action.dest for action in subject._parser()._actions}

    assert "dev" in actions
    assert "test" not in actions
