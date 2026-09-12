"""Paired comparisons must reject incomplete or incompatible measurements."""

import copy

import pytest

from benchmarks.compare_evidence import compare, paired_statistics


def report():
    return {
        "kind": "source-evidence-retrieval", "profile": "raw-turns-static",
        "run_id": "r", "complete": True, "errors": 0, "provenance": {},
        "tokenizer": "cl100k_base", "budgets": [100], "candidate_limit": 10,
        "dataset": {"sha256": "abc", "variant": "s", "split": "dev", "split_seed": "frozen",
                    "selected_question_ids": ["q"]},
        "summary": {"prme": {}},
        "details": [{"question_id": "q", "category": "temporal", "source_count": 100,
                     "evidence_source_ids": ["s0:t0"], "methods": {"prme": {
                         "metrics": {"mrr": .5}, "packing": {"100": {"evidence_recall": .5}},
                     }}}],
    }


def test_paired_changes_and_bootstrap_are_reproducible():
    pairs = [(0, 1), (.5, .5), (1, .5)]
    result = paired_statistics(pairs, samples=100)
    assert result == paired_statistics(pairs, samples=100)
    assert result["delta"] == pytest.approx(1 / 6)
    assert (result["wins"], result["ties"], result["losses"]) == (1, 1, 1)
    assert paired_statistics([])["delta"] is None


def test_complete_matched_comparison():
    before = report()
    after = copy.deepcopy(before)
    after["details"][0]["methods"]["prme"]["metrics"]["mrr"] = 1
    result = compare(before, after, samples=100)
    assert result["methods"]["prme"]["metrics"]["mrr"]["delta"] == .5
    assert result["categories"]["temporal"]["prme"]["metrics"]["mrr"]["queries"] == 1
    assert result["protocol_changes"] == {}


def test_clock_change_is_explicit_and_not_an_isolated_algorithm_claim():
    before, after = report(), report()
    after["query_clock"] = "question"
    after["concurrency"] = 4
    result = compare(before, after, samples=100)
    assert result["protocol_changes"] == {
        "query_clock": {"before": "wall", "after": "question"},
        "concurrency": {"before": 1, "after": 4},
    }
    assert any("not an isolated algorithm comparison" in item for item in result["limitations"])


@pytest.mark.parametrize("failure", ["incomplete", "duplicate", "missing", "dataset", "budget", "labels"])
def test_comparison_rejects_partial_or_different_measurements(failure):
    before, after = report(), report()
    if failure == "incomplete":
        after["complete"] = False
    elif failure == "duplicate":
        after["details"] *= 2
    elif failure == "missing":
        after["details"] = []
    elif failure == "dataset":
        after["dataset"]["sha256"] = "different"
    elif failure == "budget":
        after["budgets"] = [200]
    elif failure == "labels":
        after["details"][0]["evidence_source_ids"] = []
    with pytest.raises(ValueError):
        compare(before, after, samples=100)


def test_within_run_comparison_keeps_paired_direction_and_unlabeled_rows():
    from benchmarks.compare_methods import compare_methods
    data = report()
    data['summary'] = {m: {'metrics': {'mrr': 0}} for m in ('prme', 'vector')}
    data['details'][0]['methods']['vector'] = {'metrics': {'mrr': .25}, 'packing': {'100': {'evidence_recall': .75}}}
    unlabeled = copy.deepcopy(data['details'][0])
    unlabeled['question_id'] = 'unlabeled'
    for method in unlabeled['methods'].values():
        method['metrics']['mrr'] = None
        method['packing']['100']['evidence_recall'] = None
    data['details'].append(unlabeled)
    data['dataset']['selected_question_ids'].append('unlabeled')
    comparison = compare_methods(data, samples=100)['comparisons']['vector']
    assert comparison['metrics']['mrr']['delta'] == .25
    assert comparison['packed_evidence_recall']['100']['delta'] == -.25
    assert comparison['metrics']['mrr']['queries'] == 1
    data['details'][0]['methods'].pop('vector')
    with pytest.raises(ValueError, match='all compared methods'):
        compare_methods(data)
