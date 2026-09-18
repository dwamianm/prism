from benchmarks.diagnostics import longmemeval_s_grouped_conversations_answer as trial


def _metrics(
    control: int,
    candidate: int,
    wins: int,
    losses: int,
    *,
    regression: bool = False,
) -> dict:
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


def _comparisons(*, combined_wins: int = 2, regression: bool = False) -> dict:
    return {
        "presentation": _metrics(70, 70, 1, 1),
        "combined": _metrics(70, 71, combined_wins, 1, regression=regression),
        "added_evidence": _metrics(70, 71, 2, 1),
    }


def test_protocol_separates_rendering_and_added_evidence() -> None:
    protocol = trial._protocol()

    assert trial.ARMS == ("control", "grouped_same_set", "grouped_fill")
    assert protocol["cohort"] == "stable 119-question PRME development split"
    assert protocol["comparisons"] == {
        "presentation": "control versus grouped_same_set",
        "combined": "control versus grouped_fill",
        "added_evidence": "grouped_same_set versus grouped_fill",
    }


def test_gate_requires_combined_strict_win_and_no_category_regression() -> None:
    assert trial._gate(_comparisons())["passed"]
    assert not trial._gate(_comparisons(combined_wins=1))["passed"]
    assert not trial._gate(_comparisons(regression=True))["passed"]


def test_evaluation_does_not_promote_product_behavior() -> None:
    evaluation = trial._evaluation()

    assert evaluation["grouped_fill_correct"] == ">= both other arms"
    assert "does not expose a public option" in evaluation["decision"]
