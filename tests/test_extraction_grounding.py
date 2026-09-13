"""Source membership is necessary; exact source content retains qualifications."""

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from prme import MemoryEngine
from prme.ingestion.extraction import _CitedExtractionResult
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


def test_builtin_fact_requires_explicit_polarity():
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "likes",
            "object": "tea",
            "evidence_quote": "Alice likes tea.",
        }],
    }
    with pytest.raises(ValidationError, match="polarity"):
        _CitedExtractionResult.model_validate(
            payload, context={"source_text": "Alice likes tea."}
        )


@pytest.mark.parametrize("condition", [None, "manager approval"])
def test_builtin_conditional_requires_verbatim_condition(condition):
    source = "If approval is granted, Alice uses email."
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "uses",
            "object": "email",
            "polarity": "positive",
            "evidence_quote": source,
            "epistemic_type": "conditional",
            "condition": condition,
        }],
    }
    with pytest.raises(ValidationError, match="verbatim condition"):
        _CitedExtractionResult.model_validate(payload, context={"source_text": source})

    payload["facts"][0]["condition"] = "approval is granted"
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].condition == "approval is granted"


def test_builtin_explicit_condition_cannot_be_materialized_as_asserted():
    source = "If approval is granted, Alice uses email."
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "uses",
            "object": "email",
            "polarity": "positive",
            "evidence_quote": source,
            "epistemic_type": "asserted",
        }],
    }
    with pytest.raises(ValidationError, match="explicit if/unless condition"):
        _CitedExtractionResult.model_validate(payload, context={"source_text": source})

    payload["facts"][0].update({
        "epistemic_type": "conditional",
        "condition": "approval is granted",
        "fact_type": "decision",
    })
    with pytest.raises(ValidationError, match="not a decision"):
        _CitedExtractionResult.model_validate(payload, context={"source_text": source})


def test_builtin_uncertainty_cannot_be_materialized_as_asserted_decision():
    source = "Alice might use Redis after evaluation."
    payload = {
        "entities": [
            {"name": "Alice", "entity_type": "person"},
            {"name": "Redis", "entity_type": "product"},
        ],
        "facts": [{
            "subject": "Alice",
            "predicate": "uses",
            "object": "Redis",
            "polarity": "positive",
            "evidence_quote": source,
            "fact_type": "decision",
            "epistemic_type": "asserted",
        }],
    }
    with pytest.raises(ValidationError, match="hypothetical or conditional"):
        _CitedExtractionResult.model_validate(payload, context={"source_text": source})

    payload["facts"][0]["epistemic_type"] = "hypothetical"
    with pytest.raises(ValidationError, match="not a decision"):
        _CitedExtractionResult.model_validate(payload, context={"source_text": source})


def test_builtin_explicit_choice_remains_a_decision():
    source = "Alice decided to use Redis."
    payload = {
        "entities": [
            {"name": "Alice", "entity_type": "person"},
            {"name": "Redis", "entity_type": "product"},
        ],
        "facts": [{
            "subject": "Alice",
            "predicate": "uses",
            "object": "Redis",
            "polarity": "positive",
            "evidence_quote": source,
            "fact_type": "decision",
            "epistemic_type": "asserted",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].fact_type == "decision"


def test_grounding_downgrades_conditionals_without_supported_condition():
    source = "If approval is granted, Alice uses email."
    unsupported = fact(
        evidence_quote=source,
        epistemic_type="conditional",
        condition="the manager agrees",
    )
    grounded = validate_grounding(ExtractionResult(facts=[unsupported]), source)
    assert grounded.facts[0].condition is None
    assert grounded.facts[0].epistemic_type == "hypothetical"

    supported = fact(
        evidence_quote=source,
        epistemic_type="conditional",
        condition="approval is granted",
    )
    grounded = validate_grounding(ExtractionResult(facts=[supported]), source)
    assert grounded.facts[0].condition == "approval is granted"
    assert grounded.facts[0].epistemic_type == "conditional"


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


async def test_materialized_claim_persists_typed_qualifiers(config, user):  # noqa: F811
    source = "If approval is granted, Alice does not use email."
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult(
            facts=[fact(
                evidence_quote=source,
                epistemic_type="conditional",
                condition="approval is granted",
                polarity="negative",
            )],
        ))
        event_id = await engine.ingest(source, user_id=user, wait_for_extraction=True)
        node = next(
            node for node in await engine.get_event_nodes(event_id, user_id=user)
            if node.node_type == NodeType.FACT
        )
        assert node.metadata["polarity"] == "negative"
        assert node.metadata["condition"] == "approval is granted"
        assert node.metadata["condition_state"] == "unknown"
