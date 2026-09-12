"""Model relationship labels become auditable claims, not authoritative edges."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from prme import MemoryEngine
from prme.ingestion.extraction import _CitedExtractionResult
from prme.ingestion.grounding import validate_grounding
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractedRelationship, ExtractionResult
from prme.types import EdgeType, EpistemicType, NodeType, RetrievalMode, SourceType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


def extraction(source, *, epistemic="hypothetical"):
    return ExtractionResult(
        entities=[ExtractedEntity(name="Alice", entity_type="person"), ExtractedEntity(name="Acme", entity_type="organization")],
        relationships=[ExtractedRelationship(source_entity="Alice", target_entity="Acme", relationship_type="works_at",
                                              evidence_quote=source, epistemic_type=epistemic)],
    )


@pytest.mark.parametrize("field,value", [("evidence_quote", None), ("evidence_quote", "Alice owns Acme"),
                                          ("epistemic_type", None), ("epistemic_type", "deprecated")])
def test_builtin_relationships_require_supported_citations_and_epistemic_types(field, value):
    source = "Alice might work at Acme."
    payload = extraction(source).model_dump()
    if value is None:
        del payload["relationships"][0][field]
    else:
        payload["relationships"][0][field] = value
    with pytest.raises(ValidationError):
        _CitedExtractionResult.model_validate(payload, context={"source_text": source})


def test_short_relationship_quote_keeps_trailing_conditions():
    source = "Alice works at Acme only during the summer. This is not permanent."
    result = validate_grounding(extraction("Alice works at Acme", epistemic="conditional"), source)
    assert result.relationships[0].evidence_quote == source


async def test_hypothetical_relationship_uses_normal_claim_filtering_and_source_context(config, user):
    source = "Alice might work at Acme if the contract is approved."
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=extraction(source))
        eid = await engine.ingest(source, user_id=user, wait_for_extraction=True)
        plan = await engine._event_store.get_derivation_plan(eid, user_id=user)
        claims = [n for n in plan.nodes if n.node_type == NodeType.FACT]
        assert len(claims) == 1
        claim = claims[0]
        assert claim.content == source and claim.epistemic_type == EpistemicType.HYPOTHETICAL
        assert claim.metadata["predicate"] == "works_at" and claim.metadata["extraction_kind"] == "relationship"
        assert {edge.edge_type for edge in plan.edges} == {EdgeType.HAS_FACT, EdgeType.MENTIONS}
        assert all(edge.source_id == claim.id or edge.target_id == claim.id for edge in plan.edges)
        response = await engine.retrieve("Alice Acme", user_id=user)
        assert claim.id not in {candidate.node.id for candidate in response.results}
        explicit = await engine.retrieve("Alice Acme", user_id=user, retrieval_mode=RetrievalMode.EXPLICIT)
        assert claim.id in {candidate.node.id for candidate in explicit.results}
        assert await engine.get_event_nodes(eid, user_id=user + "-other") == []


async def test_legacy_relationship_is_unverified_and_does_not_create_causal_edges(config, user):
    source = "Alice is mentioned alongside Acme."
    payload = extraction(source).model_dump()
    del payload["relationships"][0]["epistemic_type"]
    del payload["relationships"][0]["evidence_quote"]
    payload["relationships"][0]["relationship_type"] = "caused_by"
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult.model_validate(payload))
        eid = await engine.ingest(source, user_id=user, wait_for_extraction=True)
        plan = await engine._event_store.get_derivation_plan(eid, user_id=user)
        claim = next(n for n in plan.nodes if n.node_type == NodeType.FACT)
        assert claim.epistemic_type == EpistemicType.UNVERIFIED and claim.content == source
        assert claim.metadata["predicate"] == "caused_by"
        assert claim.source_type == SourceType.SYSTEM_INFERRED
        assert claim.confidence == engine._pipeline._confidence_matrix.lookup(
            EpistemicType.UNVERIFIED, SourceType.SYSTEM_INFERRED,
        )
        response = await engine.retrieve("Alice Acme", user_id=user)
        assert claim.id not in {candidate.node.id for candidate in response.results}
        explicit = await engine.retrieve("Alice Acme", user_id=user, retrieval_mode=RetrievalMode.EXPLICIT)
        assert claim.id in {candidate.node.id for candidate in explicit.results}
        assert not any(edge.edge_type == EdgeType.CAUSED_BY for edge in plan.edges)


async def test_covering_fact_preserves_qualification_over_duplicate_relationship_label(config, user):
    source = "Alice might work at Acme if the contract is approved."
    result = extraction(source, epistemic="asserted")
    result.relationships[0].relationship_type = "part_of"
    result.facts.append(ExtractedFact(subject="Alice", predicate="works_at", object="Acme",
                                     evidence_quote=source, epistemic_type="hypothetical"))
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=result)
        eid = await engine.ingest(source, user_id=user, wait_for_extraction=True)
        plan = await engine._event_store.get_derivation_plan(eid, user_id=user)
        facts = [n for n in plan.nodes if n.node_type == NodeType.FACT]
        assert len(facts) == 1 and facts[0].epistemic_type == EpistemicType.HYPOTHETICAL
        assert facts[0].metadata["predicate"] == "works_at"
        assert not any(edge.edge_type == EdgeType.PART_OF for edge in plan.edges)
