"""Diagnostic joins reject mismatches and avoid treating pointers as evidence."""
from copy import deepcopy

import pytest

from benchmarks.diagnostics import packing_reader as runtime
from benchmarks.diagnostics.reader_evidence_map import summarize


def inputs():
    ids = ["answerable", "unanswerable_abs", "unlabelled"]
    context = "a frozen context"
    digest = runtime.digest(context.encode())
    rows = [{"question_id": qid, "category": "multi-session", "evidence_source_ids": ["needed"] if qid != "unlabelled" else [],
             "variants": {"score": {"4096": {"context_sha256": digest, "content_source_ids": [],
                                             "pointer_source_ids": ["needed"]}}}} for qid in ids]
    packing = {"complete": True, "baseline_reproduction_passed": True, "input_sha256": "source",
               "dataset": {"selected_question_ids": ids}, "details": rows}
    plan = {"source_report_file_sha256": "source", "selected_question_ids": ids, "budget": 4096, "reader": {"model": "test"}}
    prepared = {"rows": [{"question_id": qid, "contexts": {"score": {"context": context, "sha256": digest}}} for qid in ids]}
    verdicts = {qid + ":score": {"correct": False} for qid in ids}
    return packing, [(plan, prepared, verdicts), deepcopy((plan, prepared, verdicts))]


def test_missing_labels_abstention_and_reference_only_context_are_distinct():
    packing, readers = inputs()
    report = summarize(packing, readers)
    assert [row["coverage"] for row in report["details"]] == ["no_labelled_evidence", "abstention", "unlabelled"]
    assert report["details"][0]["missing_labelled_source_ids"] == ["needed"]
    assert sum(row["questions"] for row in report["strata"]) == 3


@pytest.mark.parametrize("corrupt", ["duplicate", "source", "context", "label", "incomplete"])
def test_invalid_comparisons_do_not_produce_a_failure_map(corrupt):
    packing, readers = inputs()
    if corrupt == "duplicate":
        packing["details"].append(deepcopy(packing["details"][0]))
    elif corrupt == "source":
        readers[1][0]["source_report_file_sha256"] = "another source"
    elif corrupt == "context":
        readers[1][1]["rows"][0]["contexts"]["score"]["context"] = "changed"
    elif corrupt == "label":
        readers[1][2]["answerable:score"]["correct"] = "false"
    else:
        packing["complete"] = False
    with pytest.raises(ValueError):
        summarize(packing, readers)
