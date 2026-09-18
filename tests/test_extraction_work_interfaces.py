"""Extraction status and processing preserve identity across package transports."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from prme import MemoryClient, MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig, MCPConfig
from prme.ingestion.errors import ExtractionError
from prme.ingestion.schema import ExtractionResult
from prme.mcp.server import create_http_app
from prme.models import Event
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def seed(engine, user):
    events = [Event(content="Hello", user_id=owner, role="user") for owner in (user, user + "-other")]
    for event in events:
        await engine._event_store.append(event, defer_extraction=True)
    return [str(event.id) for event in events]


async def save_failed_plan(engine, event_id, user):
    event = await engine.get_event(event_id, user_id=user)
    work = engine._event_store.extraction_work
    claim = await work.claim(user_id=user, event_id=event_id)
    plan = await engine._pipeline._prepare_plan(ExtractionResult(), event)
    await engine._event_store.record_derivation_plan(plan, claim=claim)
    await work.fail(claim, error="StaleDerivationPlanError")


async def test_http_extraction_controls_are_owned_and_validated(config, user):
    config.api = APIConfig(user_keys={user: "first-token", user + "-other": "second-token"})
    async with MemoryEngine.open(config) as engine:
        own, other = await seed(engine, user)
        provider = AsyncMock(return_value=ExtractionResult())
        engine._pipeline._extraction_provider.extract = provider
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                      headers={"Authorization": "Bearer first-token"}) as client:
            assert (await client.get(f"/v1/events/{own}/extraction-status")).json()["status"] == "pending"
            assert (await client.get(f"/v1/events/{other}/extraction-status")).status_code == 404
            assert (await client.post(f"/v1/events/{other}/retry-extraction")).status_code == 404
            assert (await client.post("/v1/extractions/process", json={"user_id": user + "-other"})).status_code == 403
            assert (await client.post("/v1/extractions/process", json={"budget_ms": 0})).json()["pending"] == 1
            assert provider.await_count == 0
            assert (await client.post(f"/v1/events/{own}/retry-extraction")).json()["status"] == "pending"
            await save_failed_plan(engine, own, user)
            assert (await client.post(f"/v1/events/{own}/retry-extraction?replan=true")).json()["plan_revision"] == 2
            result = await client.post("/v1/extractions/process", json={})
            assert result.status_code == 200 and result.json() == {"processed": 1, "pending": 0, "failed": 0}
            assert provider.await_count == 1
            assert (await client.get(f"/v1/events/{own}/extraction-status")).json()["phase"] == "complete"
            assert (await client.get("/v1/events/not-a-uuid/extraction-status")).status_code == 422
            assert (await client.post("/v1/extractions/process", json={"limit": -1})).status_code == 422
        assert (await engine.extraction_status(other, user_id=user + "-other")).status == "pending"


async def test_mcp_http_processing_uses_request_identity(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        own, other = await seed(engine, user)
        await save_failed_plan(engine, own, user)
    from tests.test_concurrency import MockExtractionProvider
    provider = MockExtractionProvider()
    provider.extract = AsyncMock(return_value=ExtractionResult())
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: provider)
    config.mcp = MCPConfig(user_keys={user: "first-token", user + "-other": "second-token"})
    app = create_http_app(config)
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8000", headers=headers) as client:
            async def call(name, arguments, token="first-token"):
                response = await client.post("/mcp", headers={"Authorization": "Bearer " + token}, json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments},
                })
                assert response.status_code == 200
                return json.loads(response.json()["result"]["content"][0]["text"])
            assert (await call("memory_extraction_status", {"event_id": own}))["status"] == "failed"
            assert (await call("memory_retry_extraction", {"event_id": own, "replan": True}))["plan_revision"] == 2
            assert "error" in await call("memory_retry_extraction", {"event_id": other})
            assert "error" in await call("memory_extraction_status", {"event_id": other})
            assert (await call("memory_process_extractions", {"budget_ms": 0}))["pending"] == 1
            assert provider.extract.await_count == 0
            assert (await call("memory_process_extractions", {}))["processed"] == 1
            assert (await call("memory_extraction_status", {"event_id": other}, "second-token"))["status"] == "pending"
            assert (await call("memory_extraction_status", {"event_id": own}))["status"] == "complete"
            assert provider.extract.await_count == 1


def test_sync_client_exposes_durable_retry_workflow(config, user):
    with MemoryClient(config=config) as client:
        pipeline = client._engine._pipeline
        pipeline._retry_delays = ()
        pipeline._extraction_provider.extract = AsyncMock(side_effect=RuntimeError("provider offline"))
        with pytest.raises(ExtractionError) as failure:
            client.ingest("Hello", user_id=user)
        event_id = failure.value.event_id
        assert client.extraction_status(event_id, user_id=user).status == "failed"
        assert client.retry_extraction(event_id, user_id=user).status == "pending"
        pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult())
        assert client.process_extractions(user_id=user).processed == 1
        assert client.extraction_status(event_id, user_id=user).status == "complete"


async def test_cli_extraction_processing_requires_scope_and_reports_completion(config, user, monkeypatch, capsys):
    from prme import cli
    async with MemoryEngine.open(config) as engine:
        own, other = await seed(engine, user)
        await save_failed_plan(engine, own, user)
    async def open_engine(path):
        assert path == config.db_path
        engine = await MemoryEngine.create(config)
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult())
        return engine
    monkeypatch.setattr(cli, "_create_engine", open_engine)
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["process-extractions", config.db_path])
    capsys.readouterr()
    args = parser.parse_args(["retry-extraction", config.db_path, own, "--user-id", user, "--replan", "--format", "json"])
    await args.func(args)
    assert '"plan_revision": 2' in capsys.readouterr().out
    args = parser.parse_args(["process-extractions", config.db_path, "--user-id", user, "--format", "json"])
    await args.func(args)
    output = capsys.readouterr().out
    assert '"processed": 1' in output and '"pending": 0' in output
    async with MemoryEngine.open(config) as engine:
        assert (await engine.extraction_status(own, user_id=user)).status == "complete"
        assert (await engine.extraction_status(other, user_id=user + "-other")).status == "pending"
