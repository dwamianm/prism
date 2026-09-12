"""Evidence measurement must remain independent of answer annotations."""

import copy
import math

import pytest

from benchmarks.evidence import (
    SourceTurn, evidence_metrics, longmemeval_sources,
    pack_sources, reciprocal_rank_fusion, select_questions,
)


def question():
    return {
        "question_id": "q-1", "question_type": "single-session-user",
        "question": "Which telescope did I buy?", "answer": "secret-answer-label",
        "haystack_session_ids": ["answer-labelled-session", "distractor-session"],
        "answer_session_ids": ["answer-labelled-session"],
        "haystack_dates": ["2025/01/01 (Wed) 12:00", "2025/01/02 (Thu) 12:00"],
        "haystack_sessions": [
            [{"role": "user", "content": "I bought a Dobsonian telescope.", "has_answer": True}],
            [{"role": "user", "content": "I ate oatmeal."}],
        ],
    }


def test_annotations_never_enter_source_content():
    original = question()
    changed = copy.deepcopy(original)
    changed["answer"] = "another-answer"
    changed["answer_session_ids"] = ["different-session"]
    changed["haystack_sessions"][0][0]["has_answer"] = False
    turns, gold = longmemeval_sources(original)
    other_turns, other_gold = longmemeval_sources(changed)
    assert turns == other_turns
    assert gold == {"s0:t0"} and other_gold == set()
    assert "answer-labelled" not in "".join(t.render() for t in turns)
    assert "secret-answer" not in "".join(t.render() for t in turns)


def test_metrics_deduplicate_and_measure_partial_evidence():
    result = evidence_metrics(["noise", "a", "a", "b"], {"a", "b"}, ks=[1, 2, 3])
    assert result["mrr"] == .5
    assert result["recall@1"] == 0
    assert result["recall@2"] == .5
    assert result["recall@3"] == 1
    expected_dcg = (1 / math.log2(3) + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
    assert result["ndcg@3"] == pytest.approx(expected_dcg)


def test_unlabeled_queries_are_not_perfect_scores():
    assert all(v is None for v in evidence_metrics(["a"], set()).values())


def test_split_is_order_independent_and_keeps_abstention_pairs_together():
    qs = [{"question_id": f"q-{i}"} for i in range(100)]
    qs += [{"question_id": f"q-{i}_abs"} for i in range(100)]
    dev = select_questions(qs, split="dev")
    test = select_questions(qs, split="test")
    assert dev == select_questions(list(reversed(qs)), split="dev")
    dev_ids = {q["question_id"] for q in dev}
    test_ids = {q["question_id"] for q in test}
    assert not dev_ids & test_ids
    assert len(dev_ids | test_ids) == 200
    for i in range(100):
        assert (f"q-{i}" in dev_ids) == (f"q-{i}_abs" in dev_ids)


def test_whole_context_budget_counts_headers_and_does_not_truncate():
    turns = [
        SourceTurn("a", "s", "user", "X" * 100 + " unless it rains", "2025"),
        SourceTurn("b", "s", "user", "Use blue", "2025"),
    ]
    context, ids, cost = pack_sources(turns, token_budget=40, count_tokens=len)
    assert ids == ["b"]
    assert "Use blue" in context and "X" not in context
    assert cost == len(context) <= 40
    assert pack_sources(turns, token_budget=0, count_tokens=len) == ("", [], 0)


def test_rrf_uses_ranks_not_incomparable_score_scales():
    ranked = reciprocal_rank_fusion([["a", "a", "b"], ["b", "c"]])
    assert ranked[0] == "b"
    assert len(ranked) == 3


async def test_real_engine_evidence_comparison(tmp_path, monkeypatch):
    from prme import PRMEConfig
    from benchmarks.retrieval_eval import evaluate_question, summarize
    from tests.test_durable_ingestion import MockEmbeddingProvider

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    result = await evaluate_question(
        question(), PRMEConfig(enable_qa_pairing=False, organizer={"opportunistic_enabled": False}),
        budgets=[100, 1000], count_tokens=len, k=10,
    )
    assert result["methods"]["bm25"]["metrics"]["recall@5"] == 1
    assert result["methods"]["none"]["metrics"]["recall@5"] == 0
    assert result["methods"]["bm25"]["packing"]["1000"]["evidence_recall"] == 1
    assert summarize([result], [100, 1000])["bm25"]["evidence_labeled_queries"] == 1

    from benchmarks.retrieval_eval import category_summary

    categories = category_summary(
        [result, {"question_id": "failed", "category": result["category"], "error": "RuntimeError"}],
        [question(), {**question(), "question_id": "failed"}, {**question(), "question_id": "pending"}],
        [100, 1000],
    )
    category = categories[result["category"]]
    assert category["coverage"] == pytest.approx(1 / 3)
    assert category["errors"] == 1
    assert category["methods"]["bm25"]["metrics"]["recall@5"] == 1


async def test_concurrent_evaluation_preserves_coverage_and_selection_order(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    from benchmarks import retrieval_eval

    questions = [{**question(), "question_id": f"q-{i}"} for i in range(4)]
    dataset = tmp_path / "custom.json"
    dataset.write_text(json.dumps(questions))
    active = peak = 0

    async def evaluate(q, *args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        raise RuntimeError("isolated question failure")

    monkeypatch.setattr(retrieval_eval, "evaluate_question", evaluate)
    args = SimpleNamespace(dataset=dataset, variant="custom", split="all", seed="test", limit=0,
                           tokenizer="cl100k_base", budgets=[100], k=10, clock="wall", concurrency=2,
                           output=tmp_path / "result.json")
    report = await retrieval_eval.run(args)
    assert peak == 2
    assert report["errors"] == 4 and report["coverage"] == 0 and not report["complete"]
    assert [d["question_id"] for d in report["details"]] == report["dataset"]["selected_question_ids"]
    assert json.loads(args.output.read_text())["concurrency"] == 2
