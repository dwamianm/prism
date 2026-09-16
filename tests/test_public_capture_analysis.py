"""Actual returned text and uncertainty must not inherit reconstructed evidence."""

import copy

from benchmarks.diagnostics.compare_public_captures import (
    actual_text_metrics,
    cluster_statistics,
    groups_for,
)
from benchmarks.diagnostics.normalize_capture_inputs import normalize
from benchmarks.evidence import SourceTurn


def test_pointer_or_partial_text_is_not_complete_original_evidence():
    sources = {
        "one": SourceTurn("one", "session", "user", "Shaded only in summer.", "date")
    }
    partial = actual_text_metrics([("one", "Shaded", True)], sources, {"one"})
    assert (
        partial["document_hit_recall"] == 1 and partial["whole_turn_record_recall"] == 0
    )
    pointer = actual_text_metrics(
        [("one", "Shaded only in summer.", False)], sources, {"one"}
    )
    assert (
        pointer["document_hit_recall"] == 0 and pointer["whole_turn_record_recall"] == 0
    )


def test_unlabelled_case_has_no_evidence_score():
    result = actual_text_metrics([], {}, set())
    assert (
        result["document_hit_recall"] is None
        and result["whole_turn_record_recall"] is None
    )


def test_single_history_group_does_not_have_a_falsely_precise_interval():
    result = cluster_statistics([(0, 1), (0, 1)], ["same", "same"])
    assert result["delta"] == 1 and result["interval_95"] is None


def test_identical_histories_and_abstention_pairs_form_transitive_groups():
    cases = [
        {"case_id": "a", "turns": ["same"]},
        {"case_id": "b", "turns": ["different"]},
        {"case_id": "c", "turns": ["different"]},
    ]
    refs = {
        "a": {"question_id": "q"},
        "b": {"question_id": "q_abs"},
        "c": {"question_id": "other"},
    }
    groups = groups_for(cases, refs)
    assert len(set(groups.values())) == 1


def test_normalization_preserves_text_whitespace_and_original_input():
    inputs = {
        "cases": [
            {
                "case_id": "a",
                "question": "q\x02?",
                "turns": [
                    {"id": "s0:t0", "content": "one\x02two\n\tx\r\x7f\u200b\ud800"}
                ],
            }
        ]
    }
    before = copy.deepcopy(inputs)
    result, audit = normalize(inputs)
    assert inputs == before
    assert result["cases"][0]["question"] == "q?"
    assert result["cases"][0]["turns"][0]["content"] == "onetwo\n\tx\r\u200b"
    assert len(audit["changes"]) == 2
    assert audit["changes"][1]["removed_codepoints"] == {
        "U+0002": 1,
        "U+007F": 1,
        "U+D800": 1,
    }


def test_capture_analysis_rejects_untrusted_completion_before_scoring(tmp_path):
    """A complete-looking report cannot bypass native exit and artifact gates."""
    import json
    from types import SimpleNamespace

    import pytest

    from benchmarks.diagnostics.compare_public_captures import analyze
    from benchmarks.diagnostics.hindsight_capture import digest

    def save(name, value):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        return path

    case = {"case_id": "one", "question": "Which?", "turns": []}
    references = [{"case_id": "one"}]
    args = SimpleNamespace(
        inputs=save("inputs", {"cases": [case]}),
        references=save("references", {"references": references}),
    )
    plan = {
        "inputs_sha256": digest(args.inputs.read_bytes()),
        "reference_sha256": digest(args.references.read_bytes()),
        "case_ids": ["one"],
        "runner_sha256": "registered-runner",
        "versions": {"library": "pinned"},
        "embedding_assets": {"model": "pinned"},
    }
    args.prme_plan = save("plan", plan)
    report = {
        "plan_sha256": digest(args.prme_plan.read_bytes()),
        "runner_sha256": plan["runner_sha256"],
        "versions": plan["versions"],
        "embedding_assets": plan["embedding_assets"],
        "complete": True,
        "errors": 0,
        "details": [{"case_id": "one"}],
    }

    def completion(report_value, exit_code=0):
        args.prme_report = save("report", report_value)
        args.completion = save(
            "completion",
            {
                "products": {
                    "prme": {
                        "native_exit_code": exit_code,
                        "report_sha256": digest(args.prme_report.read_bytes()),
                    }
                }
            },
        )

    completion(report, 130)
    with pytest.raises(ValueError, match="Native success"):
        analyze(args)

    completion(report)
    args.prme_report.write_text("{}")
    with pytest.raises(ValueError, match="Completion hash"):
        analyze(args)

    completion(dict(report, complete=False))
    with pytest.raises(ValueError, match="Successful complete"):
        analyze(args)

    completion(dict(report, details=[]))
    with pytest.raises(ValueError, match="Successful complete"):
        analyze(args)

    completion(dict(report, versions={"library": "unexpected"}))
    with pytest.raises(ValueError, match="Reported runtime"):
        analyze(args)

    completion(report)
    args.prme_plan = save("changed-plan", dict(plan, case_ids=[]))
    completion(dict(report, plan_sha256=digest(args.prme_plan.read_bytes())))
    with pytest.raises(ValueError, match="Registered case coverage"):
        analyze(args)

    args.references = save("duplicate-references", {"references": references * 2})
    with pytest.raises(ValueError, match="Ambiguous input/reference coverage"):
        analyze(args)
