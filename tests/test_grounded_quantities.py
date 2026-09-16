"""Quantities remain exact, source-bound, and unit-explicit through ingestion."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from prme import MemoryEngine
from prme.ingestion.extraction import _CitedExtractionResult
from prme.ingestion.grounding import validate_grounding
from prme.ingestion.schema import ExtractedFact, ExtractedQuantity, ExtractionResult
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


def test_builtin_discards_json_float_quantity_without_losing_fact():
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
    assert result.facts[0].quantity is None


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
def test_builtin_discards_unsupported_quantity_without_losing_fact(quantity):
    source, payload = _payload(quantity)
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert len(result.facts) == 1
    assert result.facts[0].quantity is None


def test_builtin_discards_malformed_quantity_without_losing_fact():
    source, payload = _payload({"value": "NaN", "unit": "", "source_text": ""})
    result = _CitedExtractionResult.model_validate(
        payload, context={"source_text": source}
    )
    assert len(result.facts) == 1
    assert result.facts[0].quantity is None


@pytest.mark.parametrize("object_value, quantity", [
    ("3-5 hours", {"value": "3", "unit": "hours", "source_text": "3-5 hours"}),
    ("1e3 requests", {"value": "1000", "unit": "requests", "source_text": "1e3 requests"}),
    ("1,5 hours", {"value": "5", "unit": "hours", "source_text": "1,5 hours"}),
    ("about 3 hours", {"value": "3", "unit": "hours", "source_text": "about 3 hours"}),
    ("(12 USD)", {"value": "12", "unit": "USD", "source_text": "(12 USD)"}),
    ("3 tickets", {"value": "3", "unit": "1", "source_text": "3 tickets"}),
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
