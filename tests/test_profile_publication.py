"""A profile replacement must become usable before its predecessor retires."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.types import Scope
from tests import test_knowledge_profile_scopes as fixtures
from tests.test_knowledge_profile_scopes import seed, profiles

config = fixtures.config
user = fixtures.user


async def test_failed_embedding_preserves_current_profile(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        original = (await profiles(engine, user))[0]
        with monkeypatch.context() as failure:
            failure.setattr(
                engine._vector_index._provider,
                "embed",
                AsyncMock(side_effect=RuntimeError("embedding unavailable")),
            )
            with pytest.raises(RuntimeError, match="embedding unavailable"):
                await engine.consolidate_knowledge(
                    user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
                )
        assert [node.id for node in await profiles(engine, user)] == [original.id]
        assert str(original.id) in {
            row["node_id"] for row in await engine._vector_index.search("Aurora", user)
        }
    async with MemoryEngine.open(config) as engine:
        assert [node.id for node in await profiles(engine, user)] == [original.id]


async def test_old_profile_remains_visible_while_embedding_runs(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        original = (await profiles(engine, user))[0]
        entered, release = asyncio.Event(), asyncio.Event()
        embed = engine._vector_index._provider.embed

        async def pause(texts):
            entered.set()
            await release.wait()
            return await embed(texts)

        monkeypatch.setattr(engine._vector_index._provider, "embed", pause)
        task = asyncio.create_task(
            engine.consolidate_knowledge(
                user_id=user,
                scope=Scope.PROJECT,
                entity_names=["Aurora"],
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert [node.id for node in await profiles(engine, user)] == [original.id]
        finally:
            release.set()
            await task
        current = await profiles(engine, user)
        assert len(current) == 1 and current[0].id != original.id


async def test_source_changed_during_preparation_rejects_publication(
    config, user, monkeypatch
):
    from prme.models.profile import StaleProfileError

    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        source_id = old.metadata["source_node_ids"][0]
        embed = engine._vector_index._provider.embed

        async def change_source(texts):
            await engine._graph_store.update_node(source_id, confidence=0.11)
            return await embed(texts)

        monkeypatch.setattr(engine._vector_index._provider, "embed", change_source)
        with pytest.raises(StaleProfileError, match="dependency changed"):
            await engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
        assert [node.id for node in await profiles(engine, user)] == [old.id]


async def test_transaction_failure_after_insert_preserves_old_view(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        with monkeypatch.context() as failure:
            if engine._pool is None:
                create = engine._graph_store._create_node_sync

                def fail(node):
                    create(node)
                    raise RuntimeError("after profile insert")

                failure.setattr(engine._graph_store, "_create_node_sync", fail)
            else:
                create = engine._graph_store._create_node_on_connection

                async def fail(conn, node):
                    await create(conn, node)
                    raise RuntimeError("after profile insert")

                failure.setattr(engine._graph_store, "_create_node_on_connection", fail)
            with pytest.raises(RuntimeError, match="after profile insert"):
                await engine.consolidate_knowledge(
                    user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
                )
        assert [node.id for node in await profiles(engine, user)] == [old.id]
        assert len(await engine.query_nodes(user_id=user)) == 5
        assert (
            await engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
            == 1
        )
        assert len(await profiles(engine, user)) == 1


async def test_concurrent_matching_rebuilds_publish_one_generation(config, user, monkeypatch):
    from prme.models.profile import StaleProfileError

    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        embed = engine._vector_index._provider.embed
        ready = asyncio.Event()
        arrivals = 0

        async def barrier(texts):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 5)
            return await embed(texts)

        monkeypatch.setattr(engine._vector_index._provider, "embed", barrier)
        results = await asyncio.gather(
            *[
                engine.consolidate_knowledge(
                    user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
                )
                for _ in range(2)
            ],
            return_exceptions=True,
        )
        assert results.count(1) >= 1
        assert all(result == 1 or isinstance(result, StaleProfileError) for result in results)
        assert len(await profiles(engine, user)) == 1
        completed = await engine.profile_jobs(user_id=user, status='complete')
        assert len(completed) == 1
        plan = await engine._profile_work.get(completed[0]['plan_id'], user_id=user)
        assert await engine._graph_store.profile_generation(plan.key) == 1


async def test_cancelled_preparation_preserves_old_profile(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        entered = asyncio.Event()

        async def pause(texts):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(engine._vector_index._provider, "embed", pause)
        task = asyncio.create_task(
            engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [node.id for node in await profiles(engine, user)] == [old.id]


async def test_lost_publication_acknowledgement_does_not_delete_committed_view(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        publish = engine._graph_store.publish_profile
        saved = []

        async def lose_ack(plan):
            saved.append(plan)
            await publish(plan)
            raise RuntimeError("acknowledgement lost")

        monkeypatch.setattr(engine._graph_store, "publish_profile", lose_ack)
        with pytest.raises(RuntimeError, match="acknowledgement lost"):
            await engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
        current = (await profiles(engine, user))[0]
        assert current.id != old.id
        assert str(current.id) in {
            row["node_id"] for row in await engine._vector_index.search("Aurora", user)
        }
        assert await publish(saved[0]) == str(current.id)
        await engine.archive(str(current.id), user_id=user)
        assert await publish(saved[0]) == str(current.id)
        assert await profiles(engine, user) == []


@pytest.mark.parametrize("boundary", ["vector", "lexical"])
async def test_failed_local_index_staging_keeps_old_profile(
    config, user, monkeypatch, boundary
):
    if config.database_url:
        pytest.skip("PostgreSQL indexes are part of graph transaction")
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        target = engine._vector_index if boundary == "vector" else engine._lexical_index
        method = "stage" if boundary == "vector" else "stage_profile"
        with monkeypatch.context() as failure:
            failure.setattr(
                target,
                method,
                AsyncMock(side_effect=RuntimeError("staging unavailable")),
            )
            with pytest.raises(RuntimeError, match="staging unavailable"):
                await engine.consolidate_knowledge(
                    user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
                )
        assert [node.id for node in await profiles(engine, user)] == [old.id]
        assert str(old.id) in {
            row["node_id"] for row in await engine._lexical_index.search("Aurora", user)
        }


@pytest.mark.parametrize("cancel", [False, True])
async def test_independent_reader_sees_no_partial_replacement(
    config, user, monkeypatch, cancel
):
    import threading

    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        entered, release = threading.Event(), threading.Event()
        if engine._pool is None:
            create = engine._graph_store._create_node_sync

            def pause(node):
                create(node)
                entered.set()
                assert release.wait(10)

            monkeypatch.setattr(engine._graph_store, "_create_node_sync", pause)
        else:
            create = engine._graph_store._create_node_on_connection

            async def pause(conn, node):
                await create(conn, node)
                entered.set()
                assert await asyncio.to_thread(release.wait, 10)

            monkeypatch.setattr(
                engine._graph_store, "_create_node_on_connection", pause
            )
        task = asyncio.create_task(
            engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if engine._pool is None:
                import duckdb

                def read():
                    with duckdb.connect(config.db_path) as conn:
                        return conn.execute(
                            "SELECT id FROM nodes WHERE user_id = ? AND node_type = 'summary' AND lifecycle_state = 'tentative'",
                            [user],
                        ).fetchall()

                rows = await asyncio.to_thread(read)
            else:
                async with engine._pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT id FROM nodes WHERE user_id = $1 AND node_type = 'summary' AND lifecycle_state = 'tentative'",
                        user,
                    )
            assert [str(row[0]) for row in rows] == [str(old.id)]
            if cancel:
                task.cancel()
        finally:
            release.set()
            if cancel and task.cancelling():
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
            # A cancelled queued caller can leave an already-requested commit
            # running. Wait behind it before examining its final visible state.
            await engine._write_queue.submit(lambda: asyncio.sleep(0))
        assert len(await profiles(engine, user)) == 1


@pytest.mark.parametrize("checkpoint", ["prepared", "vector", "staged", "inserted", "committed"])
async def test_abrupt_exit_keeps_a_complete_profile(config, user, checkpoint):
    import subprocess
    import sys

    if config.database_url:
        pytest.skip("Local WAL and external index recovery")
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        source_ids = set(old.metadata["source_node_ids"])
    script = """
