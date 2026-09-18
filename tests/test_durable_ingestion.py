"""Behavioral recovery contract, exercised against both supported backends."""

import asyncio
import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest

from prme import FastIngestItem, MemoryEngine, PRMEConfig, StoreReceipt
from prme.models import Event
from prme.storage.fast_ingest import FastIngestConflict
from prme.types import Scope


class MockEmbeddingProvider:
    model_name = "durability-test"
    model_version = "1"
    dimension = 384

    async def embed(self, texts):
        return [
            [hashlib.sha256(text.encode()).digest()[i % 32] / 255 for i in range(self.dimension)]
            for text in texts
        ]


async def test_public_processing_status_survives_retry_and_restart(config, user, monkeypatch, caplog, capsys):
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest_fast("The telescope is blue", user_id=user)
        foreign = await engine.ingest_fast("Other user's note", user_id=user + "-other")
        assert await engine.processing_status(event_id, user_id=user + "-other") is None
        status = await engine.processing_status(event_id, user_id=user)
        assert status.status == "pending" and status.attempts == 0
        result = await engine.process_pending(user_id=user, budget_ms=0)
        assert (result.processed, result.pending, result.failed) == (0, 1, 0)

        with monkeypatch.context() as failure:
            failure.setattr(engine._vector_index, "index", AsyncMock(side_effect=RuntimeError("private provider detail")))
            result = await engine.process_pending(user_id=user)
        assert (result.processed, result.pending, result.failed) == (0, 1, 1)
        status = await engine.processing_status(event_id, user_id=user)
        assert status.last_error == "RuntimeError" and status.attempts == 1
        assert "private provider" not in status.model_dump_json()
        assert "private provider" not in caplog.text
        output = capsys.readouterr()
        assert "private provider" not in output.out + output.err
        assert any(event_id in record.getMessage() and "RuntimeError" in record.getMessage()
                   for record in caplog.records)

    async with MemoryEngine.open(config) as engine:
        status = await engine.processing_status(event_id, user_id=user)
        assert status.last_error == "RuntimeError"
        result = await engine.process_pending(user_id=user)
        assert (result.processed, result.pending, result.failed) == (1, 0, 0)
        status = await engine.processing_status(event_id, user_id=user)
        assert status.status == "complete" and status.attempts == 2
        assert status.last_error is None
        assert (await engine.processing_status(foreign, user_id=user + "-other")).status == "pending"
        assert (await engine.process_pending(user_id=user)).processed == 0


async def test_fast_batch_admission_preserves_order_fields_and_recovery(config, user):
    source_time = datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
    metadata = {"source": {"page": 1}}
    async with MemoryEngine.open(config) as engine:
        event_ids = await engine.ingest_fast_many(
            [
                FastIngestItem(
                    content="First batch source",
                    role="tool",
                    session_id="episode-a",
                    scope=Scope.PROJECT,
                    metadata=metadata,
                    event_time=source_time,
                ),
                {
                    "content": "Second batch source",
                    "role": "assistant",
                    "session_id": "episode-b",
                    "scope": "personal",
                },
            ],
            user_id=user,
        )
        metadata["source"]["page"] = 9

        assert len(event_ids) == len(set(event_ids)) == 2
        events = [await engine.get_event(event_id, user_id=user) for event_id in event_ids]
        assert [event.content for event in events] == [
            "First batch source",
            "Second batch source",
        ]
        assert events[0].metadata == {"source": {"page": 1}}
        assert events[0].event_time == source_time
        assert events[0].scope == Scope.PROJECT
        assert events[1].scope == Scope.PERSONAL
        statuses = [
            await engine.processing_status(event_id, user_id=user)
            for event_id in event_ids
        ]
        assert all(status.status == "pending" for status in statuses)

    async with MemoryEngine.open(config) as engine:
        result = await engine.process_pending(user_id=user, budget_ms=5000)
        assert (result.processed, result.pending, result.failed) == (2, 0, 0)
        for event_id in event_ids:
            nodes = await engine.get_event_nodes(event_id, user_id=user)
            assert len(nodes) == 1
            assert str(nodes[0].id) == event_id
            assert nodes[0].evidence_refs == [UUID(event_id)]


