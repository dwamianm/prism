"""Exact temporal state preserves ambiguity, provenance, and every clock."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from mcp.shared.memory import create_connected_server_and_client_session

from prme import AssertionStateQuery, MemoryClient, MemoryEngine
from prme.api.app import create_app
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from prme.types import EpistemicType, NodeType, Scope
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def _store_claim(
    engine,
    *,
    owner: str,
    predicate: str,
    object_value: str,
    subject: str = "Alice",
    epistemic_type: EpistemicType = EpistemicType.ASSERTED,
):
    content = f"{subject} {predicate} {object_value}."
    receipt = await engine.store_with_receipt(
        content,
        user_id=owner,
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        epistemic_type=epistemic_type,
        event_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        metadata={
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "polarity": "positive",
            "evidence_quote": content,
        },
    )
    return receipt.node


def _query(predicate: str, *, valid_at: datetime | None = None, **kwargs):
    return AssertionStateQuery(
        subject=" alice ",
        predicate=predicate,
        scope=Scope.PERSONAL,
        valid_at=valid_at or datetime.now(timezone.utc) + timedelta(seconds=1),
        **kwargs,
    )


async def test_assertion_state_preserves_supersedence_and_unresolved_multiplicity(
    config, user
):
    async with MemoryEngine.open(config) as engine:
        old = await _store_claim(
            engine, owner=user, predicate="lives-in", object_value="Seattle"
        )
        current = await _store_claim(
            engine, owner=user, predicate="lives in", object_value="Chicago"
        )
        await engine.supersede(str(old.id), str(current.id), user_id=user)

        state = await engine.get_assertion_state(
            _query("lives_in"), user_id=user, batch_size=1
        )
        assert state.status == "single"
        assert state.matched_records == 2
        assert state.current_candidate_count == 1
        assert state.current_values[0].normalized_object == "chicago"
        assert state.current_candidates[0].node_id == current.id
        history = {entry.node_id: entry for entry in state.timeline}
        assert history[old.id].superseded_by == current.id
        assert "lifecycle_not_active:superseded" in history[old.id].exclusion_reasons
        assert "superseded_pointer" in history[old.id].exclusion_reasons
        assert state.timeline_order == "validity_then_ingestion_ascending"
        assert state.state_semantics == "eligible_claims_not_verified_truth"

        coexist = await _store_claim(
            engine, owner=user, predicate="lives_in", object_value="Suburbs"
        )
        state = await engine.get_assertion_state(_query("lives-in"), user_id=user)
        assert state.status == "multiple"
        assert state.current_candidate_count == 2
        assert {value.normalized_object for value in state.current_values} == {
            "chicago",
            "suburbs",
        }
        assert coexist.id in {entry.node_id for entry in state.current_candidates}
        assert state.conflict_count == 0


async def test_assertion_state_distinguishes_contested_and_resolved_claims(config, user):
    async with MemoryEngine.open(config) as engine:
        first = await _store_claim(
            engine, owner=user, predicate="office", object_value="Austin"
        )
        second = await _store_claim(
            engine, owner=user, predicate="office", object_value="Boston"
        )
        await engine.contradict(str(first.id), str(second.id), user_id=user)

        contested = await engine.get_assertion_state(_query("office"), user_id=user)
        assert contested.status == "contested"
        assert contested.current_candidate_count == 2
        assert contested.conflict_count == 1
        assert contested.conflicts[0].active_between_current_candidates is True
        assert contested.current_candidates[0].contradiction_node_ids

        await engine.resolve_contradiction(
            str(second.id), str(first.id), user_id=user
        )
        resolved = await engine.get_assertion_state(_query("office"), user_id=user)
        assert resolved.status == "single"
        assert resolved.current_candidate_count == 1
        assert resolved.current_candidates[0].node_id == second.id
        assert resolved.conflicts[0].active_between_current_candidates is False
        loser = next(entry for entry in resolved.timeline if entry.node_id == first.id)
        assert "lifecycle_not_active:deprecated" in loser.exclusion_reasons


async def test_assertion_state_reports_validity_and_historical_boundaries(config, user):
    async with MemoryEngine.open(config) as engine:
        node = await _store_claim(
            engine, owner=user, predicate="timezone", object_value="Central"
        )
        before_validity = await engine.get_assertion_state(
            _query("timezone", valid_at=node.valid_from - timedelta(microseconds=1)),
            user_id=user,
        )
        assert before_validity.status == "unknown"
        assert before_validity.timeline[0].exclusion_reasons == (
            "outside_validity_window",
        )

        before_ingestion = await engine.get_assertion_state(
            _query(
                "timezone",
                knowledge_at=node.created_at - timedelta(microseconds=1),
            ),
            user_id=user,
        )
        assert before_ingestion.matched_records == 1
        assert before_ingestion.current_candidate_count == 0
        assert before_ingestion.timeline == ()
        assert before_ingestion.exclusions["after_knowledge_cutoff"] == 1
        assert before_ingestion.historical_coverage is not None
        assert before_ingestion.historical_coverage.exact_snapshot is False


async def test_assertion_state_applies_epistemic_rules_and_reports_truncation(
    config, user
):
    async with MemoryEngine.open(config) as engine:
        await _store_claim(
            engine, owner=user, predicate="locale", object_value="Chicago"
        )
        await _store_claim(
            engine, owner=user, predicate="locale", object_value="chicago"
        )
        await _store_claim(
            engine,
            owner=user,
            predicate="locale",
            object_value="Boston",
            epistemic_type=EpistemicType.HYPOTHETICAL,
        )

        state = await engine.get_assertion_state(
            _query("locale", limit=1), user_id=user
        )
        assert state.status == "consistent"
        assert state.current_candidate_count == 2
        assert state.current_values[0].candidate_count == 2
        assert state.current_values[0].normalized_object == "chicago"
        assert state.current_candidates_truncated is True
        assert state.timeline_truncated is True
        assert state.exclusions["epistemic_filtered"] == 1


def test_sync_client_exposes_assertion_state(config, user):
    with MemoryClient(config=config) as client:
        content = "Alice locale Chicago."
        client.store(
            content,
            user_id=user,
            node_type=NodeType.FACT,
            metadata={
                "subject": "Alice",
                "predicate": "locale",
                "object": "Chicago",
                "polarity": "positive",
            },
        )
        state = client.get_assertion_state(_query("locale"), user_id=user)
        assert state.status == "single"
        assert state.current_values[0].object == "Chicago"


async def test_http_and_mcp_expose_owner_bound_assertion_state(config, user):
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        await _store_claim(
            engine, owner=user, predicate="locale", object_value="Chicago"
        )
        await _store_claim(
            engine, owner=user + "-other", predicate="locale", object_value="Paris"
        )
        valid_at = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()

        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/assertions/state", json={
                "user_id": user,
                "query": {
                    "subject": "Alice",
                    "predicate": "locale",
                    "scope": "personal",
                    "valid_at": valid_at,
                },
            })
            assert response.status_code == 200
            assert response.json()["current_values"][0]["object"] == "Chicago"
            assert (await client.post(
                "/v1/assertions/state",
                json={"user_id": user, "query": {"subject": "Alice"}},
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
            assert "memory_get_assertion_state" in tools
            result = await session.call_tool("memory_get_assertion_state", {
                "subject": "Alice",
                "predicate": "locale",
                "scope": "personal",
                "valid_at": valid_at,
            })
            payload = json.loads(result.content[0].text)
            assert payload["status"] == "single"
            assert payload["current_values"][0]["object"] == "Chicago"

            invalid = await session.call_tool("memory_get_assertion_state", {
                "subject": "Alice",
                "predicate": "locale",
                "scope": "personal",
                "valid_at": "now",
            })
            assert invalid.isError or "error" in json.loads(invalid.content[0].text)
