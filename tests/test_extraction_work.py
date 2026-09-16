"""Durable extraction admission, ownership, ordering and commit fences."""

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.models import Event
from prme.models.extraction import ExtractionRecord
from prme.models.extraction_work import StaleExtractionClaimError
from prme.types import Scope
from prme.storage.extraction_work import ExtractionWorkRepository
from tests import test_durable_ingestion
from tests.test_derivation_commits import stage
from tests.test_derivation_planning import extraction

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def set_expiry(engine, event_id, when):
    if engine._conn is not None:
        async with engine._graph_store._conn_lock:
            engine._conn.execute("UPDATE event_extractions SET lease_expires_at = ? WHERE event_id = ?", [when, event_id])
    else:
        async with engine._pool.acquire() as conn:
            await conn.execute("UPDATE event_extractions SET lease_expires_at = $1 WHERE event_id = $2", when, event_id)


async def prepare(engine, user):
    event = Event(content="Alice uses Rust", user_id=user, role="user")
    await engine._event_store.append(event, defer_extraction=True, defer_materialization=True)
    claim = await engine._event_store.extraction_work.claim(user_id=user, event_id=str(event.id))
    plan = await engine._pipeline._prepare_plan(extraction(event.content, old=None), event)
    plan = await engine._event_store.record_derivation_plan(plan, claim=claim)
    return event, claim, plan


async def test_atomic_admission_has_separate_scoped_status(config, user):
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Alice uses Rust", user_id=user, role="user")
        await engine._event_store.append(event, defer_extraction=True, defer_materialization=True)
        work = engine._event_store.extraction_work
        status = await work.status(str(event.id), user_id=user)
        assert status.status == "pending" and status.phase == "extraction" and status.attempts == 0
        assert (await engine.processing_status(str(event.id), user_id=user)).status == "pending"
        assert await work.status(str(event.id), user_id=user + "-other") is None
        plain = await engine.ingest_fast("Raw only", user_id=user)
        assert await work.status(plain, user_id=user) is None
    async with MemoryEngine.open(config) as engine:
        assert await engine._event_store.extraction_work.status(str(event.id), user_id=user) == status


async def test_concurrent_claims_serialize_scope_in_append_order(config, user):
    async with MemoryEngine.open(config) as engine:
        events = [Event(content=f"source {i}", user_id=user, role="user",
                        timestamp=datetime(2040 - i, 1, 1, tzinfo=timezone.utc)) for i in range(2)]
        for event in events:
            await engine._event_store.append(event, defer_extraction=True)
        work = engine._event_store.extraction_work
        claims = await asyncio.gather(*(work.claim(user_id=user) for _ in range(6)))
        active = [claim for claim in claims if claim is not None]
        assert len(active) == 1 and active[0].event_id == events[0].id
        assert await work.claim(user_id=user, event_id=str(events[1].id)) is None
        other_scope = Event(content="Independent", user_id=user, role="user", scope=Scope.PROJECT)
        await engine._event_store.append(other_scope, defer_extraction=True)
        assert (await work.claim(user_id=user)).event_id == other_scope.id
        assert await work.fail(active[0], error="ProviderUnavailable")
        assert (await work.claim(user_id=user)).event_id == events[1].id


