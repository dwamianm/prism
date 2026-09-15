"""Unverified alias links must be durable, idempotent graph proposals."""

import asyncio

import pytest

from prme import MemoryEngine
from prme.models.edges import MemoryEdge
from prme.organizer.alias_resolution import AliasCandidate, resolve_aliases
from prme.storage.alias_proposal import read_record
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, NodeType, Scope
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


async def seed(engine, user):
    for content in ("Alice", "Alicia"):
        await engine.store(
            content,
            user_id=user,
            node_type=NodeType.ENTITY,
            metadata={"entity_type": "person"},
        )
    return sorted(
        (str(node.id) for node in await engine.query_nodes(user_id=user))
    )


async def proposal_rows(engine, user_id, operation_id=None):
    graph = engine._graph_store
    if hasattr(graph, "_conn"):
        async with graph._conn_lock:
            if operation_id is None:
                return graph._conn.execute(
                    "SELECT id,payload FROM operations "
                    "WHERE op_type='ALIAS_PROPOSED' AND actor_id=? ORDER BY id",
                    [user_id],
                ).fetchall()
            return graph._conn.execute(
                "SELECT id,payload FROM operations "
                "WHERE op_type='ALIAS_PROPOSED' AND actor_id=? AND id=?",
                [user_id, operation_id],
            ).fetchall()
    async with graph._pool.acquire() as conn:
        if operation_id is None:
            rows = await conn.fetch(
                "SELECT id,payload FROM operations "
                "WHERE op_type='ALIAS_PROPOSED' AND actor_id=$1 ORDER BY id",
                user_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT id,payload FROM operations "
                "WHERE op_type='ALIAS_PROPOSED' AND actor_id=$1 AND id=$2",
                user_id,
                operation_id,
            )
    return [(row["id"], row["payload"]) for row in rows]


async def test_alias_proposal_retains_complete_inputs_and_replays(config, user):
    async with MemoryEngine.open(config) as engine:
        ids = await seed(engine, user)
        before = {str(node.id): node for node in await engine.query_nodes(user_id=user)}
        first = await engine._graph_store.propose_alias(
            *ids, user_id=user, alias_type="semantic", score=0.871234
        )
        assert first is not None and first.applied and first.operation_id is not None

        second = await engine._graph_store.propose_alias(
            *reversed(ids), user_id=user, alias_type="semantic", score=0.871234
        )
        assert second == first.__class__(first.operation_id, first.edge_id, False)

        edges = await engine._graph_store.get_edges(
            node_ids=ids, edge_type=EdgeType.RELATES_TO
        )
        rows = await proposal_rows(engine, user, first.operation_id)
        assert len(edges) == len(rows) == 1
        record = read_record(rows[0][1])
        assert record.left_before == before[ids[0]]
        assert record.right_before == before[ids[1]]
        assert record.edge == edges[0]
        assert record.edge.metadata == {
            "relation": "alias",
            "alias_type": "semantic",
            "identity_verified": False,
            "alias_operation_id": first.operation_id,
        }

    async with MemoryEngine.open(config) as engine:
        again = await engine._graph_store.propose_alias(
            *ids, user_id=user, alias_type="semantic", score=0.871234
        )
        assert again is not None and not again.applied
        assert (again.operation_id, again.edge_id) == (
            first.operation_id,
            first.edge_id,
        )
        assert len(await proposal_rows(engine, user)) == 1
        assert len(await engine._graph_store.get_edges(node_ids=ids)) == 1


@pytest.mark.parametrize("stage", ["validated", "edge", "journal"])
async def test_alias_proposal_failure_rolls_back_edge_and_journal(
    config, user, stage, monkeypatch
):
    from prme.storage import alias_proposal

    async with MemoryEngine.open(config) as engine:
        ids = await seed(engine, user)

        def fail(actual):
            if actual == stage:
                raise RuntimeError("Authored alias transaction fault")

        with monkeypatch.context() as fault:
            fault.setattr(alias_proposal, "_checkpoint", fail)
            with pytest.raises(RuntimeError, match="Authored alias transaction fault"):
                await engine._graph_store.propose_alias(
                    *ids, user_id=user, alias_type="semantic", score=0.87
                )
        assert await engine._graph_store.get_edges(node_ids=ids) == []
        assert await proposal_rows(engine, user) == []

    async with MemoryEngine.open(config) as engine:
        result = await engine._graph_store.propose_alias(
            *ids, user_id=user, alias_type="semantic", score=0.87
        )
        assert result is not None and result.applied
        assert len(await engine._graph_store.get_edges(node_ids=ids)) == 1
        assert len(await proposal_rows(engine, user)) == 1


