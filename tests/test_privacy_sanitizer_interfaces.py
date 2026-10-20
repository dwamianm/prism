"""Actual adapter dispatch and single-send provider guards; synthetic I/O only."""

from contextlib import asynccontextmanager
from dataclasses import replace
import json
import logging
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from prme import MemoryClient, MemoryEngine
from prme.ingestion.extraction import InstructorExtractionProvider
from prme.privacy import PrivacyDenied
from tests.privacy_fakes import config_for
from tests.privacy_fakes import isolated_privacy_postgres as isolated_privacy_postgres
from tests.privacy_sanitizer_support import admit, setup
from tests.privacy_sanitizer_support import local_analyzer_fixture as local_analyzer_fixture
from tests.test_privacy_sanitizer_scenarios import backend_layout as backend_layout


def test_SN01_SN11_sync_HTTP_and_history_boundaries(tmp_path, layout, analyzer, monkeypatch, caplog, capsys):
    from fastapi.testclient import TestClient
    from langchain_core.messages import HumanMessage
    from llama_index.core.llms import ChatMessage
    from prme.api.app import create_app
    from prme.integrations import langchain, llamaindex

    runtime, policy, _ = setup(analyzer)
    owner = str(policy.owner_id)
    marker = "password=synthetic-value"
    config = config_for(tmp_path, layout=layout, policy=policy)
    with MemoryClient(config=config, embedding_provider=runtime.embedder, privacy_runtime=runtime) as client:
        with pytest.raises(PrivacyDenied):
            client.store(marker, user_id=owner)
        assert client.get_events(owner) == []
        identifier = client.store("The password policy is documented.", user_id=owner)
        assert client.get_event_nodes(identifier, user_id=owner)
        # These history APIs are explicitly unsupported. Their refusal is
        # negative boundary evidence, not a benign sanitizer-success control.
        monkeypatch.setattr(langchain, "MemoryClient", lambda *args, **kwargs: client)
        history = langchain.PRMEChatMessageHistory(user_id=owner, session_id=str(uuid4()))
        before = len(client.get_events(owner))
        for text in (marker, "The password policy is documented."):
            with pytest.raises(PrivacyDenied, match="privacy_unsupported_input"):
                history.add_message(HumanMessage(content=text))
        monkeypatch.setattr(llamaindex, "MemoryClient", lambda *args, **kwargs: client)
        store = llamaindex.PRMEChatStore()
        for text in (marker, "The password policy is documented."):
            with pytest.raises(PrivacyDenied, match="privacy_unsupported_input"):
                store.add_message(owner + ":" + str(uuid4()), ChatMessage(role="user", content=text))
        assert len(client.get_events(owner)) == before
    app = create_app(config, embedding_provider=runtime.embedder, privacy_runtime=runtime)
    with TestClient(app) as client:
        denied = client.post("/v1/store", json={"content": marker, "user_id": owner})
        assert denied.status_code == 422 and marker not in denied.text
        positive = client.post("/v1/store", json={"content": "The password policy is documented.", "user_id": owner})
        assert positive.status_code == 200
        denied = client.post("/v1/store", json={"content": "The cup is on the table.", "user_id": owner,
                                               "metadata": {"password": "synthetic-value"}})
        assert denied.status_code == 422
    captured = capsys.readouterr()
    assert marker not in caplog.text + captured.out + captured.err


async def test_SN01_SN11_MCP_real_dispatch(tmp_path, layout, analyzer, caplog, capsys):
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme.mcp.server import create_mcp_server

    runtime, policy, _ = setup(analyzer)
    marker = "password=synthetic-value"
    server = create_mcp_server(config_for(tmp_path, layout=layout, policy=policy),
                               embedding_provider=runtime.embedder, privacy_runtime=runtime)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as client:
        await client.initialize()
        denied = await client.call_tool("memory_store", {"content": marker, "user_id": str(policy.owner_id)})
        assert "privacy_denied" in denied.model_dump_json() and marker not in denied.model_dump_json()
        positive = await client.call_tool("memory_store", {"content": "The password policy is documented.",
                                                          "user_id": str(policy.owner_id)})
        assert json.loads(positive.content[0].text)["node_id"]
    captured = capsys.readouterr()
    assert marker not in caplog.text + captured.out + captured.err


