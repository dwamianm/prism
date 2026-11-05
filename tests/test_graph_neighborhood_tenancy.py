"""Graph neighborhood reads never return another user's node (#433).

The plain graph store's traversal has no owner filter, and an edge can cross
users in an older store (an unscoped merge before v0.12.0) or after a direct
graph write. Retrieval's graph expansion and the API neighborhood route must
leave the other user's node out.
"""

from __future__ import annotations

from uuid import uuid4

import httpx

from prme import MemoryEngine
from prme.api.app import create_app
from prme.config import APIConfig
from prme.models import MemoryEdge, MemoryNode
from prme.retrieval.candidates import _generate_graph_candidates
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import QueryAnalysis
from prme.types import EdgeType, NodeType, QueryIntent, RetrievalMode
from tests.privacy_source_support import (
    assert_no_source, opened, store_pair,
    backend_layout as backend_layout, isolated_privacy_postgres as isolated_privacy_postgres,  # noqa: F401
    local_analyzer_fixture as local_analyzer_fixture, native as native_fixture,  # noqa: F401
)
from tests.test_durable_ingestion import config, user  # noqa: F401


async def _crossing_graph(engine: MemoryEngine, owner: str, other: str) -> dict[str, MemoryNode]:
    """Two entities of the owner, the owner's fact, and another user's nodes joined by crossing edges."""
    graph = engine._graph_store
    nodes = {
        "acme": MemoryNode(content="Acme", user_id=owner, node_type=NodeType.ENTITY),
        "globex": MemoryNode(content="Globex", user_id=owner, node_type=NodeType.ENTITY),
        "own_fact": MemoryNode(content="Acme ships widgets to Globex", user_id=owner,
                               node_type=NodeType.FACT),
        "other_fact": MemoryNode(content="Acme owes Globex money", user_id=other,
                                 node_type=NodeType.FACT),
        "other_task": MemoryNode(content="Call Acme on Monday", user_id=other,
                                 node_type=NodeType.TASK),
    }
    for node in nodes.values():
        await graph.create_node(node)
    for source, target, edge_owner in (
        ("acme", "own_fact", owner),
        ("globex", "own_fact", owner),
        # Crossing edges, one owned by each user, both reached from two seeds.
        ("acme", "other_fact", other),
        ("globex", "other_fact", owner),
        ("acme", "other_task", owner),
    ):
        await graph.create_edge(MemoryEdge(
            source_id=nodes[source].id, target_id=nodes[target].id,
            edge_type=EdgeType.RELATES_TO, user_id=edge_owner,
        ))
    return nodes


def _analysis(query: str, entities: list[str]) -> QueryAnalysis:
    return QueryAnalysis(
        query=query, intent=QueryIntent.SEMANTIC, entities=entities,
        retrieval_mode=RetrievalMode.DEFAULT,
    )


