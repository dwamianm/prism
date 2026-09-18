"""Fail-closed contracts for the grouped-conversation source trial."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.diagnostics import longmemeval_s_grouped_conversations as runner


def _evidence(recall: float, complete: bool) -> dict:
    return {
        "applicable": True,
        "session_recall": recall,
        "complete_session_recall": complete,
        "turn_recall": recall,
        "complete_turn_recall": complete,
    }


def _arm(evidence: dict, *, tokens: int, records: int, groups: int) -> dict:
    return {
        "evidence": evidence,
        "tokens": tokens,
        "records": records,
        "conversation_groups": groups,
    }


def _row(
    *,
    question_id: str = "q1",
    control: dict,
    fill: dict,
    losses: bool = False,
    guidance_loss: bool = False,
    identity_matches: bool = True,
    fill_tokens: int = 3990,
) -> dict:
    return {
        "question_id": question_id,
        "question_type": "multi-session",
        "case_sha256": question_id * 8,
        "control_record_losses": {
            "grouped_same_set": ["lost"] if losses else [],
            "grouped_fill": [],
        },
        "control_guidance_losses": {
            "grouped_same_set": guidance_loss,
            "grouped_fill": False,
        },
        "same_set_identity_matches": identity_matches,
        "arms": {
            "control": _arm(control, tokens=3990, records=20, groups=0),
            "grouped_same_set": _arm(control, tokens=3700, records=20, groups=3),
            "grouped_fill": _arm(fill, tokens=fill_tokens, records=24, groups=4),
        },
    }


def _registration(tmp_path: Path) -> Path:
    path = tmp_path / "registration.json"
    path.write_text(json.dumps({"registered": True}))
    return path


def test_gate_accepts_monotonic_fill_with_strict_complete_turn_gain(
    tmp_path: Path,
) -> None:
    partial = _evidence(0.5, False)
    complete = _evidence(1.0, True)

    result = runner._summary(
        [_row(control=partial, fill=complete)], _registration(tmp_path)
    )

    assert result["gate"]["passed"] is True
    assert result["decision"] == "advance_to_paired_answer_development"
    assert result["comparisons"]["grouped_fill"]["turn_recall_wins"] == 1
    assert result["comparisons"]["grouped_same_set"]["turn_recall_wins"] == 0


def test_gate_rejects_loss_guidance_identity_and_budget_failures(
    tmp_path: Path,
) -> None:
    complete = _evidence(1.0, True)
    row = _row(
        control=complete,
        fill=complete,
        losses=True,
        guidance_loss=True,
        identity_matches=False,
        fill_tokens=3997,
    )

    result = runner._summary([row], _registration(tmp_path))

    assert result["gate"]["passed"] is False
    assert result["gate"]["budget_violations"] == 1
    assert result["gate"]["control_record_loss_questions"] == 1
    assert result["gate"]["control_guidance_loss_questions"] == 1
    assert result["gate"]["same_set_identity_mismatch_questions"] == 1


def test_gate_rejects_fill_recall_loss_and_complete_turn_tie(
    tmp_path: Path,
) -> None:
    partial = _evidence(0.5, False)
    complete = _evidence(1.0, True)

    loss = runner._summary(
        [_row(control=complete, fill=partial)], _registration(tmp_path)
    )
    tie = runner._summary(
        [_row(control=complete, fill=complete)], _registration(tmp_path)
    )

    assert loss["gate"]["fill_per_question_turn_recall_losses"] == 1
    assert loss["gate"]["fill_per_question_session_recall_losses"] == 1
    assert loss["gate"]["fill_category_mean_turn_recall_losses"] == 1
    assert loss["gate"]["passed"] is False
    assert tie["gate"]["fill_complete_turn_questions_improved"] is False
    assert tie["gate"]["passed"] is False
