"""store() queues the background extraction ingest() runs (#260)."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from prme import MemoryClient, MemoryEngine
from prme.api.app import create_app
from prme.client import config_from_directory
from prme.config import APIConfig
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.types import NodeType
from tests import test_durable_ingestion
from tests.test_batched_extraction import ScriptedBatches, drain

config = test_durable_ingestion.config
user = test_durable_ingestion.user

SENTENCE = "Alex and Jordan plan a trip to Japan in April 2027."
FOUND = ExtractionResult(
    entities=[
        ExtractedEntity(name="Alex", entity_type="person"),
        ExtractedEntity(name="Jordan", entity_type="person"),
        ExtractedEntity(name="Japan", entity_type="location"),
    ],
    facts=[ExtractedFact(subject="Alex", predicate="plans_trip_to", object="Japan",
                         evidence_quote=SENTENCE)],
)


def script(engine, result=FOUND, *, gate=None):
    """Make the engine's model return ``result`` once ``gate`` (an Event) is set."""
    calls = []

    async def extract(content, *, role="user"):
        calls.append(content)
        if gate is not None:
            await gate.wait()
        return result

    engine._pipeline._extraction_provider.extract = extract
    return calls


async def names(engine, user, node_type):
    return sorted(node.content.split(":")[0] for node in await engine.query_nodes(user_id=user, node_type=node_type))


async def test_extract_returns_at_once_and_adds_nodes_in_the_background(config, user):
    gate = asyncio.Event()
    async with MemoryEngine.open(config) as engine:
        calls = script(engine, gate=gate)
        receipt = await engine.store_with_receipt(SENTENCE, user_id=user, extract=True)
        # The reply does not wait for the model: only the stored node exists.
        assert receipt.node.content == SENTENCE and receipt.processing_status.status == "complete"
        assert await engine.count_nodes(user_id=user) == 1
        assert (await engine.extraction_status(str(receipt.event_id), user_id=user)).status in {"pending", "running"}

        gate.set()
        await drain(engine._pipeline)
        assert calls == [SENTENCE]
        assert (await engine.extraction_status(str(receipt.event_id), user_id=user)).status == "complete"
        assert await names(engine, user, NodeType.ENTITY) == ["Alex", "Japan", "Jordan"]
        assert len(await engine.query_nodes(user_id=user, node_type=NodeType.FACT)) == 1
        # The stored node is the same node, and it is still the only note.
        notes = await engine.query_nodes(user_id=user, node_type=NodeType.NOTE)
        assert [(note.id, note.content) for note in notes] == [(receipt.node.id, SENTENCE)]
        event_nodes = await engine.get_event_nodes(str(receipt.event_id), user_id=user)
        assert receipt.node.id in {node.id for node in event_nodes} and len(event_nodes) > 1
        # Its direct-store snapshot is still what the event holds.
        assert await engine.processing_status(str(receipt.event_id), user_id=user) is not None


async def test_the_default_stores_without_extraction(config, user):
    async with MemoryEngine.open(config) as engine:
        calls = script(engine)
        for extract in (None, False):
            event_id = await engine.store(f"{SENTENCE} {extract}", user_id=user, extract=extract)
            assert await engine.extraction_status(event_id, user_id=user) is None
        await drain(engine._pipeline)
        assert calls == [] and await engine.count_nodes(user_id=user) == 2


async def test_the_config_default_extracts_and_a_call_can_opt_out(config, user):
    on = config.model_copy(update={"enable_store_extraction": True})
    async with MemoryEngine.open(on) as engine:
        calls = script(engine)
        followed = await engine.store(SENTENCE, user_id=user)
        refused = await engine.store("They dropped Hiroshima to save time.", user_id=user, extract=False)
        await drain(engine._pipeline)
        assert calls == [SENTENCE]
        assert (await engine.extraction_status(followed, user_id=user)).status == "complete"
        assert await engine.extraction_status(refused, user_id=user) is None


async def test_extraction_reads_the_source_and_leaves_the_retrieval_text(config, user):
    async with MemoryEngine.open(config) as engine:
        calls = script(engine)
        receipt = await engine.store_with_receipt(SENTENCE, user_id=user, extract=True,
                                                  retrieval_content="Trip to Japan, April 2027")
        await drain(engine._pipeline)
        assert calls == [SENTENCE]
        assert receipt.node.content == "Trip to Japan, April 2027"
        notes = await engine.query_nodes(user_id=user, node_type=NodeType.NOTE)
        assert [note.content for note in notes] == ["Trip to Japan, April 2027"]
        assert await names(engine, user, NodeType.ENTITY) == ["Alex", "Japan", "Jordan"]


async def test_the_saved_job_outlives_the_process(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        script(engine)
        # The process stops after the store is committed and before anything runs.
        monkeypatch.setattr(engine._pipeline, "queue_extraction", lambda *args, **kwargs: None)
        event_id = await engine.store(SENTENCE, user_id=user, extract=True)
        assert (await engine.extraction_status(event_id, user_id=user)).status == "pending"
    async with MemoryEngine.open(config) as engine:
        calls = script(engine)
        result = await engine.process_extractions(user_id=user)
        assert (result.processed, result.pending, result.failed) == (1, 0, 0)
        assert calls == [SENTENCE]
        assert await names(engine, user, NodeType.ENTITY) == ["Alex", "Japan", "Jordan"]
        assert len(await engine.query_nodes(user_id=user, node_type=NodeType.NOTE)) == 1


async def test_a_failed_extraction_keeps_the_stored_node_and_retries(config, user):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=RuntimeError("provider down"))
        engine._pipeline._retry_delays = ()
        receipt = await engine.store_with_receipt(SENTENCE, user_id=user, extract=True)
        await drain(engine._pipeline)
        assert (await engine.extraction_status(str(receipt.event_id), user_id=user)).status == "failed"
        assert [node.content for node in await engine.query_nodes(user_id=user)] == [SENTENCE]
        script(engine)
        await engine.retry_extraction(str(receipt.event_id), user_id=user)
        assert (await engine.process_extractions(user_id=user)).processed == 1
        assert await names(engine, user, NodeType.ENTITY) == ["Alex", "Japan", "Jordan"]


