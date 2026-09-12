"""Interrupted profile preparation remains inspectable and replayable without models."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.models.profile import StaleProfileError
from prme.types import Scope
from tests import test_knowledge_profile_scopes as fixtures
from tests.test_knowledge_profile_scopes import seed, profiles

config = fixtures.config
user = fixtures.user


async def prepare_only(engine, user, monkeypatch, *, scope=Scope.PROJECT):
    with monkeypatch.context() as fault:
        fault.setattr(
            engine,
            "_publish_prepared_profile",
            AsyncMock(side_effect=OSError("authored staging outage")),
        )
        with pytest.raises(OSError):
            await engine.consolidate_knowledge(
                user_id=user, scope=scope, entity_names=["Aurora"]
            )
    jobs = await engine.profile_jobs(user_id=user, scope=scope)
    assert len(jobs) == 1
    return await engine._profile_work.get(jobs[0]["plan_id"], user_id=user)


async def test_prepared_profile_survives_restart_and_reuses_exact_model_output(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        assert await profiles(engine, user) == []
        assert await engine.profile_jobs(user_id=user + "-other") == []
        assert (
            await engine.resume_profile(str(plan.node.id), user_id=user + "-other")
            is None
        )
        assert await engine.profile_jobs(user_id=user, scope=Scope.PERSONAL) == []
    async with MemoryEngine.open(config) as engine:
        engine._vector_index._provider.embed = AsyncMock(
            side_effect=AssertionError("Recovery must reuse numerical embedding")
        )
        engine._pipeline._extraction_provider.extract = AsyncMock(
            side_effect=AssertionError("Recovery must not extract")
        )
        result = await engine.process_profiles(user_id=user, scope=Scope.PROJECT)
        assert result == {"processed": 1, "failed": 0, "pending": 0, "errors": {}}
        nodes = await profiles(engine, user)
        assert len(nodes) == 1
        for field, expected in plan.node.__dict__.items():
            # Graph score columns use float32 on both supported backends.
            assert getattr(nodes[0], field) == (
                pytest.approx(expected) if isinstance(expected, float) else expected
            )
        assert (
            await engine._profile_work.get(str(plan.node.id), user_id=user)
        ).checksum == plan.checksum
        assert await engine.resume_profile(str(plan.node.id), user_id=user) == str(
            plan.node.id
        )
        assert (await engine.profile_jobs(user_id=user, status="complete"))[0][
            "attempts"
        ] == 1
        await engine.archive(str(plan.node.id), user_id=user)
        with monkeypatch.context() as fault:
            fault.setattr(
                engine._vector_index,
                "stage",
                AsyncMock(side_effect=AssertionError("Completed retry cannot restage")),
                raising=False,
            )
            assert await engine.resume_profile(str(plan.node.id), user_id=user) == str(
                plan.node.id
            )
        assert await profiles(engine, user) == []


async def test_matching_consolidation_retries_reuse_prepared_identity_without_embedding(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        engine._vector_index._provider.embed = AsyncMock(
            side_effect=AssertionError("Matching retry cannot embed")
        )
        assert (
            await engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
            == 1
        )
        assert (await profiles(engine, user))[0].id == plan.node.id


async def test_new_request_retires_preparation_and_fences_its_replay(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        old = await prepare_only(engine, user, monkeypatch)
        await engine.store(
            "Aurora requires a final optical inspection.",
            user_id=user,
            scope=Scope.PROJECT,
        )
        new = await prepare_only(engine, user, monkeypatch)
        assert new.node.id != old.node.id
        assert (await engine.profile_jobs(user_id=user, status="abandoned"))[0][
            "plan_id"
        ] == str(old.node.id)
        with pytest.raises(StaleProfileError):
            await engine.resume_profile(str(old.node.id), user_id=user)
        assert await engine.resume_profile(str(new.node.id), user_id=user) == str(
            new.node.id
        )
        assert [n.id for n in await profiles(engine, user)] == [new.node.id]


async def test_changed_dependencies_fail_without_losing_pending_inputs(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        await engine._graph_store.update_node(str(plan.sources[0].id), confidence=0.1)
        result = await engine.process_profiles(user_id=user)
        assert result["processed"] == 0 and result["failed"] == result["pending"] == 1
        assert result["errors"] == {str(plan.node.id): "StaleProfileError"}
        assert await profiles(engine, user) == []
        assert (
            await engine._profile_work.get(str(plan.node.id), user_id=user)
        ).checksum == plan.checksum


async def test_prepare_rejects_mutation_of_saved_plan_and_foreign_reservations(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        changed = plan.model_copy(
            update={"node": plan.node.model_copy(update={"salience": 0.2})}
        )
        with pytest.raises(ValueError, match="overwritten"):
            await engine._profile_work.prepare(changed)
        assert (
            await engine._profile_work.get(str(plan.node.id), user_id=user)
        ).checksum == plan.checksum
        fresh_id = uuid4()
        changed = plan.model_copy(
            update={
                "node": plan.node.model_copy(update={"id": fresh_id, "salience": 0.2}),
                "embedding": plan.embedding.model_copy(update={"node_id": fresh_id}),
            }
        )
        if engine._pool is None:
            engine._conn.execute(
                "INSERT INTO derivation_artifact_owners VALUES (?,?)",
                [str(fresh_id), "another-operation"],
            )
        else:
            async with engine._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO derivation_artifact_owners VALUES ($1,$2)",
                    str(fresh_id),
                    "another-operation",
                )
        with pytest.raises(ValueError, match="another prepared"):
            await engine._profile_work.prepare(changed)
        assert (await engine.profile_jobs(user_id=user))[0]["plan_id"] == str(
            plan.node.id
        )
        assert await engine._profile_work.get(str(fresh_id), user_id=user) is None


async def test_budget_and_failure_fairness_preserve_scope(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        project = await prepare_only(engine, user, monkeypatch)
        personal = await prepare_only(engine, user, monkeypatch, scope=Scope.PERSONAL)
        assert (await engine.process_profiles(user_id=user, budget_ms=0)) == {
            "processed": 0,
            "failed": 0,
            "pending": 2,
            "errors": {},
        }
        assert (await engine.process_profiles(user_id=user, scope=Scope.PROJECT))[
            "processed"
        ] == 1
        assert (await engine.profile_jobs(user_id=user))[0]["plan_id"] == str(
            personal.node.id
        )
        assert await engine.resume_profile(str(project.node.id), user_id=user) == str(
            project.node.id
        )
        for budget in [-1, float("inf"), float("nan")]:
            with pytest.raises(ValueError):
                await engine.process_profiles(user_id=user, budget_ms=budget)


async def test_cancelled_profile_pass_keeps_preparation_pending(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        entered = asyncio.Event()
        original = engine._publish_prepared_profile

        async def pause(plan):
            entered.set()
            await asyncio.Event().wait()

        with monkeypatch.context() as fault:
            fault.setattr(engine, "_publish_prepared_profile", pause)
            task = asyncio.create_task(engine.process_profiles(user_id=user))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert (await engine.profile_jobs(user_id=user))[0]["plan_id"] == str(
            plan.node.id
        )
        assert await original(plan) == str(plan.node.id)


async def test_failed_job_does_not_starve_unattempted_scope(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        first = await prepare_only(engine, user, monkeypatch)
        second = await prepare_only(engine, user, monkeypatch, scope=Scope.PERSONAL)
        await engine._graph_store.update_node(str(first.sources[0].id), confidence=0.1)
        result = await engine.process_profiles(user_id=user, limit=1)
        assert result["errors"] == {str(first.node.id): "StaleProfileError"}
        result = await engine.process_profiles(user_id=user, limit=1)
        assert result == {"processed": 1, "failed": 0, "pending": 1, "errors": {}}
        assert [n.id for n in await profiles(engine, user)] == [second.node.id]


@pytest.mark.parametrize(
    "damage", ["missing_operation", "wrong_scope", "wrong_operation_owner"]
)
async def test_damaged_owned_work_is_a_failure_not_a_success(
    config, user, monkeypatch, damage
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        if damage == "missing_operation":
            sql, args = (
                "DELETE FROM operations WHERE id=?",
                [plan.prepared_operation_id],
            )
        elif damage == "wrong_operation_owner":
            sql, args = (
                "UPDATE operations SET actor_id=? WHERE id=?",
                [user + "-other", plan.prepared_operation_id],
            )
        else:
            sql, args = (
                "UPDATE profile_work SET scope=? WHERE plan_id=?",
                ["personal", str(plan.node.id)],
            )
        # Deliberate storage damage, never a supported application mutation.
        if engine._pool is None:
            engine._conn.execute(sql, args)
        else:
            from prme.storage.profile_work import pg_sql

            async with engine._pool.acquire() as conn:
                await conn.execute(pg_sql(sql), *args)
        assert (
            await engine.resume_profile(str(plan.node.id), user_id=user + "-other")
            is None
        )
        result = await engine.process_profiles(user_id=user)
        assert result == {
            "processed": 0,
            "failed": 1,
            "pending": 1,
            "errors": {str(plan.node.id): "ValueError"},
        }
        assert await profiles(engine, user) == []


def test_sync_profile_recovery_uses_saved_inputs(config, user, monkeypatch):
    from prme import MemoryClient, ProfileJobStatus, ProfileProcessingResult

    with MemoryClient(config=config) as client:
        for i in range(2):
            client.store(f"Aurora team note {i}", user_id=user, scope=Scope.PROJECT)
        with monkeypatch.context() as fault:
            fault.setattr(
                client._engine,
                "_publish_prepared_profile",
                AsyncMock(side_effect=OSError("outage")),
            )
            with pytest.raises(OSError):
                client.consolidate_knowledge(
                    user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
                )
        jobs: list[ProfileJobStatus] = client.profile_jobs(user_id=user)
        assert len(jobs) == 1
    with MemoryClient(config=config) as client:
        client._engine._vector_index._provider.embed = AsyncMock(
            side_effect=AssertionError("No new inference")
        )
        result: ProfileProcessingResult = client.process_profiles(
            user_id=user, scope=Scope.PROJECT
        )
        assert result == {"processed": 1, "failed": 0, "pending": 0, "errors": {}}
        assert (
            client.resume_profile(jobs[0]["plan_id"], user_id=user)
            == jobs[0]["plan_id"]
        )


@pytest.mark.parametrize("index_name", ["vector", "lexical"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_replacement_cannot_overtake_native_stage(
    config, user, monkeypatch, index_name, cancel
):
    if config.database_url:
        pytest.skip("PostgreSQL indexes are inside the publication transaction")
    import threading
    from prme.storage.profile_work import ProfileStageFence, ProfileWorkStore

    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        new_id = uuid4()
        replacement = plan.model_copy(
            update={
                "node": plan.node.model_copy(update={"id": new_id, "salience": 0.2}),
                "embedding": plan.embedding.model_copy(update={"node_id": new_id}),
            }
        )
        connection = engine._conn.cursor()
        other = ProfileWorkStore(conn=connection, conn_lock=asyncio.Lock())
        fence = ProfileStageFence(engine._conn, engine._event_store._conn_lock, plan)
        index = getattr(engine, f"_{index_name}_index")
        original = index._do_stage
        entered, release = threading.Event(), threading.Event()

        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)

        try:
            with monkeypatch.context() as fault:
                fault.setattr(index, "_do_stage", blocked)
                operation = (
                    index.stage(plan.embedding, user_id=user, fence=fence)
                    if index_name == "vector"
                    else index.stage_profile(plan, fence=fence)
                )
                task = asyncio.create_task(operation)
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    if cancel:
                        task.cancel()
                        await asyncio.sleep(0.01)
                        task.cancel()
                        assert not task.done()
                    with pytest.raises(StaleProfileError):
                        await other.prepare(replacement)
                finally:
                    release.set()
                    if cancel:
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        await task
            assert (await other.prepare(replacement)).checksum == replacement.checksum
            before = engine._conn.execute(
                "SELECT count(*) FROM vector_staging"
            ).fetchone()[0]
            documents = engine._lexical_index._index.searcher().num_docs
            with pytest.raises(StaleProfileError):
                if index_name == "vector":
                    await index.stage(plan.embedding, user_id=user, fence=fence)
                else:
                    await index.stage_profile(plan, fence=fence)
            assert (
                engine._conn.execute("SELECT count(*) FROM vector_staging").fetchone()[
                    0
                ]
                == before
            )
            assert engine._lexical_index._index.searcher().num_docs == documents
        finally:
            release.set()
            connection.close()


async def test_distinct_preparations_cannot_admit_two_pending_plans(
    config, user, monkeypatch
):
    if config.database_url:
        pytest.skip("DuckDB optimistic preparation admission")
    import threading
    from prme.storage.profile_work import ProfileWorkStore

    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        old = await prepare_only(engine, user, monkeypatch)

        def changed(salience):
            identity = uuid4()
            return old.model_copy(
                update={
                    "node": old.node.model_copy(
                        update={"id": identity, "salience": salience}
                    ),
                    "embedding": old.embedding.model_copy(update={"node_id": identity}),
                }
            )

        first, second = changed(0.2), changed(0.3)
        a, b = engine._conn.cursor(), engine._conn.cursor()
        entered, release = threading.Event(), threading.Event()

        class PausedConnection:
            def execute(self, sql, *args):
                if sql.startswith(
                    "SELECT plan_id, request_hash, prepared_operation_id"
                ):
                    entered.set()
                    assert release.wait(10)
                return a.execute(sql, *args)

        left = ProfileWorkStore(conn=PausedConnection(), conn_lock=asyncio.Lock())
        right = ProfileWorkStore(conn=b, conn_lock=asyncio.Lock())
        task = asyncio.create_task(left.prepare(first))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(StaleProfileError):
                await right.prepare(second)
        finally:
            release.set()
            await task
            a.close()
            b.close()
        assert [j["plan_id"] for j in await engine.profile_jobs(user_id=user)] == [
            str(first.node.id)
        ]
        assert await engine._profile_work.get(str(second.node.id), user_id=user) is None
