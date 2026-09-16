"""Contradiction states, edges, and audit records commit or roll back together."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from prme import MemoryEngine
from prme.models import MemoryEdge, MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


class FailingConnection:
    def __init__(self, conn, statement):
        self.conn, self.statement = conn, statement

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execute(self, sql, *args):
        if self.statement in sql:
            raise RuntimeError("Injected database write failure")
        return self.conn.execute(sql, *args)


class FailingPool:
    def __init__(self, pool, statement):
        self.pool, self.statement = pool, statement

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as conn:
            yield FailingConnection(conn, self.statement)


def fail_write(graph, monkeypatch, statement):
    if hasattr(graph, "_pool"):
        monkeypatch.setattr(graph, "_pool", FailingPool(graph._pool, statement))
    else:
        monkeypatch.setattr(graph, "_conn", FailingConnection(graph._conn, statement))


async def operation_count(graph, node_ids):
    if hasattr(graph, "_pool"):
        async with graph._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT count(*) FROM operations WHERE target_id = ANY($1::text[])", node_ids,
            )
    return graph._conn.execute(
        "SELECT count(*) FROM operations WHERE target_id IN (?, ?)", node_ids,
    ).fetchone()[0]


async def create_pair(graph, owner, **second):
    a = MemoryNode(user_id=owner, node_type=NodeType.FACT, content="Alice works at A")
    b = MemoryNode(user_id=second.pop("user_id", owner), node_type=NodeType.FACT,
                   content="Alice works at B", **second)
    for node in (a, b):
        await graph.create_node(node)
    return str(a.id), str(b.id)


@pytest.mark.parametrize("statement", ["INSERT INTO edges", "CONTRADICTION_NOTED"])
async def test_failed_contradiction_restores_both_nodes_and_edge(config, user, monkeypatch, statement):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        ids = await create_pair(graph, user)
        before = [await graph.get_node(nid, include_superseded=True) for nid in ids]
        with monkeypatch.context() as failure:
            fail_write(graph, failure, statement)
            with pytest.raises(RuntimeError, match="Injected"):
                await graph.contradict(*ids)
        assert [await graph.get_node(nid, include_superseded=True) for nid in ids] == before
        assert await graph.get_edges(source_id=ids[1], edge_type=EdgeType.CONTRADICTS) == []
        assert await operation_count(graph, list(ids)) == 0
        # The same operation is still valid after rollback.
        await graph.contradict(*ids)
        for nid in ids:
            assert (await graph.get_node(nid, include_superseded=True)).lifecycle_state == LifecycleState.CONTESTED
        assert await operation_count(graph, list(ids)) == 1


async def test_failed_resolution_restores_states_and_partial_audit_records(config, user, monkeypatch):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        ids = await create_pair(graph, user)
        await graph.contradict(*ids)
        before = [await graph.get_node(nid, include_superseded=True) for nid in ids]
        with monkeypatch.context() as failure:
            fail_write(graph, failure, "CONTRADICTION_RESOLVED")
            with pytest.raises(RuntimeError, match="Injected"):
                await graph.resolve_contradiction(*ids)
        assert [await graph.get_node(nid, include_superseded=True) for nid in ids] == before
        assert await operation_count(graph, list(ids)) == 1
        assert len(await graph.get_edges(source_id=ids[1], edge_type=EdgeType.CONTRADICTS)) == 1
        await graph.resolve_contradiction(*ids)
        assert (await graph.get_node(ids[0])).lifecycle_state == LifecycleState.STABLE
        assert (await graph.get_node(ids[1], include_superseded=True)).lifecycle_state == LifecycleState.DEPRECATED
        assert await operation_count(graph, list(ids)) == 4


@pytest.mark.parametrize("boundary", ["user", "scope", "self"])
@pytest.mark.parametrize("operation", ["contradict", "resolve_contradiction"])
async def test_invalid_endpoints_never_mutate_nodes(config, user, boundary, operation):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        ids = await create_pair(graph, user, user_id=user + "-other" if boundary == "user" else user,
                                scope=Scope.PROJECT if boundary == "scope" else Scope.PERSONAL)
        if boundary == "self":
            # Alternate UUID spelling must still be recognized as the same node.
            ids = (ids[0], ids[0].upper())
        if operation == "resolve_contradiction":
            # Simulate a legacy invalid edge; validation must protect old stores too.
            for nid in set(ids):
                await graph.update_node(nid, lifecycle_state=LifecycleState.CONTESTED)
            await graph.create_edge(MemoryEdge(source_id=ids[0], target_id=ids[1],
                                               edge_type=EdgeType.CONTRADICTS, user_id=user))
        before = [await graph.get_node(nid, include_superseded=True) for nid in ids]
        with pytest.raises(ValueError, match="same user and scope|itself"):
            await getattr(graph, operation)(*ids)
        assert [await graph.get_node(nid, include_superseded=True) for nid in ids] == before
        assert await operation_count(graph, list(ids)) == 0


async def test_concurrent_opposite_resolutions_have_only_one_winner(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        ids = await create_pair(graph, user)
        await graph.contradict(*ids)
        results = await asyncio.gather(graph.resolve_contradiction(*ids),
                                       graph.resolve_contradiction(*reversed(ids)), return_exceptions=True)
        assert sum(result is None for result in results) == 1
        assert sum(isinstance(result, ValueError) for result in results) == 1
        states = [(await graph.get_node(nid, include_superseded=True)).lifecycle_state for nid in ids]
        assert set(states) == {LifecycleState.STABLE, LifecycleState.DEPRECATED}
        assert await operation_count(graph, list(ids)) == 4


async def test_exact_contradiction_and_resolution_retries_are_noops(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        ids = await create_pair(graph, user)
        await graph.contradict(*ids, actor_id="reviewer")
        await graph.contradict(ids[0].upper(), ids[1].upper(), actor_id="reviewer")
        assert len(await graph.get_edges(node_ids=list(ids), edge_type=EdgeType.CONTRADICTS)) == 1
        assert await operation_count(graph, list(ids)) == 1
        with pytest.raises(ValueError, match="different inputs"):
            await graph.contradict(*ids, actor_id="another-reviewer")

        await graph.resolve_contradiction(*ids, resolver_actor_id="reviewer")
        await graph.resolve_contradiction(
            ids[0].upper(), ids[1].upper(), resolver_actor_id="reviewer"
        )
        assert await operation_count(graph, list(ids)) == 4
        with pytest.raises(ValueError, match="different inputs"):
            await graph.resolve_contradiction(*ids, resolver_actor_id="another-reviewer")
