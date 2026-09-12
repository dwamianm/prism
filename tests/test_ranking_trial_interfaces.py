"""Ranking trials preserve request semantics across authenticated HTTP and MCP."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme import MemoryEngine, RankingMultipliers
from prme.config import MCPConfig, PRMEConfig, APIConfig
from prme.api.app import create_app
from prme.mcp.server import create_mcp_server
from prme.types import RepresentationLevel, RetrievalMode, Scope
from tests import test_durable_ingestion
from tests.test_http_write_fidelity import app_for, client_for

config = test_durable_ingestion.config
user = test_durable_ingestion.user
NOW = datetime(2026, 9, 12, 18, tzinfo=timezone.utc)
ADJUSTMENT = RankingMultipliers(semantic=.25, lexical=4, graph=.25)


async def seed(engine, user):
    for scope, owner, text in (
        (Scope.PROJECT, user, "The telescope is blue."),
        (Scope.PROJECT, user, "The telescope has a red lens."),
        (Scope.PERSONAL, user, "Private telescope note."),
        (Scope.PROJECT, user + "-other", "Foreign telescope note."),
    ):
        await engine.store(text, user_id=owner, scope=scope, event_time=NOW - timedelta(days=1))


def arguments(user):
    return dict(user_id=user, scope=[Scope.PROJECT], reference_time=NOW,
                time_from=NOW-timedelta(days=2), time_to=NOW+timedelta(days=1),
                knowledge_at=NOW, event_time_from=NOW-timedelta(days=2), event_time_to=NOW,
                include_cross_scope=False, min_fidelity=RepresentationLevel.FULL,
                retrieval_mode=RetrievalMode.DEFAULT, token_budget=1024, limit=2, min_score=0)


def transport_arguments(user):
    args = arguments(user)
    args.pop("user_id")
    args["scope"] = ["project"]
    args["mode"] = args.pop("retrieval_mode").value
    args["min_fidelity"] = args["min_fidelity"].value
    for key, value in list(args.items()):
        if isinstance(value, datetime):
            args[key] = value.isoformat()
    return args


async def test_http_trial_matches_python_scores_context_and_receipt(config, user):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        original_weights = engine._config.scoring.model_dump_json()
        baseline = await engine.retrieve("blue telescope", **arguments(user))
        expected = await engine.retrieve("blue telescope", **arguments(user), ranking_multipliers=ADJUSTMENT)
        assert baseline.results and expected.results
        assert [c.composite_score for c in baseline.results] != [c.composite_score for c in expected.results]
        body = transport_arguments(user)
        body["query"] = "blue telescope"
        body["filters"] = {key: body.pop(key) for key in (
            "scope", "time_from", "time_to", "knowledge_at", "event_time_from", "event_time_to", "include_cross_scope")}
        async with client_for(app_for(config, engine, user)) as client:
            response = await client.post("/v1/retrieve", json={**body, "ranking_multipliers": ADJUSTMENT.model_dump()})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["bundle"] == expected.bundle.model_dump(mode="json")
            assert [(item["node_id"], item["score"]) for item in data["results"]] == [
                (str(c.node.id), c.composite_score) for c in expected.results]
            receipt = await engine.get_retrieval_receipt(data["metrics"]["request_id"], user_id=user)
            assert receipt.execution.parameters["ranking_multipliers"] == ADJUSTMENT.model_dump()
            assert receipt.replay_ranking() == tuple(c.node.id for c in expected.results)
            foreign = await client.get(f'/v1/retrievals/{receipt.request_id}', headers={"Authorization": "Bearer other-token"})
            assert foreign.status_code == 404
            unchanged = (await client.post("/v1/retrieve", json=body)).json()
            assert [item["score"] for item in unchanged["results"]] == [c.composite_score for c in baseline.results]
        assert engine._config.scoring.model_dump_json() == original_weights


async def test_mcp_trial_matches_python_context_and_retains_owned_receipt(config, user):
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        expected = await engine.retrieve("blue telescope", **arguments(user), ranking_multipliers=ADJUSTMENT)
        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}
        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
            await session.initialize()
            tool = next(tool for tool in (await session.list_tools()).tools if tool.name == "memory_retrieve")
            assert {"ranking_multipliers", "reference_time", "include_context"} <= tool.inputSchema["properties"].keys()
            args = {"query": "blue telescope", **transport_arguments(user),
                    "ranking_multipliers": ADJUSTMENT.model_dump(), "include_context": True}
            response = await session.call_tool("memory_retrieve", args)
            assert not response.isError, response
            data = json.loads(response.content[0].text)
            assert "error" not in data, data
            assert data["context"] == expected.bundle.render()
            assert "Private telescope" not in data["context"] and "Foreign telescope" not in data["context"]
            receipt = await engine.get_retrieval_receipt(data["metrics"]["request_id"], user_id=user)
            assert receipt.execution.parameters["ranking_multipliers"] == ADJUSTMENT.model_dump()
            assert [c.score for c in receipt.candidates] == [c.composite_score for c in expected.results]
            assert receipt.replay_ranking() == tuple(c.node.id for c in expected.results)
            forbidden = await session.call_tool("memory_retrieve", {**args, "user_id": user + "-other"})
            assert "error" in json.loads(forbidden.content[0].text)
            ordinary = await session.call_tool("memory_retrieve", {"query": "blue telescope"})
            assert "context" not in json.loads(ordinary.content[0].text)


@pytest.mark.parametrize("extra", [
    {"ranking_multipliers": {"lexical": 0}},
    {"ranking_multipliers": {"lexical": 4.01}},
    {"ranking_multipliers": {"lexical": "NaN"}},
    {"ranking_multipliers": {"lexcial": 2}},
    {"reference_time": "2026-09-12T18:00:00"},
    {"min_fidelity": "made_up"},
])
async def test_http_invalid_trial_is_rejected_before_engine(extra):
    config = PRMEConfig(api=APIConfig(user_keys={"alice": "owner-token"}))
    app = create_app(config)
    spy = AsyncMock()
    app.state.engine = spy
    async with client_for(app) as client:
        response = await client.post("/v1/retrieve", json={"query": "telescope", **extra})
    assert response.status_code == 422
    spy.retrieve.assert_not_awaited()


async def test_mcp_invalid_trials_do_not_execute_retrieval(config, user, monkeypatch):
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        spy = AsyncMock(side_effect=AssertionError("invalid trials must not reach the engine"))
        monkeypatch.setattr(engine, "retrieve", spy)
        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}
        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
            await session.initialize()
            for extra in (
                {"ranking_multipliers": {"lexical": 0}}, {"ranking_multipliers": {"lexical": 5}},
                {"ranking_multipliers": {"lexical": "NaN"}}, {"ranking_multipliers": {"lexcial": 2}},
                {"reference_time": "2026-09-12T18:00:00"}, {"event_time_from": "2024-01-01"},
                {"min_fidelity": "made_up"}, {"scope": []}, {"include_context": "false"},
                {"include_cross_scope": "false"},
            ):
                response = await session.call_tool("memory_retrieve", {"query": "telescope", **extra})
                assert response.isError or "error" in json.loads(response.content[0].text), extra
            spy.assert_not_awaited()