async def test_fast_batch_rejects_any_invalid_item_before_admission(config, user):
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError):
            await engine.ingest_fast_many(
                [
                    {"content": "Would otherwise be accepted"},
                    {
                        "content": "Invalid source clock",
                        "event_time": datetime(2026, 9, 14, 12, 30),
                    },
                ],
                user_id=user,
            )
        with pytest.raises(ValueError):
            await engine.ingest_fast_many(
                [
                    {"content": "Still must not be accepted"},
                    {"content": "Invalid metadata", "metadata": {"score": float("nan")}},
                ],
                user_id=user,
            )
        with pytest.raises(ValueError, match="request_id must be a UUID"):
            await engine.ingest_fast_many(
                [{"content": "Invalid request identity"}],
                user_id=user,
                request_id="not-a-uuid",
            )
        assert await engine._event_store.get_by_user(user) == []
        assert await engine.ingest_fast_many([], user_id=user) == []


async def test_fast_batch_snapshots_metadata_before_waiting_for_admission(
    config, user, monkeypatch
):
    reached, release = asyncio.Event(), asyncio.Event()
    metadata = {"source": {"page": 1}}
    async with MemoryEngine.open(config) as engine:
        original_submit = engine._write_queue.submit

        async def gated_submit(coro_factory, label=""):
            reached.set()
            await release.wait()
            return await original_submit(coro_factory, label=label)

        monkeypatch.setattr(engine._write_queue, "submit", gated_submit)
        task = asyncio.create_task(
            engine.ingest_fast_many(
                [{"content": "Frozen batch source", "metadata": metadata}],
                user_id=user,
            )
        )
        try:
            await asyncio.wait_for(reached.wait(), 5)
            metadata["source"]["page"] = 9
        finally:
            release.set()
        event_id = (await task)[0]
        event = await engine.get_event(event_id, user_id=user)
        assert event.metadata == {"source": {"page": 1}}


async def test_fast_batch_request_id_is_concurrent_and_restart_safe(config, user):
    request_id = uuid4()
    items = [
        {"content": "Idempotent first source", "scope": "project"},
        {"content": "Idempotent second source", "role": "tool"},
    ]
    async with MemoryEngine.open(config) as engine:
        first, concurrent_retry = await asyncio.gather(
            engine.ingest_fast_many(items, user_id=user, request_id=request_id),
            engine.ingest_fast_many(items, user_id=user, request_id=str(request_id)),
        )
        assert concurrent_retry == first
        assert len(await engine._event_store.get_by_user(user)) == 2

        with pytest.raises(FastIngestConflict):
            await engine.ingest_fast_many(
                [{"content": "Changed retry"}],
                user_id=user,
                request_id=request_id,
            )
        assert len(await engine._event_store.get_by_user(user)) == 2

    async with MemoryEngine.open(config) as engine:
        restart_retry = await engine.ingest_fast_many(
            items,
            user_id=user,
            request_id=request_id,
        )
        assert restart_retry == first
        assert len(await engine._event_store.get_by_user(user)) == 2
        assert (await engine.process_pending(user_id=user, budget_ms=5000)).processed == 2


async def test_event_store_batch_rolls_back_all_admissions_on_conflict(config, user):
    async with MemoryEngine.open(config) as engine:
        existing = Event(content="Existing source", user_id=user, role="user")
        await engine._event_store.append(existing)
        fresh = Event(content="Fresh source", user_id=user, role="user")
        conflicting = Event(
            id=existing.id,
            content="Conflicting source",
            user_id=user,
            role="user",
        )

        with pytest.raises(Exception):
            await engine._event_store.append_many(
                [fresh, conflicting],
                defer_materialization=True,
            )

        assert await engine.get_event(str(fresh.id), user_id=user) is None
        assert await engine.get_event(str(existing.id), user_id=user) == existing
        assert await engine.processing_status(str(fresh.id), user_id=user) is None


