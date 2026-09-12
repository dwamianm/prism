"""Cancellation must either clean partial derivations or preserve a committed replacement."""

import asyncio

import pytest

from prme import MemoryEngine
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.models import Event
from prme.types import LifecycleState
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


def extraction(value="Python", replaces=None):
    return ExtractionResult(
        entities=[ExtractedEntity(name="Alice", entity_type="person")],
        facts=[ExtractedFact(subject="Alice", predicate="uses", object=value,
                             temporal_intent="update" if replaces else "assertion",
                             replaces_object=replaces)],
    )


@pytest.mark.parametrize("stage", ["node", "edge", "vector"])
async def test_cancelled_materialization_cleans_late_writes(config, user, monkeypatch, stage):
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Alice uses Python", user_id=user, role="user")
        await engine._event_store.append(event)
        entered, release = asyncio.Event(), asyncio.Event()
        target = engine._vector_index if stage == "vector" else engine._graph_store
        method = {"node": "create_node", "edge": "create_edge", "vector": "index"}[stage]
        original = getattr(target, method)
        first = True
        async def blocked(*args, **kwargs):
            nonlocal first
            if not first:
                return await original(*args, **kwargs)
            first = False
            # The database write has committed, but its caller has not received
            # the ID yet. Vector faults instead block before native indexing.
            result = await original(*args, **kwargs) if stage != "vector" else None
            entered.set()
            await release.wait()
            return result if stage != "vector" else await original(*args, **kwargs)
        monkeypatch.setattr(target, method, blocked)
        task = asyncio.create_task(engine._pipeline._materialize(extraction(), event, str(event.id)))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            # Flush any late write queued by a cancelled producer before reads.
            await engine._write_queue.submit(lambda: asyncio.sleep(0))
        assert await engine.get_event_nodes(str(event.id), user_id=user) == []
        assert await engine._lexical_index.search("Alice", user) == []
        if engine._conn is not None:
            assert engine._conn.execute("SELECT count(*) FROM vector_metadata WHERE user_id = ?", [user]).fetchone()[0] == 0
        assert await engine.get_event(str(event.id)) is not None


async def test_cancel_after_replacement_commit_keeps_complete_new_fact(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        old_event = Event(content="Alice uses Python", user_id=user, role="user")
        await engine._event_store.append(old_event)
        await engine._pipeline._materialize(extraction(), old_event, str(old_event.id))
        old_fact = next(n for n in await engine.get_event_nodes(str(old_event.id), user_id=user)
                        if (n.metadata or {}).get("predicate") == "uses")
        event = Event(content="Alice switched from Python to Rust", user_id=user, role="user")
        await engine._event_store.append(event)
        entered, release = asyncio.Event(), asyncio.Event()
        original = engine._graph_store.supersede_many
        async def committed_then_blocked(*args, **kwargs):
            await original(*args, **kwargs)
            entered.set()
            await release.wait()
        monkeypatch.setattr(engine._graph_store, "supersede_many", committed_then_blocked)
        task = asyncio.create_task(engine._pipeline._materialize(extraction("Rust", "Python"), event, str(event.id)))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            await engine._write_queue.submit(lambda: asyncio.sleep(0))
        old = await engine._graph_store.get_node(str(old_fact.id), include_superseded=True)
        assert old.lifecycle_state == LifecycleState.SUPERSEDED
        replacement = await engine.get_node(str(old.superseded_by))
        assert replacement is not None and replacement.metadata["object"] == "Rust"
        assert await engine.get_event_nodes(str(event.id), user_id=user) == [replacement]
