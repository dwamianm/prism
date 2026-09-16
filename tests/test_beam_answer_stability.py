import pytest

from benchmarks.integrations.run_beam_answer_stability import _summarize


def _sample(arm: str, score: float, repeat: int):
    return {
        "arm": arm,
        "repeat": repeat,
        "evaluation": {"cutoff_results": {"top_50": {"score": score}}},
    }


def test_answer_stability_summary_keeps_repeats_and_paired_deltas():
    summary = _summarize(
        [
            _sample("baseline", 0.5, 0),
            _sample("candidate", 0.0, 0),
            _sample("candidate", 1.0, 1),
            _sample("baseline", 0.5, 1),
        ]
    )

    assert summary["arms"]["baseline"] == {
        "scores": [0.5, 0.5],
        "mean_score": 0.5,
        "pass_count": 2,
        "repeats": 2,
    }
    assert summary["arms"]["candidate"]["pass_count"] == 1
    assert summary["candidate_mean_delta"] == 0.0
    assert summary["paired"] == [
        {
            "repeat": 0,
            "baseline_score": 0.5,
            "candidate_score": 0.0,
            "score_delta": -0.5,
            "pass_transition": "True->False",
        },
        {
            "repeat": 1,
            "baseline_score": 0.5,
            "candidate_score": 1.0,
            "score_delta": 0.5,
            "pass_transition": "True->True",
        },
    ]


def test_answer_stability_summary_rejects_unpaired_repeats():
    with pytest.raises(ValueError, match="repeats differ"):
        _summarize(
            [
                _sample("baseline", 0.5, 0),
                _sample("candidate", 0.5, 1),
            ]
        )