import asyncio, os, sys
from prme import MemoryEngine, PRMEConfig
from prme.types import Scope
from tests.test_durable_ingestion import MockEmbeddingProvider
import prme.storage.engine
prme.storage.engine.create_embedding_provider = lambda _: MockEmbeddingProvider()
async def main():
    engine = await MemoryEngine.create(PRMEConfig.model_validate_json(sys.argv[1]))
    checkpoint = sys.argv[3]
    if checkpoint == "prepared":
        async def fault(plan):
            os._exit(42)
        engine._publish_prepared_profile = fault
    elif checkpoint == "vector":
        original = engine._vector_index.stage
        async def fault(*args, **kwargs):
            await original(*args, **kwargs)
            os._exit(42)
        engine._vector_index.stage = fault
    elif checkpoint == "staged":
        original = engine._lexical_index.stage_profile
        async def fault(plan, **kwargs):
            await original(plan, **kwargs)
            os._exit(42)
        engine._lexical_index.stage_profile = fault
    elif checkpoint == "inserted":
        original = engine._graph_store._create_node_sync
        def fault(node):
            original(node)
            os._exit(42)
        engine._graph_store._create_node_sync = fault
    else:
        original = engine._graph_store.publish_profile
        async def fault(plan):
            await original(plan)
            os._exit(42)
        engine._graph_store.publish_profile = fault
    await engine.consolidate_knowledge(user_id=sys.argv[2], scope=Scope.PROJECT, entity_names=["Aurora"])
    raise AssertionError("checkpoint not reached")
