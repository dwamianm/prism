import json

from benchmarks.diagnostics.packing_regression import grouping, summary


def test_history_groups_ignore_answers_and_connect_abstention_pairs(tmp_path):
    common = {"haystack_session_ids": ["s"], "haystack_dates": ["2026-01-01"],
              "haystack_sessions": [[{"role": "user", "content": "shared history"}]]}
    cases = [{**common, "question_id": q, "answer": q} for q in ("a", "a_abs", "b")]
    cases.append({**common, "question_id": "c", "haystack_dates": ["2026-02-01"]})
    path = tmp_path / "raw.json"
    path.write_text(json.dumps(cases))
    groups = grouping(path, [c["question_id"] for c in cases])
    assert groups["a"] == groups["a_abs"] == groups["b"]
    assert groups["c"] != groups["a"]


def test_independent_summary_counts_losses_and_unlabelled_cases():
    rows = []
    for i, values in enumerate(((.5, 1), (1, .5), (None, None), (0, 1))):
        arms = {f"{name}:{budget}": {"evidence_recall": values[name not in ('density', 'score')]}
                for name in ("density", "score", "quarter", "head1_quarter")
                for budget in (2048, 4096, 8192)}
        rows.append({"group": str(i), "arms": arms})
    result = summary(rows)
    assert result == summary(rows, independent=True)
    primary = result["quarter_vs_density"]["4096"]
    assert primary["queries"] == 3 and primary["wins"] == 2 and primary["losses"] == 1
