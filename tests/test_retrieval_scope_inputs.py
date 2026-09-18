"""Malformed or mutable scope input must never broaden a retrieval."""
from unittest.mock import AsyncMock

import pytest

from prme import MemoryClient, MemoryEngine, Scope
from tests import test_durable_ingestion
from tests.test_http_write_fidelity import app_for, client_for

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def seed(engine, user):
    for scope, content in ((Scope.PERSONAL, "Private telescope memory"), (Scope.PROJECT, "Team telescope memory")):
        await engine.store(content, user_id=user, scope=scope)


@pytest.mark.parametrize("scope", ["project", ["project"], (Scope.PROJECT,)])
async def test_scope_names_and_sequences_filter_actual_candidates(config, user, scope):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        for retrieve in (engine.retrieve, engine._retrieval_pipeline.retrieve):
            response = await retrieve("telescope", user_id=user, scope=scope, include_cross_scope=False)
            assert response.results
            assert {candidate.node.scope for candidate in response.results} == {Scope.PROJECT}
            assert "Private telescope" not in response.bundle.render()
            receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
            assert receipt.scopes == (Scope.PROJECT,)


@pytest.mark.parametrize("scope", [[], "typo", ["project", "typo"], {}, b"project"])
async def test_invalid_scope_fails_before_materialization_or_retrieval(config, user, scope, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        pending = await engine.ingest_fast("Pending source", user_id=user)
        debt = AsyncMock(wraps=engine._materialization_queue.debt)
        monkeypatch.setattr(engine._materialization_queue, "debt", debt)
        with pytest.raises(ValueError, match="scope"):
            await engine.retrieve("telescope", user_id=user, scope=scope)
        debt.assert_not_awaited()
        assert (await engine.processing_status(pending, user_id=user)).status == "pending"
        with pytest.raises(ValueError, match="scope"):
            await engine._retrieval_pipeline.retrieve("telescope", user_id=user, scope=scope)


async def test_scope_list_is_copied_before_first_await(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        requested_scope = [Scope.PROJECT]
        original = engine._materialization_queue.debt
        async def change_callers_list():
            requested_scope.clear()
            return await original()
        monkeypatch.setattr(engine._materialization_queue, "debt", change_callers_list)
        response = await engine.retrieve("telescope", user_id=user, scope=requested_scope, include_cross_scope=False)
        assert requested_scope == []
        assert response.results and all(candidate.node.scope == Scope.PROJECT for candidate in response.results)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert receipt.scopes == (Scope.PROJECT,)


async def test_http_empty_scope_is_validation_error(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        spy = AsyncMock()
        monkeypatch.setattr(engine, "retrieve", spy)
        async with client_for(app_for(config, engine, user)) as client:
            response = await client.post("/v1/retrieve", json={"query": "telescope", "filters": {"scope": []}})
        assert response.status_code == 422
        spy.assert_not_awaited()


def test_sync_scope_name_is_supported_and_invalid_scope_is_rejected(config, user):
    with MemoryClient(config=config) as client:
        client.store("Private telescope memory", user_id=user, scope=Scope.PERSONAL)
        client.store("Team telescope memory", user_id=user, scope=Scope.PROJECT)
        result = client.retrieve("telescope", user_id=user, scope="project", include_cross_scope=False)
        assert result.results and all(candidate.node.scope == Scope.PROJECT for candidate in result.results)
        with pytest.raises(ValueError, match="scope"):
            client.retrieve("telescope", user_id=user, scope=[])
