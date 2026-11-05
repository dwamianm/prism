"""Supersedence chains and contradiction annotations stay with their owner (#437).

The plain graph store follows SUPERSEDES and CONTRADICTS links without an
owner filter, and a link can cross users in an older store (an unscoped merge)
or after a direct graph write. The chain route, ``MemoryEngine`` chain reads
and retrieval's contradiction annotation must not surface the other user's
node or its id.
"""

from __future__ import annotations

from uuid import uuid4

import httpx

from prme import MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig
from prme.models import MemoryEdge, MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType
from tests.privacy_source_support import (
    assert_no_source, opened, store_pair,
    backend_layout as backend_layout, isolated_privacy_postgres as isolated_privacy_postgres,  # noqa: F401
    local_analyzer_fixture as local_analyzer_fixture, native as native_fixture,  # noqa: F401
)
from tests.test_durable_ingestion import config, user  # noqa: F401


async def _crossing_chain(engine: MemoryEngine, owner: str, other: str) -> dict[str, MemoryNode]:
    """first <- second <- foreign <- third: the owner's chain runs through another user's node."""
    graph = engine._graph_store
    nodes = {
        "first": MemoryNode(content="Office is in Austin", user_id=owner, node_type=NodeType.FACT),
        "second": MemoryNode(content="Office is in Denver", user_id=owner, node_type=NodeType.FACT),
        "foreign": MemoryNode(content="FOREIGN-CHAIN-NODE", user_id=other, node_type=NodeType.FACT),
        "third": MemoryNode(content="Office is in Boston", user_id=owner, node_type=NodeType.FACT),
    }
    for node in nodes.values():
        await graph.create_node(node)
    # SUPERSEDES runs from the newer node to the one it replaces.
    for newer, older in (("second", "first"), ("foreign", "second"), ("third", "foreign")):
        await graph.create_edge(MemoryEdge(
            source_id=nodes[newer].id, target_id=nodes[older].id,
            edge_type=EdgeType.SUPERSEDES, user_id=owner,
        ))
    return nodes


def _ids(nodes) -> list[str]:
    return [str(n.id) for n in nodes]