asyncio.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-c",
            script,
            config.model_dump_json(),
            user,
            checkpoint,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 42, result.stderr
    async with MemoryEngine.open(config) as engine:
        current = await profiles(engine, user)
        assert len(current) == 1
        assert (current[0].id == old.id) is (checkpoint != "committed")
        assert set(current[0].metadata["source_node_ids"]) == source_ids
        for source_id in source_ids:
            assert await engine.get_node(source_id) is not None
        for index in (engine._vector_index, engine._lexical_index):
            assert str(current[0].id) in {
                row["node_id"] for row in await index.search("Aurora", user)
            }
        # Recover the exact saved preparation with models unavailable, including
        # a commit whose acknowledgement was lost at process exit.
        engine._vector_index._provider.embed = AsyncMock(side_effect=AssertionError("Cannot infer during recovery"))
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=AssertionError("Cannot extract during recovery"))
        jobs = await engine.profile_jobs(user_id=user, status="complete" if checkpoint == "committed" else "pending")
        replacement = next(job for job in jobs if job['plan_id'] != str(old.id))
        saved = await engine._profile_work.get(replacement['plan_id'], user_id=user)
        assert await engine.resume_profile(replacement['plan_id'], user_id=user) == replacement['plan_id']
        assert [str(n.id) for n in await profiles(engine, user)] == [replacement['plan_id']]
        assert (await engine._profile_work.get(replacement['plan_id'], user_id=user)).checksum == saved.checksum



async def test_independent_connections_cannot_publish_same_generation(
    config, user, monkeypatch
):
    from prme.models.profile import StaleProfileError

    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        captured = []

        async def capture(plan):
            captured.append(plan)
            raise RuntimeError("prepared only")

        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "publish_profile", capture)
            for _ in range(2):
                with pytest.raises(RuntimeError, match="prepared only"):
                    await engine.consolidate_knowledge(
                        user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
                    )
        # Matching workflow requests now share a journaled plan. Keep this
        # component test about *distinct* publications racing one generation.
        from uuid import uuid4
        from prme.models.profile import ProfilePublication
        second_id = uuid4()
        first = captured[0]
        second_plan = ProfilePublication(
            node=first.node.model_copy(update={'id': second_id}), sources=first.sources,
            previous=first.previous, generation=first.generation,
            embedding=first.embedding.model_copy(update={'node_id': second_id}),
        )
        if engine._pool is None:
            await engine._vector_index.stage(second_plan.embedding, user_id=user)
            await engine._lexical_index.stage_profile(second_plan)
        captured[1] = second_plan
        if engine._pool is None:
            import duckdb
            from prme.storage.duckpgq_graph import DuckPGQGraphStore

            connection = duckdb.connect(config.db_path)
            second = DuckPGQGraphStore(connection)
        else:
            from prme.storage.pg.graph_store import PgGraphStore

            connection = None
            second = PgGraphStore(engine._pool)
        try:
            results = await asyncio.gather(
                engine._graph_store.publish_profile(captured[0]),
                second.publish_profile(captured[1]),
                return_exceptions=True,
            )
        finally:
            if connection is not None:
                connection.close()
        assert sum(isinstance(result, str) for result in results) == 1, results
        assert sum(isinstance(result, StaleProfileError) for result in results) == 1, (
            results
        )
        assert len(await profiles(engine, user)) == 1


