"""Opt-in policies through public configuration, durable receipts and learning gates."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig, RankingMultipliers
from prme.models.relevance import RelevanceSubmission
from prme.retrieval.execution import reranker_identity
from prme.retrieval.pipeline import RetrievalPipeline
from prme.retrieval.reranker import CrossEncoderReranker
from prme.types import Scope
from tests import test_durable_ingestion, test_ranking_profiles
from tests.previous_defaults import previous_defaults
from tests.test_reformulation_merge import candidate

durable_config = test_durable_ingestion.config


@pytest.fixture
def config(durable_config):
    # Experimental policies of the weighted formula and their receipt versions: they keep the previous retrieval
    # defaults, which rank fusion and the reader format replaced on 2026-09-24.
    return previous_defaults(durable_config)


user = test_durable_ingestion.user


def test_policy_defaults_and_invalid_values():
    cfg = PRMEConfig()
    assert not cfg.enable_reranker and not cfg.enable_query_reformulation
    assert cfg.reranker_policy == 'legacy'
    assert cfg.query_reformulation_merge_policy == 'new_only'
    assert cfg.query_intent_order == 'entity_first'
    for key in ('reranker_policy', 'query_reformulation_merge_policy', 'query_intent_order'):
        with pytest.raises(ValidationError):
            PRMEConfig(**{key: 'unknown'})
    with pytest.raises(ValueError, match='policy'):
        CrossEncoderReranker(policy='unknown')
    assert reranker_identity(CrossEncoderReranker()) == {
        'enabled': True, 'provider': 'prme.retrieval.reranker.CrossEncoderReranker',
        'model': 'cross-encoder/ms-marco-MiniLM-L-6-v2',
    }
    assert reranker_identity(None) == {
        'enabled': False, 'provider': 'builtins.NoneType', 'model': None,
    }


@pytest.mark.parametrize('rank,merge', [
    ('legacy', 'new_only'), ('score_envelope', 'new_only'),
    ('anchored_score_envelope', 'new_only'), ('legacy', 'max_signals'),
    ('anchored_score_envelope', 'max_signals'),
])
async def test_public_config_receipt_replay_owner_feedback_and_restart(config, user, monkeypatch, rank, merge):
    reformulate = AsyncMock(return_value=['blue telescope', 'telescope optics'])
    monkeypatch.setattr('prme.retrieval.reformulation.reformulate_query', reformulate)
    monkeypatch.setattr(CrossEncoderReranker, '_predict_sync', lambda self, pairs: [.01] * len(pairs))
    enabled = config.model_copy(update={
        'enable_reranker': True, 'reranker_policy': rank,
        'enable_query_reformulation': True, 'query_reformulation_merge_policy': merge,
    })
    async with MemoryEngine.open(enabled) as engine:
        for text in ('I use a blue telescope.', 'I used a green telescope last year.'):
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        await engine.store('A red telescope is my current preference.', user_id=user, scope=Scope.PERSONAL)
        await engine.store('I use a secret telescope.', user_id=user+'-foreign', scope=Scope.PROJECT)
        response = await engine.retrieve('Which telescope do I use?', user_id=user, scope=Scope.PROJECT, min_score=0)
        assert response.metadata.receipt_persisted and not response.metadata.backend_failures
        assert response.results
        assert all(c.node.user_id == user and c.node.scope == Scope.PROJECT for c in response.results)
        saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert saved.schema_version == (12 if rank == 'legacy' else 13)
        assert saved.replay_ranking() == tuple(c.node.id for c in response.results)
        assert saved.execution.features['reranker'].get('policy', 'legacy') == rank
        assert saved.execution.parameters['query_reformulation'].get('merge_policy', 'new_only') == merge
        if merge == 'max_signals':
            assert saved.execution.features['query_reformulation_merge']['policy'] == merge
        else:
            assert 'query_reformulation_merge' not in saved.execution.features
        assert reformulate.call_args.kwargs['provider'] == enabled.extraction.provider
        assert reformulate.call_args.kwargs['model'] == enabled.extraction.model
        feedback = await engine.record_relevance(RelevanceSubmission(
            request_id=saved.request_id, labels={saved.candidates[0].node_id: True}), user_id=user)
    async with MemoryEngine.open(config) as engine:
        restored = await engine.get_retrieval_receipt(str(saved.request_id), user_id=user)
        assert restored.checksum == saved.checksum and restored.replay_ranking() == saved.replay_ranking()
        assert await engine.get_retrieval_receipt(str(saved.request_id), user_id=user+'-foreign') is None
        restored_feedback = await engine.get_relevance(str(feedback.feedback_id), user_id=user)
        assert restored_feedback.receipt_checksum == saved.checksum
        calls = reformulate.call_count
        response = await engine.retrieve('telescope', user_id=user, scope=Scope.PROJECT)
        assert reformulate.call_count == calls
        ordinary = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert ordinary.schema_version == 12
        assert 'policy' not in ordinary.execution.features['reranker']
        assert 'merge_policy' not in ordinary.execution.parameters['query_reformulation']
        assert 'query_reformulation_merge' not in ordinary.execution.features


@pytest.mark.parametrize('changed_policy', ['rank', 'merge', 'intent'])
async def test_policy_change_prevents_profile_activation(config, user, changed_policy):
    async with MemoryEngine.open(config) as engine:
        proposal, holdout = test_ranking_profiles._evidence(engine, RankingMultipliers(lexical=2), owner=user)
        profile = await engine.create_ranking_profile(proposal, holdout, user_id=user)
        pipeline = engine._retrieval_pipeline
        if changed_policy == 'rank':
            pipeline._reranker = CrossEncoderReranker(policy='anchored_score_envelope')
        elif changed_policy == 'merge':
            pipeline._enable_query_reformulation = True
            pipeline._query_reformulation_merge_policy = 'max_signals'
        else:
            # The order decides which questions get temporal scoring (issue #85).
            pipeline._query_intent_order = 'temporal_first'
        with pytest.raises(ValueError, match='feature_identity_mismatch'):
            await engine.activate_ranking_profile(str(profile.profile_id), user_id=user)


async def test_disabled_flags_never_activate_selected_policy(config, user, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError('disabled feature invoked'))
    monkeypatch.setattr('prme.retrieval.reformulation.reformulate_query', forbidden)
    cfg = config.model_copy(update={
        'reranker_policy': 'anchored_score_envelope', 'query_reformulation_merge_policy': 'max_signals',
    })
    async with MemoryEngine.open(cfg) as engine:
        assert engine._retrieval_pipeline._reranker is None
        assert 'query_reformulation_merge' not in engine._retrieval_pipeline.execution_features()
        await engine.store('blue telescope', user_id=user, scope=Scope.PROJECT)
        await engine.retrieve('telescope', user_id=user, scope=Scope.PROJECT)
        forbidden.assert_not_awaited()


async def test_merge_failure_settles_all_passes_before_propagation(monkeypatch):
    from prme.retrieval import pipeline as module
    monkeypatch.setattr('prme.retrieval.reformulation.reformulate_query', AsyncMock(return_value=['fail', 'finish']))
    monkeypatch.setattr(module, 'analyze_query', AsyncMock(side_effect=lambda query, **kw: query))
    finished = []
    async def generate(query, **kwargs):
        if query == 'fail':
            raise RuntimeError('authored failure')
        await asyncio.sleep(.01)
        finished.append(True)
        return [candidate(2, ['VECTOR'], .9)], {}
    monkeypatch.setattr(module, 'generate_candidates', generate)
    pipeline = SimpleNamespace(_query_reformulation_provider='ollama', _query_reformulation_model='authored',
        _query_reformulation_count=2, _temporal_languages=['en'], _graph_store=None,
        _vector_index=None, _lexical_index=None, _query_reformulation_merge_policy='max_signals',
        _query_reformulation_api_key=None, _query_reformulation_base_url=None, _query_reformulation_timeout=None,
        _query_reformulation_clients={}, _query_intent_order='entity_first')
    values = [candidate(1, ['VECTOR'], .3)]
    with pytest.raises(RuntimeError, match='authored failure'):
        await RetrievalPipeline._expand_reformulated_queries(pipeline, 'telescope', candidates=values,
            user_id='authored', scope=[Scope.PERSONAL], time_from=None, time_to=None, retrieval_mode=None, config=None)
    assert finished == [True] and len(values) == 1
