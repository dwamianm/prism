"""Extraction references stay closed and never choose an arbitrary namesake."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from prme import MemoryEngine
from prme.ingestion.extraction import _CitedExtractionResult
from prme.ingestion.schema import ExtractionResult
from prme.types import EdgeType, NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


def namesakes(*, qualified=True, reverse=False):
    entities = [{"name": "Jordan", "entity_type": "person"}, {"name": "Jordan", "entity_type": "location"}]
    fact = {"subject": "Jordan", "predicate": "lives_in", "object": "Jordan", "evidence_quote": "Jordan lives in Jordan."}
    rel = {"source_entity": "Jordan", "target_entity": "Jordan", "relationship_type": "relates_to"}
    if qualified:
        fact["subject_entity_type"] = rel["source_entity_type"] = "person"
        rel["target_entity_type"] = "location"
    return {"entities": entities[::-1] if reverse else entities, "facts": [fact], "relationships": [rel]}


@pytest.mark.parametrize("payload", [
    {"entities": [{"name": "Aster", "entity_type": "product"}], "facts": [
        {"subject": "Aster service", "predicate": "uses", "object": "PostgreSQL", "evidence_quote": "The Aster service uses PostgreSQL."}]},
    {"entities": [{"name": "Aster", "entity_type": "product"}, {"name": "PostgreSQL", "entity_type": "product"}],
     "relationships": [{"source_entity": "Aster", "target_entity": "uses PostgreSQL", "relationship_type": "relates_to"}]},
    namesakes(qualified=False),
])
def test_builtin_schema_rejects_missing_and_ambiguous_references(payload):
    with pytest.raises(ValidationError, match="missing|ambiguous"):
        _CitedExtractionResult.model_validate(payload)


@pytest.mark.parametrize("reverse", [False, True])
def test_builtin_schema_accepts_explicit_namesake_types(reverse):
    result = _CitedExtractionResult.model_validate(namesakes(reverse=reverse), context={"source_text": "Jordan lives in Jordan."})
    assert result.facts[0].subject_entity_type == "person"
    assert result.relationships[0].target_entity_type == "location"


def test_builtin_schema_accepts_case_normalization_and_duplicate_same_identity():
    payload = namesakes()
    payload["entities"].append({"name": " JORDAN ", "entity_type": "person"})
    assert len(_CitedExtractionResult.model_validate(payload).entities) == 3


@pytest.mark.parametrize("reverse", [False, True])
async def test_namesake_types_wire_correct_entities_independent_of_order(config, user, reverse):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult.model_validate(namesakes(reverse=reverse)))
        event_id = await engine.ingest("Jordan lives in Jordan.", user_id=user, wait_for_extraction=True)
        nodes = await engine.get_event_nodes(event_id, user_id=user)
        entities = {n.metadata["entity_type"]: n for n in nodes if n.node_type == NodeType.ENTITY}
        fact = next(n for n in nodes if n.node_type == NodeType.FACT)
        assert fact.metadata["subject_link_status"] == "resolved"
        subject_edges = await engine._graph_store.get_edges(target_id=str(fact.id))
        assert len(subject_edges) == 1 and subject_edges[0].source_id == entities["person"].id
        relationships = await engine._graph_store.get_edges(source_id=str(entities["person"].id), target_id=str(entities["location"].id))
        assert len(relationships) == 1 and relationships[0].edge_type == EdgeType.RELATES_TO
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        assert plan.materialization_policy == "typed_references_v2"
        assert await engine.get_event_nodes(event_id, user_id=user + "-other") == []


@pytest.mark.parametrize("missing", [False, True])
async def test_custom_provider_unresolved_facts_are_preserved_without_guessing(config, user, missing):
    payload = namesakes(qualified=False)
    if missing:
        payload["entities"] = []
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult.model_validate(payload))
        event_id = await engine.ingest("Jordan lives in Jordan.", user_id=user, wait_for_extraction=True)
        fact = next(n for n in await engine.get_event_nodes(event_id, user_id=user) if n.node_type == NodeType.FACT)
        assert fact.content == "Jordan lives in Jordan."
        assert fact.metadata["subject_link_status"] == ("missing" if missing else "ambiguous")
        assert await engine._graph_store.get_edges(target_id=str(fact.id)) == []