async def test_stores_of_one_session_share_a_batched_request(config, user):
    batched = config.model_copy(update={"extraction_batch_target_tokens": 512})
    model = ScriptedBatches()
    async with MemoryEngine.open(batched) as engine:
        engine._pipeline._extraction_provider.extract_batch = model.extract_batch
        engine._pipeline._batch_flush_seconds = 0.05
        first = await engine.store("Jordan moved to Denver last spring.", user_id=user, session_id="s1", extract=True)
        second = await engine.store("Caroline paints sunsets over the lake.", user_id=user, session_id="s1", extract=True)
        await drain(engine._pipeline)
        assert model.contents() == [["Jordan moved to Denver last spring.", "Caroline paints sunsets over the lake."]]
        for event_id in (first, second):
            assert (await engine.extraction_status(event_id, user_id=user)).status == "complete"
        assert len(await engine.query_nodes(user_id=user, node_type=NodeType.FACT)) == 2


async def test_a_store_among_ingested_turns_joins_their_batch(config, user):
    batched = config.model_copy(update={"extraction_batch_target_tokens": 512})
    model = ScriptedBatches()
    async with MemoryEngine.open(batched) as engine:
        engine._pipeline._extraction_provider.extract_batch = model.extract_batch
        engine._pipeline._batch_flush_seconds = 3600.0
        await engine.ingest("Jordan moved to Denver last spring.", user_id=user, session_id="s1")
        stored = await engine.store("Caroline paints sunsets over the lake.", user_id=user, session_id="s1", extract=True)
        assert (await engine.process_extractions(user_id=user)).processed == 2
        assert len(model.calls) == 1 and len(model.calls[0]) == 2
        assert (await engine.extraction_status(stored, user_id=user)).status == "complete"


async def test_extract_without_a_pipeline_is_refused_before_anything_is_saved(config, user):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline = None
        with pytest.raises(ValueError, match="ingestion pipeline"):
            await engine.store(SENTENCE, user_id=user, extract=True)
        assert await engine.get_events(user_id=user) == []
        # Followed from the configuration, the missing pipeline just means no extraction.
        engine._config = engine._config.model_copy(update={"enable_store_extraction": True})
        await engine.store(SENTENCE, user_id=user)
        assert await engine.count_nodes(user_id=user) == 1


def app_for(config, engine, owner):
    config.api = APIConfig(user_keys={owner: "owner-token"})
    app = create_app(config)
    app.state.engine = engine
    return app


async def test_the_route_takes_extract(config, user):
    async with MemoryEngine.open(config) as engine:
        calls = script(engine)
        app = app_for(config, engine, user)
        transport = httpx.ASGITransport(app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     headers={"Authorization": "Bearer owner-token"}) as client:
            plain = await client.post("/v1/store", json={"content": "A plain note."})
            raw = await client.post("/v1/store", json={"content": "Raw on purpose.", "extract": False})
            asked = await client.post("/v1/store", json={"content": SENTENCE, "extract": True})
            wrong = await client.post("/v1/store", json={"content": SENTENCE, "extract": "yes"})
            assert [r.status_code for r in (plain, raw, asked)] == [200, 200, 200]
            assert wrong.status_code == 422
            await drain(engine._pipeline)
            assert calls == [SENTENCE]
            # The reply is the store's: the event, the node, and the node's processing.
            assert set(asked.json()) == {"event_id", "node_id", "processing_status"}
            status = await client.get(f'/v1/events/{asked.json()["event_id"]}/extraction-status')
            assert status.status_code == 200 and status.json()["status"] == "complete"
            assert (await client.get(f'/v1/events/{raw.json()["event_id"]}/extraction-status')).status_code == 404
        schema = (await _openapi(app))["components"]["schemas"]["StoreRequest"]["properties"]["extract"]
        assert "boolean" in str(schema)


async def _openapi(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        return (await client.get("/openapi.json")).json()


def test_the_sync_client_takes_extract(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider",
                        lambda _: test_durable_ingestion.MockEmbeddingProvider())
    settings = config_from_directory(str(tmp_path))
    settings.organizer.opportunistic_enabled = False
    with MemoryClient(config=settings) as client:
        client._engine._pipeline._extraction_provider.extract = AsyncMock(return_value=FOUND)
        asked = client.store(SENTENCE, user_id="alice", extract=True)
        raw = client.store("Raw on purpose.", user_id="alice", extract=False)
        assert client.extraction_status(raw, user_id="alice") is None
        assert client.extraction_status(asked, user_id="alice") is not None
        client.process_extractions(user_id="alice")
        assert client.extraction_status(asked, user_id="alice").status == "complete"
        assert client.extraction_status(raw, user_id="alice") is None
