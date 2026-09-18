"""Exact stored-quantity aggregation never mixes or converts units."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme import AssertionQuery, MemoryClient, MemoryEngine, QuantityAggregationQuery
from prme.api.app import create_app
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from prme.types import EpistemicType, NodeType
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def _store_quantity(
    engine,
    *,
    owner: str,
    value: str,
    unit: str,
    source_text: str,
    subject: str = "Alice",
    predicate: str = "spent",
    polarity: str = "positive",
    epistemic_type: EpistemicType = EpistemicType.ASSERTED,
    year: int = 2025,
    stored_value: str | None = None,
):
    content = f"{subject} {predicate} {source_text}."
    return await engine.store_with_receipt(
        content,
        user_id=owner,
        node_type=NodeType.FACT,
        epistemic_type=epistemic_type,
        event_time=datetime(year, 1, 1, tzinfo=timezone.utc),
        metadata={
            "subject": subject,
            "predicate": predicate,
            "object": source_text,
            "polarity": polarity,
            "evidence_quote": content,
            "quantity": {
                "value": stored_value if stored_value is not None else value,
                "unit": unit,
                "source_text": source_text,
                "grounding": "object_decimal_v1",
            },
        },
    )


async def test_quantity_aggregation_is_exact_auditable_and_unit_separated(config, user):
    async with MemoryEngine.open(config) as engine:
        await _store_quantity(
            engine, owner=user, value="0.1", unit="$", source_text="$0.1"
        )
        await _store_quantity(
            engine, owner=user, value="0.2", unit="$", source_text="$0.2", year=2024
        )
        await _store_quantity(
            engine, owner=user, value="3", unit="USD", source_text="3 USD"
        )
        await _store_quantity(
            engine, owner=user, value="7", stored_value="8", unit="$", source_text="$7"
        )
        await _store_quantity(
            engine, owner=user, value="9", unit="$", source_text="$9",
            polarity="negative",
        )
        await _store_quantity(
            engine, owner=user, value="10", unit="$", source_text="$10",
            epistemic_type=EpistemicType.HYPOTHETICAL,
        )
        await _store_quantity(
            engine, owner=user + "-other", value="100", unit="$", source_text="$100"
        )
        await engine.store(
            "Alice spent an unspecified amount.",
            user_id=user,
            node_type=NodeType.FACT,
            metadata={
                "subject": "Alice",
                "predicate": "spent",
                "object": "an unspecified amount",
                "polarity": "positive",
            },
        )

        result = await engine.aggregate_quantities(
            QuantityAggregationQuery(
                subjects=["alice"],
                predicates=["spent"],
                group_by=["unit"],
                sample_limit=1,
            ),
            user_id=user,
            batch_size=2,
        )

        assert result.matched_quantity_records == 3
        assert result.distinct_count == 2
        assert result.unit_conversion == "none"
        assert result.stored_set_exhaustive is True
        assert result.source_extraction_coverage == "unknown"
        assert result.semantic_equivalence == "normalized_exact_only"
        assert result.real_world_coverage == "unknown"
        groups = {group.normalized_values["unit"]: group for group in result.groups}
        dollars = groups["$"]
        assert dollars.value_count == 2
        assert dollars.total == Decimal("0.3")
        assert dollars.minimum == Decimal("0.1")
        assert dollars.maximum == Decimal("0.2")
        assert dollars.evidence_count == 2
        assert len(dollars.samples) == 1
        assert dollars.samples[0].source_text in {"$0.1", "$0.2"}
        assert len(dollars.samples[0].evidence_refs) == 1
        assert dollars.samples_truncated is True
        assert groups["usd"].total == Decimal("3")
        assert result.exclusions["invalid_quantity"] == 1
        assert result.exclusions["missing_quantity"] == 1
        assert result.exclusions["selector_mismatch"] == 1
        assert result.exclusions["epistemic_filtered"] == 1


async def test_quantity_total_does_not_round_beyond_decimal_context(config, user):
    value = "9" * 38
    expected = Decimal(str(int(value) * 2))
    async with MemoryEngine.open(config) as engine:
        for _ in range(2):
            await _store_quantity(
                engine,
                owner=user,
                value=value,
                unit="widgets",
                source_text=f"{value} widgets",
                predicate="counted",
            )
        result = await engine.aggregate_quantities(
            QuantityAggregationQuery(predicates=["counted"]),
            user_id=user,
        )
        assert result.groups[0].total == expected
        assert result.model_dump(mode="json")["groups"][0]["total"] == str(expected)


async def test_quantity_unit_selector_is_normalized_without_conversion(config, user):
    async with MemoryEngine.open(config) as engine:
        await _store_quantity(
            engine, owner=user, value="5", unit="KG", source_text="5 KG",
            predicate="lifted",
        )
        result = await engine.aggregate_quantities(
            QuantityAggregationQuery(predicates=["lifted"], units=[" kg "]),
            user_id=user,
        )
        assert result.matched_quantity_records == 1
        assert result.groups[0].values["unit"] == "KG"
        assert result.groups[0].normalized_values["unit"] == "kg"


async def test_quantity_predicate_prefix_is_explicit_and_token_bounded(config, user):
    async with MemoryEngine.open(config) as engine:
        await _store_quantity(
            engine,
            owner=user,
            value="250",
            unit="$",
            source_text="$250",
            predicate="raised_amount_for_beneficiary",
        )
        await _store_quantity(
            engine,
            owner=user,
            value="900",
            unit="$",
            source_text="$900",
            predicate="fundraised",
        )

        result = await engine.aggregate_quantities(
            QuantityAggregationQuery(predicate_prefixes=["raised"]),
            user_id=user,
        )

        assert result.matched_quantity_records == 1
        assert result.groups[0].total == Decimal("250")
        assert (
            result.semantic_equivalence
            == "normalized_exact_and_predicate_prefix"
        )
        assert result.exclusions["selector_mismatch"] == 1


def test_quantity_query_requires_unit_in_every_group():
    with pytest.raises(ValueError, match="must include unit"):
        QuantityAggregationQuery(group_by=["predicate"])


def test_aggregation_queries_round_trip_default_all_scopes():
    assertion = AssertionQuery()
    quantity = QuantityAggregationQuery()

    assert AssertionQuery.model_validate_json(assertion.model_dump_json()) == assertion
    assert QuantityAggregationQuery.model_validate_json(
        quantity.model_dump_json()
    ) == quantity


def test_sync_client_exposes_quantity_aggregation(config, user):
    with MemoryClient(config=config) as client:
        content = "I spent $4.50."
        client.store(
            content,
            user_id=user,
            node_type=NodeType.FACT,
            metadata={
                "subject": "I",
                "predicate": "spent",
                "object": "$4.50",
                "polarity": "positive",
                "evidence_quote": content,
                "quantity": {
                    "value": "4.50",
                    "unit": "$",
                    "source_text": "$4.50",
                    "grounding": "object_decimal_v1",
                },
            },
        )
        result = client.aggregate_quantities(
            QuantityAggregationQuery(predicates=["spent"]), user_id=user
        )
        assert result.groups[0].total == Decimal("4.50")
        planned = client.aggregate_quantities_from_text(
            "How much did I spend?", user_id=user
        )
        assert planned.plan.status == "ready"
        assert planned.aggregation is not None
        assert planned.aggregation.groups[0].total == Decimal("4.50")


async def test_http_and_mcp_expose_owner_bound_quantity_totals(config, user):
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        await _store_quantity(
            engine, owner=user, value="4.50", unit="$", source_text="$4.50",
            subject="I",
        )
        await _store_quantity(
            engine, owner=user + "-other", value="100", unit="$", source_text="$100",
            subject="I",
        )

        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/quantities/aggregate", json={
                "user_id": user,
                "query": {"predicates": ["spent"]},
            })
            assert response.status_code == 200
            assert response.json()["groups"][0]["total"] == "4.50"
            prefix_response = await client.post(
                "/v1/quantities/aggregate",
                json={
                    "user_id": user,
                    "query": {"predicate_prefixes": ["spent"]},
                },
            )
            assert prefix_response.status_code == 200
            assert (
                prefix_response.json()["semantic_equivalence"]
                == "normalized_exact_and_predicate_prefix"
            )
            text_response = await client.post(
                "/v1/quantities/aggregate-text",
                json={
                    "user_id": user,
                    "question": "How much did I spend?",
                },
            )
            assert text_response.status_code == 200
            assert text_response.json()["plan"]["status"] == "ready"
            assert text_response.json()["aggregation"]["groups"][0]["total"] == "4.50"
            refused_response = await client.post(
                "/v1/quantities/aggregate-text",
                json={
                    "user_id": user,
                    "question": "How much did I spend on lunch?",
                },
            )
            assert refused_response.status_code == 200
            assert refused_response.json()["plan"]["status"] == "unsupported"
            assert refused_response.json()["aggregation"] is None
            assert (await client.post(
                "/v1/quantities/aggregate",
                json={"user_id": user, "query": {"group_by": ["predicate"]}},
            )).status_code == 422

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(
            server._mcp_server, raise_exceptions=True,
        ) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert "memory_aggregate_quantities" in tools
            assert "memory_aggregate_quantities_from_text" in tools
            result = await session.call_tool(
                "memory_aggregate_quantities", {"predicates": ["spent"]}
            )
            payload = json.loads(result.content[0].text)
            assert payload["groups"][0]["total"] == "4.50"
            assert payload["matched_quantity_records"] == 1
            prefix_result = await session.call_tool(
                "memory_aggregate_quantities",
                {"predicate_prefixes": ["spent"]},
            )
            prefix_payload = json.loads(prefix_result.content[0].text)
            assert (
                prefix_payload["semantic_equivalence"]
                == "normalized_exact_and_predicate_prefix"
            )
            text_result = await session.call_tool(
                "memory_aggregate_quantities_from_text",
                {"question": "How much did I spend?"},
            )
            text_payload = json.loads(text_result.content[0].text)
            assert text_payload["plan"]["status"] == "ready"
            assert text_payload["aggregation"]["groups"][0]["total"] == "4.50"
            for arguments in (
                {"user_id": user + "-other"},
                {"group_by": ["predicate"]},
                {"units": [""]},
            ):
                invalid = await session.call_tool(
                    "memory_aggregate_quantities", arguments
                )
                assert invalid.isError or "error" in json.loads(invalid.content[0].text)
