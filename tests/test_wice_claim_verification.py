import json
from pathlib import Path

import pytest

from benchmarks.integrations import run_wice_claim_verification as wice


def _row(case_id: str, label: str, *, claim: str = "A claim") -> dict:
    return {
        "label": label,
        "claim": claim,
        "evidence": ["One evidence sentence."],
        "meta": {"id": case_id},
    }


def test_group_dataset_preserves_first_seen_order_and_oracle_variants():
    cases = wice._group_dataset(
        [
            _row("b", "supported"),
            _row("a", "not_supported"),
            _row("b", "supported"),
        ]
    )

    assert [case["id"] for case in cases] == ["b", "a"]
    assert len(cases[0]["variants"]) == 2


def test_group_dataset_rejects_inconsistent_oracle_variants():
    with pytest.raises(ValueError, match="oracle variants disagree"):
        wice._group_dataset(
            [_row("case", "supported"), _row("case", "partially_supported")]
        )


def test_load_dataset_rejects_evidence_without_content(tmp_path: Path):
    path = tmp_path / "test.jsonl"
    row = _row("case", "supported")
    row["evidence"] = []
    path.write_text(json.dumps(row) + "\n")

    with pytest.raises(ValueError, match="invalid evidence"):
        wice._load_dataset(path)


def test_load_dataset_accepts_official_empty_sentence_placeholders(tmp_path: Path):
    path = tmp_path / "test.jsonl"
    row = _row("case", "supported")
    row["evidence"] = ["", "Evidence remains."]
    path.write_text(json.dumps(row) + "\n")

    assert wice._load_dataset(path) == [row]


def test_binary_metrics_treat_partial_support_as_negative():
    samples = [
        {"label": "supported", "product_supported": True},
        {"label": "supported", "product_supported": False},
        {"label": "partially_supported", "product_supported": True},
        {"label": "not_supported", "product_supported": False},
    ]

    metrics = wice._binary_metrics(samples, prediction="product_supported")

    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["true_negative"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["balanced_accuracy"] == 0.5


def test_quality_gates_keep_not_supported_safety_slice_separate():
    registration = {
        "evaluation": {
            "gates": {
                "claims_evaluated_min": 3,
                "supported_precision_min": 0.5,
                "supported_recall_min": 0.5,
                "balanced_accuracy_min": 0.5,
                "false_support_rate_max": 0.5,
                "not_supported_false_support_rate_max": 0.0,
            }
        }
    }
    samples = [
        {"label": "supported", "product_supported": True},
        {"label": "partially_supported", "product_supported": False},
        {"label": "not_supported", "product_supported": True},
    ]
    metrics = wice._binary_metrics(samples, prediction="product_supported")

    result = wice._gate_results(registration, samples, metrics)

    assert result["passed"] is False
    assert result["results"]["not_supported_false_support_rate_max"] == {
        "required": 0.0,
        "observed": 1.0,
        "passed": False,
    }
