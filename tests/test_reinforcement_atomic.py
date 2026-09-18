"""Concurrent confirmations must accumulate instead of overwriting evidence."""

import asyncio
from uuid import UUID

import pytest

from prme import MemoryEngine
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_concurrent_reinforcement_preserves_every_confirmation(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An observation", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        evidence = [
            await engine.store(f"Confirmation {i}", user_id=user) for i in range(3)
        ]
        original = engine._owned_node
        ready = asyncio.Event()
        arrivals = 0

        async def simultaneous_read(*args, **kwargs):
            nonlocal arrivals
            result = await original(*args, **kwargs)
            arrivals += 1
            if arrivals >= 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=10)
            return result

        with monkeypatch.context() as overlap:
            overlap.setattr(engine, "_owned_node", simultaneous_read)
            await asyncio.gather(
                *(
                    engine.reinforce(str(node.id), evidence_id=eid, user_id=user)
                    for eid in evidence
                )
            )
        after = await engine.get_node(str(node.id), user_id=user)
        assert after.reinforcement_boost == pytest.approx(0.45)
        assert after.confidence_base == pytest.approx(
            min(node.confidence_base + 0.15, 0.95)
        )
        assert set(node.evidence_refs) | {UUID(eid) for eid in evidence} <= set(
            after.evidence_refs
        )


async def records(engine, node_id):
    from prme.storage.reinforcement import read_record

    graph = engine._graph_store
    if hasattr(graph, "_conn"):
        async with graph._conn_lock:
            rows = graph._conn.execute(
                "SELECT payload FROM operations WHERE op_type='REINFORCE' AND target_id=? ORDER BY created_at, id",
                [node_id],
            ).fetchall()
        return [read_record(row[0]) for row in rows]
    async with graph._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT payload FROM operations WHERE op_type='REINFORCE' AND target_id=$1 ORDER BY created_at, id",
            node_id,
        )
    return [read_record(row["payload"]) for row in rows]


@pytest.mark.parametrize("stage", ["validated", "updated", "journal"])
async def test_failed_reinforcement_rolls_back_node_and_journal(
    config, user, stage, monkeypatch
):
    from prme.storage import reinforcement

    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An observation", user_id=user)
        before = (await engine.get_event_nodes(source, user_id=user))[0]
        evidence = await engine.store("A confirming observation", user_id=user)

        def fail(actual):
            if actual == stage:
                raise RuntimeError("Authored reinforcement transaction fault")

        with monkeypatch.context() as fault:
            fault.setattr(reinforcement, "_checkpoint", fail)
            with pytest.raises(
                RuntimeError, match="Authored reinforcement transaction fault"
            ):
                await engine.reinforce(
                    str(before.id), evidence_id=evidence, user_id=user
                )
        assert await engine.get_node(str(before.id), user_id=user) == before
        assert await records(engine, str(before.id)) == []
    async with MemoryEngine.open(config) as engine:
        assert await engine.get_node(str(before.id), user_id=user) == before
        assert await records(engine, str(before.id)) == []
        await engine.reinforce(str(before.id), evidence_id=evidence, user_id=user)
        after = await engine.get_node(str(before.id), user_id=user)
        journal = await records(engine, str(before.id))
        assert len(journal) == 1
        assert journal[0].before == before and journal[0].after == after
        assert journal[0].evidence_id == UUID(evidence)
    async with MemoryEngine.open(config) as engine:
        assert (await records(engine, str(before.id)))[0] == journal[0]