async def test_processing_status_tracks_direct_store_and_rejects_unknown_sources(config, user):
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.store("An ordinary direct write", user_id=user)
        assert (await engine.processing_status(event_id, user_id=user)).status == "complete"
        assert await engine.processing_status(event_id, user_id=user + "-other") is None
        assert await engine.processing_status(str(uuid4()), user_id=user) is None
        with pytest.raises(ValueError, match="budget_ms"):
            await engine.process_pending(user_id=user, budget_ms=-1)


async def test_structured_store_receipt_resolves_the_exact_direct_node(config, user):
    async with MemoryEngine.open(config) as engine:
        first, second = await asyncio.gather(*[
            engine.store_with_receipt(
                "The same concurrent memory", user_id=user, session_id="receipt-test",
            )
            for _ in range(2)
        ])

        assert all(isinstance(receipt, StoreReceipt) for receipt in (first, second))
        assert first.event_id != second.event_id
        assert first.node_id != second.node_id
        for receipt in (first, second):
            assert receipt.processing_status.status == "complete"
            assert receipt.processing_status.event_id == receipt.event_id
            assert receipt.node.evidence_refs == [receipt.event_id]
            assert await engine.get_node(str(receipt.node_id), user_id=user) == receipt.node


@pytest.fixture(params=["duckdb", "postgres"])
def config(request, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider(),
    )
    (tmp_path / "lexical").mkdir()
    args = dict(
        db_path=str(tmp_path / "memory.duckdb"),
        lexical_path=str(tmp_path / "lexical"),
        vector_path=str(tmp_path / "vectors.usearch"),
        organizer={"opportunistic_enabled": False},
        materialization_budget_ms=5000,
    )
    if request.param == "postgres":
        if not os.environ.get("PRME_TEST_DATABASE_URL"):
            pytest.skip("PRME_TEST_DATABASE_URL not set")
        args["database_url"] = os.environ["PRME_TEST_DATABASE_URL"]
        # Use the same dimension as the existing PostgreSQL test schema.
        provider = MockEmbeddingProvider()
        provider.dimension = 384
        monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: provider)
    return PRMEConfig(**args)


@pytest.fixture
def user():
    return f"durable-{uuid4()}"


async def test_restart_recovers_original_event(config, user):
    old_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest_fast(
            "Alice prefers dark mode", user_id=user, scope=Scope.PROJECT,
            session_id="session", metadata={"source": "test"}, event_time=old_time,
        )
        original = await engine.get_event(event_id)
        assert await engine.count_nodes(user_id=user) == 0
    async with MemoryEngine.open(config) as engine:
        assert engine.materialization_debt >= 1
        response = await engine.retrieve("dark mode", user_id=user, scope=Scope.PROJECT)
        assert any(str(r.node.id) == event_id for r in response.results)
        nodes = await engine.query_nodes(user_id=user)
        assert len(nodes) == 1
        node = nodes[0]
        assert node.evidence_refs == [UUID(event_id)]
        assert node.created_at == original.created_at
        assert node.valid_from == original.timestamp
        assert node.event_time == old_time
        assert node.session_id == "session"
        assert node.metadata == {"source": "test"}
        assert len(await engine.get_events(user)) == 1
        assert await engine._materialization_queue.drain(engine, user_id=user) == 0


