"""Replaced derivations reclaim external indexes without losing retryable work."""
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.storage.derivation_staging import DuckDBStageFence
from tests.test_durable_ingestion import MockEmbeddingProvider
from tests.test_extraction_work import prepare


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr('prme.storage.engine.create_embedding_provider', lambda _: MockEmbeddingProvider())
    (tmp_path / 'lexical').mkdir()
    return PRMEConfig(database_url=None, db_path=str(tmp_path/'memory.duckdb'),
        vector_path=str(tmp_path/'vectors.usearch'), lexical_path=str(tmp_path/'lexical'),
        organizer={'opportunistic_enabled': False}, materialization_budget_ms=5000)


async def staged(engine, user, *, replace=True):
    event, claim, plan = await prepare(engine, user)
    fence = DuckDBStageFence(engine._conn, engine._graph_store._conn_lock, plan, claim)
    for embedding in plan.embeddings:
        await engine._vector_index.stage(embedding, user_id=user, fence=fence)
    await engine._lexical_index.stage(plan, fence=fence)
    await engine._event_store.extraction_work.fail(claim, error='StaleDerivationPlanError')
    if replace:
        await engine.retry_extraction(str(event.id), user_id=user, replan=True)
    return event, plan


def vector_ids(engine):
    return {row[0] for row in engine._conn.execute('SELECT node_id FROM vector_metadata').fetchall()}


async def test_scoped_collection_reclaims_only_replaced_revision_and_preserves_journals(config):
    async with MemoryEngine.open(config) as engine:
        event, old = await staged(engine, 'alice')
        _, foreign = await staged(engine, 'bob')
        old_ids = {str(n.id) for n in old.nodes}
        foreign_ids = {str(n.id) for n in foreign.nodes}
        assert old_ids | foreign_ids <= vector_ids(engine)
        await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
        assert old_ids.isdisjoint(vector_ids(engine))
        assert foreign_ids <= vector_ids(engine)
        assert await engine.get_event(str(event.id), user_id='alice') == event
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id='alice', revision=1) == old
        assert engine._conn.execute('SELECT count(*) FROM derivation_artifact_owners WHERE operation_id = ?', [old.prepared_operation_id]).fetchone()[0] == len(old.nodes)
        lexical_ids = {hit['node_id'] for hit in await engine._lexical_index.search('Alice Rust', 'alice', limit=100)}
        assert old_ids.isdisjoint(lexical_ids)
        await engine.organize(user_id='bob', jobs=['index_compaction'], budget_ms=5000)
        assert foreign_ids.isdisjoint(vector_ids(engine))
    async with MemoryEngine.open(config) as engine:
        assert (old_ids | foreign_ids).isdisjoint(vector_ids(engine))
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id='alice', revision=1) == old


@pytest.mark.parametrize('retry', [False, True])
async def test_failed_or_pending_current_plan_remains_recoverable(config, retry):
    async with MemoryEngine.open(config) as engine:
        event, plan = await staged(engine, 'alice', replace=False)
        if retry:
            await engine.retry_extraction(str(event.id), user_id='alice')
        await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
        assert {str(n.id) for n in plan.nodes} <= vector_ids(engine)
        if not retry:
            await engine.retry_extraction(str(event.id), user_id='alice')
        claim = await engine._event_store.extraction_work.claim(user_id='alice')
        await engine._pipeline._publish_plan(plan, claim=claim)
        await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
        assert {str(n.id) for n in plan.nodes} <= vector_ids(engine)
        assert (await engine.extraction_status(str(event.id), user_id='alice')).status == 'complete'


