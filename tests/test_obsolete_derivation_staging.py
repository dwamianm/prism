"""Known isolation gap which must close before abandoned-stage collection."""
import pytest

from prme import MemoryEngine
from prme.models.extraction_work import StaleExtractionClaimError
from tests import test_durable_ingestion
from tests.test_extraction_work import prepare

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_obsolete_revision_cannot_write_external_staging(config, user):
    if config.backend != 'duckdb':
        pytest.skip('PostgreSQL writes prepared indexes inside the fenced graph transaction')
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        await engine._event_store.extraction_work.fail(claim, error='StaleDerivationPlanError')
        await engine.retry_extraction(str(event.id), user_id=user, replan=True)
        assert engine._conn.execute('SELECT count(*) FROM vector_staging').fetchone()[0] == 0
        with pytest.raises((ValueError, StaleExtractionClaimError)):
            # Model an old worker resuming with the plan it loaded before its
            # lease expired and a replacement revision was queued.
            await engine._pipeline._publish_plan(plan, claim=claim)
        assert await engine.get_event_nodes(str(event.id), user_id=user) == []
        vectors = engine._conn.execute('SELECT count(*) FROM vector_staging').fetchone()[0]
        documents = engine._lexical_index._index.searcher().num_docs
        assert vectors == documents == 0


@pytest.mark.parametrize('index_name', ['vector', 'lexical'])
async def test_revision_change_waits_for_inflight_native_stage(config, user, monkeypatch, index_name):
    if config.backend != 'duckdb':
        pytest.skip('Native index fencing is local-backend specific')
    import asyncio
    import threading
    from datetime import datetime, timedelta, timezone
    from prme.storage.extraction_work import ExtractionWorkRepository
    from tests.test_extraction_work import set_expiry, extraction
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        await set_expiry(engine, str(event.id), datetime.now(timezone.utc) + timedelta(seconds=1))
        connection = engine._conn.cursor()
        other = ExtractionWorkRepository(conn=connection)
        entered, release = threading.Event(), threading.Event()
        index = getattr(engine, f'_{index_name}_index')
        original = index._do_stage
        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        try:
            with monkeypatch.context() as fault:
                fault.setattr(index, '_do_stage', blocked)
                task = asyncio.create_task(engine._pipeline._publish_plan(plan, claim=claim))
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    await asyncio.sleep(1.05)
                    assert not await other.replan(str(event.id), user_id=user)
                    assert await other.claim(user_id=user) is None
                finally:
                    release.set()
                    with pytest.raises(StaleExtractionClaimError):
                        await task
            # Once the native call has finished, the obsolete epoch can retire.
            assert await other.replan(str(event.id), user_id=user)
            before_vectors = engine._conn.execute('SELECT count(*) FROM vector_staging').fetchone()[0]
            before_documents = engine._lexical_index._index.searcher().num_docs
            with pytest.raises((ValueError, StaleExtractionClaimError)):
                await engine._pipeline._publish_plan(plan, claim=claim)
            assert engine._conn.execute('SELECT count(*) FROM vector_staging').fetchone()[0] == before_vectors
            assert engine._lexical_index._index.searcher().num_docs == before_documents
            successor = await other.claim(user_id=user)
            replacement = await engine._pipeline._prepare_plan(extraction(event.content, old=None), event)
            replacement = replacement.model_copy(update={"revision": 2})
            replacement = await engine._event_store.record_derivation_plan(replacement, claim=successor)
            await engine._pipeline._publish_plan(replacement, claim=successor)
            assert {node.id for node in await engine.get_event_nodes(str(event.id), user_id=user)} == {node.id for node in replacement.nodes}
        finally:
            release.set()
            connection.close()


@pytest.mark.parametrize('index_name', ['vector', 'lexical'])
async def test_cancellation_does_not_release_native_stage_fence_early(config, user, monkeypatch, index_name):
    if config.backend != 'duckdb':
        pytest.skip('Native index fencing is local-backend specific')
    import asyncio
    import threading
    from datetime import datetime, timedelta, timezone
    from prme.storage.derivation_staging import DuckDBStageFence
    from prme.storage.extraction_work import ExtractionWorkRepository
    from tests.test_extraction_work import set_expiry
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        await set_expiry(engine, str(event.id), datetime.now(timezone.utc) + timedelta(seconds=1))
        fence = DuckDBStageFence(engine._conn, engine._graph_store._conn_lock, plan, claim)
        connection = engine._conn.cursor()
        other = ExtractionWorkRepository(conn=connection)
        entered, release = threading.Event(), threading.Event()
        index = getattr(engine, f'_{index_name}_index')
        original = index._do_stage
        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        try:
            with monkeypatch.context() as fault:
                fault.setattr(index, '_do_stage', blocked)
                operation = (index.stage(plan.embeddings[0], user_id=user, fence=fence) if index_name == 'vector'
                             else index.stage(plan, fence=fence))
                task = asyncio.create_task(operation)
                try:
                    assert await asyncio.to_thread(entered.wait, 5)
                    task.cancel()
                    await asyncio.sleep(1.05)
                    task.cancel()
                    assert not task.done()
                    assert await other.claim(user_id=user) is None
                finally:
                    release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await task
            successor = await other.claim(user_id=user)
            assert successor is not None
            await engine._pipeline._publish_plan(plan, claim=successor)
            assert (await engine.extraction_status(str(event.id), user_id=user)).status == 'complete'
        finally:
            release.set()
            connection.close()


async def test_stage_fence_rejects_inputs_outside_its_saved_plan(config, user):
    if config.backend != 'duckdb':
        pytest.skip('Native index fencing is local-backend specific')
    from uuid import uuid4
    from prme.storage.derivation_staging import DuckDBStageFence
    async with MemoryEngine.open(config) as engine:
        _, claim, plan = await prepare(engine, user)
        fence = DuckDBStageFence(engine._conn, engine._graph_store._conn_lock, plan, claim)
        with pytest.raises(ValueError, match='belong'):
            await engine._vector_index.stage(plan.embeddings[0].model_copy(update={'content': 'Different input'}),
                                              user_id=user, fence=fence)
        with pytest.raises(ValueError, match='belong'):
            await engine._lexical_index.stage(plan.model_copy(update={'id': uuid4()}), fence=fence)
        assert engine._conn.execute('SELECT count(*) FROM vector_staging').fetchone()[0] == 0
        assert engine._lexical_index._index.searcher().num_docs == 0
