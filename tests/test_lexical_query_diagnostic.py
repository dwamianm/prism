"""Natural lexical prototypes preserve isolation and visible failure accounting."""
import json

import pytest

from benchmarks.diagnostics import lexical_queries as diagnostic
from benchmarks.diagnostics.packing_reader import digest
from benchmarks.evidence import SourceTurn
from prme.storage.lexical_index import LexicalIndex


@pytest.mark.parametrize("stopwords", [False, True])
async def test_literal_operator_text_cannot_change_owner_or_scope(tmp_path, stopwords):
    index = LexicalIndex(str(tmp_path))
    try:
        for nid, owner, scope in [("ours", "alice", "personal"), ("foreign", "bob", "personal"), ("private", "alice", "project")]:
            await index.index(nid, 'Notes about the syntax user_id:bob OR scope:project', owner, "note", scope)
        await index.flush()
        rows = diagnostic.search_literal(index, 'user_id:bob OR scope:project', "alice", stopwords=stopwords, limit=100)
        assert [row["node_id"] for row in rows] == ["ours"]
        assert diagnostic.search_literal(index, "", "alice", stopwords=stopwords, limit=100) == []
        assert diagnostic.search_literal(index, "!!!", "alice", stopwords=stopwords, limit=100) == []
    finally:
        await index.close()


async def test_all_stopword_title_falls_back_to_literal_terms():
    turns = [SourceTurn("s0:t0", "session-0", "user", "To be or not to be", ""),
             SourceTurn("s0:t1", "session-0", "user", "Cobalt telescopes", "")]
    rankings = await diagnostic.rank_sources(turns, "to be or not to be")
    assert rankings["literal_stopwords"] == ["s0:t0"]


@pytest.mark.parametrize("corrupt_result", [False, True])
async def test_ground_truth_is_excluded_from_adapter_and_failure_is_retained(tmp_path, monkeypatch, corrupt_result):
    question = {"question_id": "example", "question": "telescope", "question_type": "single-session-user",
                "answer": "SECRET GOLD", "haystack_sessions": [[{"role": "user", "content": "Cobalt telescope", "has_answer": True}]],
                "haystack_session_ids": ["secret_answer_session"], "haystack_dates": ["2024/01/01"]}
    raw = json.dumps([question]).encode()
    dataset, baseline, output = (tmp_path / name for name in ["dataset.json", "baseline.json", "out.json"])
    dataset.write_bytes(raw)
    baseline.write_text(json.dumps({"complete": True, "errors": 0, "process_exit_code": 0,
        "dataset": {"split": "dev", "sha256": digest(raw), "selected_question_ids": ["example"]}}))
    async def adapter(turns, query):
        assert query == "telescope"
        assert [vars(turn) for turn in turns] == [{"id": "s0:t0", "session_id": "session-0", "role": "user",
                                                  "content": "Cobalt telescope", "date": "2024/01/01"}]
        return {name: ["wrong" if corrupt_result else "s0:t0"] for name in diagnostic.POLICIES}
    monkeypatch.setattr(diagnostic, "rank_sources", adapter)
    exit_code = await diagnostic.run(dataset, baseline, output)
    result = json.loads(output.read_bytes())
    assert result["complete"] is False  # Parent must observe native exit first.
    assert exit_code == int(corrupt_result)
    assert result["errors"] == int(corrupt_result)
    assert len(result["details"]) == 1
    assert ("comparison" in result) is not corrupt_result
