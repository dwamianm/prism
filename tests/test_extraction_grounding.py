"""Source membership is necessary; exact source content retains qualifications."""

from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.grounding import validate_grounding
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.types import NodeType
from tests.test_durable_ingestion import config, user  # noqa: F401


def fact(**kwargs):
    return ExtractedFact(subject="Alice", predicate="uses", object="email", **kwargs)


@pytest.mark.parametrize("source,extracted", [
    ("Marianne uses email", ExtractedFact(subject="Ann", predicate="uses", object="email")),
    ("Alice uses email", ExtractedFact(subject="Alice", predicate="uses", object="Slack")),
    ("Alice uses email", fact(evidence_quote="Alice always uses email")),
    ("Alice uses email", fact(evidence_quote="")),
    ("Alice uses email", ExtractedFact(subject="", predicate="uses", object="email")),
])
def test_unsupported_mentions_and_fabricated_citations_are_rejected(source, extracted):
    result = validate_grounding(ExtractionResult(facts=[extracted]), source)
    assert result.facts == []


def test_short_real_quote_cannot_remove_trailing_conditions():
    source = "Unrelated introduction.\n\nAlice uses email only for nonurgent requests. Never for emergencies.\n\nUnrelated conclusion."
    extracted = fact(evidence_quote="Alice uses email")
    result = validate_grounding(ExtractionResult(facts=[extracted]), source)
    assert result.facts[0].evidence_quote == "Alice uses email only for nonurgent requests. Never for emergencies."
    assert extracted.evidence_quote == "Alice uses email"


def test_legacy_custom_provider_keeps_full_source_and_non_ascii_mentions():
    source = "José uses C++ only for embedded applications."
    result = validate_grounding(ExtractionResult(facts=[
        ExtractedFact(subject="José", predicate="uses", object="C++"),
    ]), source)
    assert result.facts[0].evidence_quote == source


def test_repeated_quote_keeps_both_distinct_qualifications():
    source = "Alice uses email at work.\n\nAlice uses email at home only when traveling."
    result = validate_grounding(ExtractionResult(facts=[fact(evidence_quote="Alice uses email")]), source)
    assert result.facts[0].evidence_quote == source


def test_entity_substrings_are_not_distinct_people():
    result = validate_grounding(ExtractionResult(entities=[
        ExtractedEntity(name="Ann", entity_type="person"),
        ExtractedEntity(name="Marianne", entity_type="person"),
    ]), "Marianne uses email")
    assert [e.name for e in result.entities] == ["Marianne"]


async def test_materialized_fact_retains_conditions_and_source_provenance(config, user):  # noqa: F811
    source = "Alice uses email only for nonurgent requests. For emergencies Alice requires a phone call."
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult(
            facts=[fact(evidence_quote="Alice uses email")],
        ))
        event_id = await engine.ingest(source, user_id=user, wait_for_extraction=True)
        nodes = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        assert len(nodes) == 1
        assert nodes[0].content == source
        assert nodes[0].metadata["evidence_quote"] == source
        assert nodes[0].metadata["grounding_method"] == "source_passage_v1"
        assert str(nodes[0].evidence_refs[0]) == str(event_id)
