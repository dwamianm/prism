import hashlib
import json

import pytest

from benchmarks.integrations import run_beam_answerability
from benchmarks.integrations.run_beam_answerability import (
    _answerability_binding,
    _evaluate_gates,
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


def test_answerability_binding_pins_implementation_prompt_and_schema(tmp_path):
    source = tmp_path / "src/prme/retrieval"
    source.mkdir(parents=True)
    implementation = source / "answerability.py"
    implementation.write_text("answerability implementation\n", encoding="utf-8")

    binding = _answerability_binding(tmp_path)

    assert binding == {
        "implementation_sha256": hashlib.sha256(
            implementation.read_bytes()
        ).hexdigest(),
        "prompt_version": run_beam_answerability.ANSWERABILITY_PROMPT_VERSION,
        "prompt_sha256": run_beam_answerability.ANSWERABILITY_PROMPT_SHA256,
        "response_schema_sha256": hashlib.sha256(
            json.dumps(
                run_beam_answerability._RawAssessment.model_json_schema(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
    }


def test_answerability_gates_are_derived_from_complete_summary():
    samples = []
    for question in range(4):
        for repeat in range(3):
            samples.append(
                _sample(
                    f"abstention-{question}",
                    "abstention",
                    repeat,
                    "abstain",
                    "insufficient",
                )
            )
    for question in range(36):
        for repeat in range(3):
            samples.append(
                _sample(
                    f"ordinary-{question}",
                    "factual",
                    repeat,
                    "answer",
                    "answerable",
                )
            )

    gates = _evaluate_gates(_summarize(samples))

    assert gates["passed"]
    assert all(result["passed"] for result in gates["results"].values())


def test_answerability_gates_fail_unsafe_answer():
    samples = []
    for question in range(4):
        for repeat in range(3):
            action = "answer" if question == 0 and repeat == 0 else "abstain"
            verdict = "answerable" if action == "answer" else "insufficient"
            samples.append(
                _sample(
                    f"abstention-{question}",
                    "abstention",
                    repeat,
                    action,
                    verdict,
                )
            )
    for question in range(36):
        for repeat in range(3):
            samples.append(
                _sample(
                    f"ordinary-{question}",
                    "factual",
                    repeat,
                    "answer",
                    "answerable",
                )
            )

    gates = _evaluate_gates(_summarize(samples))

    assert not gates["passed"]
    assert not gates["results"]["unsafe_full_answer_count_max"]["passed"]
