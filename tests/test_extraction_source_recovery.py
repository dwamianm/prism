"""Extraction failures leave restart-safe, searchable original evidence."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.errors import ExtractionError
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.models import Event
from prme.types import NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_failed_extraction_recovers_source_as_searchable_note_after_restart(config, user):  # noqa: F811
    text = "Use the cobalt staging key only for staging. Never use it in production."
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._retry_delays = ()
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=RuntimeError("provider offline"))
        with pytest.raises(ExtractionError) as failure:
            await engine.ingest(text, user_id=user, scope=Scope.PROJECT, wait_for_extraction=True)
        event_id = failure.value.event_id
        source = await engine.get_event(event_id, user_id=user)
        assert (await engine.processing_status(event_id, user_id=user)).status == "pending"
    async with MemoryEngine.open(config) as engine:
        response = await engine.retrieve("cobalt staging key", user_id=user, scope=Scope.PROJECT)
        recovered = [c.node for c in response.results if str(c.node.id) == event_id]
        assert len(recovered) == 1 and recovered[0].content == text
        assert recovered[0].node_type == NodeType.NOTE
        assert recovered[0].created_at == source.created_at
        assert recovered[0].evidence_refs == [source.id]
        assert (await engine.processing_status(event_id, user_id=user)).status == "complete"
        assert (await engine.get_event(event_id, user_id=user)).model_dump() == source.model_dump()
        assert (await engine.retrieve("cobalt staging key", user_id=user + "-other")).results == []


async def test_completed_extraction_cannot_replace_source_index_with_lossy_summary(config, user):  # noqa: F811
    text = "Alice uses a staging key only for staging, never for production."
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult(
            entities=[], facts=[], relationships=[], summary="Alice uses a production key",
        ))
        event_id = await engine.ingest(text, user_id=user, wait_for_extraction=True)
        await engine.process_pending(user_id=user)
        source = await engine.get_event(event_id, user_id=user)
        # Re-running the delayed completion after the raw note is indexed must
        # not overwrite the source with the model's unsupported summary.
        await engine._pipeline._extract_and_materialize(source, event_id)
        response = await engine.retrieve("staging key", user_id=user)
        assert any(c.node.content == text for c in response.results)
        assert all(c.node.content != "Alice uses a production key" for c in response.results)
        lexical = await engine._lexical_index.search("staging", user_id=user)
        assert any(str(hit["node_id"]) == event_id for hit in lexical)


async def test_delayed_temporal_extraction_uses_source_clock(config, user):  # noqa: F811
    source_time = datetime(2024, 5, 10, 12, tzinfo=timezone.utc)
    text = "Alice moved to Paris yesterday."
    async with MemoryEngine.open(config) as engine:
        event = Event(user_id=user, role="user", content=text, timestamp=source_time, created_at=source_time)
        await engine._event_store.append(event)
        extracted = ExtractionResult(
            entities=[ExtractedEntity(name="Alice", entity_type="person")],
            facts=[ExtractedFact(subject="Alice", predicate="moved_to", object="Paris", temporal_ref="yesterday", evidence_quote=text)],
            relationships=[],
        )
        await engine._pipeline._materialize(extracted, event, str(event.id))
        fact = (await engine.query_nodes(user_id=user, node_type=NodeType.FACT))[0]
        assert fact.event_time == datetime(2024, 5, 9, 12, tzinfo=timezone.utc)
        assert fact.metadata["resolved_date"] == "2024-05-09T12:00:00+00:00"
