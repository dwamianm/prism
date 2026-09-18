"""Retiring prepared profiles preserves source history and fences old workers."""

import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine, StaleProfileError
from prme.types import Scope
from prme.storage.profile_retirement import discard
from prme.storage.profile_work import ProfileStageFence, ProfileWorkStore
from tests.test_profile_work import prepare_only
from tests.test_profile_registry import execute
from tests.test_knowledge_profile_scopes import seed, profiles
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


async def test_discard_is_owned_idempotent_and_rebuilt_from_journal(
    config, user, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
        assert not await engine.discard_profile(
            str(plan.node.id), user_id=user + "-other"
        )
        assert await engine.discard_profile(str(plan.node.id), user_id=user)
        assert await engine.discard_profile(str(plan.node.id), user_id=user)
        with pytest.raises(StaleProfileError):
            await engine.resume_profile(str(plan.node.id), user_id=user)
        assert not await engine.profile_jobs(user_id=user)
        for source in plan.sources:
            assert await engine.get_node(str(source.id)) is not None
        await execute(engine, "DELETE FROM profile_work WHERE user_id=?", [user])
    async with MemoryEngine.open(config) as engine:
        jobs = await engine.profile_jobs(user_id=user, status="abandoned")
        assert len(jobs) == 1 and jobs[0]["last_error"] == "DiscardedPreparation"
        assert jobs[0]["plan_id"] == str(plan.node.id)
        assert await engine.process_profiles(user_id=user) == {
            "processed": 0,
            "failed": 0,
            "pending": 0,
            "errors": {},
        }
        assert (
            await engine._profile_work.get(str(plan.node.id), user_id=user)
        ).checksum == plan.checksum
        assert (
            await engine.consolidate_knowledge(
                user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
            )
            == 1
        )
        assert (await profiles(engine, user))[0].id != plan.node.id


async def test_completed_profile_cannot_be_discarded(config, user):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        await engine.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        plan_id = (await engine.profile_jobs(user_id=user, status="complete"))[0][
            "plan_id"
        ]
        assert not await engine.discard_profile(plan_id, user_id=user)
        assert [str(n.id) for n in await profiles(engine, user)] == [plan_id]


@pytest.mark.parametrize("index_name", ["vector", "lexical"])
async def test_discard_cannot_overtake_native_stage(
    config, user, monkeypatch, index_name
):
    if config.database_url:
        pytest.skip("Local native stage fence")
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user)
        plan = await prepare_only(engine, user, monkeypatch)
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
                    with pytest.raises(StaleProfileError):
                        await discard(other, str(plan.node.id), user_id=user)
                finally:
                    release.set()
                    await task
            assert await discard(other, str(plan.node.id), user_id=user)
            with pytest.raises(StaleProfileError):
                await engine._publish_prepared_profile(plan)
        finally:
            release.set()
            connection.close()


def test_sync_discard_preserves_previous_view(config, user, monkeypatch):
    from prme import MemoryClient

    with MemoryClient(config=config) as client:
        for i in range(2):
            client.store(f"Aurora team note {i}", user_id=user, scope=Scope.PROJECT)
        client.consolidate_knowledge(
            user_id=user, scope=Scope.PROJECT, entity_names=["Aurora"]
        )
        old = client.profile_jobs(user_id=user, status="complete")[0]["plan_id"]
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
        pending = client.profile_jobs(user_id=user)[0]["plan_id"]
        assert client.discard_profile(pending, user_id=user)
        assert not client.discard_profile(old, user_id=user)
        assert client.resume_profile(old, user_id=user) == old
