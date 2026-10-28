"""Public adapter subchecks of the unchanged AP01/AP02/AP06/AP07 cases."""

import json
from uuid import uuid4

import pytest

from prme import MemoryClient
from prme.privacy import PrivacyDenied
from tests.privacy_fakes import config_for, make_runtime, open_engine, policy_for

SOURCE = "Alex does not like tea unless it is cold."


def test_reingest_sync_and_HTTP_reopened_refusal(tmp_path, monkeypatch, caplog, capsys):
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    from prme.api.app import create_app
    from tests.test_reingest import settings

    r = make_runtime()
    p = policy_for(r)
    config = settings(config_for(tmp_path))
    with MemoryClient(config=config, embedding_provider=r.embedder, privacy_runtime=r) as client:
        client.store(SOURCE, user_id=str(p.owner_id), policy=p)
    embed = AsyncMock(side_effect=AssertionError("unexpected provider effect"))
    monkeypatch.setattr(r.embedder, "embed", embed)
    # A protected store refuses to open without its privacy runtime (#325, #326), so
    # neither surface reaches reingest, a provider or the source.
    with pytest.raises(PrivacyDenied, match="privacy_capability_unavailable"):
        with MemoryClient(config=config, embedding_provider=r.embedder) as client:
            client.reingest(str(p.owner_id), dry_run=True, limit=0)
    with pytest.raises(PrivacyDenied, match="privacy_capability_unavailable"):
        with TestClient(create_app(config, embedding_provider=r.embedder)) as client:
            client.post("/v1/reingest", json={"user_id": str(p.owner_id), "dry_run": True, "limit": 0})
    # Reopened with the runtime but no configured policy, reingest itself refuses.
    with MemoryClient(config=config, embedding_provider=r.embedder, privacy_runtime=r) as client:
        with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
            client.reingest(str(p.owner_id), dry_run=True, limit=0)
    with TestClient(create_app(config, embedding_provider=r.embedder, privacy_runtime=r)) as client:
        result = client.post("/v1/reingest", json={"user_id": str(p.owner_id), "dry_run": True, "limit": 0})
        assert result.status_code == 422 and result.json()["detail"] == "privacy_provider_unavailable"
    embed.assert_not_awaited()
    captured = capsys.readouterr()
    assert SOURCE not in captured.out + captured.err + caplog.text


async def test_reingest_MCP_actual_dispatch_fixed_refusal(tmp_path, caplog, capsys):
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme import MemoryEngine
    from prme.config import MCPConfig
    from prme.mcp.server import create_mcp_server
    from tests.test_reingest import settings

    r = make_runtime()
    p = policy_for(r)
    config = settings(config_for(tmp_path), mcp=MCPConfig(user_id=str(p.owner_id)))
    engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
    await engine.store(SOURCE, user_id=str(p.owner_id), policy=p)
    await engine.close()
    before = len(r.embedder.sends)
    server = create_mcp_server(config, embedding_provider=r.embedder)
    # The server's lifespan opens the store, which refuses without the privacy
    # runtime (#325), before any tool call reaches reingest or a provider.
    with pytest.raises(BaseExceptionGroup) as refused:
        async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as client:
            await client.initialize()
            await client.call_tool("memory_reingest", {"dry_run": True, "limit": 0})
    assert refused.group_contains(PrivacyDenied, match="privacy_capability_unavailable")
    # With the runtime, the tool call itself returns the fixed refusal.
    server = create_mcp_server(config, embedding_provider=r.embedder, privacy_runtime=r)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as client:
        await client.initialize()
        response = await client.call_tool("memory_reingest", {"dry_run": True, "limit": 0})
        assert json.loads(response.content[0].text) == {"error": "privacy_provider_unavailable"}
    assert len(r.embedder.sends) == before and not r.extractor.sends
    captured = capsys.readouterr()
    assert SOURCE not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize("layout", ["directory", "pack"])
def test_reingest_CLI_real_open_refuse_close_keeps_pending_protected_work(tmp_path, monkeypatch, capsys, caplog, layout):
    import asyncio
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import structlog
    from prme import MemoryEngine, cli
    import prme.ingestion.extraction as extraction
    import prme.storage.engine as engine_module
    from tests.test_reingest import settings

    r = make_runtime()
    p = policy_for(r)
    config = settings(config_for(tmp_path, layout=layout))

    async def prepare():
        engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
        engine._pipeline.queue_extraction = lambda *args: None
        event_id = await engine.ingest(SOURCE, user_id=str(p.owner_id), policy=p)
        status = await engine._event_store.extraction_work.status(event_id, user_id=str(p.owner_id))
        await engine.close()
        return event_id, status

    event_id, before = asyncio.run(prepare())
    embed, extract = AsyncMock(), AsyncMock()
    monkeypatch.setattr(r.embedder, "embed", embed)
    monkeypatch.setattr(engine_module, "create_embedding_provider", lambda *args: r.embedder)
    monkeypatch.setattr(extraction, "create_extraction_provider", lambda *args: SimpleNamespace(
        provider_name="unused", model_name="unused", extract=extract, extract_batch=extract))
    monkeypatch.setenv("PRME_ENABLE_REINGEST", "true")
    path = config.pack_path or config.db_path
    monkeypatch.setattr(sys, "argv", ["prme", "reingest", path, "--user-id", str(p.owner_id), "--dry-run"])
    logging_config = structlog.get_config()
    try:
        with pytest.raises(SystemExit) as stopped:
            cli.main()
        assert stopped.value.code == 1
    finally:
        structlog.configure(**logging_config)
    output = capsys.readouterr()
    # The open refuses without the privacy runtime (#325), before reingest.
    assert "privacy_capability_unavailable" in output.err
    assert SOURCE not in output.out + output.err + caplog.text
    embed.assert_not_awaited()
    extract.assert_not_awaited()

    async def inspect():
        engine = await MemoryEngine.create(config, embedding_provider=r.embedder, privacy_runtime=r)
        try:
            # The CLI cannot supply a runtime, so the refused reingest is
            # repeated here with one; neither may change the pending work.
            with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
                await engine.reingest(str(p.owner_id), dry_run=True)
            status = await engine._event_store.extraction_work.status(event_id, user_id=str(p.owner_id))
            assert status == before
            assert await engine._graph_store.query_nodes(user_id=str(p.owner_id)) == []
            assert await engine._event_store.get_extraction(event_id, user_id=str(p.owner_id)) is None
        finally:
            await engine.close()
    asyncio.run(inspect())


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
