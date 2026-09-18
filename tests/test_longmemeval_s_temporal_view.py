from __future__ import annotations

from benchmarks.diagnostics import longmemeval_s_temporal_view as trial


def test_numbered_records_counts_only_evidence_rows() -> None:
    context = "\n".join(
        [
            "Today's date: 2023-04-01",
            "[1] (2023-03-01, 31 days ago) first",
            "[stable_facts]",
            "[2] (2023-03-20, 12 days ago) second",
        ]
    )

    assert trial._numbered_records(context) == 2


def test_protocol_requires_strict_development_gain() -> None:
    protocol = trial._protocol()

    assert protocol["record_set"] == "identical per question"
    assert protocol["gate"]["candidate_correct"] == "> control_correct"
    assert "cannot promote" in protocol["status"]