async def test_engine_chain_stops_before_another_users_node(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _crossing_chain(engine, user, other)
        first, third = str(nodes["first"].id), str(nodes["third"].id)
        # The plain store walks straight through the other user's node.
        raw = await engine._graph_store.get_supersedence_chain(first, direction="forward")
        assert _ids(raw) == _ids([nodes["second"], nodes["foreign"], nodes["third"]])

        scoped = await engine.get_supersedence_chain(first, user_id=user, direction="forward")
        assert _ids(scoped) == [str(nodes["second"].id)]
        assert await engine.get_supersedence_chain(third, user_id=user, direction="backward") == []
        # Another user asking from the owner's node gets nothing, and the
        # owner gets nothing from the other user's node, though the owner's
        # own node replaces it.
        assert await engine.get_supersedence_chain(first, user_id=other, direction="forward") == []
        foreign = str(nodes["foreign"].id)
        assert _ids(await engine._graph_store.get_supersedence_chain(foreign, direction="forward")) == [third]
        assert await engine.get_supersedence_chain(foreign, user_id=user, direction="forward") == []
        # Without a user it stays the unscoped operator read.
        unscoped = await engine.get_supersedence_chain(first, direction="forward")
        assert _ids(unscoped) == _ids(raw)


async def test_chain_route_leaves_out_another_users_node(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _crossing_chain(engine, user, other)
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            # No identity on the request: the starting node's owner reads.
            response = await client.get(f"/v1/nodes/{nodes['first'].id}/chain", params={"direction": "forward"})
            assert response.status_code == 200
            body = response.json()
            assert [n["id"] for n in body["nodes"]] == [str(nodes["second"].id)]
            assert body["count"] == 1
            assert "FOREIGN-CHAIN-NODE" not in response.text

            response = await client.get(f"/v1/nodes/{nodes['third'].id}/chain", params={"direction": "backward"})
            assert response.status_code == 200
            assert response.json()["nodes"] == []

            # Started from the other user's node, the owner's nodes stay out.
            for direction in ("forward", "backward"):
                response = await client.get(f"/v1/nodes/{nodes['foreign'].id}/chain",
                                            params={"direction": direction})
                assert response.status_code == 200
                assert response.json()["nodes"] == []

    keyed = config.model_copy(update={"api": APIConfig(user_keys={user: "owner-key", other: "other-key"})})
    async with MemoryEngine.open(keyed) as engine:
        app = create_app(keyed)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get(f"/v1/nodes/{nodes['first'].id}/chain",
                                        params={"direction": "forward"},
                                        headers={"Authorization": "Bearer owner-key"})
            assert response.status_code == 200
            assert [n["id"] for n in response.json()["nodes"]] == [str(nodes["second"].id)]


async def test_chain_route_reads_a_protected_store_through_the_admitted_view(tmp_path, layout, analyzer, native):  # noqa: F811
    engine, _runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner, foreign = str(policy.owner_id), str(uuid4())
    try:
        start = (await engine.get_event_nodes(await store_pair(engine, owner), user_id=owner))[0]
        # The owner's own node, written directly so no admission covers it,
        # replaces the start, and another user's node replaces that.
        unadmitted = MemoryNode(content="UNADMITTED-REPLACER", user_id=owner, node_type=NodeType.FACT)
        replacer = MemoryNode(content="FOREIGN-REPLACER", user_id=foreign, node_type=NodeType.FACT)
        for newer, older in ((unadmitted, start), (replacer, unadmitted)):
            await engine._graph_store.create_node(newer)
            await engine._graph_store.create_edge(MemoryEdge(
                source_id=newer.id, target_id=older.id, edge_type=EdgeType.SUPERSEDES, user_id=owner,
            ))
        raw = await engine._graph_store.get_supersedence_chain(str(start.id), direction="forward")
        assert [n.id for n in raw] == [unadmitted.id, replacer.id]

        config = engine._config.model_copy(update={"api": APIConfig(user_keys={owner: "owner-key", foreign: "foreign-key"})})
        engine._config = config
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get(f"/v1/nodes/{start.id}/chain", params={"direction": "forward"},
                                        headers={"Authorization": "Bearer owner-key"})
            assert response.status_code == 200
            assert response.json()["nodes"] == []
            assert "FOREIGN-REPLACER" not in response.text
            assert "UNADMITTED-REPLACER" not in response.text
            assert_no_source(response.text)

            response = await client.get(f"/v1/nodes/{replacer.id}/chain", params={"direction": "backward"},
                                        headers={"Authorization": "Bearer foreign-key"})
            assert response.status_code in (404, 422)
            assert str(start.id) not in response.text
    finally:
        await engine.close()


async def test_contradiction_annotation_leaves_out_another_users_counterpart(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        for content in ("the deployment region is east", "the deployment region is west",
                        "the deployment region is south", "the deployment region is unknown"):
            await engine.store(content, user_id=user, node_type=NodeType.FACT)
        own = {n.content.rsplit(" ", 1)[-1]: n for n in await engine.scan_nodes(user_id=user)}
        east, west, south, unknown = (own[k] for k in ("east", "west", "south", "unknown"))
        await engine.store("the deployment region is north", user_id=other, node_type=NodeType.FACT)
        (foreign,) = await engine.scan_nodes(user_id=other)

        await graph.contradict(str(east.id), str(west.id))
        # South is contested, but its own edge goes away, so its only
        # contradiction is the crossing one below.
        await graph.contradict(str(south.id), str(unknown.id))
        for edge in await graph.get_edges(node_ids=[str(south.id)], edge_type=EdgeType.CONTRADICTS):
            await graph.delete_edge(str(edge.id))
        # Crossing edges: east's is outgoing, so it is read before east's own edge.
        for source, target, edge_owner in ((east, foreign, user), (foreign, south, other)):
            await graph.create_edge(MemoryEdge(
                source_id=source.id, target_id=target.id,
                edge_type=EdgeType.CONTRADICTS, user_id=edge_owner,
            ))

        response = await engine.retrieve("deployment region", user_id=user)
        results = {str(c.node.id): c for c in response.results}
        assert {str(east.id), str(west.id), str(south.id)} <= set(results)
        assert results[str(east.id)].node.lifecycle_state == LifecycleState.CONTESTED
        assert results[str(east.id)].conflict_flag
        assert results[str(east.id)].contradicts_id == west.id
        assert results[str(west.id)].contradicts_id == east.id
        assert results[str(south.id)].node.lifecycle_state == LifecycleState.CONTESTED
        assert not results[str(south.id)].conflict_flag
        assert results[str(south.id)].contradicts_id is None
        assert str(foreign.id) not in response.model_dump_json()

        # Once the other user's node is gone, the crossing edges dangle, and
        # the id still does not come back.
        await graph.delete_node(str(foreign.id))
        assert await graph.get_edges(node_ids=[str(south.id)], edge_type=EdgeType.CONTRADICTS)
        response = await engine.retrieve("deployment region", user_id=user)
        results = {str(c.node.id): c for c in response.results}
        assert results[str(east.id)].contradicts_id == west.id
        assert results[str(south.id)].contradicts_id is None
        assert not results[str(south.id)].conflict_flag
        assert str(foreign.id) not in response.model_dump_json()
