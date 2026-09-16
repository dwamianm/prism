"""Retrieval distinguishes no matches, backend outages, and incompatible vectors."""

from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.types import Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_empty_search_is_not_an_embedding_mismatch(config, user):
    async with MemoryEngine.open(config) as engine:
        response = await engine.retrieve("cobalt telescope", user_id=user)
        assert response.metadata.embedding_mismatch is False
        assert response.metadata.backend_failures == {}


async def test_embedding_outage_is_reported_without_exposing_provider_detail(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The cobalt telescope is blue", user_id=user)
        monkeypatch.setattr(engine._vector_index._provider, "embed", AsyncMock(side_effect=RuntimeError("private api-key detail")))
        response = await engine.retrieve("cobalt telescope", user_id=user)
        assert response.results
        assert response.metadata.embedding_mismatch is False
        assert response.metadata.backend_failures == {"VECTOR": "backend_error"}
        assert "private api-key" not in response.model_dump_json()


@pytest.mark.parametrize("field", ["model_name", "model_version"])
async def test_model_change_falls_back_to_lexical_until_reembedded(config, user, monkeypatch, field):
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.store("The cobalt telescope is blue", user_id=user)
        node = (await engine.get_event_nodes(event_id, user_id=user))[0]
        monkeypatch.setattr(engine._vector_index._provider, field, "incompatible-model")
        response = await engine.retrieve("cobalt telescope", user_id=user)
        assert str(node.id) in [str(result.node.id) for result in response.results]
        assert response.metadata.embedding_mismatch is True
        assert response.metadata.backend_failures == {"VECTOR": "embedding_mismatch"}
        assert response.metadata.candidates_generated["VECTOR"] == 0
        await engine._vector_index.delete_by_node_id(str(node.id))
        await engine._vector_index.index(str(node.id), node.content, user)
        response = await engine.retrieve("cobalt telescope", user_id=user)
        assert response.metadata.embedding_mismatch is False
        assert response.metadata.backend_failures == {}


async def test_other_owner_or_scope_model_does_not_disable_compatible_search(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        provider = engine._vector_index._provider
        with monkeypatch.context() as older:
            older.setattr(provider, "model_version", "old-version")
            await engine.store("The cobalt telescope is blue", user_id=user + "-other")
            await engine.store("The cobalt telescope is blue", user_id=user, scope=Scope.PROJECT)
        await engine.store("The cobalt telescope is blue", user_id=user)
        response = await engine.retrieve("cobalt telescope", user_id=user, scope=Scope.PERSONAL)
        assert response.metadata.candidates_generated["VECTOR"] == 1
        assert response.metadata.embedding_mismatch is False


async def test_legacy_postgres_vectors_require_known_model_metadata(config, user):
    if config.backend != "postgres":
        pytest.skip("Local vectors already carry model metadata")
    async with MemoryEngine.open(config) as engine:
        await engine.store("The cobalt telescope is blue", user_id=user)
        async with engine._pool.acquire() as conn:
            await conn.execute("UPDATE nodes SET embedding_model = NULL, embedding_version = NULL WHERE user_id = $1", user)
        response = await engine.retrieve("cobalt telescope", user_id=user)
        assert response.results and response.metadata.embedding_mismatch


async def test_lexical_outage_is_separate_from_embedding_status(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The cobalt telescope is blue", user_id=user)
        monkeypatch.setattr(engine._lexical_index, "search", AsyncMock(side_effect=OSError("private path")))
        response = await engine.retrieve("cobalt telescope", user_id=user)
        assert response.results
        assert response.metadata.embedding_mismatch is False
        assert response.metadata.backend_failures == {"LEXICAL": "backend_error"}
        assert "private path" not in response.model_dump_json()


async def test_reopen_with_different_model_preserves_lexical_access(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The cobalt telescope is blue", user_id=user)
    changed = test_durable_ingestion.MockEmbeddingProvider()
    changed.model_name = "another-model"
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: changed)
    async with MemoryEngine.open(config) as engine:
        response = await engine.retrieve("cobalt telescope", user_id=user)
        assert response.results and response.metadata.embedding_mismatch
        assert response.metadata.candidates_generated["VECTOR"] == 0


async def test_http_exposes_sanitized_degraded_status(config, user, monkeypatch):
    import httpx
    from prme.api.app import create_app
    from prme.config import APIConfig

    config.api = APIConfig(user_keys={user: "test-token"})
    async with MemoryEngine.open(config) as engine:
        await engine.store("The cobalt telescope is blue", user_id=user)
        monkeypatch.setattr(engine._vector_index._provider, "embed", AsyncMock(side_effect=RuntimeError("private detail")))
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/v1/retrieve", json={"query": "cobalt telescope"}, headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 200
        assert response.json()["results"]
        assert response.json()["metrics"]["backend_failures"] == {"VECTOR": "backend_error"}
        assert "private detail" not in response.text


async def test_mcp_exposes_sanitized_degraded_status(config, user, monkeypatch):
    import json
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme.config import MCPConfig
    from prme.mcp.server import create_mcp_server

    config.mcp = MCPConfig(user_id=user)
    server = create_mcp_server(config)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
        await session.initialize()
        result = await session.call_tool("memory_store", {"content": "The cobalt telescope is blue"})
        assert "error" not in json.loads(result.content[0].text)
        monkeypatch.setattr(test_durable_ingestion.MockEmbeddingProvider, "embed", AsyncMock(side_effect=RuntimeError("private detail")))
        result = await session.call_tool("memory_retrieve", {"query": "cobalt telescope"})
        data = json.loads(result.content[0].text)
        assert data["results"]
        assert data["metrics"]["backend_failures"] == {"VECTOR": "backend_error"}
        assert "private detail" not in result.content[0].text