@pytest.mark.parametrize(
    "mutation", ["source_owner", "source_scope", "asserted_profile", "retire_source"]
)
async def test_publication_revalidates_mutated_dependency_boundaries(
    config, user, monkeypatch, mutation
):
    from prme.types import EpistemicType

    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        captured = []

        async def capture(plan):
            captured.append(plan)
            raise RuntimeError("prepared only")

        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "publish_profile", capture)
            with pytest.raises(RuntimeError, match="prepared only"):
                await engine.consolidate_knowledge(
                    user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
                )
        plan = captured[0]
        # Nested MemoryNodes are mutable; every storage boundary must snapshot
        # and revalidate them instead of trusting the model's initial check.
        if mutation == "source_owner":
            plan.sources[0].user_id = user + "-other"
        elif mutation == "source_scope":
            plan.sources[0].scope = Scope.PERSONAL
        elif mutation == "asserted_profile":
            plan.node.epistemic_type = EpistemicType.ASSERTED
        else:
            plan = plan.model_copy(
                update={"previous": (plan.sources[0],), "sources": plan.sources[1:]}
            )
        with pytest.raises(ValueError):
            await engine._graph_store.publish_profile(plan)
        assert await profiles(engine, user) == []
        assert len(await engine.query_nodes(user_id=user)) == 4


async def test_failure_after_predecessor_archival_rolls_back_whole_publication(
    config, user, monkeypatch
):
    from unittest.mock import Mock

    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        with monkeypatch.context() as failure:
            failure.setattr(
                "prme.storage.profile_publication._payload",
                Mock(side_effect=RuntimeError("publication journal unavailable")),
            )
            with pytest.raises(RuntimeError, match="publication journal unavailable"):
                await engine.consolidate_knowledge(
                    user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
                )
        assert [node.id for node in await profiles(engine, user)] == [old.id]
        assert len(await engine.query_nodes(user_id=user)) == 5
        assert (
            await engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
            == 1
        )


async def test_older_profile_sources_survive_large_history(config, user):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        # The older qualifying sources fall outside the former newest-5000
        # window. Bulk graph fixtures avoid embedding 5,001 irrelevant texts.
        if engine._pool is None:
            async with engine._graph_store._conn_lock:
                engine._conn.execute(
                    "INSERT INTO nodes (id, node_type, user_id, scope, content) "
                    "SELECT uuid()::VARCHAR, 'note', ?, 'project', 'Unrelated background memory' FROM range(5001)",
                    [user],
                )
        else:
            async with engine._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO nodes (id, node_type, user_id, scope, content) "
                    "SELECT gen_random_uuid(), 'note', $1, 'project', 'Unrelated background memory' FROM generate_series(1, 5001)",
                    user,
                )
        assert (
            await engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
            == 1
        )
        current = await profiles(engine, user)
        assert len(current) == 1 and current[0].content == old.content
        assert set(current[0].metadata["source_node_ids"]) == set(
            old.metadata["source_node_ids"]
        )


async def test_incomplete_source_scan_preserves_existing_profile(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = (await profiles(engine, user))[0]
        scan = engine._graph_store.scan_nodes
        calls = 0

        async def fail_second_page(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("source scan interrupted")
            kwargs["limit"] = 1
            return await scan(**kwargs)

        monkeypatch.setattr(engine._graph_store, "scan_nodes", fail_second_page)
        with pytest.raises(RuntimeError, match="source scan interrupted"):
            await engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
        assert [node.id for node in await profiles(engine, user)] == [old.id]
