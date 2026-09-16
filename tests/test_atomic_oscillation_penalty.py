"""Oscillation penalties must keep confidence and audit state inseparable."""

import asyncio
import json

import pytest

from prme import MemoryEngine
from prme.models.nodes import MemoryNode
from prme.storage.oscillation_penalty import (
    OscillationPenaltyConflict,
    read_record,
)
from prme.types import NodeType
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


async def seed_chain(engine: MemoryEngine, user_id: str):
    oldest = MemoryNode(
        user_id=user_id,
        node_type=NodeType.PREFERENCE,
        content="I prefer dark mode for my editor",
        confidence=0.8,
        confidence_base=0.8,
    )
    middle = MemoryNode(
        user_id=user_id,
        node_type=NodeType.PREFERENCE,
        content="I switched to light mode for my editor",
        confidence=0.8,
        confidence_base=0.8,
    )
    current = MemoryNode(
        user_id=user_id,
        node_type=NodeType.PREFERENCE,
        content="I prefer dark mode for my editor",
        confidence=0.8,
        confidence_base=0.8,
    )
    for node in (oldest, middle, current):
        await engine._graph_store.create_node(node)
    await engine._graph_store.supersede(
        str(oldest.id), str(middle.id), actor_id=user_id
    )
    await engine._graph_store.supersede(
        str(middle.id), str(current.id), actor_id=user_id
    )
    return current, middle, oldest


async def penalty_rows(engine: MemoryEngine, node_id: str):
    graph = engine._graph_store
    if hasattr(graph, "_conn"):
        async with graph._conn_lock:
            return graph._conn.execute(
                "SELECT id,target_id,payload,actor_id,namespace_id "
                "FROM operations WHERE op_type='PENALTY' AND target_id=?",
                [node_id],
            ).fetchall()
    async with graph._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id,target_id,payload,actor_id,namespace_id "
            "FROM operations WHERE op_type='PENALTY' AND target_id=$1",
            node_id,
        )
    return [tuple(row) for row in rows]


async def test_penalty_retains_locked_chain_and_replays(config, user):
    async with MemoryEngine.open(config) as engine:
        current, middle, oldest = await seed_chain(engine, user)
        ids = [str(current.id), str(middle.id), str(oldest.id)]
        before = await engine.get_node(str(current.id), user_id=user)

        assert await engine._graph_store.apply_oscillation_penalty(
            str(current.id), ids, user_id=user
        )
        after = await engine.get_node(str(current.id), user_id=user)
        rows = await penalty_rows(engine, str(current.id))
        assert len(rows) == 1
        operation_id, target_id, payload, actor_id, namespace_id = rows[0]
        record = read_record(payload)
        assert str(record.operation_id) == operation_id
        assert target_id == str(current.id)
        assert actor_id == user
        assert namespace_id == current.scope.value
        assert record.chain_before[0] == before
        assert record.after == after
        assert record.topic == "dark editor mode prefer"
        assert record.cycle_count == 1
        assert record.delta == pytest.approx(0.1)
        assert after.confidence_base == pytest.approx(0.7)

        assert not await engine._graph_store.apply_oscillation_penalty(
            str(current.id), ids, user_id=user
        )
        assert await penalty_rows(engine, str(current.id)) == rows
        with pytest.raises(OscillationPenaltyConflict):
            await engine._graph_store.apply_oscillation_penalty(
                str(current.id),
                [str(current.id), str(oldest.id), str(middle.id)],
                user_id=user,
            )

    async with MemoryEngine.open(config) as engine:
        assert not await engine._graph_store.apply_oscillation_penalty(
            str(current.id), ids, user_id=user
        )
        assert len(await penalty_rows(engine, str(current.id))) == 1


