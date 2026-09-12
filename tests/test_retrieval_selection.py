"""Selection affects both exposed results and packed context, including APIs."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme import MemoryEngine
from prme.api.app import create_app
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from prme.types import EpistemicType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_selection_filters_context_and_records_exclusions(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope studies Saturn's rings", user_id=user)
        await engine.store("The project uses PostgreSQL databases", user_id=user)
        now = datetime.now(timezone.utc)
        kwargs = {"user_id": user, "reference_time": now}
        original = await engine.retrieve("telescope Saturn rings", **kwargs)
        assert len(original.results) == 2
        best = original.results[0]
        assert best.composite_score > original.results[1].composite_score
        selected = await engine.retrieve("telescope Saturn rings", min_score=best.composite_score, **kwargs)
        assert [c.node.id for c in selected.results] == [best.node.id]
        assert selected.bundle.included_count == 1
        assert len(selected.excluded) == 1 and selected.excluded[0].reason == "below_threshold"
        assert original.results[1].node.content not in selected.bundle.rendered_context
        assert selected.score_traces == [best.score_trace]
        assert selected.metadata.min_score == best.composite_score
        limited = await engine.retrieve("telescope Saturn rings", limit=1, **kwargs)
        assert len(limited.results) == 1 and limited.bundle.included_count == 1
        assert limited.excluded[0].reason == "result_limit"
        for bounds in ({"limit": 0}, {"min_score": 2}):
            empty = await engine.retrieve("telescope Saturn rings", **kwargs, **bounds)
            assert empty.results == [] and empty.bundle.included_count == 0
            assert not empty.bundle.rendered_context and empty.score_traces == []
        # Request overrides do not mutate defaults for the next caller.
        assert len((await engine.retrieve("telescope Saturn rings", **kwargs)).results) == 2


@pytest.mark.parametrize("bounds", [{"min_score": -1}, {"min_score": float("nan")},
                                     {"min_score": float("inf")}, {"limit": -1}, {"limit": 1.5}, {"limit": True}])
async def test_invalid_selection_cannot_drain_pending_work(config, user, bounds):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        event = await engine.ingest_fast("A pending source", user_id=user)
        with pytest.raises(ValueError):
            await engine.retrieve("source", user_id=user, **bounds)
        assert (await engine.processing_status(event, user_id=user)).attempts == 0


async def test_http_filters_mode_and_limit_are_applied(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await engine.store("Personal telescope", user_id=user, scope=Scope.PERSONAL)
        await engine.store("Project telescope", user_id=user, scope=Scope.PROJECT)
        await engine.store("Hypothetical project telescope", user_id=user, scope=Scope.PROJECT,
                           epistemic_type=EpistemicType.HYPOTHETICAL)
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            body = {"query": "telescope", "user_id": user,
                    "filters": {"scope": "project", "include_cross_scope": False}}
            response = await client.post("/v1/retrieve", json=body)
            assert response.status_code == 200
            assert [r["content"] for r in response.json()["results"]] == ["Project telescope"]
            response = await client.post("/v1/retrieve", json={**body, "mode": "explicit"})
            assert response.status_code == 200 and len(response.json()["results"]) == 2
            for override in ({"limit": 0}, {"min_score": 2}, {"filters": {"knowledge_at": "2000-01-01T00:00:00Z"}}):
                response = await client.post("/v1/retrieve", json={**body, **override})
                assert response.status_code == 200 and response.json()["results"] == []
                assert response.json()["bundle"]["included_count"] == 0
            for override in ({"limit": -1}, {"min_score": -1}, {"mode": "typo"},
                             {"filters": {"scpoe": "project"}}, {"filters": {"user_id": "foreign"}},
                             {"filters": {"knowledge_at": "yesterday"}}, {"min_socre": 0.5}):
                assert (await client.post("/v1/retrieve", json={**body, **override})).status_code == 422


async def test_mcp_selection_contract(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await engine.store("A telescope observation", user_id=user)
    config.mcp = MCPConfig(user_id=user)
    server = create_mcp_server(config)
    async with create_connected_server_and_client_session(server._mcp_server, raise_exceptions=True) as session:
        await session.initialize()
        for bounds in ({"limit": 0}, {"min_score": 2}):
            response = await session.call_tool("memory_retrieve", {"query": "telescope", **bounds})
            result = json.loads(response.content[0].text)
            assert result["results"] == [] and result["count"] == 0
        response = await session.call_tool("memory_retrieve", {"query": "telescope", "min_score": -1})
        assert "min_score" in json.loads(response.content[0].text)["error"]
