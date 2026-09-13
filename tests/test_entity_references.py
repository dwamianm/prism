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
    fact = {"subject": "Jordan", "object_entity_type": "location", "predicate": "lives_in", "object": "Jordan", "polarity": "positive", "evidence_quote": "Jordan lives in Jordan."}
    rel = {"source_entity": "Jordan", "target_entity": "Jordan", "relationship_type": "relates_to", "polarity": "positive", "evidence_quote": "Jordan lives in Jordan.", "epistemic_type": "asserted"}
    if qualified:
        fact["subject_entity_type"] = rel["source_entity_type"] = "person"
        rel["target_entity_type"] = "location"
    return {"entities": entities[::-1] if reverse else entities, "facts": [fact], "relationships": [rel]}


@pytest.mark.parametrize("payload", [
    {"entities": [{"name": "Aster", "entity_type": "product"}], "facts": [
        {"subject": "Aster service", "predicate": "uses", "object": "PostgreSQL", "polarity": "positive", "evidence_quote": "The Aster service uses PostgreSQL."}]},
    {"entities": [{"name": "Aster", "entity_type": "product"}, {"name": "PostgreSQL", "entity_type": "product"}],
     "relationships": [{"source_entity": "Aster", "target_entity": "uses PostgreSQL", "relationship_type": "relates_to", "polarity": "positive", "evidence_quote": "Jordan lives in Jordan.", "epistemic_type": "asserted"}]},
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
        relationships = await engine._graph_store.get_edges(source_id=str(fact.id), target_id=str(entities["location"].id))
        assert len(relationships) == 1 and relationships[0].edge_type == EdgeType.MENTIONS
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        assert plan.materialization_policy == "claim_qualifiers_v5"
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


@pytest.mark.parametrize("qualifier", [None, "organization"])
def test_builtin_rejects_ambiguous_or_wrongly_typed_object(qualifier):
    payload = namesakes()
    payload["facts"][0]["object_entity_type"] = qualifier
    with pytest.raises(ValidationError, match=r"facts\[0\].object is (ambiguous|missing)"):
        _CitedExtractionResult.model_validate(payload)


def test_builtin_accepts_literal_object_without_entity_entry():
    payload = {"entities": [{"name": "Alice", "entity_type": "person"}], "facts": [
        {"subject": "Alice", "predicate": "likes", "object": "green", "polarity": "positive", "evidence_quote": "Alice likes green."}
    ]}
    assert _CitedExtractionResult.model_validate(payload).facts[0].object == "green"


async def test_custom_ambiguous_object_keeps_claim_without_guessing_link(config, user):
    payload = namesakes()
    payload["facts"][0]["object_entity_type"] = None
    payload["relationships"] = []
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult.model_validate(payload))
        eid = await engine.ingest("Jordan lives in Jordan.", user_id=user, wait_for_extraction=True)
        fact = next(n for n in await engine.get_event_nodes(eid, user_id=user) if n.node_type == NodeType.FACT)
        assert fact.metadata["object_link_status"] == "ambiguous"
        assert not await engine._graph_store.get_edges(source_id=str(fact.id))
        assert len(await engine._graph_store.get_edges(target_id=str(fact.id))) == 1
