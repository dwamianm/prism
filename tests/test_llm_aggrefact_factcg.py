from __future__ import annotations

from benchmarks.integrations import run_llm_aggrefact_factcg as subject


def _sample(score: float, label: int, ident: str = "id") -> dict[str, object]:
    return {
        "id": ident,
        "dataset": "dataset",
        "label": label,
        "support_probability": score,
        "source_chunks": 1,
    }


def test_select_cohort_is_balanced_and_deterministic() -> None:
    rows = [
        {
            "dataset": dataset,
            "label": label,
            "contamination_identifier": f"{dataset}-{label}-{index}",
        }
        for dataset in ("a", "b")
        for label in (0, 1)
        for index in range(5)
    ]

    first = subject._select_cohort(
        rows, split="dev", seed="seed", per_label_per_dataset=2
    )
    second = subject._select_cohort(
        list(reversed(rows)), split="dev", seed="seed", per_label_per_dataset=2
    )

    assert [row["contamination_identifier"] for row in first] == [
        row["contamination_identifier"] for row in second
    ]
    assert len(first) == 8


def test_calibration_selects_maximum_recall_then_precision() -> None:
    samples = [
        _sample(0.95, 1, "p1"),
        _sample(0.90, 1, "p2"),
        _sample(0.80, 1, "p3"),
        _sample(0.70, 0, "n1"),
        _sample(0.10, 0, "n2"),
    ]

    threshold, metrics = subject._calibrate_threshold(
        samples, precision_min=1.0, recall_min=0.6
    )

    assert threshold == 0.7
    assert metrics is not None
    assert metrics["supported_precision"] == 1.0
    assert metrics["supported_recall"] == 1.0


def test_calibration_failure_does_not_invent_threshold() -> None:
    samples = [_sample(0.9, 0, "n"), _sample(0.8, 1, "p")]

    threshold, metrics = subject._calibrate_threshold(
        samples, precision_min=1.0, recall_min=1.0
    )

    assert threshold is None
    assert metrics is None


def test_metrics_use_strict_threshold_and_false_support_rate() -> None:
    samples = [
        _sample(0.5, 1, "p-equal"),
        _sample(0.6, 1, "p"),
        _sample(0.7, 0, "fp"),
        _sample(0.4, 0, "tn"),
    ]

    metrics = subject._metrics(samples, 0.5)

    assert (metrics["tp"], metrics["fp"], metrics["tn"], metrics["fn"]) == (
        1,
        1,
        1,
        1,
    )
    assert metrics["supported_precision"] == 0.5
    assert metrics["supported_recall"] == 0.5
    assert metrics["balanced_accuracy"] == 0.5
    assert metrics["false_support_rate"] == 0.5


def test_result_hash_is_stable_for_key_order() -> None:
    assert subject._canonical_sha256({"b": 2, "a": 1}) == subject._canonical_sha256(
        {"a": 1, "b": 2}
    )
