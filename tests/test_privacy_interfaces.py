"""Public adapter subchecks of the unchanged AP01/AP02/AP06/AP07 cases."""

import json
from uuid import uuid4

import pytest

from prme import MemoryClient
from prme.privacy import PrivacyDenied
from tests.privacy_fakes import config_for, make_runtime, open_engine, policy_for

SOURCE = "Alex does not like tea unless it is cold."


def test_AP01_sync_python_retains_sanitized_memory_with_policy(tmp_path):
    r = make_runtime()
    p = policy_for(r, durable_processing_input=False)
    r.sanitizer.replace_text = ("private@example.test", "[EMAIL_REMOVED]")
    with MemoryClient(config=config_for(tmp_path, policy=p), embedding_provider=r.embedder, privacy_runtime=r) as client:
        receipt = client.store_with_receipt("Email private@example.test", user_id=str(p.owner_id))
        assert receipt.node.content == "Email [EMAIL_REMOVED]"
        event_id = client.ingest(SOURCE, user_id=str(p.owner_id))
        assert client.get_event_nodes(event_id, user_id=str(p.owner_id))
        assert client.inspect_privacy_policy(user_id=str(p.owner_id))["unlimited_memory"]
        with pytest.raises(PrivacyDenied, match="async_forbidden"):
            client.ingest_fast(SOURCE, user_id=str(p.owner_id))


def test_AP01_AP02_HTTP_real_lifespan_and_content_free_errors(tmp_path):
    from fastapi.testclient import TestClient
    from prme.api.app import create_app
    r = make_runtime()
    p = policy_for(r, durable_processing_input=False)
    app = create_app(config_for(tmp_path, policy=p), privacy_runtime=r, embedding_provider=r.embedder)
    with TestClient(app) as client:
        body = {"content": SOURCE, "user_id": str(p.owner_id)}
        response = client.post("/v1/store", json=body)
        assert response.status_code == 200, response.text
        response = client.post("/v1/ingest", json={**body, "wait_for_extraction": True})
        assert response.status_code == 200, response.text
        response = client.post("/v1/ingest", json=body)
        assert response.status_code == 422 and response.json()["detail"] == "privacy_async_forbidden"
        bad = {**p.model_dump(mode="json"), "language": "private-invalid-language"}
        response = client.post("/v1/store", json={**body, "policy": bad})
        assert response.status_code == 422
        assert "private-invalid" not in response.text and SOURCE not in response.text
        response = client.post("/v1/privacy/policy", json={"user_id": str(p.owner_id)})
        assert response.status_code == 200 and response.json()["protected_product_ready"] is False


async def test_AP01_AP02_MCP_public_tool_dispatch(tmp_path):
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme.mcp.server import create_mcp_server
    r = make_runtime()
    p = policy_for(r)
    server = create_mcp_server(config_for(tmp_path, policy=p), privacy_runtime=r, embedding_provider=r.embedder)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as client:
        await client.initialize()
        tools = await client.list_tools()
        assert "memory_inspect_privacy_policy" in {tool.name for tool in tools.tools}
        response = await client.call_tool("memory_store", {"content": SOURCE, "user_id": str(p.owner_id)})
        body = json.loads(response.content[0].text)
        assert body.get("node_id"), body
        response = await client.call_tool("memory_ingest", {"content": SOURCE, "user_id": str(p.owner_id), "wait_for_extraction": True})
        assert "event_id" in json.loads(response.content[0].text)
        r.authority.denied.add("admission")
        before = len(r.embedder.sends)
        response = await client.call_tool("memory_store", {"content": "must not escape", "user_id": str(p.owner_id)})
        assert "privacy_stale_policy" in response.content[0].text
        assert len(r.embedder.sends) == before and "must not escape" not in response.content[0].text


async def test_MCP_environment_configuration_is_resolved_per_lifespan(tmp_path, monkeypatch):
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme.mcp.server import create_mcp_server
    r = make_runtime()
    server = create_mcp_server(privacy_runtime=r, embedding_provider=r.embedder)
    owner = str(uuid4())
    for index in range(2):
        path = tmp_path / str(index)
        path.mkdir()
        monkeypatch.setenv("PRME_DB_PATH", str(path / "memory.duckdb"))
        monkeypatch.setenv("PRME_VECTOR_PATH", str(path / "vectors.usearch"))
        monkeypatch.setenv("PRME_LEXICAL_PATH", str(path / "lexical"))
        monkeypatch.setenv("PRME_PACKING__CONTEXT_FORMAT", "auditable" if index == 0 else "reader")
        monkeypatch.setenv("PRME_PACKING__CONTEXT_CITATIONS", "false" if index == 0 else "true")
        async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as client:
            await client.initialize()
            response = await client.call_tool("memory_retrieve", {"query": "telescope", "user_id": owner, "include_context": True})
            assert json.loads(response.content[0].text)["count"] == 0
            response = await client.call_tool("memory_store", {"content": "The telescope is blue.", "user_id": owner})
            assert json.loads(response.content[0].text)["node_id"]
            response = await client.call_tool("memory_retrieve", {"query": "telescope", "user_id": owner, "include_context": True})
            if index:
                assert json.loads(response.content[0].text)["context_references"]


