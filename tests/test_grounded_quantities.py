"""Quantities remain exact, source-bound, and unit-explicit through ingestion."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from prme import MemoryEngine
from prme.ingestion.extraction import _CitedExtractionResult
from prme.ingestion.grounding import (
    exact_decimal_string_from_quantity_text,
    recover_exact_quantity_from_object,
    validate_grounding,
)
from prme.ingestion.schema import (
    ExtractedFact,
    ExtractedQuantity,
    ExtractedRelationship,
    ExtractionResult,
)
from prme.types import NodeType
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


def _payload(quantity):
    source = "Alice spent $12.50 on lunch."
    return source, {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "spent",
            "object": "$12.50",
            "polarity": "positive",
            "evidence_quote": source,
            "quantity": quantity,
        }],
    }


def test_quantity_schema_rejects_nonfinite_or_unbounded_decimals():
    for value in ("NaN", "Infinity", "1e101", "123456789012345678901234567890123456789"):
        with pytest.raises(ValidationError, match="value"):
            ExtractedQuantity(value=value, unit="$", source_text="$1")


@pytest.mark.parametrize(("encoded", "expected"), [
    ('5000', Decimal("5000")),
    ('"12.50"', Decimal("12.50")),
])
def test_quantity_schema_accepts_exact_json_decimals_under_strict_validation(
    encoded, expected
):
    quantity = ExtractedQuantity.model_validate_json(
        f'{{"value":{encoded},"unit":"1","source_text":"{expected}"}}',
        strict=True,
    )
    assert quantity.value == expected


def test_builtin_strict_json_preserves_exact_quantity_after_result_sanitizing():
    source = "app.run(port=5000)"
    raw = (
        '{"entities":[{"name":"app","entity_type":"product"}],'
        '"facts":[{"subject":"app","predicate":"runs_on_port",'
        '"object":"5000","quantity":{"value":5000,"unit":"1",'
        '"source_text":"5000"},"polarity":"positive",'
        f'"evidence_quote":"{source}"}}]}}'
    )
    result = _CitedExtractionResult.model_validate_json(
        raw, context={"source_text": source}, strict=True
    )
    assert result.facts[0].quantity.value == Decimal("5000")


def test_builtin_recovers_exact_decimal_from_source_instead_of_json_float():
    source = "Alice spent $12.50 on lunch."
    raw = (
        '{"entities":[{"name":"Alice","entity_type":"person"}],'
        '"facts":[{"subject":"Alice","predicate":"spent",'
        '"object":"$12.50","quantity":{"value":12.5,"unit":"$",'
        '"source_text":"$12.50"},"polarity":"positive",'
        f'"evidence_quote":"{source}"}}]}}'
    )
    result = _CitedExtractionResult.model_validate_json(
        raw, context={"source_text": source}, strict=True
    )
    assert len(result.facts) == 1
    assert result.facts[0].quantity == ExtractedQuantity(
        value="12.50", unit="$", source_text="$12.50"
    )


def test_builtin_recovers_terminal_dimensionless_attribute_for_user_source():
    source = "Today was competitive. My final score was 3. It improved."
    result = _CitedExtractionResult.model_validate(
        {"entities": [], "facts": [], "relationships": []},
        context={"source_text": source, "source_role": "user"},
    )
    assert [(entity.name, entity.entity_type) for entity in result.entities] == [
        ("final score", "concept")
    ]
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert (fact.subject, fact.subject_entity_type, fact.predicate, fact.object) == (
        "final score",
        "concept",
        "was",
        "3",
    )
    assert fact.quantity == ExtractedQuantity(value="3", unit="1", source_text="3")
    assert fact.evidence_quote == source


@pytest.mark.parametrize(
    "source",
    [
        "For example, my final score was 3.",
        "My ticket number was 3.",
        "My final score was about 3.",
        "My final score was 3 out of 5.",
        "My final score was 3-4.",
    ],
)
def test_builtin_dimensionless_recovery_stays_narrow(source):
    result = _CitedExtractionResult.model_validate(
        {"entities": [], "facts": [], "relationships": []},
        context={"source_text": source, "source_role": "user"},
    )
    assert result.entities == []
    assert result.facts == []


def test_builtin_does_not_recover_dimensionless_attribute_for_assistant_source():
    source = "My final score was 3."
    result = _CitedExtractionResult.model_validate(
        {"entities": [], "facts": [], "relationships": []},
        context={"source_text": source, "source_role": "assistant"},
    )
    assert result.entities == []
    assert result.facts == []


@pytest.mark.parametrize(
    ("object_value", "claim_passage", "expected"),
    [
        ("$500 for the shelter", "I raised $500 for the shelter.", ("500", "$", "$500")),
        ("250 USD to the bank", "I donated 250 USD to the bank.", ("250", "USD", "250 USD")),
        ("1.5 liters of water", "I drank 1.5 liters of water.", ("1.5", "liters", "1.5 liters")),
        ("-3.25 volts", "The battery delivered -3.25 volts.", ("-3.25", "volts", "-3.25 volts")),
        ("5kg", "I lifted 5kg.", ("5", "kg", "5kg")),
        ("$500 for the shelter", "I raised about $500 for the shelter.", None),
        ("$400-$500", "The budget was $400-$500.", None),
        ("CUDA 12.4", "I installed CUDA 12.4.", None),
        ("AB-1234 packages", "The code was AB-1234 packages.", None),
        ("1,5 liters", "The bottle held 1,5 liters.", None),
        ("3 A.M.", "The call starts at 3 A.M.", None),
        ("500 Main Street", "The office is at 500 Main Street.", None),
        ("2nd place", "I finished in 2nd place.", None),
    ],
)
def test_bounded_quantity_recovery_from_grounded_object(
    object_value, claim_passage, expected
):
    quantity = recover_exact_quantity_from_object(
        object_value,
        claim_passage=claim_passage,
    )
    if expected is None:
        assert quantity is None
    else:
        value, unit, source_text = expected
        assert quantity == ExtractedQuantity(
            value=value,
            unit=unit,
            source_text=source_text,
        )


def test_builtin_enriches_grounded_fact_when_provider_omits_quantity():
    source = "I did not raise $500 for the shelter."
    result = _CitedExtractionResult.model_validate(
        {
            "entities": [{"name": "shelter", "entity_type": "organization"}],
            "facts": [
                {
                    "subject": "I",
                    "predicate": "raised",
                    "object": "$500 for the shelter",
                    "polarity": "negative",
                    "evidence_quote": source,
                    "confidence": 1.0,
                    "epistemic_type": "observed",
                }
            ],
            "relationships": [],
        },
        context={"source_text": source, "source_role": "user"},
    )
    assert result.facts[0].quantity == ExtractedQuantity(
        value="500", unit="$", source_text="$500"
    )
    assert result.facts[0].polarity == "negative"


def test_builtin_recovers_omitted_conditional_quantified_action():
    source = "If the campaign succeeds, I will donate $500 to the shelter."
    result = _CitedExtractionResult.model_validate(
        {"entities": [], "facts": [], "relationships": []},
        context={"source_text": source, "source_role": "user"},
    )
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert (fact.subject, fact.predicate, fact.object, fact.epistemic_type) == (
        "I",
        "will_donate",
        "$500 to the shelter",
        "conditional",
    )
    assert fact.condition == "If the campaign succeeds"
    assert fact.quantity == ExtractedQuantity(value="500", unit="$", source_text="$500")


@pytest.mark.parametrize("role", ["assistant", "system", "tool"])
def test_builtin_conditional_quantity_recovery_is_user_only(role):
    source = "If the campaign succeeds, I will donate $500 to the shelter."
    result = _CitedExtractionResult.model_validate(
        {"entities": [], "facts": [], "relationships": []},
        context={"source_text": source, "source_role": role},
    )
    assert result.facts == []


def test_builtin_does_not_recover_conditional_quantity_from_example():
    source = "For example, if the campaign succeeds, I will donate $500 to the shelter."
    result = _CitedExtractionResult.model_validate(
        {"entities": [], "facts": [], "relationships": []},
        context={"source_text": source, "source_role": "user"},
    )
    assert result.facts == []


@pytest.mark.parametrize(
    ("source_text", "expected"),
    [
        ("$12.50", "12.50"),
        ("-3.25 volts", "-3.25"),
        ("1,234 packages", "1234"),
        ("about $500", None),
        ("an estimated $500", None),
        ("up to 12 packages", None),
        ("$400-$500", None),
    ],
)
def test_exact_decimal_recovery_uses_supported_source_text(source_text, expected):
    assert exact_decimal_string_from_quantity_text(source_text) == expected


def test_grounding_rejects_clipped_approximation_cue_from_full_evidence():
    source = "Alice raised about $500 for the shelter."
    result = validate_grounding(
        ExtractionResult(
            facts=[
                ExtractedFact(
                    subject="Alice",
                    predicate="raised",
                    object="$500 for the shelter",
                    evidence_quote=source,
                    quantity=ExtractedQuantity(
                        value="500", unit="$", source_text="$500"
                    ),
                )
            ]
        ),
        source,
    )
    assert result.facts[0].quantity is None


def test_grounding_does_not_apply_approximation_from_an_earlier_clause():
    source = "Alice spoke about the event, then raised $500 for the shelter."
    result = validate_grounding(
        ExtractionResult(
            facts=[
                ExtractedFact(
                    subject="Alice",
                    predicate="raised",
                    object="$500 for the shelter",
                    evidence_quote=source,
                    quantity=ExtractedQuantity(
                        value="500", unit="$", source_text="$500"
                    ),
                )
            ]
        ),
        source,
    )
    assert result.facts[0].quantity == ExtractedQuantity(
        value="500", unit="$", source_text="$500"
    )


def test_grounding_does_not_apply_unrelated_nearby_inexact_word():
    source = "Alice moved over and then paid $500 for the booking."
    result = validate_grounding(
        ExtractionResult(
            facts=[
                ExtractedFact(
                    subject="Alice",
                    predicate="paid",
                    object="$500 for the booking",
                    evidence_quote=source,
                    quantity=ExtractedQuantity(
                        value="500", unit="$", source_text="$500"
                    ),
                )
            ]
        ),
        source,
    )
    assert result.facts[0].quantity is not None


@pytest.mark.parametrize(
    ("source", "object_value", "source_text", "value"),
    [
        ("The estimate was between $400 and $500.", "$500", "$500", "500"),
        ("The estimate was $400-$500.", "$400", "$400", "400"),
        ("The estimate was $400-$500.", "$500", "$500", "500"),
        ("The estimate was from $400 to $500.", "$400", "$400", "400"),
        ("The estimate was from $400 to $500.", "$500", "$500", "500"),
    ],
)
def test_grounding_rejects_clipped_range_context(
    source, object_value, source_text, value
):
    result = validate_grounding(
        ExtractionResult(
            facts=[
                ExtractedFact(
                    subject="estimate",
                    predicate="was",
                    object=object_value,
                    evidence_quote=source,
                    quantity=ExtractedQuantity(
                        value=value, unit="$", source_text=source_text
                    ),
                )
            ]
        ),
        source,
    )
    assert result.facts[0].quantity is None


def test_grounding_accepts_comma_grouped_dimensionless_count():
    source = "The final count was 1,234."
    result = validate_grounding(
        ExtractionResult(
            facts=[
                ExtractedFact(
                    subject="final count",
                    predicate="was",
                    object="1,234",
                    evidence_quote=source,
                    quantity=ExtractedQuantity(
                        value="1234", unit="1", source_text="1,234"
                    ),
                )
            ]
        ),
        source,
    )
    assert result.facts[0].quantity is not None


def test_builtin_keeps_exact_grounded_quantity():
    source, payload = _payload({
        "value": "12.50",
        "unit": "$",
        "source_text": "$12.50",
    })
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    quantity = result.facts[0].quantity
    assert quantity.value == Decimal("12.50")
    assert quantity.unit == "$"
    assert quantity.source_text == "$12.50"


@pytest.mark.parametrize("quantity", [
    {"value": "13", "unit": "$", "source_text": "$12.50"},
    {"value": "12.50", "unit": "USD", "source_text": "$12.50"},
    {"value": "99", "unit": "$", "source_text": "$99"},
])
def test_builtin_repairs_unsupported_quantity_from_grounded_object(quantity):
    source, payload = _payload(quantity)
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert len(result.facts) == 1
    assert result.facts[0].quantity == ExtractedQuantity(
        value="12.50", unit="$", source_text="$12.50"
    )


def test_builtin_repairs_malformed_quantity_from_grounded_object():
    source, payload = _payload({"value": "NaN", "unit": "", "source_text": ""})
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert len(result.facts) == 1
    assert result.facts[0].quantity == ExtractedQuantity(
        value="12.50", unit="$", source_text="$12.50"
    )


@pytest.mark.parametrize("object_value, quantity", [
    ("3-5 hours", {"value": "3", "unit": "hours", "source_text": "3-5 hours"}),
    ("1e3 requests", {"value": "1000", "unit": "requests", "source_text": "1e3 requests"}),
    ("1,5 hours", {"value": "5", "unit": "hours", "source_text": "1,5 hours"}),
    ("about 3 hours", {"value": "3", "unit": "hours", "source_text": "about 3 hours"}),
    ("(12 USD)", {"value": "12", "unit": "USD", "source_text": "(12 USD)"}),
])
def test_ambiguous_or_unsupported_notation_is_not_admitted(object_value, quantity):
    source = f"Alice requested {object_value}."
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "requested",
            "object": object_value,
            "polarity": "positive",
            "evidence_quote": source,
            "quantity": quantity,
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert len(result.facts) == 1
    assert result.facts[0].quantity is None


def test_builtin_repairs_wrong_dimensionless_unit_to_verbatim_count_unit():
    source = "Alice requested 3 tickets."
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "requested",
            "object": "3 tickets",
            "polarity": "positive",
            "evidence_quote": source,
            "quantity": {"value": "3", "unit": "1", "source_text": "3 tickets"},
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].quantity == ExtractedQuantity(
        value="3", unit="tickets", source_text="3 tickets"
    )


@pytest.mark.parametrize(("object_value", "quantity", "expected"), [
    ("1,234 requests", {"value": "1234", "unit": "requests", "source_text": "1,234 requests"}, Decimal("1234")),
    ("5kg", {"value": "5", "unit": "kg", "source_text": "5kg"}, Decimal("5")),
    ("3", {"value": "3", "unit": "1", "source_text": "3"}, Decimal("3")),
])
def test_supported_exact_notation_is_admitted(object_value, quantity, expected):
    source = f"Alice recorded {object_value}."
    payload = {
        "entities": [{"name": "Alice", "entity_type": "person"}],
        "facts": [{
            "subject": "Alice",
            "predicate": "recorded",
            "object": object_value,
            "polarity": "positive",
            "evidence_quote": source,
            "quantity": quantity,
        }],
    }
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert result.facts[0].quantity.value == expected


def test_custom_grounding_removes_mismatched_quantity_only():
    source = "Alice spent $12.50 on lunch."
    result = validate_grounding(
        ExtractionResult(facts=[ExtractedFact(
            subject="Alice",
            predicate="spent",
            object="$12.50",
            evidence_quote=source,
            quantity=ExtractedQuantity(
                value="99", unit="$", source_text="$12.50"
            ),
        )]),
        source,
    )
    assert len(result.facts) == 1
    assert result.facts[0].quantity is None


async def test_materialization_preserves_grounded_decimal_and_policy(config, user):
    source = "Alice spent $12.50 on lunch."
    extraction = ExtractionResult(
        facts=[ExtractedFact(
            subject="Alice",
            predicate="spent",
            object="$12.50",
            evidence_quote=source,
            quantity=ExtractedQuantity(
                value="12.50", unit="$", source_text="$12.50"
            ),
        )]
    )
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=extraction)
        event_id = await engine.ingest(
            source, user_id=user, wait_for_extraction=True
        )
        node = next(
            node for node in await engine.get_event_nodes(event_id, user_id=user)
            if node.node_type == NodeType.FACT
        )
        assert node.metadata["quantity"] == {
            "value": "12.50",
            "unit": "$",
            "source_text": "$12.50",
            "grounding": "object_decimal_v1",
        }
        extraction_record = await engine.get_extraction(event_id, user_id=user)
        assert extraction_record.result["facts"][0]["quantity"] == {
            "value": "12.50",
            "unit": "$",
            "source_text": "$12.50",
        }
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        assert plan.materialization_policy == "speech_act_v12"


def test_legacy_fact_payloads_remain_unchanged_when_quantity_is_absent():
    original = {
        "subject": "Alice",
        "predicate": "uses",
        "object": "email",
        "polarity": "positive",
    }
    fact = ExtractedFact.model_validate(original)
    assert fact.quantity is None
    dumped = fact.model_dump(mode="json", exclude_defaults=True)
    assert "quantity" not in dumped


def test_extraction_contract_keeps_quantified_phrase_in_targeted_object():
    from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT

    object_description = ExtractedFact.model_fields["object"].description
    quantity_description = ExtractedFact.model_fields["quantity"].description
    relationship_description = ExtractedRelationship.model_fields[
        "relationship_type"
    ].description
    assert object_description is not None
    assert quantity_description is not None
    assert relationship_description is not None
    assert "exact quantified phrase" in object_description
    assert "keep the exact quantified phrase in object" in quantity_description
    assert '"$500 for the shelter"' in EXTRACTION_SYSTEM_PROMPT
    assert "entity-only object that loses" in EXTRACTION_SYSTEM_PROMPT
    assert "must be a fact, not a relationship" in EXTRACTION_SYSTEM_PROMPT
    assert "explicit numeric amount" in relationship_description
    assert "Dates, times, versions, identifiers" in EXTRACTION_SYSTEM_PROMPT
    assert "date, time, version, identifier" in quantity_description
    assert '"My final score was 3"' in EXTRACTION_SYSTEM_PROMPT