async def test_concurrent_alias_proposals_publish_once(config, user):
    async with MemoryEngine.open(config) as engine:
        ids = await seed(engine, user)
        results = await asyncio.gather(
            *[
                engine._graph_store.propose_alias(
                    *pair, user_id=user, alias_type="semantic", score=0.87
                )
                for pair in (ids, list(reversed(ids)))
            ]
        )
        assert sorted(result.applied for result in results if result is not None) == [
            False,
            True,
        ]
        assert len(await engine._graph_store.get_edges(node_ids=ids)) == 1
        assert len(await proposal_rows(engine, user)) == 1


async def test_alias_proposal_cancellation_has_one_durable_outcome(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        ids = await seed(engine, user)
        graph = engine._graph_store
        if hasattr(graph, "_conn"):
            import threading

            from prme.storage import alias_proposal

            entered, release = threading.Event(), threading.Event()

            def pause(stage):
                if stage == "edge":
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("Alias cancellation synchronization timed out")

            with monkeypatch.context() as patch:
                patch.setattr(alias_proposal, "_checkpoint", pause)
                task = asyncio.create_task(
                    graph.propose_alias(
                        *ids,
                        user_id=user,
                        alias_type="semantic",
                        score=0.87,
                    )
                )
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    task.cancel()
                    await asyncio.sleep(0)
                    assert not task.done()
                finally:
                    release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            committed = True
        else:
            entered = asyncio.Event()
            create = graph._create_edge_on_connection

            async def pause(conn, edge):
                await create(conn, edge)
                entered.set()
                await asyncio.Event().wait()

            with monkeypatch.context() as patch:
                patch.setattr(graph, "_create_edge_on_connection", pause)
                task = asyncio.create_task(
                    graph.propose_alias(
                        *ids,
                        user_id=user,
                        alias_type="semantic",
                        score=0.87,
                    )
                )
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                finally:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            committed = False

        if not committed:
            assert await graph.get_edges(node_ids=ids) == []
            assert await proposal_rows(engine, user) == []
        retry = await graph.propose_alias(
            *ids, user_id=user, alias_type="semantic", score=0.87
        )
        assert retry is not None and retry.applied == (not committed)
        assert len(await graph.get_edges(node_ids=ids)) == 1
        assert len(await proposal_rows(engine, user)) == 1


async def test_legacy_unverified_alias_is_reused_without_inventing_history(
    config, user
):
    async with MemoryEngine.open(config) as engine:
        ids = await seed(engine, user)
        legacy = MemoryEdge(
            source_id=ids[1],
            target_id=ids[0],
            edge_type=EdgeType.RELATES_TO,
            user_id=user,
            confidence=0.86,
            metadata={
                "relation": "alias",
                "alias_type": "semantic",
                "identity_verified": False,
            },
        )
        await engine._graph_store.create_edge(legacy)
        result = await engine._graph_store.propose_alias(
            *ids, user_id=user, alias_type="semantic", score=0.87
        )
        assert result is not None and not result.applied
        assert result.operation_id is None and result.edge_id == str(legacy.id)
        assert len(await engine._graph_store.get_edges(node_ids=ids)) == 1
        assert await proposal_rows(engine, user) == []


@pytest.mark.parametrize("difference", ["owner", "scope", "type"])
async def test_direct_alias_proposal_revalidates_namespace_and_type(
    config, user, difference
):
    async with MemoryEngine.open(config) as engine:
        left = MemoryNode(
            content="Alice",
            user_id=user,
            node_type=NodeType.ENTITY,
            metadata={"entity_type": "person"},
        )
        right = MemoryNode(
            content="Alicia",
            user_id=user + "-other" if difference == "owner" else user,
            scope=Scope.PROJECT if difference == "scope" else Scope.PERSONAL,
            node_type=NodeType.NOTE if difference == "type" else NodeType.ENTITY,
            metadata={"entity_type": "person"},
        )
        await engine._graph_store.create_node(left)
        await engine._graph_store.create_node(right)
        result = await engine._graph_store.propose_alias(
            str(left.id),
            str(right.id),
            user_id=user,
            alias_type="semantic",
            score=0.87,
        )
        assert result is None
        assert (
            await engine._graph_store.get_edges(
                node_ids=[str(left.id), str(right.id)]
            )
            == []
        )
        assert await proposal_rows(engine, user) == []


async def test_resolver_counts_only_new_alias_publications(config, user):
    async with MemoryEngine.open(config) as engine:
        ids = await seed(engine, user)
        alias = AliasCandidate(*ids, "semantic", 0.87)
        assert await resolve_aliases(engine, [alias]) == 1
        assert await resolve_aliases(engine, [alias]) == 0
        assert len(await engine._graph_store.get_edges(node_ids=ids)) == 1
        assert len(await proposal_rows(engine, user)) == 1
