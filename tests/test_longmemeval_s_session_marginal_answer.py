from benchmarks.diagnostics import longmemeval_s_session_marginal_answer as trial


def _metrics(
    *, control: int, candidate: int, wins: int, losses: int, regression: bool = False
):
    return {
        "overall": {
            "control_correct": control,
            "candidate_correct": candidate,
            "paired_wins": wins,
            "paired_losses": losses,
        },
        "categories": {
            "multi-session": {
                "control_correct": 5,
                "candidate_correct": 4 if regression else 5,
            }
        },
        "complete_reader_execution": True,
        "complete_judge_execution": True,
        "reader_failed_attempts": 0,
        "judge_failed_attempts": 0,
    }


def test_answer_protocol_fixes_the_lossless_changed_context_cohort() -> None:
    protocol = trial._protocol()
    assert trial.ARMS == ("control", "marginal")
    assert trial.SOURCE_ARM == "marginal_f8_d090"
    assert trial.SOURCE_CONFIG == {"free_slots": 8, "decay": 0.9}
    assert protocol["cohort"] == "all 31 contexts changed by the fixed mild source arm"
    assert protocol["references_available_only_after_complete_reader"] is True


def test_gate_requires_a_strict_paired_gain_without_category_regression() -> None:
    assert trial._gate(_metrics(control=20, candidate=21, wins=2, losses=1))["passed"]
    assert not trial._gate(_metrics(control=20, candidate=20, wins=1, losses=1))[
        "passed"
    ]
    assert not trial._gate(
        _metrics(control=20, candidate=21, wins=2, losses=1, regression=True)
    )["passed"]


def test_source_gate_is_stric_and_records_no_public_promotion() -> None:
    evaluation = trial._evaluation()
    assert evaluation["candidate_correct"] == ">= control_correct"
    assert evaluation["paired_wins"] == "> paired_losses"
    assert "does not expose a public option" in evaluation["decision"]