async def test_SN12_actual_provider_guard_no_sdk_retry_or_raw_logging(analyzer, monkeypatch, caplog):
    runtime, policy, saved = setup(analyzer)
    source = await admit(runtime.sanitizer, saved, "Email sample.person@example.com.")
    marker = "password=synthetic-provider-value"
    active = False
    sends = []
    status = 200

    @asynccontextmanager
    async def guard():
        nonlocal active
        assert not active
        active = True
        try:
            yield
        finally:
            active = False

    async def exchange(request):
        assert active
        assert b"sample.person@example.com" not in request.content
        sends.append(json.loads(request.content))
        logging.getLogger("openai._base_client").warning("private output %s", marker, exc_info=ValueError(marker))
        if status != 200:
            return httpx.Response(status, json={"error": {"message": marker, "type": "server_error"}})
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 0,
            "model": "synthetic", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps({"summary": marker})}}]})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(exchange))
    provider = InstructorExtractionProvider("openai/synthetic", api_key=SecretStr("synthetic-offline-key"),
                                             base_url="https://example.invalid/v1", max_retries=5).privacy_adapter()
    output = await provider.extract(source, saved, guard=guard)
    assert output == {"summary": marker} and len(sends) == 1 and not active
    with pytest.raises(PrivacyDenied):
        await admit(runtime.sanitizer, saved, "", mode="generated_output", source=source,
                    raw_fields=json.dumps(output))
    assert marker not in caplog.text
    status = 500
    with pytest.raises(PrivacyDenied, match="privacy_provider_unavailable"):
        await provider.extract(source, saved, guard=guard)
    assert len(sends) == 2 and not active and marker not in caplog.text
    assert sends[0]["response_format"] == {"type": "json_object"}


async def test_SN12_native_provider_accepted_and_denied_at_real_plan_boundary(tmp_path, analyzer, monkeypatch):
    from tests.test_privacy_sanitizer_scenarios import SOURCE

    runtime, policy, _ = setup(analyzer)
    output = {"entities": [], "facts": [{"subject": "John Smith", "predicate": "likes", "object": "tea",
               "evidence_quote": SOURCE, "polarity": "negative", "condition": "unless it is cold",
               "epistemic_type": "conditional"}], "relationships": [], "summary": None}

    async def exchange(request):
        assert runtime.authority._lock.locked()
        return httpx.Response(200, json={"id": "synthetic", "object": "chat.completion", "created": 0,
            "model": "synthetic", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(output)}}]})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(exchange))
    provider = InstructorExtractionProvider("openai/synthetic", api_key=SecretStr("synthetic-offline-key"),
                                             base_url="https://example.invalid/v1").privacy_adapter()
    runtime = replace(runtime, extractor=provider)
    engine = await MemoryEngine.create(config_for(tmp_path, policy=policy),
                                       embedding_provider=runtime.embedder, privacy_runtime=runtime)
    try:
        output["summary"] = "password=synthetic-output"
        with pytest.raises(PrivacyDenied):
            await engine.ingest(SOURCE, user_id=str(policy.owner_id), wait_for_extraction=True)
        assert not runtime.embedder.sends
        assert await engine._event_store.get_by_user(str(policy.owner_id)) == []
        output["summary"] = None
        identifier = await engine.ingest(SOURCE, user_id=str(policy.owner_id), wait_for_extraction=True)
        assert await engine.get_event_nodes(identifier, user_id=str(policy.owner_id))
    finally:
        await engine.close()
