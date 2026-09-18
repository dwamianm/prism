"""Validated inference is immutable, source-bound, and reusable after failures."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.ingestion.errors import ExtractionError
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.models import Event
from prme.models.extraction import ExtractionRecord
from prme.types import NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


def extraction():
    return ExtractionResult(
        entities=[ExtractedEntity(name="Alice", entity_type="person")],
        facts=[ExtractedFact(subject="Alice", predicate="uses", object="Python", evidence_quote="Alice uses Python.")],
    )


async def test_indexing_retry_reuses_saved_extraction_and_restart_can_read_it(config, user, monkeypatch):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        pipeline = engine._pipeline
        pipeline._retry_delays = (0,)
        provider = AsyncMock(return_value=extraction())
        pipeline._extraction_provider.extract = provider
        actual_commit = engine._graph_store.commit_derivation
        attempts = 0
        async def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected indexing fault")
            return await actual_commit(*args, **kwargs)
        monkeypatch.setattr(engine._graph_store, "commit_derivation", fail_once)
        with pytest.raises(ExtractionError) as failure:
            await engine.ingest("Alice uses Python.", user_id=user, wait_for_extraction=True)
        event_id = failure.value.event_id
        async def wait_for_retry():
            while pipeline._retry_tasks:
                await asyncio.gather(*list(pipeline._retry_tasks.values()))
                await asyncio.sleep(0)
        await asyncio.wait_for(wait_for_retry(), timeout=10)
        assert provider.await_count == 1
        assert len(await engine.query_nodes(user_id=user, node_type=NodeType.FACT)) == 1
        saved = await engine._event_store.get_extraction(event_id, user_id=user)
        assert saved.grounding_policy == "speech_act_v11"
        assert saved.result["facts"][0]["object"] == "Python"
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        assert plan.materialization_policy == "speech_act_v12"
        assert await engine._event_store.get_extraction(event_id, user_id=user + "-other") is None
        assert (await engine.processing_status(event_id, user_id=user)).status == "pending"
    async with MemoryEngine.open(config) as engine:
        source = await engine.get_event(event_id, user_id=user)
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=AssertionError("must use durable output"))
        cached = await engine._pipeline._extract_or_load(source)
        assert cached.facts[0].object == "Python"
        assert (await engine._event_store.get_extraction(event_id, user_id=user)).model_dump() == saved.model_dump()
        assert len(await engine.query_nodes(user_id=user, node_type=NodeType.FACT)) == 1


@pytest.mark.parametrize(
    ("grounding_policy", "materialization_policy"),
    [
        ("source_passage_v1", "temporal_validity_v7"),
        ("speech_act_v2", "speech_act_v8"),
        ("speech_act_v3", "speech_act_v9"),
        ("speech_act_v4", "speech_act_v10"),
        ("speech_act_v5", "speech_act_v11"),
        ("speech_act_v6", "speech_act_v12"),
        ("speech_act_v7", "speech_act_v12"),
        ("speech_act_v8", "speech_act_v12"),
        ("speech_act_v9", "speech_act_v12"),
        ("speech_act_v10", "speech_act_v12"),
        ("speech_act_v11", "speech_act_v12"),
    ],
)
async def test_legacy_extraction_recovery_keeps_legacy_materialization_policy(
    config, user, grounding_policy, materialization_policy  # noqa: F811
):
    async with MemoryEngine.open(config) as engine:
        event = Event(
            content="Alice uses Python.",
            user_id=user,
            role="user",
            scope=Scope.PROJECT,
        )
        await engine._event_store.append(event)
        legacy = ExtractionRecord(
            event_id=event.id,
            user_id=user,
            scope=event.scope,
            content_hash=event.content_hash,
            provider="legacy-test",
            model="legacy-test",
            grounding_policy=grounding_policy,
            result=extraction().model_dump(mode="json"),
        )
        await engine._event_store.record_extraction(legacy)
        assert legacy.grounding_policy == grounding_policy

    async with MemoryEngine.open(config) as engine:
        source = await engine.get_event(str(event.id), user_id=user)
        saved = await engine._event_store.get_extraction(str(event.id), user_id=user)
        await engine._pipeline._materialize(
            ExtractionResult.model_validate(saved.result),
            source,
            str(event.id),
            event.scope,
        )
        plan = await engine._event_store.get_derivation_plan(
            str(event.id), user_id=user
        )
        assert plan.materialization_policy == materialization_policy


async def test_journal_failure_prevents_graph_materialization(config, user, monkeypatch):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        pipeline = engine._pipeline
        pipeline._retry_delays = ()
        pipeline._extraction_provider.extract = AsyncMock(return_value=extraction())
        materialize = AsyncMock()
        monkeypatch.setattr(pipeline, "_materialize", materialize)
        monkeypatch.setattr(engine._event_store, "record_extraction", AsyncMock(side_effect=OSError("injected journal failure")))
        with pytest.raises(ExtractionError) as failure:
            await engine.ingest("Alice uses Python.", user_id=user, wait_for_extraction=True)
        assert materialize.await_count == 0
        assert await engine.get_event(failure.value.event_id, user_id=user) is not None
        assert await engine._event_store.get_extraction(failure.value.event_id, user_id=user) is None


async def test_concurrent_records_preserve_first_committed_output(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Alice uses Python.", user_id=user, role="user", scope=Scope.PROJECT)
        await engine._event_store.append(event)
        records = [ExtractionRecord(
            event_id=event.id, user_id=user, scope=event.scope, content_hash=event.content_hash,
            provider="test", model=f"model-{i}", result={"facts": [], "summary": f"candidate-{i}"},
        ) for i in range(8)]
        outputs = await asyncio.gather(*[engine._event_store.record_extraction(r) for r in records])
        assert all(output.model_dump() == outputs[0].model_dump() for output in outputs)
        original = outputs[0].model_dump()
        # Returned dictionaries are detached from the durable operation.
        outputs[0].result["summary"] = "caller mutation"
        assert (await engine._event_store.get_extraction(str(event.id), user_id=user)).model_dump() == original
        if engine._conn is not None:
            count = engine._conn.execute("SELECT count(*) FROM operations WHERE id = ?", [records[0].operation_id]).fetchone()[0]
        else:
            async with engine._pool.acquire() as conn:
                count = await conn.fetchval("SELECT count(*) FROM operations WHERE id = $1", records[0].operation_id)
        assert count == 1


@pytest.mark.parametrize("mismatch", ["owner", "scope", "hash", "missing_source"])
async def test_record_requires_exact_immutable_source(config, user, mismatch):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Alice uses Python.", user_id=user, role="user")
        await engine._event_store.append(event)
        record = ExtractionRecord(
            event_id=event.id, user_id=user, scope=event.scope, content_hash=event.content_hash,
            provider="test", model="test", result=extraction().model_dump(mode="json"),
        )
        changes = {"owner": {"user_id": user + "-other"}, "scope": {"scope": Scope.PROJECT},
                   "hash": {"content_hash": "0" * 64}, "missing_source": {"event_id": uuid4()}}[mismatch]
        with pytest.raises(ValueError, match="source"):
            await engine._event_store.record_extraction(record.model_copy(update=changes))
        assert await engine._event_store.get_extraction(str(event.id), user_id=user) is None


async def test_empty_extraction_is_saved_and_reused(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        provider = AsyncMock(return_value=ExtractionResult())
        engine._pipeline._extraction_provider.extract = provider
        event_id = await engine.ingest("Hello.", user_id=user, wait_for_extraction=True)
        event = await engine.get_event(event_id, user_id=user)
        assert (await engine._pipeline._extract_or_load(event)).facts == []
        assert provider.await_count == 1
        assert await engine._event_store.get_extraction(event_id, user_id=user) is not None


async def test_public_extraction_reads_are_bound_to_source_owner(config, user):  # noqa: F811
    import json
    from types import SimpleNamespace
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    pytest.importorskip("mcp")
    from prme.api.app import create_app
    from prme.config import APIConfig, MCPConfig
    from prme.mcp.server import memory_get_extraction
    config.api = APIConfig(user_keys={user: "owner-token", user + "-other": "other-token"})
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=extraction())
        owned = await engine.ingest("Alice uses Python.", user_id=user, wait_for_extraction=True)
        foreign = await engine.ingest("Alice uses Python.", user_id=user + "-other", wait_for_extraction=True)
        absent = await engine.ingest_fast("Unprocessed raw source", user_id=user)
        saved = await engine.get_extraction(owned, user_id=user)
        assert saved is not None
        assert await engine.get_extraction(foreign, user_id=user) is None
        with pytest.raises(ValueError, match="user_id"):
            await engine.get_extraction(owned, user_id="")
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                     headers={"Authorization": "Bearer owner-token"}) as client:
            response = await client.get(f"/v1/events/{owned}/extraction")
            assert response.status_code == 200 and response.json() == saved.model_dump(mode="json")
            for hidden in (owned, foreign, absent, str(uuid4())):
                headers = {"Authorization": "Bearer other-token"} if hidden == owned else {}
                assert (await client.get(f"/v1/events/{hidden}/extraction", headers=headers)).status_code == 404
            assert (await client.get("/v1/events/invalid/extraction")).status_code == 422
        ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"engine": engine}))
        assert json.loads(await memory_get_extraction(owned, ctx)) == saved.model_dump(mode="json")
        assert json.loads(await memory_get_extraction(foreign, ctx)) == {"error": "Extraction not found"}
        assert "error" in json.loads(await memory_get_extraction("invalid", ctx))


def test_sync_client_exposes_saved_extraction(tmp_path, monkeypatch):
    from prme import MemoryClient
    from tests.test_durable_ingestion import MockEmbeddingProvider
    from prme.ingestion.extraction import InstructorExtractionProvider
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    monkeypatch.setattr(InstructorExtractionProvider, "extract", AsyncMock(return_value=extraction()))
    with MemoryClient(str(tmp_path)) as client:
        event_id = client.ingest("Alice uses Python.", user_id="alice")
        saved = client.get_extraction(event_id, user_id="alice")
        assert isinstance(saved, ExtractionRecord)
        assert saved.result["facts"][0]["object"] == "Python"
        assert client.get_extraction(event_id, user_id="bob") is None


async def test_extraction_record_survives_abrupt_exit_before_graph_writes(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import duckdb
    from prme.storage.event_store import EventStore
    script = tmp_path / "journal_writer.py"
    script.write_text('''
import asyncio, os, sys
import duckdb
from prme.models import Event, ExtractionRecord
from prme.storage.event_store import EventStore
from prme.storage.schema import initialize_database
async def main():
    conn = duckdb.connect(sys.argv[1])
    initialize_database(conn)
    store = EventStore(conn)
    event = Event(id="11111111-1111-1111-1111-111111111111", content="Hello.", user_id="alice", role="user")
    await store.append(event, defer_materialization=True)
    await store.record_extraction(ExtractionRecord(
        event_id=event.id, user_id=event.user_id, scope=event.scope,
        content_hash=event.content_hash, provider="test", model="test", result={"facts": []},
    ))
    os._exit(42)
asyncio.run(main())
''')
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = await asyncio.to_thread(
        subprocess.run, [sys.executable, str(script), str(tmp_path / "journal.duckdb")],
        env=env, capture_output=True, timeout=30,
    )
    assert completed.returncode == 42, completed.stderr.decode()
    conn = duckdb.connect(str(tmp_path / "journal.duckdb"))
    try:
        store = EventStore(conn)
        record = await store.get_extraction("11111111-1111-1111-1111-111111111111", user_id="alice")
        assert record.result == {"facts": []}
        assert await store.materialization_count() == 1
        assert conn.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0
    finally:
        conn.close()
