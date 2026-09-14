"""Lifecycle races must not reactivate terminal memories."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from prme import MemoryEngine
from prme.types import LifecycleState
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_concurrent_archive_cannot_be_overwritten_by_stale_promotion(
    config, user, monkeypatch
):
    if config.backend != "postgres":
        pytest.skip("PostgreSQL inter-connection transition race")
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("Memory to archive", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        graph = engine._graph_store
        original = graph._pool
        read, release = asyncio.Event(), asyncio.Event()
        promotion = None

        class Connection:
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            async def fetchrow(self, query, *args, **kwargs):
                row = await self.conn.fetchrow(query, *args, **kwargs)
                if (
                    asyncio.current_task() is promotion
                    and "FROM nodes" in query
                    and ("SELECT lifecycle_state" in query or "FOR UPDATE" in query)
                ):
                    read.set()
                    await asyncio.wait_for(release.wait(), 15)
                return row

        class Pool:
            @asynccontextmanager
            async def acquire(self):
                async with original.acquire() as conn:
                    yield Connection(conn)

        with monkeypatch.context() as scheduling:
            scheduling.setattr(graph, "_pool", Pool())
            promotion = asyncio.create_task(engine.promote(str(node.id), user_id=user))
            archival = None
            try:
                await asyncio.wait_for(read.wait(), 10)
                archival = asyncio.create_task(
                    engine.archive(str(node.id), user_id=user)
                )
                # An unprotected archive can commit while the old promotion is
                # paused. With a held row lock it waits; do not cancel that work.
                await asyncio.wait({archival}, timeout=1)
            finally:
                release.set()
                await asyncio.gather(promotion, *([archival] if archival else []))
        after = await engine.get_node(
            str(node.id), include_superseded=True, user_id=user
        )
        assert after.lifecycle_state == LifecycleState.ARCHIVED


async def test_supported_backends_deprecate_contested_memory(config, user):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("Disputed memory", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        await engine._graph_store.update_node(
            str(node.id), lifecycle_state=LifecycleState.CONTESTED
        )
        await engine._graph_store.deprecate(str(node.id))
        after = await engine.get_node(
            str(node.id), include_superseded=True, user_id=user
        )
        assert after.lifecycle_state == LifecycleState.DEPRECATED


async def journal_records(engine, node_id):
    from prme.storage.lifecycle import read_record

    graph = engine._graph_store
    if hasattr(graph, "_conn"):
        async with graph._conn_lock:
            rows = graph._conn.execute(
                "SELECT payload FROM operations WHERE op_type='LIFECYCLE_CHANGED' AND target_id=? ORDER BY created_at,id",
                [node_id],
            ).fetchall()
        return [read_record(row[0]) for row in rows]
    async with graph._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT payload FROM operations WHERE op_type='LIFECYCLE_CHANGED' AND target_id=$1 ORDER BY created_at,id",
            node_id,
        )
    return [read_record(row["payload"]) for row in rows]


@pytest.mark.parametrize("action", ["promote", "archive", "deprecate"])
@pytest.mark.parametrize("stage", ["updated", "journal"])
async def test_lifecycle_failure_rolls_back_state_and_journal(
    config, user, action, stage, monkeypatch
):
    from prme.storage import lifecycle

    async with MemoryEngine.open(config) as engine:
        source = await engine.store("A qualified memory", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        if action == "deprecate":
            await engine._graph_store.update_node(
                str(node.id), lifecycle_state=LifecycleState.CONTESTED
            )
        before = await engine.get_node(str(node.id), user_id=user)

        def fail(actual):
            if actual == stage:
                raise RuntimeError("Authored lifecycle transaction fault")

        with monkeypatch.context() as fault:
            fault.setattr(lifecycle, "_checkpoint", fail)
            with pytest.raises(
                RuntimeError, match="Authored lifecycle transaction fault"
            ):
                await getattr(engine._graph_store, action)(str(node.id))
        assert (
            await engine.get_node(str(node.id), include_superseded=True, user_id=user)
            == before
        )
        assert await journal_records(engine, str(node.id)) == []
    async with MemoryEngine.open(config) as engine:
        assert (
            await engine.get_node(str(node.id), include_superseded=True, user_id=user)
            == before
        )
        await getattr(engine._graph_store, action)(str(node.id))
        after = await engine.get_node(
            str(node.id), include_superseded=True, user_id=user
        )
        records = await journal_records(engine, str(node.id))
        assert len(records) == 1
        assert records[0].action == action
        assert records[0].before == before and records[0].after == after
    async with MemoryEngine.open(config) as engine:
        assert await journal_records(engine, str(node.id)) == records


async def test_archive_stays_committed_when_index_eviction_fails(
    config, user, monkeypatch
):
    from unittest.mock import AsyncMock

    async with MemoryEngine.open(config) as engine:
        source = await engine.store("Retired evidence", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        monkeypatch.setattr(
            engine,
            "_delete_from_indexes",
            AsyncMock(side_effect=OSError("Authored index failure")),
        )
        await engine.archive(str(node.id), user_id=user)
        after = await engine.get_node(
            str(node.id), include_superseded=True, user_id=user
        )
        assert after.lifecycle_state == LifecycleState.ARCHIVED
        assert (await journal_records(engine, str(node.id)))[0].after == after
        assert await engine.get_node(str(node.id), user_id=user) is None


@pytest.mark.parametrize("action", ["promote", "archive"])
async def test_public_lifecycle_request_is_retry_safe_across_restart(
    config, user, action
):
    from uuid import uuid4
    from prme.storage.lifecycle import LifecycleConflict

    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("Retry-safe lifecycle", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        await getattr(engine, action)(
            str(node.id), user_id=user, request_id=request_id, actor_id="reviewer"
        )
    async with MemoryEngine.open(config) as engine:
        await getattr(engine, action)(
            str(node.id).upper(), user_id=user,
            request_id=request_id, actor_id="reviewer",
        )
        assert len(await journal_records(engine, str(node.id))) == 1
        second_source = await engine.store("Different memory", user_id=user)
        second = (await engine.get_event_nodes(second_source, user_id=user))[0]
        with pytest.raises(LifecycleConflict, match="different inputs"):
            await getattr(engine, action)(
                str(second.id), user_id=user,
                request_id=request_id, actor_id="reviewer",
            )


@pytest.mark.parametrize("action", ["promote", "archive"])
async def test_foreign_owner_cannot_transition_or_journal_node(config, user, action):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("Owned memory", user_id=user)
        before = (await engine.get_event_nodes(source, user_id=user))[0]
        with pytest.raises(ValueError, match="not found"):
            await getattr(engine, action)(str(before.id), user_id=user + "-foreign")
        assert await engine.get_node(str(before.id), user_id=user) == before
        assert await journal_records(engine, str(before.id)) == []


@pytest.mark.parametrize("action", ["promote", "archive", "deprecate"])
async def test_terminal_node_rejects_transition_without_new_record(
    config, user, action
):
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("An archived memory", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]
        await engine.archive(str(node.id), user_id=user)
        before = await engine.get_node(
            str(node.id), include_superseded=True, user_id=user
        )
        records = await journal_records(engine, str(node.id))
        with pytest.raises(ValueError, match="not allowed"):
            await getattr(engine._graph_store, action)(str(node.id))
        assert (
            await engine.get_node(str(node.id), include_superseded=True, user_id=user)
            == before
        )
        assert await journal_records(engine, str(node.id)) == records


async def test_cancelled_native_archive_finishes_graph_and_journal(
    config, user, monkeypatch
):
    import threading
    from prme.storage import lifecycle

    if config.backend != "duckdb":
        pytest.skip("DuckDB native worker cancellation boundary")
    entered, release = threading.Event(), threading.Event()
    async with MemoryEngine.open(config) as engine:
        source = await engine.store("Retired native work", user_id=user)
        node = (await engine.get_event_nodes(source, user_id=user))[0]

        def pause(stage):
            if stage == "journal":
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Authored checkpoint was not released")

        with monkeypatch.context() as fault:
            fault.setattr(lifecycle, "_checkpoint", pause)
            task = asyncio.create_task(engine.archive(str(node.id), user_id=user))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                task.cancel()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        after = await engine.get_node(
            str(node.id), include_superseded=True, user_id=user
        )
        assert after.lifecycle_state == LifecycleState.ARCHIVED
        assert (await journal_records(engine, str(node.id)))[0].after == after
        assert await engine.get_node(str(node.id), user_id=user) is None


def test_record_parser_rejects_corruption_and_unrelated_changes():
    import json
    from uuid import uuid4
    from prme.models.nodes import MemoryNode
    from prme.storage.lifecycle import LifecycleRecord, _payload, read_record
    from prme.types import NodeType

    before = MemoryNode(
        user_id="record-owner", node_type=NodeType.FACT, content="Original"
    )
    after = before.model_copy(update={"lifecycle_state": LifecycleState.STABLE})
    record = LifecycleRecord(
        operation_id=uuid4(), action="promote", before=before, after=after
    )
    assert read_record(_payload(record)) == record
    corrupt = json.loads(_payload(record))
    corrupt["record"] += " "
    with pytest.raises(ValueError, match="checksum"):
        read_record(corrupt)
    changed = record.model_copy(
        update={"after": after.model_copy(update={"content": "Changed"})}
    )
    with pytest.raises(ValueError, match="unrelated"):
        read_record(_payload(changed))
    invalid = record.model_copy(
        update={
            "before": before.model_copy(
                update={"lifecycle_state": LifecycleState.ARCHIVED}
            )
        }
    )
    with pytest.raises(ValueError, match="not allowed"):
        read_record(_payload(invalid))
