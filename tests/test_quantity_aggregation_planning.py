"""Natural-language quantity planning stays narrow and auditable."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.retrieval.aggregation_planning import plan_quantity_aggregation
from tests import test_durable_ingestion
from tests.test_quantity_aggregation import _store_quantity


config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize(
    ("question", "reason", "prefix", "unit"),
    [
        ("How much did I raise?", "matched_amount_question", "raised", None),
        ("What's the total amount we spent?", "matched_amount_question", "spent", None),
        ("How many kilometers did I run?", "matched_count_question", "ran", "kilometers"),
    ],
)
def test_quantity_planner_returns_auditable_structured_queries(
    question, reason, prefix, unit
):
    plan = plan_quantity_aggregation(question)

    assert plan.status == "ready"
    assert plan.reason == reason
    assert plan.query is not None
    assert prefix in plan.query.predicate_prefixes
    expected_subject = "we" if " we " in question.casefold() else "I"
    assert plan.query.subjects == (expected_subject,)
    assert plan.query.units == ((unit,) if unit is not None else ())
    assert "unit_groups_are_never_converted" in plan.assumptions


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("How much did I raise for charity?", "unsupported_shape"),
        ("How much did I not raise?", "unsupported_shape"),
        ("How much will I raise?", "unsupported_shape"),
        ("How much did Alice raise?", "unsupported_shape"),
        ("How much did I frobnicate?", "unsupported_action"),
        ("How many dollars did I raise?", "unsupported_unit"),
    ],
)
def test_quantity_planner_refuses_qualifiers_and_unknown_semantics(question, reason):
    plan = plan_quantity_aggregation(question)

    assert plan.status == "unsupported"
    assert plan.reason == reason
    assert plan.query is None


def test_quantity_planner_rejects_empty_question():
    with pytest.raises(ValueError, match="nonempty"):
        plan_quantity_aggregation("  ")


async def test_planned_quantity_aggregation_executes_exact_owner_scoped_scan(
    config, user
):
    async with MemoryEngine.open(config) as engine:
        await _store_quantity(
            engine,
            owner=user,
            value="100",
            unit="$",
            source_text="$100",
            subject="I",
            predicate="raised_amount_for",
        )
        await _store_quantity(
            engine,
            owner=user,
            value="200",
            unit="$",
            source_text="$200",
            subject="we",
            predicate="helped_raise_funds_for",
        )
        await _store_quantity(
            engine,
            owner=user,
            value="50",
            unit="USD",
            source_text="50 USD",
            subject="I",
            predicate="raised",
        )
        await _store_quantity(
            engine,
            owner=user,
            value="900",
            unit="$",
            source_text="$900",
            subject="I",
            predicate="raised",
            polarity="negative",
        )
        await _store_quantity(
            engine,
            owner=user + "-other",
            value="10000",
            unit="$",
            source_text="$10000",
            subject="I",
            predicate="raised",
        )

        result = await engine.aggregate_quantities_from_text(
            "How much did I raise?", user_id=user
        )
        refused = await engine.aggregate_quantities_from_text(
            "How much did I raise for charity?", user_id=user
        )

    assert result.plan.status == "ready"
    assert result.aggregation is not None
    groups = {
        group.normalized_values["unit"]: group
        for group in result.aggregation.groups
    }
    assert groups["$"].total == Decimal("100")
    assert groups["usd"].total == Decimal("50")
    assert result.aggregation.semantic_equivalence == (
        "normalized_exact_and_predicate_prefix"
    )
    assert refused.plan.status == "unsupported"
    assert refused.aggregation is None


async def test_unsupported_quantity_plan_does_not_scan(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        aggregate_quantities = AsyncMock()
        monkeypatch.setattr(engine, "aggregate_quantities", aggregate_quantities)

        result = await engine.aggregate_quantities_from_text(
            "How much did I raise for charity?", user_id=user
        )

    assert result.plan.status == "unsupported"
    assert result.aggregation is None
    aggregate_quantities.assert_not_awaited()