async def test_fast_path_never_calls_embedding(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        index = AsyncMock(side_effect=AssertionError("fast path must not embed"))
        monkeypatch.setattr(engine._vector_index, "index", index)
        eid = await engine.ingest_fast("Durable before indexing", user_id=user)
        assert await engine.get_event(eid) is not None
        index.assert_not_awaited()


async def test_retry_after_partial_index_write_preserves_identity(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        eid = await engine.ingest_fast("A memorable telescope", user_id=user)
        original_index = engine._lexical_index.index
        monkeypatch.setattr(engine._lexical_index, "index", AsyncMock(side_effect=OSError("disk")))
        if hasattr(engine._lexical_index, "replace_many"):
            monkeypatch.setattr(engine._lexical_index, "replace_many", AsyncMock(side_effect=OSError("disk")))
        assert await engine._materialization_queue.drain(engine, 5000, user_id=user) == 0
        assert len(await engine._event_store.pending_materializations(user_id=user)) == 1
        assert await engine.count_nodes(user_id=user) == 1
        monkeypatch.setattr(engine._lexical_index, "index", original_index)
    async with MemoryEngine.open(config) as engine:
        assert await engine._materialization_queue.drain(engine, 5000, user_id=user) == 1
        assert len(await engine.get_events(user)) == 1
        assert await engine.count_nodes(user_id=user) == 1
        hits = await engine._lexical_index.search("telescope", user, limit=10)
        assert [hit["node_id"] for hit in hits] == [eid]
        if engine._conn is not None:
            count = engine._conn.execute(
                "SELECT count(*) FROM vector_metadata WHERE node_id = ?", [eid],
            ).fetchone()[0]
            assert count == 1


async def test_scoped_drain_and_concurrent_consumers(config, user):
    other = f"other-{uuid4()}"
    async with MemoryEngine.open(config) as engine:
        await engine.ingest_fast("Other tenant's memory", user_id=other)
        ids = await asyncio.gather(*[
            engine.ingest_fast(f"Alice's memory number {i}", user_id=user)
            for i in range(5)
        ])
        await asyncio.gather(*[
            engine._materialization_queue.drain(engine, 5000, user_id=user)
            for _ in range(3)
        ])
        assert len(await engine.get_events(user)) == len(ids)
        assert await engine.count_nodes(user_id=user) == len(ids)
        assert await engine.count_nodes(user_id=other) == 0
        assert len(await engine._event_store.pending_materializations(user_id=other)) == 1


async def test_failed_item_does_not_starve_later_items(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        first = await engine.ingest_fast("Cannot index this yet", user_id=user)
        second = await engine.ingest_fast("Can index this", user_id=user)
        materialize = engine._materialize_event
        async def sometimes_fail(event, **kwargs):
            if str(event.id) == first:
                raise RuntimeError("transient error")
            await materialize(event, **kwargs)
        monkeypatch.setattr(engine, "_materialize_event", sometimes_fail)
        assert await engine._materialization_queue.drain(engine, 5000, user_id=user) == 1
        assert await engine.get_node(second) is not None
        assert [str(e.id) for e in await engine._event_store.pending_materializations(user_id=user)] == [first]


async def test_event_and_work_commit_atomically(config, user):
    if config.backend != "duckdb":
        pytest.skip("DuckDB transaction fault injection")
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Must roll back", user_id=user, role="user")
        # Force the second insert to fail after the event insert succeeded.
        engine._conn.execute(
            "INSERT INTO event_materializations (event_id) VALUES (?)", [str(event.id)],
        )
        with pytest.raises(Exception):
            await engine._event_store.append(event, defer_materialization=True)
        assert await engine.get_event(str(event.id)) is None


async def test_process_exit_without_close_recovers(config, user):
    if config.backend != "duckdb":
        pytest.skip("Local process crash recovery")
    script = '''
import asyncio, os, sys
from prme import MemoryEngine, PRMEConfig
async def main():
    engine = await MemoryEngine.create(PRMEConfig.model_validate_json(sys.argv[1]))
    await engine.ingest_fast("The emergency contact is Alice", user_id=sys.argv[2])
    os._exit(0)
asyncio.run(main())
'''
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, config.model_dump_json(), user],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    async with MemoryEngine.open(config) as engine:
        response = await engine.retrieve("emergency contact", user_id=user)
        assert any("Alice" in r.node.content for r in response.results)
        assert len(await engine.get_events(user)) == 1


async def test_postgres_independent_consumers(config, user):
    if config.backend != "postgres":
        pytest.skip("Independent PostgreSQL workers")
    async with MemoryEngine.open(config) as first, MemoryEngine.open(config) as second:
        eid = await first.ingest_fast("A shared worker memory", user_id=user)
        await asyncio.gather(
            first._materialization_queue.drain(first, 5000, user_id=user),
            second._materialization_queue.drain(second, 5000, user_id=user),
        )
        assert await first.count_nodes(user_id=user) == 1
        assert len(await first.get_events(user)) == 1
        assert await first._event_store.pending_materializations(user_id=user) == []
        assert any(
            r["node_id"] == eid
            for r in await second._vector_index.search("shared worker memory", user)
        )
