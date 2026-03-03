"""Tests for PgGraphStore against a real PostgreSQL instance.

Requires PRME_TEST_DATABASE_URL environment variable.
"""

from __future__ import annotations

import os
import uuid

import pytest

from prme.models.nodes import MemoryNode
from prme.models.edges import MemoryEdge
from prme.types import EdgeType, LifecycleState, NodeType, Scope

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("PRME_TEST_DATABASE_URL"),
        reason="PRME_TEST_DATABASE_URL not set",
    ),
]


@pytest.fixture
async def pg_pool():
    import asyncpg

    url = os.environ["PRME_TEST_DATABASE_URL"]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
async def graph_store(pg_pool):
    from prme.storage.pg.schema import initialize_pg_database
    from prme.storage.pg.graph_store import PgGraphStore

    await initialize_pg_database(pg_pool)
    return PgGraphStore(pg_pool)


def _make_node(user_id: str = "test-user", content: str = "test content") -> MemoryNode:
    return MemoryNode(
        user_id=user_id,
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        content=content,
    )


async def test_create_and_get_node(graph_store):
    node = _make_node()
    nid = await graph_store.create_node(node)
    assert nid == str(node.id)

    retrieved = await graph_store.get_node(nid)
    assert retrieved is not None
    assert retrieved.content == "test content"


async def test_query_nodes(graph_store):
    uid = f"user-{uuid.uuid4().hex[:8]}"
    node = _make_node(user_id=uid, content="queryable fact")
    await graph_store.create_node(node)

    results = await graph_store.query_nodes(user_id=uid, node_type=NodeType.FACT)
    assert len(results) >= 1
    assert any(r.content == "queryable fact" for r in results)


async def test_create_edge(graph_store):
    uid = f"user-{uuid.uuid4().hex[:8]}"
    n1 = _make_node(user_id=uid, content="node A")
    n2 = _make_node(user_id=uid, content="node B")
    await graph_store.create_node(n1)
    await graph_store.create_node(n2)

    edge = MemoryEdge(
        source_id=n1.id, target_id=n2.id,
        edge_type=EdgeType.RELATES_TO, user_id=uid,
    )
    eid = await graph_store.create_edge(edge)
    assert eid == str(edge.id)

    edges = await graph_store.get_edges(source_id=str(n1.id))
    assert len(edges) >= 1


async def test_promote(graph_store):
    node = _make_node()
    nid = await graph_store.create_node(node)

    await graph_store.promote(nid)
    promoted = await graph_store.get_node(nid)
    assert promoted is not None
    assert promoted.lifecycle_state == LifecycleState.STABLE


async def test_supersede(graph_store):
    uid = f"user-{uuid.uuid4().hex[:8]}"
    old = _make_node(user_id=uid, content="old fact")
    new = _make_node(user_id=uid, content="new fact")
    await graph_store.create_node(old)
    await graph_store.create_node(new)

    await graph_store.supersede(str(old.id), str(new.id))
    old_node = await graph_store.get_node(str(old.id), include_superseded=True)
    assert old_node is not None
    assert old_node.lifecycle_state == LifecycleState.SUPERSEDED


async def test_get_neighborhood(graph_store):
    uid = f"user-{uuid.uuid4().hex[:8]}"
    n1 = _make_node(user_id=uid, content="center")
    n2 = _make_node(user_id=uid, content="neighbor")
    await graph_store.create_node(n1)
    await graph_store.create_node(n2)

    edge = MemoryEdge(
        source_id=n1.id, target_id=n2.id,
        edge_type=EdgeType.RELATES_TO, user_id=uid,
    )
    await graph_store.create_edge(edge)

    neighbors = await graph_store.get_neighborhood(str(n1.id), max_hops=1)
    assert len(neighbors) >= 1
    assert any(n.content == "neighbor" for n in neighbors)


async def test_delete_node(graph_store):
    node = _make_node()
    nid = await graph_store.create_node(node)
    await graph_store.delete_node(nid)
    assert await graph_store.get_node(nid, include_superseded=True) is None
