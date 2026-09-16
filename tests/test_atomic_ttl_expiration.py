"""TTL policy archival must keep state and tombstone inseparable."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from prme import MemoryEngine
from prme.models.nodes import MemoryNode
from prme.organizer.jobs import run_job
from prme.storage.retention import read_record
from prme.types import LifecycleState, NodeType
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user

CREATED = datetime(2025, 1, 1, tzinfo=timezone.utc)
EVALUATED = CREATED + timedelta(days=2)


async def seed(engine, user, *, ttl_days=1, pinned=False, created_at=CREATED):
    node = MemoryNode(
        user_id=user,
        node_type=NodeType.NOTE,
        content="Short-lived source",
        created_at=created_at,
        updated_at=created_at,
        valid_from=created_at,
        last_reinforced_at=created_at,
        ttl_days=ttl_days,
        pinned=pinned,
    )
    await engine._graph_store.create_node(node)
    return node


async def tombstones(engine, node_id=None):
    graph = engine._graph_store
    if hasattr(graph, "_conn"):
        async with graph._conn_lock:
            if node_id is None:
                return graph._conn.execute(
                    "SELECT id,target_id,payload,actor_id,namespace_id "
                    "FROM operations WHERE op_type='TOMBSTONE_SWEEP' ORDER BY id"
                ).fetchall()
            return graph._conn.execute(
                "SELECT id,target_id,payload,actor_id,namespace_id "
                "FROM operations WHERE op_type='TOMBSTONE_SWEEP' AND target_id=?",
                [node_id],
            ).fetchall()
    async with graph._pool.acquire() as conn:
        if node_id is None:
            rows = await conn.fetch(
                "SELECT id,target_id,payload,actor_id,namespace_id "
                "FROM operations WHERE op_type='TOMBSTONE_SWEEP' ORDER BY id"
            )
        else:
            rows = await conn.fetch(
                "SELECT id,target_id,payload,actor_id,namespace_id "
                "FROM operations WHERE op_type='TOMBSTONE_SWEEP' AND target_id=$1",
                node_id,
            )
    return [tuple(row) for row in rows]


async def test_expiration_retains_complete_transition_and_replays(config, user):
    async with MemoryEngine.open(config) as engine:
        node = await seed(engine, user)
        before = await engine.get_node(str(node.id), user_id=user)
        assert await engine._graph_store.archive_expired(
            str(node.id), user_id=user, evaluated_at=EVALUATED
        )
        after = await engine.get_node(
            str(node.id), user_id=user, include_superseded=True
        )
        rows = await tombstones(engine, str(node.id))
        assert len(rows) == 1
        operation_id, target_id, payload, actor_id, namespace_id = rows[0]
        record = read_record(payload)
        assert str(record.operation_id) == operation_id
        assert target_id == str(node.id)
        assert actor_id == user
        assert namespace_id == node.scope.value
        assert record.before == before and record.after == after
        assert record.expires_at == CREATED + timedelta(days=1)
        assert record.evaluated_at == EVALUATED
        assert record.content_hash_of_deleted == hashlib.sha256(
            node.content.encode()
        ).hexdigest()
        assert record.after.lifecycle_state == LifecycleState.ARCHIVED
        summary = json.loads(payload) if isinstance(payload, str) else payload
        assert summary["reason"] == "retention_policy_expiry"
        assert summary["policy_ref"] == "ttl_expiration_v1"
        tampered = dict(summary)
        tampered["reason"] = "user_request"
        with pytest.raises(ValueError, match="summary mismatch"):
            read_record(tampered)

    async with MemoryEngine.open(config) as engine:
        assert not await engine._graph_store.archive_expired(
            str(node.id),
            user_id=user,
            evaluated_at=EVALUATED + timedelta(days=1),
        )
        assert len(await tombstones(engine, str(node.id))) == 1
        assert (
            await engine.get_node(
                str(node.id), user_id=user, include_superseded=True
            )
        ) == after


@pytest.mark.parametrize("stage", ["validated", "updated", "journal"])
async def test_expiration_failure_rolls_back_state_and_tombstone(
    config, user, stage, monkeypatch
):
    from prme.storage import retention

    async with MemoryEngine.open(config) as engine:
        node = await seed(engine, user)
        before = await engine.get_node(str(node.id), user_id=user)

        def fail(actual):
            if actual == stage:
                raise RuntimeError("Authored retention transaction fault")

        with monkeypatch.context() as fault:
            fault.setattr(retention, "_checkpoint", fail)
            with pytest.raises(
                RuntimeError, match="Authored retention transaction fault"
            ):
                await engine._graph_store.archive_expired(
                    str(node.id), user_id=user, evaluated_at=EVALUATED
                )
        assert await engine.get_node(str(node.id), user_id=user) == before
        assert await tombstones(engine, str(node.id)) == []

    async with MemoryEngine.open(config) as engine:
        assert await engine._graph_store.archive_expired(
            str(node.id), user_id=user, evaluated_at=EVALUATED
        )
        assert len(await tombstones(engine, str(node.id))) == 1


async def test_concurrent_expiration_publishes_once(config, user):
    async with MemoryEngine.open(config) as engine:
        node = await seed(engine, user)
        results = await asyncio.gather(
            *[
                engine._graph_store.archive_expired(
                    str(node.id), user_id=user, evaluated_at=EVALUATED
                )
                for _ in range(2)
            ]
        )
        assert sorted(results) == [False, True]
        assert len(await tombstones(engine, str(node.id))) == 1
        archived = await engine.get_node(
            str(node.id), user_id=user, include_superseded=True
        )
        assert archived.lifecycle_state == LifecycleState.ARCHIVED


@pytest.mark.parametrize("condition", ["no_ttl", "pinned", "not_expired"])
async def test_ineligible_retention_targets_are_unchanged(config, user, condition):
    async with MemoryEngine.open(config) as engine:
        node = await seed(
            engine,
            user,
            ttl_days=None if condition == "no_ttl" else 1,
            pinned=condition == "pinned",
            created_at=EVALUATED if condition == "not_expired" else CREATED,
        )
        before = await engine.get_node(str(node.id), user_id=user)
        assert not await engine._graph_store.archive_expired(
            str(node.id), user_id=user, evaluated_at=EVALUATED
        )
        assert await engine.get_node(str(node.id), user_id=user) == before
        assert await tombstones(engine, str(node.id)) == []


async def test_expiration_rejects_foreign_owner_and_naive_clock(config, user):
    async with MemoryEngine.open(config) as engine:
        node = await seed(engine, user)
        with pytest.raises(ValueError, match="unavailable"):
            await engine._graph_store.archive_expired(
                str(node.id), user_id=user + "-other", evaluated_at=EVALUATED
            )
        with pytest.raises(ValueError, match="timezone-aware"):
            await engine._graph_store.archive_expired(
                str(node.id),
                user_id=user,
                evaluated_at=EVALUATED.replace(tzinfo=None),
            )
        assert await engine.get_node(str(node.id), user_id=user) == node
        assert await tombstones(engine, str(node.id)) == []


async def test_expiration_cancellation_has_one_durable_outcome(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        node = await seed(engine, user)
        graph = engine._graph_store
        if hasattr(graph, "_conn"):
            import threading

            from prme.storage import retention

            entered, release = threading.Event(), threading.Event()

            def pause(stage):
                if stage == "updated":
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError(
                            "Retention cancellation synchronization timed out"
                        )

            with monkeypatch.context() as patch:
                patch.setattr(retention, "_checkpoint", pause)
                task = asyncio.create_task(
                    graph.archive_expired(
                        str(node.id), user_id=user, evaluated_at=EVALUATED
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
            from prme.storage import retention

            entered = asyncio.Event()
            checkpoint = retention._checkpoint

            def pause(stage):
                checkpoint(stage)
                if stage == "updated":
                    entered.set()

            with monkeypatch.context() as patch:
                patch.setattr(retention, "_checkpoint", pause)
                original = graph._record_to_node

                def block_after_update(row):
                    result = original(row)
                    if entered.is_set():
                        raise asyncio.CancelledError
                    return result

                patch.setattr(graph, "_record_to_node", block_after_update)
                task = asyncio.create_task(
                    graph.archive_expired(
                        str(node.id), user_id=user, evaluated_at=EVALUATED
                    )
                )
                with pytest.raises(asyncio.CancelledError):
                    await task
            committed = False

        if not committed:
            assert await engine.get_node(str(node.id), user_id=user) == node
            assert await tombstones(engine, str(node.id)) == []
        retry = await graph.archive_expired(
            str(node.id), user_id=user, evaluated_at=EVALUATED
        )
        assert retry == (not committed)
        assert len(await tombstones(engine, str(node.id))) == 1


async def test_tombstone_job_uses_atomic_backend_operation(config, user):
    async with MemoryEngine.open(config) as engine:
        node = await seed(engine, user)
        result = await run_job(
            "tombstone_sweep", engine, config.organizer, 5000, user_id=user
        )
        assert (result.nodes_modified, result.errors) == (1, 0)
        rows = await tombstones(engine, str(node.id))
        assert len(rows) == 1
        record = read_record(rows[0][2])
        assert record.before.id == node.id
        assert record.after.lifecycle_state == LifecycleState.ARCHIVED
