"""Tool observations retain their origin across admission and recovery paths."""

from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine, NodeType
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.types import SourceType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize("method,role", [("store", "tool"), ("ingest_fast", "TOOL"), ("ingest", "tool")])
async def test_tool_origin_survives_recovery_and_retrieval(config, user, monkeypatch, method, role):
    source = "Cobalt telescope recorded 42 observations."
    async with MemoryEngine.open(config) as engine:
        if method == "ingest":
            monkeypatch.setattr(engine._pipeline._extraction_provider, "extract", AsyncMock(return_value=ExtractionResult(
                entities=[ExtractedEntity(name="Cobalt telescope", entity_type="product")],
                facts=[ExtractedFact(subject="Cobalt telescope", predicate="recorded", object="42 observations",
                                     evidence_quote=source, epistemic_type="observed")],
            )))
            event_id = await engine.ingest(source, user_id=user, role=role, wait_for_extraction=True)
        else:
            event_id = await getattr(engine, method)(source, user_id=user, role=role)
    async with MemoryEngine.open(config) as engine:
        if method == "ingest_fast":
            assert (await engine.process_pending(user_id=user)).processed == 1
        nodes = await engine.get_event_nodes(event_id, user_id=user)
        relevant = [node for node in nodes if node.node_type in (NodeType.NOTE, NodeType.FACT)]
        assert relevant
        for node in relevant:
            assert node.source_type == SourceType.TOOL_OUTPUT
            assert node.confidence_base == pytest.approx(engine._confidence_matrix.lookup_with_fallback(
                node.epistemic_type, SourceType.TOOL_OUTPUT,
            ))
            assert str(node.evidence_refs[0]) == event_id
        assert (await engine.get_event(event_id, user_id=user)).role == role
        result = await engine.retrieve("Cobalt telescope observations", user_id=user)
        assert '"source_type":"tool_output"' in result.bundle.render()
        assert await engine.get_event_nodes(event_id, user_id=user + "-other") == []


async def test_explicit_provenance_override_is_durable(config, user):
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.store("Imported Cobalt observation", user_id=user, role="tool",
                                      source_type=SourceType.EXTERNAL_DOCUMENT, confidence=.83)
    async with MemoryEngine.open(config) as engine:
        node = (await engine.get_event_nodes(event_id, user_id=user))[0]
        assert node.source_type == SourceType.EXTERNAL_DOCUMENT
        assert node.confidence == pytest.approx(.83)