@pytest.mark.parametrize("stage", ["validated", "updated", "journal"])
async def test_penalty_failure_rolls_back_node_and_journal(
    config, user, stage, monkeypatch
):
    from prme.storage import oscillation_penalty

    async with MemoryEngine.open(config) as engine:
        current, middle, oldest = await seed_chain(engine, user)
        ids = [str(current.id), str(middle.id), str(oldest.id)]
        before = await engine.get_node(str(current.id), user_id=user)

        def fail(actual):
            if actual == stage:
                raise RuntimeError("Authored oscillation transaction fault")

        with monkeypatch.context() as fault:
            fault.setattr(oscillation_penalty, "_checkpoint", fail)
            with pytest.raises(
                RuntimeError, match="Authored oscillation transaction fault"
            ):
                await engine._graph_store.apply_oscillation_penalty(
                    str(current.id), ids, user_id=user
                )
        assert await engine.get_node(str(current.id), user_id=user) == before
        assert await penalty_rows(engine, str(current.id)) == []

        assert await engine._graph_store.apply_oscillation_penalty(
            str(current.id), ids, user_id=user
        )
        assert len(await penalty_rows(engine, str(current.id))) == 1


async def test_concurrent_penalty_publishes_once(config, user):
    async with MemoryEngine.open(config) as engine:
        current, middle, oldest = await seed_chain(engine, user)
        ids = [str(current.id), str(middle.id), str(oldest.id)]
        results = await asyncio.gather(
            *[
                engine._graph_store.apply_oscillation_penalty(
                    str(current.id), ids, user_id=user
                )
                for _ in range(2)
            ]
        )
        assert sorted(results) == [False, True]
        assert len(await penalty_rows(engine, str(current.id))) == 1
        after = await engine.get_node(str(current.id), user_id=user)
        assert after.confidence_base == pytest.approx(0.7)


async def test_stale_or_foreign_chain_cannot_change_confidence(config, user):
    async with MemoryEngine.open(config) as engine:
        current, middle, oldest = await seed_chain(engine, user)
        before = await engine.get_node(str(current.id), user_id=user)
        assert not await engine._graph_store.apply_oscillation_penalty(
            str(current.id),
            [str(current.id), str(oldest.id), str(middle.id)],
            user_id=user,
        )
        assert not await engine._graph_store.apply_oscillation_penalty(
            str(current.id),
            [str(current.id), str(middle.id), str(oldest.id)],
            user_id=user + "-other",
        )
        assert await engine.get_node(str(current.id), user_id=user) == before
        assert await penalty_rows(engine, str(current.id)) == []


async def test_engine_check_uses_atomic_penalty(config, user):
    async with MemoryEngine.open(config) as engine:
        current, _, _ = await seed_chain(engine, user)
        await engine._check_oscillation(str(current.id))
        after = await engine.get_node(str(current.id), user_id=user)
        assert after.confidence_base == pytest.approx(0.7)
        assert len(await penalty_rows(engine, str(current.id))) == 1


async def test_penalty_record_rejects_tampering(config, user):
    async with MemoryEngine.open(config) as engine:
        current, middle, oldest = await seed_chain(engine, user)
        ids = [str(current.id), str(middle.id), str(oldest.id)]
        assert await engine._graph_store.apply_oscillation_penalty(
            str(current.id), ids, user_id=user
        )
        payload = (await penalty_rows(engine, str(current.id)))[0][2]
        value = json.loads(payload) if isinstance(payload, str) else payload
        value["record"] += " "
        with pytest.raises(ValueError, match="checksum mismatch"):
            read_record(value)


async def test_duckdb_penalty_cancellation_has_one_durable_outcome(
    config, user, monkeypatch
):
    from prme.storage import oscillation_penalty

    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        if not hasattr(graph, "_conn"):
            pytest.skip("Native cancellation completion applies to DuckDB")
        current, middle, oldest = await seed_chain(engine, user)
        ids = [str(current.id), str(middle.id), str(oldest.id)]

        import threading

        entered, release = threading.Event(), threading.Event()

        def pause(stage):
            if stage == "updated":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Oscillation cancellation synchronization timed out")

        with monkeypatch.context() as patch:
            patch.setattr(oscillation_penalty, "_checkpoint", pause)
            task = asyncio.create_task(
                graph.apply_oscillation_penalty(
                    str(current.id), ids, user_id=user
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

        assert len(await penalty_rows(engine, str(current.id))) == 1
        assert not await graph.apply_oscillation_penalty(
            str(current.id), ids, user_id=user
        )
