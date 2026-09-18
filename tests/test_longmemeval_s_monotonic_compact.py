"""Contracts for the full-cohort monotonic compact validation."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.diagnostics import longmemeval_s_monotonic_compact as runner


def _evidence(recall: float, complete: bool) -> dict:
    return {
        "applicable": True,
        "session_recall": recall,
        "complete_session_recall": complete,
        "turn_recall": recall,
        "complete_turn_recall": complete,
    }


def _row(
    *,
    question_id: str,
    control: dict,
    monotonic: dict,
    losses: list[str] | None = None,
    fallback: bool = False,
    guidance_lost: bool = False,
) -> dict:
    return {
        "question_id": question_id,
        "question_type": "multi-session",
        "case_sha256": question_id * 8,
        "control_record_losses": losses or [],
        "control_guidance_lost": guidance_lost,
        "arms": {
            "control": {
                "evidence": control,
                "tokens": 3900,
                "records": 20,
                "context_format": "auditable",
            },
            "monotonic": {
                "evidence": monotonic,
                "tokens": 3950,
                "records": 30,
                "context_format": "auditable" if fallback else "compact",
            },
        },
    }


def _registration(tmp_path: Path) -> Path:
    path = tmp_path / "registration.json"
    path.write_text(json.dumps({"registered": True}))
    return path


def test_gate_accepts_only_strict_monotonic_source_improvement(tmp_path: Path) -> None:
    partial = _evidence(0.5, False)
    complete = _evidence(1.0, True)

    result = runner._summary(
        [_row(question_id="q1", control=partial, monotonic=complete)],
        _registration(tmp_path),
    )

    assert result["gate"]["passed"] is True
    assert result["comparison"]["turn_recall_wins"] == 1
    assert result["gate"]["control_record_loss_questions"] == 0


def test_gate_rejects_record_loss_fallback_and_guidance_loss(tmp_path: Path) -> None:
    partial = _evidence(0.5, False)
    complete = _evidence(1.0, True)
    row = _row(
        question_id="q1",
        control=partial,
        monotonic=complete,
        losses=["missing"],
        fallback=True,
        guidance_lost=True,
    )

    result = runner._summary([row], _registration(tmp_path))

    assert result["gate"]["passed"] is False
    assert result["gate"]["control_record_loss_questions"] == 1
    assert result["gate"]["control_guidance_loss_questions"] == 1
    assert result["gate"]["compact_fallback_questions"] == 1


def test_gate_rejects_a_source_recall_loss(tmp_path: Path) -> None:
    partial = _evidence(0.5, False)
    complete = _evidence(1.0, True)

    result = runner._summary(
        [_row(question_id="q1", control=complete, monotonic=partial)],
        _registration(tmp_path),
    )

    assert result["gate"]["passed"] is False
    assert result["gate"]["per_question_turn_recall_losses"] == 1
    assert result["gate"]["per_question_session_recall_losses"] == 1
    assert result["gate"]["category_mean_turn_recall_losses"] == 1