async def test_MCP_dispatch_uses_actual_lifespan_policy(tmp_path, caplog, capsys):
    from contextlib import asynccontextmanager
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme.mcp.server import create_mcp_server
    engine, r, p = await open_engine(tmp_path)

    @asynccontextmanager
    async def lifespan(server):
        yield {"engine": engine}

    server = create_mcp_server(lifespan=lifespan)
    sentinel = "private-lifespan-validation-sentinel"
    try:
        async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as client:
            await client.initialize()
            response = await client.call_tool("memory_store", {"content": {"private": sentinel}, "user_id": str(p.owner_id)})
            assert response.isError and "privacy_unsupported_input" in response.model_dump_json()
            assert sentinel not in response.model_dump_json()
            assert not r.sanitizer.calls and not r.embedder.sends
    finally:
        await engine.close()
    captured = capsys.readouterr()
    assert sentinel not in caplog.text + captured.out + captured.err


@pytest.mark.parametrize("default", [True, False])
@pytest.mark.parametrize("tool", ["memory_store", "memory_ingest_fast_many"])
async def test_MCP_protected_validation_never_echoes_input(tmp_path, caplog, capsys, default, tool):
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme.mcp.server import create_mcp_server
    r = make_runtime()
    p = policy_for(r)
    server = create_mcp_server(config_for(tmp_path, policy=p if default else None),
                               privacy_runtime=r, embedding_provider=r.embedder)
    sentinel = "private-invalid-mcp-sentinel"
    arguments = {"user_id": str(p.owner_id)}
    arguments.update({"content": {"private": sentinel}} if tool == "memory_store"
                     else {"items": [{"content": {"private": sentinel}}]})
    if not default:
        arguments["policy"] = p.model_dump(mode="json")
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as client:
        await client.initialize()
        response = await client.call_tool(tool, arguments)
        text = response.model_dump_json()
        assert response.isError and "privacy_unsupported_input" in text
        assert sentinel not in text
        assert not r.sanitizer.calls and not r.embedder.sends and not r.extractor.sends
    engine, _, _ = await open_engine(tmp_path, runtime=r, policy=p)
    try:
        assert await engine._event_store.get_by_user(str(p.owner_id)) == []
    finally:
        await engine.close()
    captured = capsys.readouterr()
    assert sentinel not in caplog.text + captured.out + captured.err


async def test_AP01_batch_supported_import_is_public_fast_batch(tmp_path):
    # The engine has no separate general import API; importers use this supported
    # atomic adapter. Export/migration format protection belongs to PV-08.
    engine, r, p = await open_engine(tmp_path)
    try:
        ids = await engine.ingest_fast_many([{"content": SOURCE}, {"content": "Second admitted note"}],
                                           user_id=str(p.owner_id), request_id=uuid4())
        result = await engine.process_pending(user_id=str(p.owner_id))
        assert result.processed == 2
        for event_id in ids:
            assert await engine.get_event_nodes(event_id, user_id=str(p.owner_id))
            record = await engine._privacy.repository.get(event_id, user_id=str(p.owner_id))
            assert record.evidence is None and record.processing is None
    finally:
        await engine.close()


def test_AP01_framework_lossless_history_explicitly_refuses_protected_profile(tmp_path, monkeypatch):
    from prme.integrations import langchain as lc
    from prme.integrations import llamaindex as li
    from langchain_core.messages import HumanMessage
    from llama_index.core.base.llms.types import ChatMessage
    r = make_runtime()
    p = policy_for(r)
    with MemoryClient(config=config_for(tmp_path, policy=p), embedding_provider=r.embedder, privacy_runtime=r) as client:
        # Inject an existing real client, not fake policy/storage/adapter behavior.
        monkeypatch.setattr(lc, "MemoryClient", lambda *a, **k: client)
        monkeypatch.setattr(li, "MemoryClient", lambda *a, **k: client)
        history = lc.PRMEChatMessageHistory(user_id=str(p.owner_id), session_id=str(uuid4()))
        store = li.PRMEChatStore()
        with pytest.raises(PrivacyDenied, match="unsupported"):
            history.add_message(HumanMessage(content=SOURCE))
        with pytest.raises(PrivacyDenied, match="unsupported"):
            store.add_message(str(p.owner_id) + ":" + str(uuid4()), ChatMessage(role="user", content=SOURCE))
        assert client.get_events(str(p.owner_id)) == []


async def test_AP03_namespace_factory_binds_actual_project_id(tmp_path):
    from prme import MemoryWorkspace
    from dataclasses import replace
    r = make_runtime()
    async with MemoryWorkspace.open(tmp_path, config=config_for(tmp_path / "unused"), embedding_provider=r.embedder,
                                    privacy_runtime_factory=lambda identity: replace(r, project_id=identity)) as workspace:
        async with workspace.namespace("one") as memory:
            p = policy_for(replace(r, project_id=memory.namespace.id))
            event_id = await memory.store(SOURCE, user_id=str(p.owner_id), policy=p)
            assert await memory.get_event_nodes(event_id, user_id=str(p.owner_id))
        async with workspace.namespace("two") as memory:
            with pytest.raises(PrivacyDenied, match="boundary"):
                await memory.store(SOURCE, user_id=str(p.owner_id), policy=p)
