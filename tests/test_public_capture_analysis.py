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
