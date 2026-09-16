"""Exact stored-assertion aggregation without semantic top-k truncation."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme import AssertionQuery, MemoryClient, MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig, MCPConfig
from prme.mcp.server import create_mcp_server
from prme.types import EpistemicType, LifecycleState, NodeType, RetrievalMode
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def _store_claim(
    engine,
    *,
    owner: str,
    subject: str = "I",
    predicate: str = "tried",
    object_value: str,
    polarity: str = "positive",
    year: int = 2025,
    epistemic_type: EpistemicType = EpistemicType.ASSERTED,
):
    return await engine.store_with_receipt(
        f"{subject} {predicate} {object_value}",
        user_id=owner,
        node_type=NodeType.FACT,
        metadata={
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "polarity": polarity,
        },
        event_time=datetime(year, 1, 1, tzinfo=timezone.utc),
        epistemic_type=epistemic_type,
    )


async def test_aggregate_assertions_counts_distinct_values_and_preserves_provenance(
    config, user
):
    async with MemoryEngine.open(config) as engine:
        await _store_claim(engine, owner=user, object_value="Korean")
        await _store_claim(engine, owner=user, object_value="korean", year=2024)
        await _store_claim(engine, owner=user, object_value="Italian")
        await _store_claim(engine, owner=user, object_value="Mexican", year=2023)
        await _store_claim(engine, owner=user, predicate="drinks", object_value="tea")
        await _store_claim(
            engine, owner=user, object_value="Indian", polarity="negative"
        )
        await _store_claim(
            engine,
            owner=user,
            object_value="Thai",
            epistemic_type=EpistemicType.HYPOTHETICAL,
        )
        await _store_claim(engine, owner=user + "-other", object_value="Japanese")
        await engine.store(
            "Unstructured fact",
            user_id=user,
            node_type=NodeType.FACT,
        )

        result = await engine.aggregate_assertions(
            AssertionQuery(
                subjects=[" i "],
                predicates=["TRIED"],
                group_by=["object"],
                group_limit=2,
                sample_limit=1,
            ),
            user_id=user,
            batch_size=2,
        )

        assert result.stored_set_exhaustive is True
        assert result.source_extraction_coverage == "unknown"
        assert result.semantic_equivalence == "normalized_exact_only"
        assert result.real_world_coverage == "unknown"
        assert result.consistency == "complete_for_unchanged_store"
        assert result.matched_records == 4
        assert result.distinct_count == 3
        assert result.groups_truncated is True
        assert [group.normalized_values["object"] for group in result.groups] == [
            "italian",
            "korean",
        ]
        korean = result.groups[1]
        assert korean.occurrence_count == 2
        assert korean.evidence_count == 2
        assert len(korean.sample_node_ids) == 1
        assert len(korean.sample_evidence_refs) == 1
        assert korean.samples_truncated is True
        assert result.exclusions["selector_mismatch"] == 2
        assert result.exclusions["epistemic_filtered"] == 1
        assert result.exclusions["missing_structured_assertion"] == 1


async def test_aggregate_assertions_applies_event_windows_and_explicit_mode(
    config, user
):
    async with MemoryEngine.open(config) as engine:
        await _store_claim(engine, owner=user, object_value="Korean", year=2025)
        await _store_claim(engine, owner=user, object_value="Italian", year=2024)
        await _store_claim(engine, owner=user, object_value="Mexican", year=2023)
        await _store_claim(
            engine,
            owner=user,
            object_value="Thai",
            year=2025,
            epistemic_type=EpistemicType.HYPOTHETICAL,
        )
        archived = await _store_claim(
            engine, owner=user, object_value="Spanish", year=2022
        )
        await engine.archive(str(archived.node_id), user_id=user)

        current = await engine.aggregate_assertions(
            AssertionQuery(
                predicates=["tried"],
                event_time_from=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
            user_id=user,
        )
        assert current.matched_records == 2
        assert current.distinct_count == 2
        assert current.exclusions["before_event_time_window"] == 1

        explicit = await engine.aggregate_assertions(
            AssertionQuery(
                predicates=["tried"],
                objects=["thai"],
                retrieval_mode=RetrievalMode.EXPLICIT,
            ),
            user_id=user,
        )
        assert explicit.matched_records == explicit.distinct_count == 1

        archived_only = await engine.aggregate_assertions(
            AssertionQuery(
                objects=["spanish"],
                lifecycle_states=[LifecycleState.ARCHIVED],
            ),
            user_id=user,
        )
        assert archived_only.matched_records == archived_only.distinct_count == 1


def test_assertion_query_rejects_ambiguous_or_invalid_bounds():
    with pytest.raises(ValueError, match="group_by"):
        AssertionQuery(group_by=[])

    with pytest.raises(ValueError, match="event_time_from"):
        AssertionQuery(
            event_time_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            event_time_to=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )


def test_sync_client_exposes_assertion_aggregation(config, user):
    with MemoryClient(config=config) as client:
        client.store(
            "Alice likes tea",
            user_id=user,
            node_type=NodeType.PREFERENCE,
            metadata={
                "subject": "Alice",
                "predicate": "likes",
                "object": "tea",
                "polarity": "positive",
            },
        )
        result = client.aggregate_assertions(
            AssertionQuery(subjects=["alice"], predicates=["likes"]),
            user_id=user,
        )
        assert result.matched_records == result.distinct_count == 1
        assert result.groups[0].normalized_values == {"object": "tea"}


async def test_http_aggregation_is_owner_bound_and_validated(config, user):
    async with MemoryEngine.open(config) as engine:
        await _store_claim(engine, owner=user, object_value="Korean")
        await _store_claim(engine, owner=user, object_value="korean", year=2024)
        await _store_claim(engine, owner=user + "-other", object_value="Italian")
        app = create_app(config)
        app.state.engine = engine
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/assertions/aggregate",
                json={
                    "user_id": user,
                    "query": {"predicates": ["tried"], "group_by": ["object"]},
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["matched_records"] == 2
            assert payload["distinct_count"] == 1
            assert payload["groups"][0]["normalized_values"] == {"object": "korean"}
            assert payload["stored_set_exhaustive"] is True

            assert (
                await client.post(
                    "/v1/assertions/aggregate",
                    json={"query": {}},
                )
            ).status_code == 422
            assert (
                await client.post(
                    "/v1/assertions/aggregate",
                    json={"user_id": user, "query": {"group_by": []}},
                )
            ).status_code == 422

        config.api = APIConfig(user_keys={user: "owner-token"})
        authenticated = create_app(config)
        authenticated.state.engine = engine
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(authenticated),
            base_url="http://test",
            headers={"Authorization": "Bearer owner-token"},
        ) as client:
            response = await client.post(
                "/v1/assertions/aggregate",
                json={"query": {"predicates": ["tried"]}},
            )
            assert response.status_code == 200
            assert response.json()["matched_records"] == 2
            assert (
                await client.post(
                    "/v1/assertions/aggregate",
                    json={"user_id": user + "-other", "query": {}},
                )
            ).status_code == 403

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(authenticated), base_url="http://test"
        ) as client:
            assert (
                await client.post(
                    "/v1/assertions/aggregate", json={"query": {}}
                )
            ).status_code == 401


async def test_mcp_aggregation_is_owner_bound_and_validated(config, user):
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        await _store_claim(engine, owner=user, object_value="Korean")
        await _store_claim(engine, owner=user, object_value="korean", year=2024)
        await _store_claim(engine, owner=user + "-other", object_value="Italian")

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(
            server._mcp_server,
            raise_exceptions=True,
        ) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert "memory_aggregate_assertions" in tools
            result = await session.call_tool(
                "memory_aggregate_assertions",
                {
                    "predicates": ["tried"],
                    "group_by": ["object"],
                },
            )
            payload = json.loads(result.content[0].text)
            assert payload["matched_records"] == 2
            assert payload["distinct_count"] == 1
            assert payload["groups"][0]["normalized_values"] == {"object": "korean"}
            assert payload["stored_set_exhaustive"] is True

            for arguments in (
                {"user_id": user + "-other"},
                {"group_by": []},
                {"retrieval_mode": "invalid"},
                {"event_time_from": "not-a-time"},
            ):
                invalid = await session.call_tool(
                    "memory_aggregate_assertions", arguments
                )
                assert invalid.isError or "error" in json.loads(invalid.content[0].text)
