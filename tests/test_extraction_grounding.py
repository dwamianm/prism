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


def test_builtin_drops_fact_without_explicit_polarity():
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "likes",
            "object": "tea",
            "evidence_quote": "Alice likes tea.",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": "Alice likes tea."}
    )
    assert result.facts == []


def test_builtin_keeps_valid_sibling_when_another_fact_is_malformed():
    source = "Alice uses email and likes tea."
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [
            {
                "subject": "Alice",
                "predicate": "uses",
                "object": "email",
                "polarity": "positive",
                "evidence_quote": source,
            },
            {
                "subject": "Alice",
                "predicate": "likes",
                "object": None,
                "polarity": "positive",
                "evidence_quote": source,
            },
        ],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert [(item.predicate, item.object) for item in result.facts] == [
        ("uses", "email")
    ]


def test_builtin_still_rejects_a_malformed_claim_envelope():
    with pytest.raises(ValidationError, match="facts"):
        _CitedExtractionResult.model_validate({"facts": {"object": None}})


def test_unrelated_condition_in_same_paragraph_does_not_contaminate_claim():
    source = "Alice uses email. Can you help if you have time?"
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "uses",
            "object": "email",
            "polarity": "positive",
            "evidence_quote": "Alice uses email.",
            "epistemic_type": "asserted",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].epistemic_type == "asserted"
    assert result.facts[0].evidence_quote == source


def test_following_condition_sentence_still_qualifies_claim():
    source = "Alice uses email. Only if her manager approves."
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "uses",
            "object": "email",
            "polarity": "positive",
            "evidence_quote": "Alice uses email.",
            "epistemic_type": "asserted",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts == []

    payload["facts"][0].update({
        "epistemic_type": "conditional",
        "condition": "her manager approves",
    })
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].condition == "her manager approves"


def test_indirect_question_if_is_not_a_claim_condition():
    source = "I will visit local antique dealers to see if they have information."
    payload = {
        "entities": [
            {"name": "local antique dealers", "entity_type": "organization"}
        ],
        "facts": [{
            "subject": "I",
            "predicate": "will visit",
            "object": "local antique dealers",
            "polarity": "positive",
            "evidence_quote": source,
            "epistemic_type": "asserted",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].epistemic_type == "asserted"

    source = "I will visit local antique dealers if they have information."
    payload["facts"][0]["evidence_quote"] = source
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts == []


@pytest.mark.parametrize(
    "source",
    [
        "Alice uses email, so could you help me configure it?",
        "Alice uses email and I was wondering if you could suggest a client.",
        "Alice uses email and I was wondering if you could also help configure it.",
        "Alice uses email; may I ask you about migration?",
    ],
)
def test_polite_request_modals_do_not_make_supported_claim_hypothetical(source):
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
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].epistemic_type == "asserted"


def test_builtin_drops_attempt_collapsed_into_completed_state():
    source = ("I'm trying to set up ESLint v8.39 with the Airbnb style guide for my "
              "JavaScript project, but I'm not sure how to customize it.")
    payload = {"facts": [{
        "subject": "I", "predicate": "uses_style_guide",
        "object": "Airbnb style guide", "polarity": "positive",
        "evidence_quote": source, "epistemic_type": "observed",
    }]}
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts == []


def test_builtin_keeps_attempt_when_predicate_preserves_speech_act():
    source = ("I'm trying to set up ESLint v8.39 with the Airbnb style guide for my "
              "JavaScript project.")
    payload = {"facts": [{
        "subject": "I", "predicate": "trying_to_set_up",
        "object": "Airbnb style guide", "polarity": "positive",
        "evidence_quote": source, "epistemic_type": "observed",
    }]}
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].predicate == "trying_to_set_up"


def test_builtin_recognizes_unicode_attempt_contractions():
    source = "I’m trying to use Redis for the cache."
    payload = {"facts": [{
        "subject": "I", "predicate": "uses", "object": "Redis",
        "polarity": "positive", "evidence_quote": source,
        "epistemic_type": "observed",
    }]}
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts == []


def test_unrelated_attempt_sentence_does_not_hide_actual_fact():
    source = "I'm trying to optimize the dashboard. The dashboard API averages 800ms."
    payload = {
        "entities": [{"name": "dashboard API", "entity_type": "product"}],
        "facts": [{
            "subject": "dashboard API", "subject_entity_type": "product",
            "predicate": "averages", "object": "800ms", "polarity": "positive",
            "evidence_quote": "The dashboard API averages 800ms.",
            "epistemic_type": "observed",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].object == "800ms"


def test_builtin_relationships_preserve_intention_too():
    source = "We plan to use Redis after the evaluation."
    relationship = {
        "source_entity": "We", "target_entity": "Redis",
        "relationship_type": "uses", "polarity": "positive",
        "evidence_quote": source, "epistemic_type": "observed",
    }
    payload = {
        "entities": [{"name": "Redis", "entity_type": "product"}],
        "relationships": [relationship],
    }
    collapsed = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    relationship["relationship_type"] = "plans_to_use"
    preserved = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert collapsed.relationships == []
    assert preserved.relationships[0].relationship_type == "plans_to_use"


def test_builtin_drops_component_relationship_inside_attempt_clause():
    source = "I'm trying to set up ESLint with the Airbnb style guide."
    payload = {
        "entities": [
            {"name": "ESLint", "entity_type": "product"},
            {"name": "Airbnb style guide", "entity_type": "concept"},
        ],
        "relationships": [{
            "source_entity": "ESLint",
            "target_entity": "Airbnb style guide",
            "relationship_type": "used_with",
            "polarity": "positive",
            "evidence_quote": source,
            "epistemic_type": "observed",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.relationships == []


def test_builtin_recovers_exact_target_when_model_keeps_only_attempt_count():
    source = "I've tried to install CUDA 12.4 twice, but the installer fails."
    payload = {
        "entities": [{"name": "CUDA 12.4", "entity_type": "product"}],
        "facts": [{
            "subject": "I",
            "predicate": "attempted_install_count",
            "object": "twice",
            "polarity": "positive",
            "evidence_quote": source,
            "epistemic_type": "observed",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    recovered = [fact for fact in result.facts if fact.object == "CUDA 12.4"]
    assert len(recovered) == 1
    assert recovered[0].subject == "I"
    assert recovered[0].predicate == "tried_to_install"
    assert recovered[0].object_entity_type == "product"
    assert recovered[0].evidence_quote == source


def test_builtin_does_not_duplicate_existing_attempt_target():
    source = "I've tried to install CUDA 12.4 twice."
    payload = {
        "entities": [{"name": "CUDA 12.4", "entity_type": "product"}],
        "facts": [{
            "subject": "I",
            "predicate": "tried_to_install",
            "object": "CUDA 12.4",
            "polarity": "positive",
            "evidence_quote": source,
            "epistemic_type": "observed",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert len([fact for fact in result.facts if fact.object == "CUDA 12.4"]) == 1


def test_builtin_does_not_recover_entity_outside_attempt_clause():
    source = "I've tried to fix the installer twice. CUDA 12.4 is documented later."
    payload = {
        "entities": [{"name": "CUDA 12.4", "entity_type": "product"}],
        "facts": [],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts == []


def test_builtin_recovery_preserves_explicit_condition():
    source = "I've tried to use Redis if the service is healthy."
    payload = {
        "entities": [{"name": "Redis", "entity_type": "product"}],
        "facts": [],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert len(result.facts) == 1
    assert result.facts[0].predicate == "tried_to_use"
    assert result.facts[0].epistemic_type == "conditional"
    assert result.facts[0].condition == "if the service is healthy"


def test_builtin_recovers_omitted_hope_target():
    source = "We hope to deploy Phoenix after the security review."
    payload = {
        "entities": [
            {"name": "Phoenix", "entity_type": "product"},
            {"name": "security review", "entity_type": "event"},
        ],
        "facts": [],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert len(result.facts) == 1
    assert result.facts[0].subject == "We"
    assert result.facts[0].predicate == "hopes_to_deploy"
    assert result.facts[0].object == "Phoenix"


@pytest.mark.parametrize(
    "source",
    [
        "Alice could use email.",
        "Could you tell me whether Alice might use email?",
        "Can you suggest tools that Alice could use for email?",
    ],
)
def test_claim_modals_remain_hypothetical_near_request_phrasing(source):
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
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts == []


@pytest.mark.parametrize("condition", [None, "manager approval"])
def test_builtin_drops_conditional_without_verbatim_condition(condition):
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
    result = _CitedExtractionResult.model_validate(payload, context={"source_text": source})
    assert result.facts == []

    payload["facts"][0]["condition"] = "approval is granted"
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].condition == "approval is granted"


def test_builtin_drops_explicit_condition_materialized_as_asserted():
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
    result = _CitedExtractionResult.model_validate(payload, context={"source_text": source})
    assert result.facts == []

    payload["facts"][0].update({
        "epistemic_type": "conditional",
        "condition": "approval is granted",
        "fact_type": "decision",
    })
    result = _CitedExtractionResult.model_validate(payload, context={"source_text": source})
    assert result.facts == []


def test_builtin_drops_uncertainty_materialized_as_asserted_decision():
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
    result = _CitedExtractionResult.model_validate(payload, context={"source_text": source})
    assert result.facts == []

    payload["facts"][0]["epistemic_type"] = "hypothetical"
    result = _CitedExtractionResult.model_validate(payload, context={"source_text": source})
    assert result.facts == []


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


def test_person_named_may_is_not_treated_as_uncertain():
    source = "May uses Redis."
    payload = {
        "entities": [
            {"name": "May", "entity_type": "person"},
            {"name": "Redis", "entity_type": "product"},
        ],
        "facts": [{
            "subject": "May",
            "predicate": "uses",
            "object": "Redis",
            "polarity": "positive",
            "evidence_quote": source,
            "fact_type": "fact",
            "epistemic_type": "asserted",
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].epistemic_type == "asserted"


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
