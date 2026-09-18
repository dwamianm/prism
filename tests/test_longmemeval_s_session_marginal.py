from benchmarks.diagnostics.longmemeval_s_session_marginal import (
    ARM_CONFIGS,
    _candidate_gate,
    _select_candidate,
)


def _arm(
    *,
    complete_turns: int,
    mean_turn: float,
    complete_sessions: int = 430,
    mean_session: float = 0.95,
    changed: int = 10,
    passed: bool = True,
):
    return {
        "summary": {
            "complete_turn_questions": complete_turns,
            "mean_turn_recall": mean_turn,
            "complete_session_questions": complete_sessions,
            "mean_session_recall": mean_session,
        },
        "comparison": {"changed_contexts": changed},
        "gate": {"passed": passed},
    }


def test_session_marginal_grid_is_fixed_and_control_is_first() -> None:
    assert list(ARM_CONFIGS) == [
        "control",
        "marginal_f1_d098",
        "marginal_f2_d098",
        "marginal_f4_d098",
        "marginal_f2_d095",
        "marginal_f4_d095",
        "marginal_f4_d090",
        "marginal_f8_d090",
    ]
    assert ARM_CONFIGS["control"] == {"free_slots": None, "decay": None}


def test_selection_prefers_complete_evidence_before_context_churn() -> None:
    results = {"control": _arm(complete_turns=403, mean_turn=0.91)}
    for name in list(ARM_CONFIGS)[1:]:
        results[name] = _arm(
            complete_turns=404,
            mean_turn=0.92,
            changed=5,
            passed=False,
        )
    results["marginal_f1_d098"] = _arm(complete_turns=405, mean_turn=0.925, changed=3)
    results["marginal_f2_d098"] = _arm(complete_turns=406, mean_turn=0.921, changed=100)

    assert _select_candidate(results) == "marginal_f2_d098"


def test_session_marginal_gate_rejects_any_per_question_source_loss() -> None:
    control = {"complete_turn_questions": 403}
    candidate = {"complete_turn_questions": 410}
    comparison = {
        "turn_recall_losses": 1,
        "session_recall_losses": 0,
        "categories": {
            "multi-session": {
                "control_mean_turn_recall": 0.8,
                "candidate_mean_turn_recall": 0.9,
            }
        },
    }
    rows = [{"arms": {"candidate": {"tokens": 3996}}}]

    gate = _candidate_gate(
        control=control,
        candidate=candidate,
        comparison=comparison,
        rows=rows,
        arm="candidate",
    )

    assert gate["passed"] is False
    assert gate["per_question_turn_recall_losses"] == 1
