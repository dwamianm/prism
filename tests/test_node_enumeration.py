"""Complete scoped enumeration must not silently stop at retrieval's top k."""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from prme import MemoryClient, MemoryEngine
from prme.api.app import create_app
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from prme.models import MemoryNode
from prme.types import LifecycleState, NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_enumeration_crosses_many_pages_with_tied_dates_and_tenant_filters(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        expected = []
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # Unique random UUID prefix per tenant avoids collisions in shared PG tests.
        from uuid import uuid4
        prefix = uuid4().int & ~((1 << 16) - 1)
        for i in range(253):
            node = MemoryNode(id=UUID(int=prefix + i), user_id=user if i % 5 else user + "-other",
                              scope=Scope.PERSONAL if i % 3 else Scope.PROJECT,
                              node_type=NodeType.FACT, content=f"A complete source {i}",
                              created_at=timestamp, updated_at=timestamp)
            await engine._graph_store.create_node(node)
            if node.user_id == user and node.scope == Scope.PERSONAL:
                expected.append(node.id)
        assert len(expected) > 100
        nodes = [n async for n in engine.iter_nodes(user_id=user, scope=Scope.PERSONAL,
                                                    node_type=NodeType.FACT, batch_size=7)]
        assert [n.id for n in nodes] == expected
        first = await engine.scan_nodes(user_id=user, scope=Scope.PERSONAL, limit=7)
        second = await engine.scan_nodes(user_id=user, scope=Scope.PERSONAL,
                                         after_id=str(first[-1].id), limit=7)
        assert [n.id for n in first + second] == expected[:14]


async def test_enumeration_lifecycle_filters_and_empty_filter(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        for state in (LifecycleState.TENTATIVE, LifecycleState.ARCHIVED):
            await engine._graph_store.create_node(MemoryNode(
                user_id=user, node_type=NodeType.FACT, content=state.value, lifecycle_state=state,
            ))
        active = [n async for n in engine.iter_nodes(user_id=user, batch_size=1)]
        assert [n.lifecycle_state for n in active] == [LifecycleState.TENTATIVE]
        all_nodes = [n async for n in engine.iter_nodes(user_id=user, lifecycle_states=list(LifecycleState), batch_size=1)]
        assert len(all_nodes) == 2
        assert [n async for n in engine.iter_nodes(user_id=user, lifecycle_states=[])] == []


async def test_enumeration_rejects_missing_tenant_and_invalid_page_size(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        for kwargs in ({"user_id": ""}, {"user_id": user, "batch_size": 0}, {"user_id": user, "batch_size": -1}):
            with pytest.raises(ValueError):
                _ = [n async for n in engine.iter_nodes(**kwargs)]
        with pytest.raises(ValueError):
            await engine.scan_nodes(user_id=user, after_id="not-a-cursor")


def test_sync_client_supports_complete_iteration_and_explicit_pages(config, user):  # noqa: F811
    with MemoryClient(config=config) as client:
        for i in range(5):
            client.store(f"Memory number {i}", user_id=user, node_type=NodeType.NOTE)
        client.store("Other tenant's memory", user_id=user + "-other", node_type=NodeType.NOTE)
        nodes = list(client.iter_nodes(user_id=user, node_type=NodeType.NOTE, batch_size=2))
        assert len(nodes) == 5 and len({n.id for n in nodes}) == 5
        assert all(n.user_id == user for n in nodes)
        assert [n.id for n in client.scan_nodes(user_id=user, node_type=NodeType.NOTE, limit=2)] == [n.id for n in nodes[:2]]


async def test_http_scan_exposes_complete_filtered_pages(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        from uuid import uuid4

        prefix = uuid4().int & ~((1 << 8) - 1)
        specifications = (
            (user, Scope.PERSONAL, NodeType.FACT, LifecycleState.TENTATIVE),
            (user, Scope.PERSONAL, NodeType.NOTE, LifecycleState.TENTATIVE),
            (user, Scope.PROJECT, NodeType.FACT, LifecycleState.TENTATIVE),
            (user, Scope.PERSONAL, NodeType.FACT, LifecycleState.ARCHIVED),
            (user, Scope.PERSONAL, NodeType.FACT, LifecycleState.STABLE),
            (user + "-other", Scope.PERSONAL, NodeType.FACT, LifecycleState.TENTATIVE),
        )
        for index, (owner, scope, node_type, state) in enumerate(specifications):
            await engine._graph_store.create_node(MemoryNode(
                id=UUID(int=prefix + index), user_id=owner, scope=scope,
                node_type=node_type, lifecycle_state=state, content=f"record {index}",
            ))
        expected = [str(UUID(int=prefix + index)) for index in (0, 3, 4)]
        app = create_app(config)
        app.state.engine = engine
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            params = [
                ("user_id", user), ("scope", "personal"), ("type", "fact"),
                ("state", "tentative"), ("state", "archived"), ("state", "stable"),
                ("limit", "2"),
            ]
            first = (await client.get("/v1/nodes/scan", params=params)).json()
            assert [node["id"] for node in first["nodes"]] == expected[:2]
            assert first["count"] == 2 and first["has_more"] is True
            assert first["next_cursor"] == expected[1]
            assert first["order"] == "id" and first["consistency"] == "page"
            assert first["nodes"][0]["decay_profile"] == "medium"
            second = (await client.get(
                "/v1/nodes/scan", params=[*params, ("after_id", first["next_cursor"])],
            )).json()
            assert [node["id"] for node in second["nodes"]] == expected[2:]
            assert second["count"] == 1 and second["has_more"] is False
            assert second["next_cursor"] is None
            assert (await client.get("/v1/nodes/scan")).status_code == 422
            assert (await client.get(
                "/v1/nodes/scan", params={"user_id": user, "after_id": "bad-cursor"},
            )).status_code == 422


async def test_mcp_scan_exposes_owner_bound_pages_and_validates_filters(config, user):  # noqa: F811
    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        for index in range(3):
            await engine._graph_store.create_node(MemoryNode(
                user_id=user, node_type=NodeType.FACT, content=f"owned record {index}",
            ))
        await engine._graph_store.create_node(MemoryNode(
            user_id=user + "-other", node_type=NodeType.FACT, content="foreign record",
        ))

        @asynccontextmanager
        async def lifespan(server):
            yield {"engine": engine}

        server = create_mcp_server(config, lifespan=lifespan)
        async with create_connected_server_and_client_session(
            server._mcp_server, raise_exceptions=True,
        ) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert "memory_scan_nodes" in tools
            first_result = await session.call_tool("memory_scan_nodes", {
                "node_type": "fact", "limit": 2,
            })
            first = json.loads(first_result.content[0].text)
            assert first["count"] == 2 and first["has_more"] is True
            assert first["next_cursor"] == first["nodes"][-1]["id"]
            assert first["order"] == "id" and first["consistency"] == "page"
            assert all(node["user_id"] == user for node in first["nodes"])
            assert first["nodes"][0]["decay_profile"] == "medium"
            second_result = await session.call_tool("memory_scan_nodes", {
                "node_type": "fact", "limit": 2, "after_id": first["next_cursor"],
            })
            second = json.loads(second_result.content[0].text)
            assert second["count"] == 1 and second["has_more"] is False
            assert second["next_cursor"] is None
            empty = await session.call_tool("memory_scan_nodes", {"lifecycle_states": []})
            assert json.loads(empty.content[0].text)["nodes"] == []
            for arguments in (
                {"user_id": user + "-other"}, {"scope": "invalid"},
                {"node_type": "invalid"}, {"lifecycle_states": ["invalid"]},
                {"after_id": "bad-cursor"}, {"limit": 1001},
            ):
                result = await session.call_tool("memory_scan_nodes", arguments)
                assert result.isError or "error" in json.loads(result.content[0].text)
