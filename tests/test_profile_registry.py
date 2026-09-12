"""Profile queue reconstruction uses the immutable prepared/published journal."""

from unittest.mock import AsyncMock

from prme import MemoryEngine
from prme.types import Scope
from tests.test_profile_work import prepare_only
from tests.test_knowledge_profile_scopes import seed, profiles
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


async def execute(engine, sql, args):
    if engine._pool is None:
        return engine._conn.execute(sql, args).fetchall()
    from prme.storage.profile_work import pg_sql

    async with engine._pool.acquire() as conn:
        return await conn.fetch(pg_sql(sql), *args)


async def test_missing_queue_and_reservations_rebuild_all_states_without_models(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        old = await prepare_only(engine, user, monkeypatch)
        await engine.store(
            "Aurora requires optical inspection.", user_id=user, scope=Scope.PROJECT
        )
        new = await prepare_only(engine, user, monkeypatch)
        await engine.resume_profile(str(new.node.id), user_id=user)
        pending = await prepare_only(engine, user, monkeypatch, scope=Scope.PERSONAL)
        expected = {str(p.node.id): p.checksum for p in (old, new, pending)}
        await execute(engine, "DELETE FROM profile_work WHERE user_id=?", [user])
        # Leave registration markers: absent artifacts still need reconstruction.
        for identity in expected:
            await execute(
                engine,
                "DELETE FROM derivation_artifact_owners WHERE node_id=?",
                [identity],
            )
    async with MemoryEngine.open(config) as engine:
        engine._vector_index._provider.embed = AsyncMock(
            side_effect=AssertionError("No inference during recovery")
        )
        for state, plan in [
            ("abandoned", old),
            ("complete", new),
            ("pending", pending),
        ]:
            jobs = await engine.profile_jobs(user_id=user, status=state)
            assert [j["plan_id"] for j in jobs] == [str(plan.node.id)]
            saved = await engine._profile_work.get(str(plan.node.id), user_id=user)
            assert saved.checksum == expected[str(plan.node.id)]
            assert (
                await execute(
                    engine,
                    "SELECT operation_id FROM derivation_artifact_owners WHERE node_id=?",
                    [str(plan.node.id)],
                )
            )[0][0] == plan.prepared_operation_id
        assert await engine.process_profiles(user_id=user) == {
            "processed": 1,
            "failed": 0,
            "pending": 0,
            "errors": {},
        }
        assert {str(n.id) for n in await profiles(engine, user)} == {
            str(new.node.id),
            str(pending.node.id),
        }
    async with MemoryEngine.open(config) as engine:
        assert len(await engine.profile_jobs(user_id=user, status="complete")) == 2


async def test_invalid_preparation_does_not_reconstruct_work_or_authorize_collection(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        await execute(engine, "DELETE FROM profile_work WHERE user_id=?", [user])
        await execute(
            engine,
            "UPDATE operations SET actor_id=? WHERE id=?",
            [user + "-other", plan.prepared_operation_id],
        )
    async with MemoryEngine.open(config) as engine:
        try:
            assert await engine.profile_jobs(user_id=user) == []
            assert await engine.profile_jobs(user_id=user + "-other") == []
            assert await engine.get_node(str(plan.sources[0].id)) is not None
            if engine._pool is None:
                from prme.storage.derivation_registry import retired_staging

                assert retired_staging(engine._conn, user_id=user) == (
                    [],
                    "unregistered_profile_plans",
                )
        finally:
            await execute(
                engine,
                "UPDATE operations SET actor_id=? WHERE id=?",
                [user, plan.prepared_operation_id],
            )


async def test_legacy_overlapping_reservation_is_retained_as_ambiguous(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        await execute(
            engine,
            "DELETE FROM profile_registered_plans WHERE operation_id=?",
            [plan.prepared_operation_id],
        )
        await execute(
            engine,
            "UPDATE derivation_artifact_owners SET operation_id=? WHERE node_id=?",
            ["another-operation", str(plan.node.id)],
        )
    async with MemoryEngine.open(config) as engine:
        assert (
            await execute(
                engine,
                "SELECT operation_id FROM derivation_artifact_owners WHERE node_id=?",
                [str(plan.node.id)],
            )
        )[0][0] is None
        result = await engine.process_profiles(user_id=user)
        assert result["processed"] == 0 and result["pending"] == result["failed"] == 1
        assert await profiles(engine, user) == []