async def test_graph_expansion_leaves_out_another_users_neighbors(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _crossing_graph(engine, user, other)
        # The traversal itself does reach the other user's nodes.
        raw = await engine._graph_store.get_neighborhood_with_depth(str(nodes["acme"].id), max_hops=2)
        assert {str(nodes["other_fact"].id), str(nodes["other_task"].id)} <= {str(n.id) for n, _ in raw}

        for seed_reach in (False, True):
            candidates = await _generate_graph_candidates(
                _analysis("What does Acme do with Globex?", ["Acme", "Globex"]),
                engine._graph_store, user, PackingConfig(), seed_reach=seed_reach,
            )
            found = {str(c["node"].id): c for c in candidates}
            # Each seed reaches the other through the owner's fact.
            assert set(found) == {str(nodes[k].id) for k in ("own_fact", "acme", "globex")}
            assert {c["node"].user_id for c in candidates} == {user}
            if seed_reach:
                # Both seeds reach the owner's fact; the other user's fact,
                # which both seeds also touch, is not walked or counted.
                assert found[str(nodes["own_fact"].id)]["graph_seed_reach"] == (2, 2, 1)

        # The other user's own expansion keeps their nodes and leaves out the owner's.
        other_acme = MemoryNode(content="Acme", user_id=other, node_type=NodeType.ENTITY)
        await engine._graph_store.create_node(other_acme)
        await engine._graph_store.create_edge(MemoryEdge(
            source_id=other_acme.id, target_id=nodes["other_fact"].id,
            edge_type=EdgeType.RELATES_TO, user_id=other,
        ))
        theirs = await _generate_graph_candidates(
            _analysis("What about Acme?", ["Acme"]), engine._graph_store, other, PackingConfig(),
        )
        assert str(nodes["other_fact"].id) in {str(c["node"].id) for c in theirs}
        assert {c["node"].user_id for c in theirs} == {other}

        results = await engine.retrieve("What does Acme do with Globex?", user_id=user)
        returned = {str(r.node.id) for r in results.results}
        assert str(nodes["other_fact"].id) not in returned
        assert str(nodes["other_task"].id) not in returned
        assert {r.node.user_id for r in results.results} <= {user}


async def test_neighborhood_route_leaves_out_another_users_neighbors(config, user):  # noqa: F811
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = await _crossing_graph(engine, user, other)
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            # No identity on the request: the starting node's owner reads.
            response = await client.get(f"/v1/nodes/{nodes['acme'].id}/neighborhood", params={"max_hops": 2})
            assert response.status_code == 200
            body = response.json()
            assert {n["id"] for n in body["nodes"]} == {str(nodes["own_fact"].id), str(nodes["globex"].id)}
            assert body["count"] == 2

            # Started from the other user's node, only their own nodes return.
            response = await client.get(f"/v1/nodes/{nodes['other_fact'].id}/neighborhood", params={"max_hops": 1})
            assert response.status_code == 200
            assert response.json()["nodes"] == []

            # A superseded starting node keeps its neighborhood, as before.
            replacement = MemoryNode(content="Acme Corp", user_id=user, node_type=NodeType.ENTITY)
            await engine._graph_store.create_node(replacement)
            await engine._graph_store.supersede(str(nodes["acme"].id), str(replacement.id))
            response = await client.get(f"/v1/nodes/{nodes['acme'].id}/neighborhood", params={"max_hops": 1})
            assert response.status_code == 200
            assert str(nodes["own_fact"].id) in {n["id"] for n in response.json()["nodes"]}
            assert {n["user_id"] for n in response.json()["nodes"]} == {user}


async def test_neighborhood_route_reads_a_protected_store_through_the_admitted_view(tmp_path, layout, analyzer, native):  # noqa: F811
    engine, _runtime, policy, *_ = await opened(tmp_path, layout, analyzer)
    owner, foreign = str(policy.owner_id), str(uuid4())
    try:
        start = (await engine.get_event_nodes(await store_pair(engine, owner), user_id=owner))[0]
        other = MemoryNode(content="FOREIGN-NEIGHBOR", user_id=foreign, node_type=NodeType.FACT)
        # The owner's own node, joined by a direct graph write that no admission covers.
        unadmitted = MemoryNode(content="UNADMITTED-NEIGHBOR", user_id=owner, node_type=NodeType.FACT)
        for node in (other, unadmitted):
            await engine._graph_store.create_node(node)
            await engine._graph_store.create_edge(MemoryEdge(
                source_id=start.id, target_id=node.id, edge_type=EdgeType.RELATES_TO, user_id=owner,
            ))
        raw = await engine._graph_store.get_neighborhood(str(start.id), max_hops=1)
        assert {other.id, unadmitted.id} <= {n.id for n in raw}

        config = engine._config.model_copy(update={"api": APIConfig(user_keys={owner: "owner-key", foreign: "foreign-key"})})
        engine._config = config
        app = create_app(config)
        app.state.engine = engine
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get(f"/v1/nodes/{start.id}/neighborhood",
                                        headers={"Authorization": "Bearer owner-key"})
            assert response.status_code == 200
            assert "FOREIGN-NEIGHBOR" not in response.text
            assert "UNADMITTED-NEIGHBOR" not in response.text
            assert_no_source(response.text)

            response = await client.get(f"/v1/nodes/{other.id}/neighborhood",
                                        headers={"Authorization": "Bearer foreign-key"})
            assert response.status_code in (404, 422)
            assert str(start.id) not in response.text
    finally:
        await engine.close()
