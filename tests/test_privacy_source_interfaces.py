"""AC01/AC04/AC08 public adapter calls over actual protected storage."""
import asyncio
from contextlib import asynccontextmanager
import json
from uuid import uuid4

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme.privacy import PrivacyDenied
from tests.privacy_source_support import (
    MEMORY, SOURCE, assert_no_source, opened, store_pair,
    backend_layout as backend_layout, isolated_privacy_postgres as isolated_privacy_postgres,
    local_analyzer_fixture as local_analyzer_fixture, native as native_fixture,  # noqa: F401
)


async def test_AC01_AC04_http_and_mcp_dispatch(tmp_path, layout, analyzer, native):
    from web.server import create_explorer_app
    from prme.config import APIConfig, MCPConfig
    from prme.mcp.server import create_mcp_server
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner, foreign = str(policy.owner_id), str(uuid4())
    try:
        event_id = await store_pair(engine, owner)
        node = (await engine.get_event_nodes(event_id, user_id=owner))[0]
        config = engine._config.model_copy(update={
            "api": APIConfig(user_keys={owner: "fixture-owner", foreign: "fixture-foreign"}),
            "mcp": MCPConfig(user_id=owner),
        })
        engine._config = config
        app = create_explorer_app(config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture", headers={
            "Authorization": "Bearer fixture-owner",
        }) as client:
            paths = [f"/v1/events/{event_id}", f"/v1/events/{event_id}/nodes",
                     f"/v1/events/{event_id}/extraction", f"/v1/nodes/{node.id}", f"/v1/nodes/{node.id}/provenance",
                     f"/v1/explorer/nodes/{node.id}/connections"]
            for path in paths:
                result = await client.get(path)
                assert result.status_code == 200, (path, result.text)
                assert_no_source(result.text)
            result = await client.post("/v1/retrieve", json={"query": MEMORY})
            assert result.status_code == 200 and MEMORY in result.text
            assert result.json()["metrics"]["source_limitation"]
            assert_no_source(result.text)
            assert (await client.post("/v1/retrieve", json={"query": MEMORY, "user_id": foreign})).status_code == 403
            foreign_result = await client.get(f"/v1/events/{event_id}", headers={"Authorization": "Bearer fixture-foreign"})
            missing_result = await client.get(f"/v1/events/{uuid4()}", headers={"Authorization": "Bearer fixture-foreign"})
            assert foreign_result.status_code == missing_result.status_code == 422
            assert foreign_result.json() == {"detail": "privacy_boundary_mismatch"}
            assert foreign_result.json() == missing_result.json()
        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}
        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
            await session.initialize()
            calls = [("memory_get_event", {"event_id": event_id}),
                     ("memory_get_extraction", {"event_id": event_id}),
                     ("memory_get_node", {"node_id": str(node.id)}),
                     ("memory_get_provenance", {"node_id": str(node.id)}),
                     ("memory_retrieve", {"query": MEMORY})]
            for name, arguments in calls:
                result = await session.call_tool(name, arguments)
                assert not result.isError
                value = json.loads(result.content[0].text)
                assert "error" not in value, (name, value)
                assert_no_source(value)
                if name == "memory_retrieve":
                    assert MEMORY in json.dumps(value) and value["metrics"]["source_limitation"]
    finally:
        await engine.close()


async def test_AC08_framework_retrieval_and_explicit_raw_history_denial(tmp_path, layout, analyzer, native):
    from prme.integrations.langchain import PRMERetriever as LangChainRetriever, PRMEChatMessageHistory
    from prme.integrations.llamaindex import PRMERetriever as LlamaRetriever, PRMEChatStore
    engine, runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner = str(policy.owner_id)
    session_id = str(uuid4())
    await store_pair(engine, owner, session_id=session_id)
    config = engine._config
    await engine.close()
    options = dict(config=config, privacy_runtime=runtime, embedding_provider=runtime.embedder)
    # Each adapter constructs a real MemoryClient and executes its real synchronous route.
    for factory in (LangChainRetriever, LlamaRetriever):
        adapter = await asyncio.to_thread(factory, user_id=owner, **options)
        try:
            documents = await asyncio.to_thread(adapter.invoke if factory is LangChainRetriever else adapter.retrieve, MEMORY)
            assert documents and MEMORY in str(documents)
            assert SOURCE not in str(documents)
            metadata = documents[0].metadata if factory is LangChainRetriever else documents[0].node.metadata
            assert metadata["source_limitation"] and metadata["source_availability"][0]["state"] == "sealed"
        finally:
            await asyncio.to_thread(adapter.close)
    history = await asyncio.to_thread(PRMEChatMessageHistory, user_id=owner, session_id=session_id, **options)
    try:
        for operation in (lambda: history.messages, history.clear, lambda: history.add_user_message(MEMORY)):
            with pytest.raises(PrivacyDenied, match="privacy_unsupported_input"):
                await asyncio.to_thread(operation)
        assert history._client._config.privacy_policy.profile == "sealed"
    finally:
        await asyncio.to_thread(history.close)
    chat = await asyncio.to_thread(PRMEChatStore, **options)
    try:
        for operation in (chat.get_messages, chat.delete_messages, lambda key: chat.delete_message(key, 0)):
            with pytest.raises(PrivacyDenied, match="privacy_unsupported_input"):
                await asyncio.to_thread(operation, f"{owner}:{session_id}")
        assert chat._client._config.privacy_policy.profile == "sealed"
    finally:
        await asyncio.to_thread(chat.close)