@pytest.mark.parametrize('failure', ['lexical', 'native_vector'])
async def test_failed_deletion_keeps_durable_retry_anchor(config, monkeypatch, failure):
    async with MemoryEngine.open(config) as engine:
        _, plan = await staged(engine, 'alice')
        ids = {str(n.id) for n in plan.nodes}
        with monkeypatch.context() as fault:
            if failure == 'lexical':
                fault.setattr(engine._lexical_index, 'delete_by_node_id', AsyncMock(side_effect=OSError('Deletion unavailable')))
            else:
                fault.setattr(engine._vector_index._index, 'remove', Mock(side_effect=RuntimeError('Native removal unavailable')))
            result = await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
            assert ids <= vector_ids(engine)
            # Failed reclamation is reported, not counted as successful work.
            job = result.per_job['index_compaction']
            assert job.errors == len(ids) and job.nodes_modified == 0
    async with MemoryEngine.open(config) as engine:
        await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
        assert ids.isdisjoint(vector_ids(engine))


@pytest.mark.parametrize('obstacle', ['ambiguous', 'unregistered', 'corrupt_journal'])
async def test_uncertain_ownership_or_journal_prevents_collection(config, obstacle):
    async with MemoryEngine.open(config) as engine:
        _, plan = await staged(engine, 'alice')
        ids = {str(n.id) for n in plan.nodes}
        if obstacle == 'ambiguous':
            engine._conn.execute('UPDATE derivation_artifact_owners SET operation_id = NULL WHERE operation_id = ?', [plan.prepared_operation_id])
        elif obstacle == 'unregistered':
            engine._conn.execute("INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id) VALUES (?, 'DERIVATION_PREPARED', ?, '{}', 'test', 'PERSONAL')", [str(uuid4()), str(uuid4())])
        else:
            engine._conn.execute("UPDATE operations SET payload = '{}' WHERE id = ?", [plan.prepared_operation_id])
        await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
        assert ids <= vector_ids(engine)


async def test_archive_lexical_outage_remains_discoverable_by_compaction(config, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.store('Cobalt telescopes', user_id='alice')
        node = (await engine.get_event_nodes(event_id, user_id='alice'))[0]
        with monkeypatch.context() as fault:
            fault.setattr(engine._lexical_index, 'delete_by_node_id', AsyncMock(side_effect=OSError('Deletion unavailable')))
            await engine.archive(str(node.id), user_id='alice')
        assert str(node.id) in vector_ids(engine)
        await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
        assert str(node.id) not in vector_ids(engine)
        assert await engine._lexical_index.search('cobalt telescopes', 'alice') == []


async def test_process_exit_after_lexical_delete_resumes_collection(config):
    import asyncio
    import os
    from pathlib import Path
    import subprocess
    import sys
    script = '''
import asyncio, os, sys
from prme import MemoryEngine, PRMEConfig
import prme.storage.engine as engine_module
from tests.test_durable_ingestion import MockEmbeddingProvider
from tests.test_retired_stage_collection import staged
async def main():
    db, vector, lexical = sys.argv[1:]
    engine_module.create_embedding_provider = lambda _: MockEmbeddingProvider()
    config = PRMEConfig(database_url=None, db_path=db, vector_path=vector, lexical_path=lexical,
                        organizer={'opportunistic_enabled': False})
    async with MemoryEngine.open(config) as engine:
        await staged(engine, 'alice')
        async def crash(*args, **kwargs):
            os._exit(42)
        engine._vector_index.delete_by_node_id = crash
        await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
asyncio.run(main())
'''
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = os.pathsep.join([str(root/'src'), str(root)])
    child = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script,
        config.db_path, config.vector_path, config.lexical_path], capture_output=True, timeout=30, env=env)
    assert child.returncode == 42, child.stderr.decode()
    async with MemoryEngine.open(config) as engine:
        event = (await engine.get_events('alice'))[0]
        plan = await engine._event_store.get_derivation_plan(str(event.id), user_id='alice', revision=1)
        ids = {str(n.id) for n in plan.nodes}
        assert ids <= vector_ids(engine)
        await engine.organize(user_id='alice', jobs=['index_compaction'], budget_ms=5000)
        assert ids.isdisjoint(vector_ids(engine))
        assert await engine.get_event(str(event.id), user_id='alice') == event
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id='alice', revision=1) == plan