async def test_expired_generation_cannot_write_inference_plan_or_graph(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        event, first, plan = await prepare(engine, user)
        work = engine._event_store.extraction_work
        assert (await work.status(str(event.id), user_id=user)).phase == "publication"
        await stage(engine, plan)
        await set_expiry(engine, str(event.id), datetime(2000, 1, 1, tzinfo=timezone.utc))
        second = await work.claim(user_id=user)
        assert second.generation == first.generation + 1
        assert not await work.renew(first)
        assert not await work.fail(first, error="OldWorkerFailed")
        record = ExtractionRecord(event_id=event.id, user_id=user, scope=event.scope,
                                  content_hash=event.content_hash, provider="test", model="test", result={})
        for write in (
            lambda: engine._event_store.record_extraction(record, claim=first),
            lambda: engine._event_store.record_derivation_plan(plan, claim=first),
            lambda: engine._graph_store.commit_derivation(plan, claim=first),
            lambda: engine._graph_store.commit_derivation(plan),
        ):
            with pytest.raises(StaleExtractionClaimError):
                await write()
        assert await engine.get_event_nodes(str(event.id), user_id=user) == []
        monkeypatch.setattr(engine._vector_index._provider, "embed", AsyncMock(side_effect=AssertionError("No inference")))
        receipt = await engine._graph_store.commit_derivation(plan, claim=second)
        assert receipt.generation == second.generation
        status = await work.status(str(event.id), user_id=user)
        assert status.status == status.phase == "complete" and status.lease_expires_at is None
        assert await engine._graph_store.commit_derivation(plan, claim=first) == receipt
        assert not await work.fail(second, error="LostAcknowledgement")
        assert not await work.retry(str(event.id), user_id=user)


async def test_retry_and_heartbeat_cannot_cross_owner_or_revive_expired_lease(config, user):
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        work = engine._event_store.extraction_work
        wrong = claim.model_copy(update={"user_id": user + "-other"})
        assert not await work.renew(wrong)
        assert not await work.fail(wrong, error="Failure")
        assert await work.renew(claim, lease_seconds=600)
        with pytest.raises(ValueError, match="reason code"):
            await work.fail(claim, error="provider returned private key details")
        assert await work.fail(claim, error="ProviderUnavailable", retry_after=3600)
        assert await work.claim(user_id=user) is None
        assert not await work.retry(str(event.id), user_id=user + "-other")
        assert await work.retry(str(event.id), user_id=user)
        next_claim = await work.claim(user_id=user)
        assert next_claim.generation == claim.generation + 1
        assert next_claim.attempts == 2
        assert (await work.status(str(event.id), user_id=user)).plan_id == plan.id


async def test_lease_expiring_during_graph_transaction_rolls_back_everything(config, user, monkeypatch):
    import time
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        await stage(engine, plan)
        await set_expiry(engine, str(event.id), datetime.now(timezone.utc) + timedelta(seconds=0.25))
        graph = engine._graph_store
        method = "_create_node_sync" if engine._conn is not None else "_create_node_on_connection"
        original = getattr(graph, method)
        if engine._conn is not None:
            def slow(*args, **kwargs):
                result = original(*args, **kwargs)
                time.sleep(0.3)
                return result
        else:
            async def slow(*args, **kwargs):
                result = await original(*args, **kwargs)
                await asyncio.sleep(0.3)
                return result
        monkeypatch.setattr(graph, method, slow)
        with pytest.raises(StaleExtractionClaimError):
            await graph.commit_derivation(plan, claim=claim)
        assert await engine.get_event_nodes(str(event.id), user_id=user) == []
        assert await engine._event_store.get_derivation_receipt(str(event.id), user_id=user) is None
        assert (await engine._event_store.extraction_work.status(str(event.id), user_id=user)).status == "running"


async def test_takeover_waits_for_expired_inflight_transaction_to_roll_back(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        await stage(engine, plan)
        await set_expiry(engine, str(event.id), datetime.now(timezone.utc) + timedelta(seconds=1))
        entered, release = threading.Event(), threading.Event()
        graph = engine._graph_store
        method = "_create_node_sync" if engine._conn is not None else "_create_node_on_connection"
        original = getattr(graph, method)
        if engine._conn is not None:
            second_conn = engine._conn.cursor()
            other = ExtractionWorkRepository(conn=second_conn)
            def blocked(*args, **kwargs):
                result = original(*args, **kwargs)
                entered.set()
                assert release.wait(5)
                return result
        else:
            second_conn = None
            other = ExtractionWorkRepository(pool=engine._pool)
            async def blocked(*args, **kwargs):
                result = await original(*args, **kwargs)
                entered.set()
                assert await asyncio.to_thread(release.wait, 5)
                return result
        with monkeypatch.context() as fault:
            fault.setattr(graph, method, blocked)
            task = asyncio.create_task(graph.commit_derivation(plan, claim=claim))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                await asyncio.sleep(1.05)
                assert await other.claim(user_id=user) is None
            finally:
                release.set()
                with pytest.raises(StaleExtractionClaimError):
                    await task
        successor = await other.claim(user_id=user)
        assert successor.generation == claim.generation + 1
        receipt = await graph.commit_derivation(plan, claim=successor)
        assert receipt.generation == successor.generation
        if second_conn is not None:
            second_conn.close()


@pytest.mark.parametrize("boundary", ["events", "event_materializations", "event_extractions"])
async def test_admission_failure_rolls_back_source_and_both_jobs(config, user, monkeypatch, boundary):
    from contextlib import asynccontextmanager

    async with MemoryEngine.open(config) as engine:
        event = Event(content="Atomic acceptance", user_id=user, role="user")
        store = engine._event_store

        def matches(sql):
            return " ".join(sql.split()).startswith(f"INSERT INTO {boundary} (")

        if engine._conn is not None:
            original = store._conn

            class Connection:
                def execute(self, sql, *args):
                    result = original.execute(sql, *args)
                    if matches(sql):
                        raise RuntimeError("Injected admission failure")
                    return result

            replacement, attribute = Connection(), "_conn"
        else:
            pool = store._pool

            class Pool:
                @asynccontextmanager
                async def acquire(self):
                    async with pool.acquire() as original:
                        class Connection:
                            def transaction(self):
                                return original.transaction()

                            async def execute(self, sql, *args):
                                result = await original.execute(sql, *args)
                                if matches(sql):
                                    raise RuntimeError("Injected admission failure")
                                return result
                        yield Connection()

            replacement, attribute = Pool(), "_pool"
        with monkeypatch.context() as fault:
            fault.setattr(store, attribute, replacement)
            with pytest.raises(RuntimeError, match="Injected admission failure"):
                await store.append(event, defer_materialization=True, defer_extraction=True)
        assert await engine.get_event(str(event.id), user_id=user) is None
        assert await engine.processing_status(str(event.id), user_id=user) is None
        assert await engine.extraction_status(str(event.id), user_id=user) is None
        # Retrying the same identity proves neither job insert survived either.
        await store.append(event, defer_materialization=True, defer_extraction=True)
        assert (await engine.extraction_status(str(event.id), user_id=user)).status == "pending"


@pytest.mark.parametrize("action", ["renew", "fail"])
async def test_waiting_postgres_update_checks_lease_after_row_lock(config, user, action):
    async with MemoryEngine.open(config) as engine:
        if engine._pool is None:
            pytest.skip("PostgreSQL waits on row locks; DuckDB rejects write conflicts")
        event, claim, _ = await prepare(engine, user)
        work = engine._event_store.extraction_work
        await set_expiry(engine, str(event.id), datetime.now(timezone.utc) + timedelta(seconds=0.3))
        async with engine._pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchrow("SELECT event_id FROM event_extractions WHERE event_id = $1 FOR UPDATE", str(event.id))
                task = asyncio.create_task(work.renew(claim) if action == "renew" else work.fail(claim, error="LateFailure"))
                await asyncio.sleep(0.05)
                assert not task.done()
                await asyncio.sleep(0.3)
        assert not await task
        successor = await work.claim(user_id=user)
        assert successor.generation == claim.generation + 1
