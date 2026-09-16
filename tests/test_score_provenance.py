"""Saved ranking replay must use actual weights, score lineage and sort policy."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme.models.nodes import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.models import MemoryBundle, QueryAnalysis, RetrievalCandidate
from prme.retrieval.reranker import CrossEncoderReranker
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import NodeType, QueryIntent, Scope

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def candidate(number, *, semantic=0.8, lexical=0.6, days=0, session=None, paths=1):
    ts = NOW - timedelta(days=days)
    return RetrievalCandidate(node=MemoryNode(
        id=UUID(int=number), user_id="owner", scope=Scope.PROJECT,
        node_type=NodeType.FACT, content="I now use the telescope on 2026-09-10.",
        created_at=ts, updated_at=ts, last_reinforced_at=ts, event_time=ts,
        session_id=session, confidence_base=0.8, salience_base=0.5,
    ), semantic_score=semantic, lexical_score=lexical, path_count=paths)


def receipt(candidates, *, query="telescope", policy="score_path_id", weights=None):
    return make_receipt(request_id=UUID(int=100), user_id="owner", query=query,
                        reference_time=NOW, scopes=(Scope.PROJECT,),
                        scoring=weights or ScoringWeights(),
                        packing=PackingConfig(multipath_ordering="density"),
                        candidates=candidates, bundle=MemoryBundle(), ranking_policy=policy)


@pytest.mark.parametrize("query,intent,expected_recency", [
    ("What telescope?", QueryIntent.FACTUAL, 0.1),
    ("What telescope do I currently use?", QueryIntent.FACTUAL, 0.25),
    ("What did we discuss recently?", QueryIntent.FACTUAL, 0.2),
    ("What happened on September 10?", QueryIntent.TEMPORAL, 0.1),
])
def test_replay_uses_query_adjusted_weights_and_frozen_features(query, intent, expected_recency):
    analysis = QueryAnalysis(query=query, intent=intent,
                             time_from=NOW - timedelta(days=3), time_to=NOW)
    items, _ = score_and_rank([candidate(1, days=10), candidate(2)], now=NOW, query_analysis=analysis)
    saved = receipt(items, query=query)
    assert saved.schema_version == 2
    assert saved.scoring.w_recency == 0.1
    assert all(p.weights.w_recency == expected_recency for p in saved.score_provenance.values())
    assert saved.replay_ranking() == tuple(c.node.id for c in items)
    for item in items:
        p = saved.score_provenance[item.node.id]
        assert p.replay_score() == item.composite_score
        item.node.confidence_base = 0.01
        assert p.replay_score() == item.composite_score
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.checksum == saved.checksum
    assert restored.replay_ranking() == saved.replay_ranking()


def test_version_nine_replays_current_update_adjustment():
    old = candidate(1, semantic=0.85, lexical=1.0)
    old.node.content = "The project budget is OLD_BUDGET dollars."
    old.node.event_time = NOW - timedelta(microseconds=1)
    update = candidate(2, semantic=0.80, lexical=0.0)
    update.node.content = "The updated project budget is NEW_BUDGET dollars."
    analysis = QueryAnalysis(
        query="What is the current project budget?",
        intent=QueryIntent.FACTUAL,
    )
    ranked, _ = score_and_rank(
        [old, update],
        now=NOW,
        query_analysis=analysis,
    )
    saved = make_receipt(
        request_id=UUID(int=101),
        user_id="owner",
        query=analysis.query,
        reference_time=NOW,
        scopes=(Scope.PROJECT,),
        scoring=ScoringWeights(),
        packing=PackingConfig(),
        candidates=ranked,
        bundle=MemoryBundle(),
        execution=RetrievalExecution(features={"test": True}, parameters={}),
    )

    assert saved.schema_version == 10
    assert saved.replay_ranking() == (update.node.id, old.node.id)
    operation = saved.score_provenance[update.node.id].adjustments[-1]
    assert operation.kind == "current_update"
    assert operation.coefficient == pytest.approx(1.3)
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.checksum == saved.checksum
    assert restored.replay_ranking() == saved.replay_ranking()


def test_relevance_cap_and_path_tie_break_are_replayed():
    items, _ = score_and_rank([candidate(1, semantic=.01, lexical=.02, paths=1),
                              candidate(2, semantic=.01, lexical=.02, paths=3)], now=NOW)
    assert [c.composite_score for c in items] == [.03, .03]
    assert [c.node.id.int for c in items] == [2, 1]
    assert receipt(items).replay_ranking() == tuple(c.node.id for c in items)


@pytest.mark.parametrize("query", ["What telescope do I currently use?", "What did we discuss recently?"])
def test_recency_redistribution_cannot_create_negative_relevance_weights(query):
    weights = ScoringWeights(w_semantic=.01, w_lexical=.02, w_graph=.62,
                             w_recency=.1, w_salience=.1, w_confidence=.15)
    items, _ = score_and_rank([candidate(1)], weights=weights, now=NOW,
                              query_analysis=QueryAnalysis(query=query, intent=QueryIntent.FACTUAL))
    applied = items[0].score_provenance.weights
    assert applied.w_semantic >= 0 and applied.w_lexical >= 0
    assert applied.w_recency == pytest.approx(.13)
    assert applied.w_graph == weights.w_graph
    assert receipt(items, query=query, weights=weights).replay_ranking() == (items[0].node.id,)


async def test_partial_neural_reranking_keeps_prefix_order_and_exact_blend():
    items, _ = score_and_rank([candidate(i) for i in (3, 2, 1)], now=NOW)
    reranker = CrossEncoderReranker()
    reranker._predict_sync = Mock(return_value=[.01])
    ranked = await reranker.rerank("telescope", items, top_k=1, prior_weight=.3)
    # Prefix remains ahead of a tail with higher scores; global sorting is wrong.
    assert ranked[0].composite_score < ranked[1].composite_score
    saved = receipt(ranked, policy="reranked_prefix")
    assert saved.replay_ranking() == tuple(c.node.id for c in ranked)
    assert saved.score_provenance[ranked[0].node.id].adjustments[0].coefficient == .3
    assert items[0].score_provenance.adjustments == ()


async def test_session_inherits_neural_score_even_if_trigger_not_returned():
    trigger = candidate(1, days=1, session="conversation")
    adjacent = candidate(2, session="conversation")
    items, _ = score_and_rank([trigger], now=NOW)
    reranker = CrossEncoderReranker()
    reranker._predict_sync = Mock(return_value=[.9])
    ranked = await reranker.rerank("telescope", items, prior_weight=.4)
    graph = Mock(query_nodes=AsyncMock(return_value=[trigger.node, adjacent.node]))
    expanded = await expand_session_context(ranked, graph, "owner", PackingConfig(), [Scope.PROJECT])
    session = next(c for c in expanded if c.node.id == adjacent.node.id)
    assert session.score_trace is None
    assert session.score_provenance.base_node_id == trigger.node.id
    assert [op.kind for op in session.score_provenance.adjustments] == ["neural_blend", "session_decay"]
    assert session.score_provenance.adjustments[-1].source_node_id == trigger.node.id
    saved = receipt([session], policy="score_id")
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.score_provenance[session.node.id].replay_score() == ranked[0].composite_score * .85
    assert restored.replay_ranking() == (session.node.id,)


async def test_existing_candidate_inherits_replayable_session_score():
    trigger = candidate(1, days=1, session="conversation")
    adjacent = candidate(
        2, days=2, session="conversation", semantic=0.1, lexical=0.1
    )
    items, _ = score_and_rank([trigger, adjacent], now=NOW)
    assert items[0].node.id == trigger.node.id
    original_adjacent_score = items[1].composite_score
    graph = Mock(query_nodes=AsyncMock(return_value=[trigger.node, adjacent.node]))

    expanded = await expand_session_context(
        items,
        graph,
        "owner",
        PackingConfig(session_context_top_k=1, session_context_score_decay=0.85),
        [Scope.PROJECT],
    )

    inherited = next(c for c in expanded if c.node.id == adjacent.node.id)
    assert inherited.composite_score > original_adjacent_score
    assert inherited.score_trace is None
    assert inherited.score_provenance.base_node_id == trigger.node.id
    assert inherited.score_provenance.adjustments[-1].kind == "session_decay"
    saved = receipt(expanded, policy="score_id")
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.score_provenance[inherited.node.id].replay_score() == inherited.composite_score
    assert restored.replay_ranking() == tuple(candidate.node.id for candidate in expanded)


async def test_provenance_does_not_restrict_existing_finite_session_multipliers():
    trigger = candidate(1, days=1, session="conversation")
    adjacent = candidate(2, session="conversation")
    items, _ = score_and_rank([trigger], now=NOW)
    graph = Mock(query_nodes=AsyncMock(return_value=[trigger.node, adjacent.node]))
    expanded = await expand_session_context(items, graph, "owner",
        PackingConfig(session_context_score_decay=1.5), [Scope.PROJECT])
    saved = receipt(expanded, policy="score_id")
    assert saved.replay_ranking() == (adjacent.node.id, trigger.node.id)
    assert saved.score_provenance[adjacent.node.id].replay_score() == trigger.composite_score * 1.5


def test_v1_canonical_bytes_and_checksum_remain_unchanged():
    # Generated by the frozen 1461fb0 runtime, before V2 fields existed.
    raw = (Path(__file__).parent / "fixtures/relevance/receipt-v1.json").read_text()
    expected = "6d62ed27541d103796e189d0d066223fc6bf8ad0a0fe3cc4fb2acf7377c103c6"
    assert hashlib.sha256(raw.encode()).hexdigest() == expected
    old = RetrievalReceipt.model_validate_json(raw)
    assert old.model_dump_json() == raw
    assert old.checksum == expected
    assert "score_provenance" not in old.model_dump(mode="json")
    with pytest.raises(ValueError, match="Version 1 receipts lack"):
        old.replay_ranking()


def test_receipt_rejects_a_sort_policy_that_does_not_reproduce_return_order():
    items, _ = score_and_rank([candidate(1, semantic=.01, lexical=.02, paths=1),
                              candidate(2, semantic=.01, lexical=.02, paths=3)], now=NOW)
    with pytest.raises(ValidationError, match="returned ranking"):
        receipt(items, policy="score_id")


@pytest.mark.parametrize("change", ["missing", "score", "base", "nonfinite", "legacy"])
def test_invalid_provenance_is_rejected(change):
    items, _ = score_and_rank([candidate(1)], now=NOW)
    data = json.loads(receipt(items).model_dump_json())
    key = str(items[0].node.id)
    if change == "missing":
        data["score_provenance"] = {}
    elif change == "score":
        data["candidates"][0]["score"] += .1
    elif change == "base":
        data["score_provenance"][key]["trace"]["semantic_similarity"] += .1
    elif change == "nonfinite":
        data["score_provenance"][key]["weights"]["recency_lambda"] = float("nan")
    else:
        data["schema_version"] = 1
    with pytest.raises(ValidationError):
        RetrievalReceipt.model_validate(data)
