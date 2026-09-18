from benchmarks.diagnostics.longmemeval_s_episode_composition import (
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


def test_episode_grid_is_fixed_and_control_is_first() -> None:
    assert list(ARM_CONFIGS) == [
        "control",
        "episode_1x4",
        "episode_2x4",
        "episode_3x4",
        "episode_2x8",
        "episode_3x8",
    ]
    assert ARM_CONFIGS["control"]["episode_context_top_k"] == 0


def test_selection_prefers_complete_evidence_before_context_churn() -> None:
    results = {"control": _arm(complete_turns=403, mean_turn=0.91)}
    for name in list(ARM_CONFIGS)[1:]:
        results[name] = _arm(
            complete_turns=404,
            mean_turn=0.92,
            changed=5,
            passed=False,
        )
    results["episode_1x4"] = _arm(
        complete_turns=405, mean_turn=0.925, changed=3
    )
    results["episode_2x4"] = _arm(
        complete_turns=406, mean_turn=0.921, changed=100
    )

    assert _select_candidate(results) == "episode_2x4"


def test_candidate_gate_rejects_any_per_question_source_loss() -> None:
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
