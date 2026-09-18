"""Tests for the frozen-pack BEAM retrieval ablation runner."""

from benchmarks.integrations.run_beam_retrieval_ablation import _paired_comparison


def _evaluation(question_id: str, score: float) -> dict:
    return {
        "question_id": question_id,
        "cutoff_results": {"top_50": {"score": score}},
    }


def test_paired_comparison_reports_pass_transitions_and_mean_delta():
    baseline = [_evaluation("q1", 0.0), _evaluation("q2", 1.0), _evaluation("q3", 0.5)]
    candidate = [_evaluation("q1", 0.5), _evaluation("q2", 0.0), _evaluation("q3", 1.0)]

    comparison = _paired_comparison(baseline, candidate)

    assert comparison["pass_level_wins"] == 1
    assert comparison["pass_level_losses"] == 1
    assert comparison["pass_level_ties"] == 1
    assert comparison["mean_score_delta"] == 0.0


def test_paired_comparison_rejects_question_drift():
    try:
        _paired_comparison([_evaluation("q1", 0.0)], [_evaluation("q2", 0.0)])
    except ValueError as exc:
        assert "question identities differ" in str(exc)
    else:
        raise AssertionError("question drift was accepted")
