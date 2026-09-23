from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme.retrieval.reranker import assign_envelope
from prme.models.nodes import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.models import RetrievalCandidate, ScoreAdjustment
from prme.retrieval.packing import pack_context
from prme.retrieval.reranker import CrossEncoderReranker
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import NodeType, Scope

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def inputs():
    rows = [RetrievalCandidate(node=MemoryNode(id=UUID(int=i), user_id='authored',
        node_type=NodeType.FACT, scope=Scope.PERSONAL, content=('Source evidence record. ' * 35),
        created_at=NOW, updated_at=NOW, valid_from=NOW, last_reinforced_at=NOW,
        session_id='session' if i < 3 else 'tail'),
        semantic_score=.95-i*.15, lexical_score=.95-i*.15,
        paths=['VECTOR', 'LEXICAL'], path_count=2) for i in (1, 2, 3)]
    return score_and_rank(rows, now=NOW)[0]


def receipt(rows, bundle, config):
    return make_receipt(request_id=UUID(int=100), user_id='authored', query='evidence',
        reference_time=NOW, scopes=[Scope.PERSONAL], scoring=ScoringWeights(),
        packing=config, candidates=rows, bundle=bundle, ranking_policy='score_id',
        execution=RetrievalExecution(features={'research_policy': 'rank_envelope_v1'}, parameters={}))


async def test_scale_mismatch_and_repair_survive_actual_balanced_packing():
    rows = inputs()
    before = [r.model_dump() for r in rows]
    legacy = CrossEncoderReranker()
    legacy._predict_sync = Mock(return_value=[.001, .002])
    broken = await legacy.rerank('evidence', rows, top_k=2)
    fixed = assign_envelope(rows, broken, 2)
    one = pack_context([rows[0]], PackingConfig(token_budget=5000, overhead_tokens=0, min_fidelity='full'))
    config = PackingConfig(token_budget=one.tokens_used, overhead_tokens=0, min_fidelity='full')
    legacy_bundle = pack_context(broken, config)
    candidate_bundle = pack_context(fixed, config)
    assert [c.node.id for values in legacy_bundle.sections.values() for c in values] == [rows[2].node.id]
    assert [c.node.id for values in candidate_bundle.sections.values() for c in values] == [fixed[0].node.id]
    assert fixed[0].node.id != rows[2].node.id
    assert sorted(c.composite_score for c in fixed[:2]) == sorted(c.composite_score for c in rows[:2])
    assert fixed[2].composite_score == rows[2].composite_score and fixed[2].reranker_score is None
    assert [r.model_dump() for r in rows] == before


async def test_version_13_replays_raw_neural_then_assignment_and_session_inheritance():
    rows = inputs()
    ranker = CrossEncoderReranker(policy="score_envelope")
    ranker._predict_sync = Mock(return_value=[.001, .8])
    ranked = await ranker.rerank('evidence', rows, top_k=2)
    assert ranked[0].node.id == rows[1].node.id
    store = Mock()
    # The compatibility graph path is intentionally used for this unit case.
    store.query_nodes = AsyncMock(return_value=[r.node for r in rows[:2]])
    config = PackingConfig(session_context_window=1, session_context_top_k=1,
                           session_context_score_decay=.99)
    expanded = await expand_session_context(ranked, store, 'authored', config)
    expanded.sort(key=lambda c: (-c.composite_score, str(c.node.id)))
    bundle = pack_context(expanded, config)
    saved = receipt(expanded, bundle, config)
    assert saved.schema_version == 13
    assert saved.replay_ranking() == tuple(c.node.id for c in expanded)
    inherited = next(c for c in expanded if c.node.id == rows[0].node.id)
    assert [op.kind for op in inherited.score_provenance.adjustments][-2:] == ['neural_rank_assignment', 'session_decay']
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.checksum == saved.checksum
    assert restored.replay_ranking() == saved.replay_ranking()
    payload = saved.model_dump(mode='json')
    payload['schema_version'] = 12
    with pytest.raises(ValidationError, match='version 13'):
        RetrievalReceipt.model_validate(payload)


async def test_no_opt_in_keeps_version_12_and_existing_reranker_unchanged():
    rows = inputs()
    saved = receipt(rows, pack_context(rows, PackingConfig()), PackingConfig())
    assert saved.schema_version == 12
    legacy = CrossEncoderReranker()
    legacy._predict_sync = Mock(return_value=[.001])
    ranked = await legacy.rerank('evidence', rows, top_k=1)
    assert ranked[0].composite_score < ranked[1].composite_score
    assert all(op.kind != 'neural_rank_assignment' for r in ranked for op in r.score_provenance.adjustments)


async def test_empty_zero_boundary_ties_and_deterministic_repeat():
    ranker = CrossEncoderReranker(policy="score_envelope")
    ranker._predict_sync = Mock(return_value=[.5, .5])
    assert await ranker.rerank('evidence', []) == []
    rows = inputs()
    assert await ranker.rerank('evidence', rows, top_k=0) == rows
    first = await ranker.rerank('evidence', rows, top_k=2)
    second = await ranker.rerank('evidence', rows, top_k=2)
    assert first == second
    assert first[-1].node.id == rows[-1].node.id


def test_malformed_mapping_fails_before_input_mutation():
    rows = inputs()
    with pytest.raises(ValueError, match='identity'):
        assign_envelope(rows, list(reversed(rows)), 2)
    with pytest.raises(ValueError, match='lineage'):
        assign_envelope(rows, rows, 2)
    with pytest.raises(ValidationError, match='nonnegative'):
        ScoreAdjustment(kind='neural_rank_assignment', coefficient=-1, source_node_id=UUID(int=1))
