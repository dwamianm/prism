"""Historical sources retain their own clock through extraction and recovery."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryClient, MemoryEngine
from prme.ingestion.errors import ExtractionError
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.types import NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401
from tests.test_http_write_fidelity import app_for, client_for

SOURCE_TIME = datetime(2024, 3, 10, 1, 30, tzinfo=timezone(timedelta(hours=-6)))
SOURCE = "Alice started using Rust yesterday."


def extraction():
    return ExtractionResult(
        entities=[ExtractedEntity(name="Alice", entity_type="person")],
        facts=[ExtractedFact(subject="Alice", predicate="uses", object="Rust",
                             evidence_quote=SOURCE, temporal_ref="yesterday")],
    )


async def test_historical_clock_survives_failed_extraction_and_restart(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._retry_delays = ()
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract",
                            AsyncMock(side_effect=TimeoutError("offline")))
        with pytest.raises(ExtractionError) as failure:
            await engine.ingest(SOURCE, user_id=user, event_time=SOURCE_TIME,
                                session_id="archive", metadata={"import": "history"},
                                scope=Scope.PROJECT, wait_for_extraction=True)
        eid = failure.value.event_id
        original = await engine.get_event(eid, user_id=user)
        assert original.event_time == SOURCE_TIME and original.timestamp > SOURCE_TIME
    async with MemoryEngine.open(config) as engine:
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", AsyncMock(return_value=extraction()))
        await engine.retry_extraction(eid, user_id=user)
        assert (await engine.process_extractions(user_id=user, budget_ms=5000)).processed == 1
        await engine.process_pending(user_id=user, budget_ms=5000)
        event = await engine.get_event(eid, user_id=user)
        assert event == original
        nodes = await engine.get_event_nodes(eid, user_id=user)
        fact = next(n for n in nodes if n.node_type == NodeType.FACT)
        raw = next(n for n in nodes if n.node_type == NodeType.NOTE)
        assert fact.event_time == SOURCE_TIME - timedelta(days=1)
        assert raw.event_time == SOURCE_TIME
        assert fact.created_at > SOURCE_TIME and raw.created_at == event.created_at
        assert fact.session_id == raw.session_id == "archive"
        assert fact.scope == raw.scope == Scope.PROJECT
        assert await engine.get_event(eid, user_id=user + "-other") is None


@pytest.mark.parametrize("pipeline_enabled", [True, False])
async def test_batch_uses_each_source_clock_and_keeps_omitted_time(config, user, monkeypatch, pipeline_enabled):
    async with MemoryEngine.open(config) as engine:
        if pipeline_enabled:
            monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", AsyncMock(return_value=extraction()))
        else:
            monkeypatch.setattr(engine, "_pipeline", None)
        ids = await engine.ingest_batch([
            {"content": SOURCE, "role": "user", "event_time": SOURCE_TIME},
            {"content": SOURCE, "role": "user"},
        ], user_id=user, wait_for_extraction=True)
        first, second = [await engine.get_event(eid, user_id=user) for eid in ids]
        assert first.event_time == SOURCE_TIME and second.event_time is None
        if pipeline_enabled:
            facts = [next(n for n in await engine.get_event_nodes(eid, user_id=user)
                          if n.node_type == NodeType.FACT) for eid in ids]
            assert facts[0].event_time == SOURCE_TIME - timedelta(days=1)
            assert facts[1].event_time == second.timestamp - timedelta(days=1)


@pytest.mark.parametrize("pipeline_enabled", [True, False])
async def test_naive_source_clock_rejected_before_admission(config, user, monkeypatch, pipeline_enabled):
    async with MemoryEngine.open(config) as engine:
        if not pipeline_enabled:
            monkeypatch.setattr(engine, "_pipeline", None)
        with pytest.raises(ValueError, match="timezone"):
            await engine.ingest(SOURCE, user_id=user, event_time=datetime(2024, 3, 10))
        assert await engine.get_events(user) == []


async def test_http_historical_ingestion_and_validation(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", AsyncMock(return_value=extraction()))
        async with client_for(app_for(config, engine, user)) as client:
            body = {"content": SOURCE, "event_time": SOURCE_TIME.isoformat(), "wait_for_extraction": True}
            response = await client.post("/v1/ingest", json=body)
            assert response.status_code == 200
            eid = response.json()["event_id"]
            event = (await client.get(f"/v1/events/{eid}")).json()
            assert datetime.fromisoformat(event["event_time"]) == SOURCE_TIME
            nodes = await engine.get_event_nodes(eid, user_id=user)
            assert next(n for n in nodes if n.node_type == NodeType.FACT).event_time == SOURCE_TIME - timedelta(days=1)
            response = await client.post("/v1/ingest", json={**body, "event_time": "2024-03-10T01:30:00"})
            assert response.status_code == 422
            assert len(await engine.get_events(user)) == 1


def test_sync_client_preserves_historical_source_and_metadata(config, user, monkeypatch):
    with MemoryClient(config=config) as client:
        monkeypatch.setattr(client._engine._pipeline._extraction_provider, "extract", AsyncMock(return_value=extraction()))
        eid = client.ingest(SOURCE, user_id=user, event_time=SOURCE_TIME, metadata={"import": "history"})
        event = client.get_event(eid, user_id=user)
        assert event.event_time == SOURCE_TIME and event.metadata == {"import": "history"}
        assert next(n for n in client.get_event_nodes(eid, user_id=user)
                    if n.node_type == NodeType.FACT).event_time == SOURCE_TIME - timedelta(days=1)


@pytest.mark.parametrize("source_year,old_state", [(2024, "tentative"), (2026, "superseded")])
async def test_import_order_does_not_override_effective_replacement_time(config, user, monkeypatch, source_year, old_state):
    async with MemoryEngine.open(config) as engine:
        initial = "Alice uses Rust."
        result = extraction()
        result.facts[0].temporal_ref = None
        result.facts[0].evidence_quote = initial
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", AsyncMock(return_value=result))
        await engine.ingest(initial, user_id=user, event_time=datetime(2025, 1, 1, tzinfo=timezone.utc), wait_for_extraction=True)
        imported = "Alice switched from Rust to Python yesterday."
        result.facts[0].evidence_quote = imported
        result.facts[0].object = "Python"
        result.facts[0].temporal_ref = "yesterday"
        result.facts[0].temporal_intent = "update"
        result.facts[0].replaces_object = "Rust"
        await engine.ingest(imported, user_id=user, event_time=datetime(source_year, 3, 10, tzinfo=timezone.utc), wait_for_extraction=True)
        from prme.types import LifecycleState
        facts = await engine.query_nodes(user_id=user, node_type=NodeType.FACT, lifecycle_states=list(LifecycleState))
        by_value = {node.metadata["object"]: node for node in facts}
        assert by_value["Rust"].lifecycle_state.value == old_state
        assert by_value["Python"].event_time == datetime(source_year, 3, 9, tzinfo=timezone.utc)
        assert by_value["Rust"].event_time == datetime(2025, 1, 1, tzinfo=timezone.utc)


async def test_mcp_historical_ingest_validates_before_admission(config, user, monkeypatch):
    import asyncio
    import json
    from contextlib import asynccontextmanager
    from mcp.shared.memory import create_connected_server_and_client_session
    from prme.config import MCPConfig
    from prme.mcp.server import create_mcp_server

    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", AsyncMock(return_value=extraction()))

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
            await session.initialize()
            arguments = {"content": SOURCE, "event_time": SOURCE_TIME.isoformat(),
                         "session_id": "archive", "metadata": {"import": "history"}}
            invalid = await session.call_tool("memory_ingest", {**arguments, "event_time": "2024-03-10T01:30:00"})
            assert invalid.isError
            assert await engine.get_events(user) == []
            accepted = await session.call_tool("memory_ingest", arguments)
            assert not accepted.isError
            eid = json.loads(accepted.content[0].text)["event_id"]
            await asyncio.gather(*engine._pipeline._background_tasks)
            event = await engine.get_event(eid, user_id=user)
            assert event.event_time == SOURCE_TIME
            assert event.session_id == "archive" and event.metadata == {"import": "history"}
            assert next(n for n in await engine.get_event_nodes(eid, user_id=user)
                        if n.node_type == NodeType.FACT).event_time == SOURCE_TIME - timedelta(days=1)
