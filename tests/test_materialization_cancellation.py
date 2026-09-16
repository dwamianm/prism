"""Cancellation preserves atomic publication and saved retry inputs."""

import asyncio
import threading
from unittest.mock import AsyncMock

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


@pytest.mark.parametrize("stage", ["node", "edge", "vector", "lexical"])
async def test_cancelled_materialization_has_no_partial_graph_and_replays_saved_plan(config, user, monkeypatch, stage):
    if config.backend == "postgres" and stage in ("vector", "lexical"):
        pytest.skip("PostgreSQL indexes publish inside the tested graph transaction")
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Alice uses Python", user_id=user, role="user")
        await engine._event_store.append(event)
        entered, release = threading.Event(), threading.Event()
        if stage in ("node", "edge"):
            target = engine._graph_store
            method = (f"_create_{stage}_sync" if config.backend == "duckdb"
                      else f"_create_{stage}_on_connection")
        else:
            target = engine._vector_index if stage == "vector" else engine._lexical_index
            method = "stage"
        original = getattr(target, method)
        first = True
        if config.backend == "duckdb" and stage in ("node", "edge"):
            def blocked(*args, **kwargs):
                nonlocal first
                result = original(*args, **kwargs)
                if first:
                    first = False
                    entered.set()
                    assert release.wait(5)
                return result
        else:
            async def blocked(*args, **kwargs):
                nonlocal first
                result = await original(*args, **kwargs)
                if first:
                    first = False
                    entered.set()
                    assert await asyncio.to_thread(release.wait, 5)
                return result
        with monkeypatch.context() as fault:
            fault.setattr(target, method, blocked)
            task = asyncio.create_task(engine._pipeline._materialize(extraction(), event, str(event.id)))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                await engine._write_queue.submit(lambda: asyncio.sleep(0))
        nodes = await engine.get_event_nodes(str(event.id), user_id=user)
        assert len(nodes) in (0, 2)
        receipt = await engine._event_store.get_derivation_receipt(str(event.id), user_id=user)
        assert (receipt is not None) == bool(nodes)
        plan = await engine._event_store.get_derivation_plan(str(event.id), user_id=user)
        assert plan is not None
        monkeypatch.setattr(engine._vector_index._provider, "embed", AsyncMock(side_effect=AssertionError("Saved plan must be reused")))
        await engine._pipeline._materialize(extraction(), event, str(event.id))
        assert {node.id for node in await engine.get_event_nodes(str(event.id), user_id=user)} == {node.id for node in plan.nodes}
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
        original = engine._graph_store.commit_derivation
        async def committed_then_blocked(*args, **kwargs):
            await original(*args, **kwargs)
            entered.set()
            await release.wait()
        monkeypatch.setattr(engine._graph_store, "commit_derivation", committed_then_blocked)
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
