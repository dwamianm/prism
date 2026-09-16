"""Abandoned staging is reclaimed without touching pending or published views."""

from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine, StaleProfileError
from prme.types import Scope
from prme.storage.profile_work import ProfileStageFence
from tests.test_profile_work import prepare_only
from tests.test_profile_registry import execute
from tests.test_knowledge_profile_scopes import seed, profiles
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


async def staged(engine, user, monkeypatch, *, scope=Scope.PROJECT, abandon=True):
    await seed(engine, user)
    plan = await prepare_only(engine, user, monkeypatch, scope=scope)
    if engine._pool is None:
        fence = ProfileStageFence(engine._conn, engine._event_store._conn_lock, plan)
        await engine._vector_index.stage(plan.embedding, user_id=user, fence=fence)
        await engine._lexical_index.stage_profile(plan, fence=fence)
    if abandon:
        assert await engine.discard_profile(str(plan.node.id), user_id=user)
    return plan


async def test_collection_preserves_other_owners_pending_work_and_all_journals(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        old = await staged(engine, user, monkeypatch)
        foreign = await staged(engine, user + "-other", monkeypatch)
        pending = await staged(
            engine, user, monkeypatch, scope=Scope.PERSONAL, abandon=False
        )
        result = await engine.collect_profile_staging(user_id=user, scope=Scope.PROJECT)
        assert result == {
            "collected": 1,
            "failed": 0,
            "remaining": 0,
            "errors": {},
            "blocked_reason": None,
        }
        assert (
            await engine._profile_work.get(str(old.node.id), user_id=user)
        ).checksum == old.checksum
        assert await engine.profile_jobs(user_id=user, scope=Scope.PROJECT) == []
        if engine._pool is None:
            ids = {
                r[0]
                for r in engine._conn.execute(
                    "SELECT node_id FROM vector_metadata"
                ).fetchall()
            }
            assert str(old.node.id) not in ids
            assert {str(foreign.node.id), str(pending.node.id)} <= ids
            assert str(old.node.id) not in {
                r["node_id"]
                for r in await engine._lexical_index.search("Aurora", user, limit=100)
            }
        assert await engine.resume_profile(str(pending.node.id), user_id=user) == str(
            pending.node.id
        )
        with pytest.raises(StaleProfileError):
            await engine.resume_profile(str(old.node.id), user_id=user)
    async with MemoryEngine.open(config) as engine:
        assert (await engine.collect_profile_staging(user_id=user))["collected"] == 0
        assert (await engine.collect_profile_staging(user_id=user + "-other"))[
            "collected"
        ] == 1
        assert [n.id for n in await profiles(engine, user)] == [pending.node.id]
        assert (
            await engine._profile_work.get(str(old.node.id), user_id=user)
        ).checksum == old.checksum


@pytest.mark.parametrize("boundary", ["lexical", "vector", "acknowledgement"])
async def test_failed_collection_retries_from_the_saved_plan(
    config, user, monkeypatch, boundary
):
    if config.database_url and boundary != "acknowledgement":
        pytest.skip("PostgreSQL has no external staged indexes")
    async with MemoryEngine.open(config) as engine:
        plan = await staged(engine, user, monkeypatch)
        with monkeypatch.context() as fault:
            if boundary == "acknowledgement":
                fault.setattr(
                    "prme.storage.profile_collection.acknowledge",
                    AsyncMock(side_effect=OSError("authored outage")),
                )
            else:
                fault.setattr(
                    getattr(engine, f"_{boundary}_index"),
                    "delete_profile_stage",
                    AsyncMock(side_effect=OSError("authored outage")),
                )
            result = await engine.collect_profile_staging(user_id=user)
            assert result == {
                "collected": 0,
                "failed": 1,
                "remaining": 1,
                "errors": {str(plan.node.id): "OSError"},
                "blocked_reason": None,
            }
    async with MemoryEngine.open(config) as engine:
        engine._vector_index._provider.embed = AsyncMock(
            side_effect=AssertionError("Cannot infer to delete")
        )
        result = await engine.collect_profile_staging(user_id=user)
        assert result["collected"] == 1 and result["remaining"] == result["failed"] == 0
        assert await engine.get_node(str(plan.sources[0].id)) is not None


async def test_lost_collection_acknowledgement_is_confirmed(config, user, monkeypatch):
    from prme.storage.profile_collection import acknowledge

    async with MemoryEngine.open(config) as engine:
        plan = await staged(engine, user, monkeypatch)

        async def lost(*args):
            await acknowledge(*args)
            raise OSError("Lost acknowledgement")

        monkeypatch.setattr("prme.storage.profile_collection.acknowledge", lost)
        result = await engine.collect_profile_staging(user_id=user)
        assert result == {
            "collected": 1,
            "failed": 0,
            "remaining": 0,
            "errors": {},
            "blocked_reason": None,
        }
        assert (
            await engine._profile_work.get(str(plan.node.id), user_id=user)
        ).checksum == plan.checksum


@pytest.mark.parametrize(
    "obstacle", ["ambiguous_owner", "graph_node", "unregistered_journal"]
)
async def test_uncertain_collection_authority_preserves_staging(
    config, user, monkeypatch, obstacle
):
    async with MemoryEngine.open(config) as engine:
        plan = await staged(engine, user, monkeypatch)
        invalid_operation = None
        if obstacle == "ambiguous_owner":
            await execute(
                engine,
                "UPDATE derivation_artifact_owners SET operation_id=NULL WHERE node_id=?",
                [str(plan.node.id)],
            )
        elif obstacle == "graph_node":
            await engine._graph_store.create_node(plan.node)
        else:
            from uuid import uuid4

            invalid_operation = str(uuid4())
            await execute(
                engine,
                "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id) VALUES (?,'PROFILE_PREPARED',?,'{}',?,?)",
                [invalid_operation, str(uuid4()), user, Scope.PROJECT.value],
            )
        try:
            result = await engine.collect_profile_staging(user_id=user)
            assert result["collected"] == 0 and result["remaining"] == 1
            if obstacle == "unregistered_journal":
                assert result["blocked_reason"] == "IncompleteOwnershipRegistry"
            else:
                assert result["failed"] == 1
            if engine._pool is None:
                assert engine._conn.execute(
                    "SELECT 1 FROM vector_staging WHERE node_id=?", [str(plan.node.id)]
                ).fetchone()
        finally:
            if invalid_operation is not None:
                await execute(
                    engine, "DELETE FROM operations WHERE id=?", [invalid_operation]
                )


@pytest.mark.parametrize("checkpoint", ["lexical", "vector", "acknowledgement"])
async def test_process_exit_resumes_collection_without_models(
    config, user, monkeypatch, checkpoint
):
    if config.database_url:
        pytest.skip("Local external-index process recovery")
    import asyncio
    import subprocess
    import sys

    async with MemoryEngine.open(config) as engine:
        plan = await staged(engine, user, monkeypatch)
    script = """
import asyncio, os, sys
from prme import MemoryEngine, PRMEConfig
from tests.test_durable_ingestion import MockEmbeddingProvider
import prme.storage.engine
import prme.storage.profile_collection as collection
prme.storage.engine.create_embedding_provider = lambda _: MockEmbeddingProvider()
async def main():
    engine = await MemoryEngine.create(PRMEConfig.model_validate_json(sys.argv[1]))
    boundary = sys.argv[3]
    if boundary == 'acknowledgement':
        original = collection.acknowledge
        async def fault(*args):
            await original(*args)
            os._exit(42)
        collection.acknowledge = fault
    else:
        index = getattr(engine, f'_{boundary}_index')
        original = index.delete_profile_stage
        async def fault(*args, **kwargs):
            await original(*args, **kwargs)
            os._exit(42)
        index.delete_profile_stage = fault
    await engine.collect_profile_staging(user_id=sys.argv[2])
    raise AssertionError('Checkpoint not reached')
asyncio.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, config.model_dump_json(), user, checkpoint],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 42, result.stderr
    async with MemoryEngine.open(config) as engine:
        engine._vector_index._provider.embed = AsyncMock(
            side_effect=AssertionError("No model calls")
        )
        result = await engine.collect_profile_staging(user_id=user)
        assert result["collected"] == (0 if checkpoint == "acknowledgement" else 1)
        assert result["remaining"] == result["failed"] == 0
        assert (
            await engine._profile_work.get(str(plan.node.id), user_id=user)
        ).checksum == plan.checksum
        assert (
            engine._conn.execute(
                "SELECT 1 FROM vector_staging WHERE node_id=?", [str(plan.node.id)]
            ).fetchone()
            is None
        )
        assert str(plan.node.id) not in {
            r["node_id"]
            for r in await engine._lexical_index.search("Aurora", user, limit=100)
        }
        # A worker retaining the old plan cannot repopulate collected indexes.
        fence = ProfileStageFence(engine._conn, engine._event_store._conn_lock, plan)
        with pytest.raises(StaleProfileError):
            await engine._vector_index.stage(plan.embedding, user_id=user, fence=fence)
        with pytest.raises(StaleProfileError):
            await engine._lexical_index.stage_profile(plan, fence=fence)
        assert await engine.get_node(str(plan.sources[0].id)) is not None


@pytest.mark.parametrize("index_name", ["vector", "lexical"])
async def test_foreign_native_inputs_are_preserved(
    config, user, monkeypatch, index_name
):
    if config.database_url:
        pytest.skip("Local native identity validation")
    async with MemoryEngine.open(config) as engine:
        plan = await staged(engine, user, monkeypatch)
        if index_name == "vector":
            engine._conn.execute(
                "UPDATE vector_metadata SET user_id=? WHERE node_id=?",
                [user + "-other", str(plan.node.id)],
            )
        else:
            await engine._lexical_index.delete_by_node_id(str(plan.node.id))
            await engine._lexical_index.index(
                str(plan.node.id),
                "Foreign document",
                user + "-other",
                "summary",
                scope=Scope.PROJECT.value,
            )
            await engine._lexical_index.flush()
        result = await engine.collect_profile_staging(user_id=user)
        assert result["collected"] == 0 and result["remaining"] == result["failed"] == 1
        assert engine._conn.execute(
            "SELECT 1 FROM vector_staging WHERE node_id=?", [str(plan.node.id)]
        ).fetchone()
        if index_name == "lexical":
            assert any(
                r["node_id"] == str(plan.node.id)
                for r in await engine._lexical_index.search(
                    "Foreign document", user + "-other"
                )
            )


async def test_collection_budget_and_failure_order(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        first = await staged(engine, user, monkeypatch)
        second = await staged(engine, user, monkeypatch, scope=Scope.PERSONAL)
        assert (await engine.collect_profile_staging(user_id=user, budget_ms=0)) == {
            "collected": 0,
            "failed": 0,
            "remaining": 2,
            "errors": {},
            "blocked_reason": None,
        }
        await execute(
            engine,
            "UPDATE derivation_artifact_owners SET operation_id=NULL WHERE node_id=?",
            [str(first.node.id)],
        )
        assert (await engine.collect_profile_staging(user_id=user, limit=1))[
            "failed"
        ] == 1
        result = await engine.collect_profile_staging(user_id=user, limit=1)
        assert result["collected"] == 1 and result["remaining"] == 1
        assert (
            await engine._profile_work.get(str(second.node.id), user_id=user)
        ).checksum == second.checksum
        for invalid in [-1, float("nan"), float("inf")]:
            with pytest.raises(ValueError):
                await engine.collect_profile_staging(user_id=user, budget_ms=invalid)


@pytest.mark.parametrize("index_name", ["vector", "lexical"])
async def test_cancelled_native_delete_keeps_fence_and_replayable_work(
    config, user, monkeypatch, index_name
):
    if config.database_url:
        pytest.skip("Local native delete fence")
    import asyncio
    import threading
    from prme.storage.profile_work import ProfileWorkStore
    from prme.storage.profile_collection import acknowledge

    async with MemoryEngine.open(config) as engine:
        plan = await staged(engine, user, monkeypatch)
        index = getattr(engine, f"_{index_name}_index")
        original = index._do_delete
        entered, release = threading.Event(), threading.Event()
        connection = engine._conn.cursor()
        other = ProfileWorkStore(conn=connection, conn_lock=asyncio.Lock())

        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)

        try:
            with monkeypatch.context() as fault:
                fault.setattr(index, "_do_delete", blocked)
                task = asyncio.create_task(engine.collect_profile_staging(user_id=user))
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    task.cancel()
                    await asyncio.sleep(0.01)
                    task.cancel()
                    assert not task.done()
                    import duckdb

                    with pytest.raises(duckdb.TransactionException):
                        await acknowledge(other, plan)
                finally:
                    release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            result = await engine.collect_profile_staging(user_id=user)
            assert (
                result["collected"] == 1
                and result["remaining"] == result["failed"] == 0
            )
        finally:
            release.set()
            connection.close()


async def test_invalid_collection_receipt_does_not_hide_or_delete_pending_cleanup(
    config, user, monkeypatch
):
    from prme.storage.profile_collection import collection_operation_id

    async with MemoryEngine.open(config) as engine:
        plan = await staged(engine, user, monkeypatch)
        await execute(
            engine,
            "INSERT INTO operations (id,op_type,target_id,payload,actor_id,namespace_id) VALUES (?,'PROFILE_STAGE_COLLECTED',?,'{}',?,?)",
            [
                collection_operation_id(plan),
                str(plan.node.id),
                user,
                Scope.PROJECT.value,
            ],
        )
        result = await engine.collect_profile_staging(user_id=user)
        assert result == {
            "collected": 0,
            "failed": 1,
            "remaining": 1,
            "errors": {str(plan.node.id): "ValueError"},
            "blocked_reason": None,
        }
        if engine._pool is None:
            assert engine._conn.execute(
                "SELECT 1 FROM vector_staging WHERE node_id=?", [str(plan.node.id)]
            ).fetchone()
            assert any(
                r["node_id"] == str(plan.node.id)
                for r in await engine._lexical_index.search("Aurora", user, limit=100)
            )
        # Restore deliberate corruption so this shared test database remains usable.
        await execute(
            engine, "DELETE FROM operations WHERE id=?", [collection_operation_id(plan)]
        )
        assert (await engine.collect_profile_staging(user_id=user))["collected"] == 1


async def test_older_work_table_gains_collection_identity_on_restart(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        plan = await staged(engine, user, monkeypatch)
        # Reproduce the preceding schema: this identity is entirely derived.
        await execute(
            engine, "ALTER TABLE profile_work DROP COLUMN collection_operation_id", []
        )
    async with MemoryEngine.open(config) as engine:
        restored = await engine._profile_work.get(str(plan.node.id), user_id=user)
        assert restored.checksum == plan.checksum
        result = await engine.collect_profile_staging(user_id=user)
        assert result["collected"] == 1 and result["remaining"] == result["failed"] == 0
        assert (await engine.collect_profile_staging(user_id=user))["collected"] == 0
