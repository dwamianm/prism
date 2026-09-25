"""Cross-encoder rank order over the fused prefix (issue #88).

A prior weight of 0 orders the reranked prefix by cross-encoder score alone.
With an envelope policy the prefix takes its original scores in that order, so
rank fusion's scale reaches session expansion and packing unchanged. The model
is mocked: these tests cover the ordering, receipts and replay, not the model.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig, RetrievalReceipt
from prme.models.nodes import MemoryNode
from prme.models.relevance import make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution, reranker_identity
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.pipeline import RetrievalPipeline
from prme.retrieval.reranker import DEFAULT_PRIOR_WEIGHT, CrossEncoderReranker
from prme.retrieval.scoring import score_and_rank
from prme.types import NodeType, Scope
from tests import test_durable_ingestion

durable_config = test_durable_ingestion.config
user = test_durable_ingestion.user

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)
RANK_FUSION = ScoringWeights(fusion="rrf")


def fused_rows(count: int = 5) -> list[RetrievalCandidate]:
    """Candidates ranked 1..count on both channels, scored by rank fusion."""
    rows = [
        RetrievalCandidate(
            node=MemoryNode(
                id=UUID(int=i), user_id="authored", node_type=NodeType.FACT,
                scope=Scope.PERSONAL, content=f"Record {i} about the telescope.",
                created_at=NOW, updated_at=NOW, valid_from=NOW, last_reinforced_at=NOW,
            ),
            semantic_score=0.9 - i * 0.1, lexical_score=0.9 - i * 0.1,
            paths=["VECTOR", "LEXICAL"], path_count=2,
        )
        for i in range(1, count + 1)
    ]
    return score_and_rank(rows, RANK_FUSION, now=NOW)[0]


def ranker(neural: list[float], **kwargs) -> CrossEncoderReranker:
    reranker = CrossEncoderReranker(**kwargs)
    reranker._predict_sync = lambda pairs: neural[: len(pairs)]
    return reranker


# The second record's model score is only slightly higher than the first's, so
# the original 0.3 blend keeps the fused order and only rank order swaps them.
NEURAL = [0.50, 0.505, 0.2, 0.1]


async def test_zero_prior_weight_orders_the_fused_prefix_by_the_model_alone():
    rows = fused_rows()
    before = [row.model_dump() for row in rows]
    blended = await ranker(NEURAL, policy="score_envelope").rerank("telescope", rows, top_k=4)
    ranked = await ranker(NEURAL, policy="score_envelope", prior_weight=0.0).rerank(
        "telescope", rows, top_k=4)

    assert [c.node.id for c in blended[:2]] == [rows[0].node.id, rows[1].node.id]
    assert [c.node.id for c in ranked[:4]] == [rows[i].node.id for i in (1, 0, 2, 3)]
    # The envelope keeps the fused scores: the prefix takes them in model order.
    assert [c.composite_score for c in ranked[:4]] == [c.composite_score for c in rows[:4]]
    assert [c.reranker_score for c in ranked[:4]] == [0.505, 0.50, 0.2, 0.1]
    # The tail is untouched and carries no model judgment.
    assert ranked[4] == rows[4] and ranked[4].reranker_score is None
    for candidate in ranked[:4]:
        blend, assignment = candidate.score_provenance.adjustments[-2:]
        assert (blend.kind, blend.coefficient) == ("neural_blend", 0.0)
        assert assignment.kind == "neural_rank_assignment"
        assert candidate.score_provenance.replay_score() == candidate.composite_score
        assert candidate.score_provenance.formula_version == 2
    assert [row.model_dump() for row in rows] == before


async def test_zero_prior_weight_with_the_legacy_policy_carries_raw_model_scores():
    rows = fused_rows()
    ranked = await ranker(NEURAL, prior_weight=0.0).rerank("telescope", rows, top_k=4)
    assert [c.composite_score for c in ranked[:4]] == [0.505, 0.50, 0.2, 0.1]
    assert ranked[4] == rows[4]


async def test_a_call_weight_overrides_the_reranker_weight():
    rows = fused_rows()
    configured = ranker(NEURAL, policy="score_envelope", prior_weight=0.0)
    assert (await configured.rerank("telescope", rows, top_k=4, prior_weight=DEFAULT_PRIOR_WEIGHT))[0].node.id \
        == rows[0].node.id
    assert (await configured.rerank("telescope", rows, top_k=4))[0].node.id == rows[1].node.id
    with pytest.raises(ValueError, match="prior_weight"):
        await configured.rerank("telescope", rows, top_k=4, prior_weight=1.5)


@pytest.mark.parametrize("weight", [-0.1, 1.5, math.nan, math.inf])
def test_prior_weight_must_be_between_zero_and_one(weight):
    with pytest.raises(ValueError, match="prior_weight"):
        CrossEncoderReranker(prior_weight=weight)
    with pytest.raises(ValidationError):
        PRMEConfig(reranker_prior_weight=weight)
    with pytest.raises(ValueError, match="reranker_prior_weight"):
        RetrievalPipeline(None, None, None, None, reranker_prior_weight=weight)


def test_default_weight_and_identity_keep_their_bytes():
    assert PRMEConfig().reranker_prior_weight == DEFAULT_PRIOR_WEIGHT == 0.3
    default = reranker_identity(CrossEncoderReranker())
    assert "prior_weight" not in default
    assert reranker_identity(CrossEncoderReranker(prior_weight=0.3)) == default
    rank_order = reranker_identity(CrossEncoderReranker(policy="score_envelope", prior_weight=0.0))
    assert rank_order == {**default, "policy": "score_envelope", "prior_weight": 0.0}


async def test_rank_fusion_receipt_replays_the_rank_order():
    rows = fused_rows()
    ranked = await ranker(NEURAL, policy="score_envelope", prior_weight=0.0).rerank(
        "telescope", rows, top_k=4)
    packing = PackingConfig()
    saved = make_receipt(
        request_id=UUID(int=100), user_id="authored", query="telescope", reference_time=NOW,
        scopes=[Scope.PERSONAL], scoring=RANK_FUSION, packing=packing, candidates=ranked,
        bundle=pack_context(ranked, packing), ranking_policy="reranked_prefix",
        execution=RetrievalExecution(features={}, parameters={}),
    )
    # Rank fusion receipts already admit neural rank assignments.
    assert saved.schema_version == 16
    assert saved.replay_ranking() == tuple(c.node.id for c in ranked)
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.checksum == saved.checksum
    assert restored.replay_ranking() == saved.replay_ranking()


def _neural(favorite: str):
    # A deterministic stand-in for the model: it prefers one record, and
    # otherwise longer text.
    return lambda self, pairs: [0.95 if text == favorite else 0.1 + len(text) / 1000 for _, text in pairs]


TEXTS = (
    "Which telescope do I use? I use a blue telescope for the moon.",
    "My telescope is a blue reflector telescope with a tripod.",
    "I bought a green refractor last year.",
    "The telescope club meets on Fridays.",
    "Telescopes need dark skies.",
)


async def test_product_defaults_with_rank_order_replay_and_repeat(durable_config, user, monkeypatch):
    config = durable_config.model_copy(update={
        "enable_reranker": True, "reranker_policy": "score_envelope", "reranker_prior_weight": 0.0,
    })
    assert config.scoring.fusion == "rrf"
    query = "Which telescope do I use?"
    args = dict(user_id=user, scope=Scope.PROJECT, reference_time=NOW, min_score=0,
                include_cross_scope=False)
    async with MemoryEngine.open(config) as engine:
        for text in TEXTS:
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        pipeline = engine._retrieval_pipeline
        reranker = pipeline._reranker
        pipeline._reranker = None
        fused = await engine.retrieve(query, **args)
        fused_receipt = await engine.get_retrieval_receipt(str(fused.metadata.request_id), user_id=user)
        # The model prefers the record the fused ranking puts last.
        favorite = fused.results[-1].node.content
        monkeypatch.setattr(CrossEncoderReranker, "_predict_sync", _neural(favorite))
        pipeline._reranker = reranker
        first = await engine.retrieve(query, **args)
        second = await engine.retrieve(query, **args)
        saved = await engine.get_retrieval_receipt(str(first.metadata.request_id), user_id=user)

        assert len(fused.results) == len(TEXTS)
        assert first.results[0].node.content == favorite
        # Every result was in the prefix, so the results take the fused scores in model order.
        fused_scores = {c.node.id: c.composite_score for c in fused.results}
        assert set(fused_scores) == {c.node.id for c in first.results}
        assert [c.composite_score for c in first.results] == sorted(fused_scores.values(), reverse=True)
        assert [c.node.id for c in first.results] == [
            c.node.id for c in sorted(first.results, key=lambda c: -c.reranker_score)]

        # Reranking under rank fusion keeps the receipt version, and the receipt replays.
        assert saved.schema_version == fused_receipt.schema_version
        assert saved.ranking_policy == "reranked_prefix"
        assert saved.replay_ranking() == tuple(c.node.id for c in first.results)
        assert saved.execution.parameters["reranker_prior_weight"] == 0.0
        assert saved.execution.features["reranker"]["policy"] == "score_envelope"
        assert saved.execution.features["reranker"]["prior_weight"] == 0.0
        assert "reranker_prior_weight" not in fused_receipt.execution.parameters

        # The same request gives the same results and context.
        assert first.bundle.render() == second.bundle.render()
        assert [(c.node.id, c.composite_score, c.reranker_score) for c in first.results] == [
            (c.node.id, c.composite_score, c.reranker_score) for c in second.results]

        pipeline._reranker = CrossEncoderReranker(policy="score_envelope")
        blended = await engine.retrieve(query, **args)
        default = await engine.get_retrieval_receipt(str(blended.metadata.request_id), user_id=user)
        assert "reranker_prior_weight" not in default.execution.parameters
        assert "prior_weight" not in default.execution.features["reranker"]

    async with MemoryEngine.open(config) as engine:
        restored = await engine.get_retrieval_receipt(str(saved.request_id), user_id=user)
        assert restored.checksum == saved.checksum
        assert restored.replay_ranking() == saved.replay_ranking()
        again = await engine.retrieve(query, **args)
        assert again.bundle.render() == first.bundle.render()
