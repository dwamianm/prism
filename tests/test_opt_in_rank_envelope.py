from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from benchmarks.diagnostics.opt_in_rank_envelope import RankEnvelopeReranker, assign_envelope
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
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

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
    ranker = RankEnvelopeReranker()
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
    ranker = RankEnvelopeReranker()
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


async def test_rank_assignment_receipt_and_feedback_survive_backend_restart(config, user):
    from prme import MemoryEngine
    from prme.models.relevance import RelevanceSubmission
    async with MemoryEngine.open(config) as engine:
        for text in ('I use the blue telescope.', 'I used a green telescope last year.', 'I enjoy playing chess.'):
            await engine.store(text, user_id=user, session_id='authored', scope=Scope.PROJECT)
        ranker = RankEnvelopeReranker()
        ranker._predict_sync = lambda pairs: [.01] * len(pairs)
        engine._retrieval_pipeline._reranker = ranker
        response = await engine.retrieve('Which telescope do I use?', user_id=user, scope=Scope.PROJECT, min_score=0)
        assert response.metadata.receipt_persisted
        saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert saved.schema_version == 13
        assert saved.replay_ranking() == tuple(c.node.id for c in response.results)
        feedback = await engine.record_relevance(RelevanceSubmission(request_id=saved.request_id,
            labels={saved.candidates[0].node_id: True}), user_id=user)
    async with MemoryEngine.open(config) as engine:
        restored = await engine.get_retrieval_receipt(str(saved.request_id), user_id=user)
        assert restored.checksum == saved.checksum and restored.replay_ranking() == saved.replay_ranking()
        assert await engine.get_retrieval_receipt(str(saved.request_id), user_id=user+'-foreign') is None
        restored_feedback = await engine.get_relevance(str(feedback.feedback_id), user_id=user)
        assert restored_feedback.receipt_checksum == saved.checksum
        ordinary = await engine.retrieve('telescope', user_id=user, scope=Scope.PROJECT)
        ordinary_receipt = await engine.get_retrieval_receipt(str(ordinary.metadata.request_id), user_id=user)
        assert ordinary_receipt.schema_version == 12


async def test_source_study_replays_both_controls_and_fails_on_drift(tmp_path, monkeypatch):
    import json
    from benchmarks.diagnostics import opt_in_rank_envelope_study as assay
    from benchmarks.diagnostics.register_opt_in_interactions import clean_config
    case = {'question_id': 'authored', 'question': 'Which telescope color do I prefer?',
        'question_date': '2024/01/02 (Tue) 12:00', 'question_type': 'single-session-user',
        'haystack_session_ids': ['authored-session'], 'haystack_dates': ['2024/01/01 (Mon) 12:00'],
        'haystack_sessions': [[{'role': 'user', 'content': 'I prefer blue telescopes, not red ones.', 'has_answer': True}]],
        'answer_session_ids': ['authored-session']}
    data = clean_config()
    data.update(db_path='{pack}/memory.duckdb', vector_path='{pack}/vectors.usearch',
        lexical_path='{pack}/lexical_index', duckdb_threads=1,
        organizer={**data['organizer'], 'opportunistic_enabled': False})
    root = tmp_path / 'controls'
    monkeypatch.setattr(assay, 'CONTROL_ROOT', root)
    monkeypatch.setattr(CrossEncoderReranker, '_predict_sync', lambda self, pairs: [.01] * len(pairs))
    for name, flag in (('baseline', False), ('reranker', True)):
        folder = root / name / 'authored'
        folder.mkdir(parents=True)
        arm = {'config': {**data, 'enable_reranker': flag}, 'stratum': 'fresh'}
        with assay.study.matched_admission():
            await assay.study.capture(case, arm, folder, None)
    result = await assay.evaluate(case, arm, tmp_path / 'source', assay.ObservedReranker())
    assert result['status'] == 'complete'
    saved = json.loads((tmp_path / 'source/cases/authored.json').read_text())
    assert len(saved['neural_calls']) == len(saved['ordinal_operations']) == 1
    assert saved['arms']['rank_envelope']['receipt']['schema_version'] == 13
    assert all(v['evidence']['complete_source_literal_recall'] for v in saved['arms'].values())
    capture = root / 'reranker/authored/capture.json'
    altered = json.loads(capture.read_text())
    altered['retrievals'][0]['context'] += ' unexpected drift'
    capture.write_text(json.dumps(altered))
    with pytest.raises(assay.study.old.ResearchFailure, match='reranker replay differs'):
        await assay.evaluate(case, arm, tmp_path / 'bad-source', assay.ObservedReranker())
