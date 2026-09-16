from __future__ import annotations

from benchmarks.integrations import run_llm_aggrefact_evidence_cascade as subject


def _sample(
    score: float,
    label: int,
    *,
    supported: bool,
    integrity: bool = True,
) -> dict[str, object]:
    return {
        "label": label,
        "factcg_max_support_probability": score,
        "verifier_supported": supported,
        "reference_integrity": integrity,
    }


def test_segments_are_stable_and_numbered() -> None:
    assert subject._segments(["One. Two?", "Three!"]) == [
        ("E0001", "One."),
        ("E0002", "Two?"),
        ("E0003", "Three!"),
    ]


def test_public_sample_requires_valid_citations_for_support() -> None:
    prepared = {
        "id": "id",
        "dataset": "dataset",
        "label": 1,
        "source_chunks": 2,
        "factcg_max_support_probability": 0.9,
        "selected_chunk_indices": [0, 1],
        "selected_chunk_scores": [0.9, 0.8],
        "evidence_segments": [("E0001", "Evidence")],
    }
    verdict = subject._Verdict(
        status="supported", evidence_segment_ids=["E9999"], explanation="Reason"
    )

    sample = subject._public_sample(prepared, verdict, elapsed_seconds=1.0)

    assert sample["reference_integrity"] is False
    assert sample["verifier_supported"] is False
    assert isinstance(sample["evidence_segments"], int)
    assert "claim" not in sample


def test_calibration_uses_verifier_and_ranker_intersection() -> None:
    samples = [
        _sample(0.95, 1, supported=True),
        _sample(0.90, 1, supported=True),
        _sample(0.85, 1, supported=False),
        _sample(0.80, 0, supported=True),
        _sample(0.20, 0, supported=False),
    ]

    threshold, metrics = subject._calibrate_threshold(
        samples,
        precision_min=1.0,
        recall_min=0.6,
        reference_integrity_min=1.0,
    )

    assert threshold == 0.8
    assert metrics is not None
    assert metrics["supported_precision"] == 1.0
    assert metrics["supported_recall"] == 2 / 3


def test_calibration_fails_when_reference_integrity_is_low() -> None:
    samples = [
        _sample(0.9, 1, supported=True, integrity=True),
        _sample(0.8, 0, supported=False, integrity=False),
    ]

    threshold, metrics = subject._calibrate_threshold(
        samples,
        precision_min=1.0,
        recall_min=1.0,
        reference_integrity_min=1.0,
    )

    assert threshold is None
    assert metrics is None
