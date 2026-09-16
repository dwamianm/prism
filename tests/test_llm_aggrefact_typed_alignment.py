from __future__ import annotations

from benchmarks.integrations import run_llm_aggrefact_typed_alignment as subject


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


def _supported_atom(**overrides):
    values = {
        "claim_span": "Maya moved to Rome",
        "subject_span": "Maya",
        "relation_span": "moved to",
        "object_span": "Rome",
        "qualifier_spans": [],
        "status": "supported",
        "evidence": [
            {
                "evidence_id": "E0001",
                "quote": "Maya moved to Rome.",
                "subject": "aligned",
                "relation": "aligned",
                "object": "aligned",
                "qualifiers": "aligned",
            }
        ],
    }
    values.update(overrides)
    return subject._ClaimAtom.model_validate(values)


def test_new_cohort_excludes_every_previously_selected_identity() -> None:
    rows = []
    for label in (0, 1):
        for index in range(8):
            rows.append(
                {
                    "dataset": "d",
                    "label": label,
                    "doc": f"doc {label} {index}",
                    "claim": f"claim {label} {index}",
                    "contamination_identifier": f"{label}-{index}",
                }
            )

    excluded, selected = subject._select_new_cohort(
        rows,
        excluded_seed="old",
        selection_seed="new",
        per_label_per_dataset=3,
    )

    assert len(excluded) == len(selected) == 6
    assert {row["contamination_identifier"] for row in excluded}.isdisjoint(
        row["contamination_identifier"] for row in selected
    )


def test_exact_span_validator_accepts_complete_atomic_decomposition() -> None:
    verdict = subject._TypedVerdict(
        atoms=[
            _supported_atom(),
            _supported_atom(
                claim_span="works as a surgeon",
                relation_span="works as",
                object_span="a surgeon",
                status="unsupported",
                evidence=[
                    {
                        "evidence_id": "E0001",
                        "quote": "She works as an architect.",
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


def test_validator_rejects_dropped_conjunct_and_nonexact_quote() -> None:
    verdict = subject._TypedVerdict(
        atoms=[
            _supported_atom(
                evidence=[
                    {
                        "evidence_id": "E0001",
                        "quote": "Maya definitely moved to Rome.",
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
    assert "atom_0_quote_not_exact" in errors
    assert "incomplete_claim_content_coverage" in errors


def test_validator_rejects_supported_atom_with_conflicting_dimension() -> None:
    verdict = subject._TypedVerdict(
        atoms=[
            _supported_atom(
                evidence=[
                    {
                        "evidence_id": "E0001",
                        "quote": "Maya moved to Rome.",
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


def test_validator_binds_relation_and_object_to_their_atom() -> None:
    verdict = subject._TypedVerdict(
        atoms=[
            _supported_atom(
                claim_span="works as a surgeon",
                relation_span="moved to",
                object_span="a surgeon",
                status="unsupported",
            ),
            _supported_atom(),
        ]
    )

    valid, errors = subject._validate_typed_verdict(_item(), verdict)

    assert valid is False
    assert "atom_0_relation_span_outside_atom" in errors


def test_validator_requires_typed_fields_to_cover_atomic_modifiers() -> None:
    item = _item("Maya possibly moved to Rome")
    verdict = subject._TypedVerdict(
        atoms=[_supported_atom(claim_span="Maya possibly moved to Rome")]
    )

    valid, errors = subject._validate_typed_verdict(item, verdict)

    assert valid is False
    assert "atom_0_incomplete_typed_coverage" in errors


def test_public_sample_contains_hashes_and_scores_without_source_text() -> None:
    item = _item("Maya moved to Rome")
    verdict = subject._TypedVerdict(atoms=[_supported_atom()])
    record = {
        "schema_success": True,
        "reference_integrity": True,
        "validation_errors": [],
        "verdict": verdict.model_dump(mode="json"),
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
