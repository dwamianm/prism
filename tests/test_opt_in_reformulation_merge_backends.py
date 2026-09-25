"""Actual scoped retrieval, receipt replay and restart on both storage backends."""
from types import MethodType
from unittest.mock import AsyncMock

import pytest

from benchmarks.diagnostics.opt_in_reformulation_merge import expand_with_merge
from prme import MemoryEngine
from prme.models.relevance import RelevanceSubmission
from prme.types import Scope
from tests import test_durable_ingestion
from tests.previous_defaults import previous_defaults

durable_config = test_durable_ingestion.config


@pytest.fixture
def config(durable_config):
    # Reformulation merges of the weighted formula and their receipt versions: they keep the previous retrieval
    # defaults, which rank fusion and the reader format replaced on 2026-09-24.
    return previous_defaults(durable_config)


user = test_durable_ingestion.user


async def test_merge_retrieval_receipt_scope_feedback_and_restart(config, user, monkeypatch):
    monkeypatch.setattr('prme.retrieval.reformulation.reformulate_query',
                        AsyncMock(return_value=['blue telescope', 'telescope optics']))
    async with MemoryEngine.open(config) as engine:
        await engine.store('I use a blue telescope.', user_id=user, scope=Scope.PROJECT)
        await engine.store('I used a green telescope last year.', user_id=user, scope=Scope.PROJECT)
        await engine.store('A red telescope is my current preference.', user_id=user, scope=Scope.PERSONAL)
        await engine.store('I use a secret telescope.', user_id=user+'-foreign', scope=Scope.PROJECT)
        pipeline = engine._retrieval_pipeline
        pipeline._enable_query_reformulation = True
        pipeline._research_merge_trace = []
        pipeline._expand_reformulated_queries = MethodType(expand_with_merge, pipeline)
        response = await engine.retrieve('Which telescope do I use?', user_id=user, scope=Scope.PROJECT, min_score=0)
        assert response.metadata.receipt_persisted and not response.metadata.backend_failures
        assert all(c.node.user_id == user and c.node.scope == Scope.PROJECT for c in response.results)
        assert len(pipeline._research_merge_trace) == 1
        saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert saved.schema_version == 12
        assert saved.replay_ranking() == tuple(c.node.id for c in response.results)
        feedback = await engine.record_relevance(RelevanceSubmission(request_id=saved.request_id,
            labels={saved.candidates[0].node_id: True}), user_id=user)
    async with MemoryEngine.open(config) as engine:
        restored = await engine.get_retrieval_receipt(str(saved.request_id), user_id=user)
        assert restored.checksum == saved.checksum and restored.replay_ranking() == saved.replay_ranking()
        assert await engine.get_retrieval_receipt(str(saved.request_id), user_id=user+'-foreign') is None
        restored_feedback = await engine.get_relevance(str(feedback.feedback_id), user_id=user)
        assert restored_feedback.receipt_checksum == saved.checksum
        assert not engine._retrieval_pipeline._enable_query_reformulation
