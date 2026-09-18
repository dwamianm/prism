"""Source lookup and store receipts follow provenance, never latest-node order."""

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from prme import MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig
from prme.models import MemoryNode
from prme.types import LifecycleState, NodeType
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_event_resolution_is_complete_scoped_and_survives_restart(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.store("source with its full qualification", user_id=user)
        original = (await engine.get_event_nodes(event_id, user_id=user))[0]
        derived = MemoryNode(user_id=user, node_type=NodeType.FACT, content="derived claim",
                             lifecycle_state=LifecycleState.ARCHIVED, evidence_refs=[UUID(event_id)])
        foreign = MemoryNode(user_id=user + "-other", node_type=NodeType.FACT,
                             content="other tenant", evidence_refs=[UUID(event_id)])
        for node in (derived, foreign):
            await engine._graph_store.create_node(node)
        # More recent nodes do not affect the event's correspondence.
        for i in range(105):
            await engine._graph_store.create_node(MemoryNode(user_id=user, node_type=NodeType.NOTE, content=f"noise {i}"))
        assert {n.id for n in await engine.get_event_nodes(event_id, user_id=user)} == {original.id, derived.id}
        assert await engine.get_event(event_id, user_id=user + "-other") is None
        assert await engine.get_event_nodes(event_id, user_id=user + "-other") == []
        assert await engine.get_event_nodes(str(uuid4()), user_id=user) == []
        with pytest.raises(ValueError, match="user_id"):
            await engine.get_event_nodes(event_id, user_id="")
    async with MemoryEngine.open(config) as engine:
        assert {n.id for n in await engine.get_event_nodes(event_id.upper(), user_id=user)} == {original.id, derived.id}
        assert (await engine.get_event(event_id, user_id=user)).content == "source with its full qualification"


async def test_api_receipt_and_source_reads_are_exact_under_interleaved_writes(config, user, monkeypatch):  # noqa: F811
    config.api = APIConfig(user_keys={user: "owner-token", user + "-other": "other-token"})
    async with MemoryEngine.open(config) as engine:
        app = create_app(config)
        app.state.engine = engine
        original_store = engine.store

        async def interleaved_store(*args, **kwargs):
            event_id = await original_store(*args, **kwargs)
            await original_store("later unrelated memory", user_id=kwargs["user_id"])
            return event_id

        monkeypatch.setattr(engine, "store", interleaved_store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test",
                                     headers={"Authorization": "Bearer owner-token"}) as client:
            responses = await asyncio.gather(*[
                client.post("/v1/store", json={"content": f"qualified source {i}"}) for i in range(5)
            ])
            for i, response in enumerate(responses):
                assert response.status_code == 200
                receipt = response.json()
                node = await engine.get_node(receipt["node_id"], user_id=user)
                assert node.content == f"qualified source {i}"
                assert node.evidence_refs == [UUID(receipt["event_id"])]
                event_url = f'/v1/events/{receipt["event_id"]}'
                source = await client.get(event_url)
                assert source.status_code == 200 and source.json()["content"] == node.content
                nodes = await client.get(event_url + "/nodes")
                assert [n["id"] for n in nodes.json()["nodes"]] == [str(node.id)]
                for path in (event_url, event_url + "/nodes"):
                    assert (await client.get(path, headers={"Authorization": "Bearer other-token"})).status_code == 404


async def test_mcp_receipt_follows_its_event_and_raw_source_obeys_identity(config, user, monkeypatch):  # noqa: F811
    from prme.config import MCPConfig
    from prme.mcp.server import memory_get_event, memory_store
    import json

    config.mcp = MCPConfig(user_id=user)
    async with MemoryEngine.open(config) as engine:
        foreign = await engine.store("other source", user_id=user + "-other")
        original_store = engine.store

        async def interleaved_store(*args, **kwargs):
            event_id = await original_store(*args, **kwargs)
            await original_store("later decoy", user_id=kwargs["user_id"])
            return event_id

        monkeypatch.setattr(engine, "store", interleaved_store)
        ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"engine": engine}))
        receipt = json.loads(await memory_store(
            "qualified MCP source with raw trace",
            retrieval_content="qualified MCP projection",
            ctx=ctx,
        ))
        node = await engine.get_node(receipt["node_id"], user_id=user)
        assert node.content == "qualified MCP projection"
        assert node.evidence_refs == [UUID(receipt["event_id"])]
        assert json.loads(await memory_get_event(receipt["event_id"], ctx=ctx))["content"] == (
            "qualified MCP source with raw trace"
        )
        assert "not found" in json.loads(await memory_get_event(foreign, ctx=ctx))["error"]
