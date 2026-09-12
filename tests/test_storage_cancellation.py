"""Cancellation must not release storage locks while native writes still run."""

import asyncio
import threading

import duckdb
import pytest

from prme.models import Event
from prme.models.nodes import MemoryNode
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.event_store import EventStore
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.types import NodeType


async def cancel_blocked_native_call(monkeypatch, store, method, coroutine, lock):
    entered, release = threading.Event(), threading.Event()
    original = getattr(store, method)
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, method, blocked)
    task = asyncio.create_task(coroutine)
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert lock.locked()
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not lock.locked()


async def test_cancelled_event_append_finishes_atomic_source_and_work_record(tmp_path, monkeypatch):
    conn = duckdb.connect(str(tmp_path / "memory.duckdb"))
    initialize_database(conn)
    store = EventStore(conn)
    event = Event(content="Durable source under cancellation", user_id="alice", role="user")
    try:
        await cancel_blocked_native_call(
            monkeypatch, store, "_append_with_work_sync",
            store.append(event, defer_materialization=True), store._conn_lock,
        )
        assert (await store.get(str(event.id))).content == event.content
        assert await store.materialization_count() == 1
    finally:
        conn.close()


async def test_cancelled_graph_replacement_finishes_before_connection_is_reused(tmp_path, monkeypatch):
    conn = duckdb.connect(str(tmp_path / "memory.duckdb"))
    initialize_database(conn)
    graph = DuckPGQGraphStore(conn)
    old, new = [MemoryNode(content=text, user_id="alice", node_type=NodeType.FACT) for text in ("old", "new")]
    try:
        await graph.create_node(old)
        await graph.create_node(new)
        await cancel_blocked_native_call(
            monkeypatch, graph, "_supersede_many_sync",
            graph.supersede(str(old.id), str(new.id)), graph._conn_lock,
        )
        retired = await graph.get_node(str(old.id), include_superseded=True)
        assert retired.superseded_by == new.id
        assert len(await graph.get_edges(source_id=str(new.id), target_id=str(old.id))) == 1
    finally:
        conn.close()


async def test_cancelled_lexical_close_finishes_commit_and_releases_directory(tmp_path, monkeypatch):
    directory = tmp_path / "lexical"
    directory.mkdir()
    index = LexicalIndex(str(directory), commit_interval=100)
    await index.index("one", "durable telescope", "alice", "fact", "personal")
    await cancel_blocked_native_call(
        monkeypatch, index, "_commit_locked", index.close(), index._write_lock,
    )
    reopened = LexicalIndex(str(directory))
    try:
        assert [r["node_id"] for r in await reopened.search("telescope", "alice")] == ["one"]
        # Acquiring a new writer proves the cancelled close released its handle.
        await reopened.index("two", "second telescope", "alice", "fact", "personal")
    finally:
        await reopened.close()
