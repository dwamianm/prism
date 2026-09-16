import pytest

from benchmarks.integrations.run_beam_answerability import (
    _summarize,
    _verify_saved_sample,
)


def _sample(
    question_id: str, question_type: str, repeat: int, action: str, verdict: str
):
    return {
        "question_id": question_id,
        "question_type": question_type,
        "repeat": repeat,
        "assessment": {
            "recommended_action": action,
            "verdict": verdict,
            "citation_errors": [],
        },
    }


def test_answerability_summary_separates_safety_coverage_and_stability():
    summary = _summarize(
        [
            _sample("q0", "abstention", 0, "abstain", "insufficient"),
            _sample("q0", "abstention", 1, "answer", "answerable"),
            _sample("q1", "factual", 0, "answer", "answerable"),
            _sample("q1", "factual", 1, "answer", "answerable"),
        ]
    )

    assert summary["questions"] == 2
    assert summary["samples"] == 4
    assert summary["unanswerable"]["unsafe_full_answer_count"] == 1
    assert summary["unanswerable"]["explicit_abstention_count"] == 1
    assert summary["answerable"]["unnecessary_abstention_count"] == 0
    assert summary["answerable"]["full_answer_count"] == 2
    assert summary["stability"] == {
        "questions_with_identical_verdicts": 1,
        "questions_with_identical_actions": 1,
        "questions": 2,
    }


def test_answerability_summary_rejects_duplicate_repeats():
    sample = _sample("q0", "abstention", 0, "abstain", "insufficient")

    with pytest.raises(ValueError, match="duplicate question repeat"):
        _summarize([sample, sample])


def test_answerability_resume_rejects_sample_from_another_artifact():
    sample = {
        **_sample("q0", "abstention", 0, "abstain", "insufficient"),
        "cohort": "conversation_0",
        "artifact": "q0.json",
        "artifact_sha256": "wrong",
    }

    with pytest.raises(ValueError, match="differs from frozen input"):
        _verify_saved_sample(
            sample,
            cohort="conversation_0",
            question_id="q0",
            question_type="abstention",
            repeat=0,
            artifact="q0.json",
            artifact_sha256="expected",
        )
