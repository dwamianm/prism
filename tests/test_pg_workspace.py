"""Public PostgreSQL workspace workflows and shared cache ownership."""

import asyncio
from contextlib import asynccontextmanager
import os
from uuid import uuid4

import pytest

asyncpg = pytest.importorskip('asyncpg')
from prme import MemoryWorkspace, PRMEConfig  # noqa: E402
from prme.storage.namespace_identity import NamespaceIdentityError  # noqa: E402
from prme.storage.pg.namespace import namespace_schema  # noqa: E402
from prme.storage.pg.vector_sql import quote_identifier  # noqa: E402
from tests import test_workspace as contracts  # noqa: E402
from tests.test_durable_ingestion import MockEmbeddingProvider  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get('PRME_TEST_DATABASE_URL'), reason='Live PostgreSQL required')


@pytest.fixture
async def pg_workspace(monkeypatch):
    name = 'authored-' + uuid4().hex
    config = PRMEConfig(database_url=os.environ['PRME_TEST_DATABASE_URL'], namespace_id=None,
                        organizer={'opportunistic_enabled': False}, materialization_budget_ms=5000)
    @asynccontextmanager
    async def open_workspace(path=None, config=config, max_open=1):
        async with MemoryWorkspace.open_postgres(config, name=name, embedding_provider=MockEmbeddingProvider(),
                                                 max_open=max_open, max_connections=1) as workspace:
            yield workspace
    # Reuse the public local workflow assertions without changing their outcomes.
    monkeypatch.setattr(contracts, 'workspace', open_workspace)
    try:
        yield name, config, open_workspace
    finally:
        conn = await asyncpg.connect(os.environ['PRME_TEST_DATABASE_URL'])
        try:
            exists = await conn.fetchval("SELECT to_regclass('prme_workspace.namespaces')")
            if exists:
                for row in await conn.fetch('SELECT id FROM prme_workspace.namespaces WHERE workspace=ANY($1::text[])', [name, name + ':other']):
                    await conn.execute(f'DROP SCHEMA IF EXISTS {quote_identifier(namespace_schema(row["id"]))} CASCADE')
                await conn.execute('DELETE FROM prme_workspace.namespaces WHERE workspace=ANY($1::text[])', [name, name + ':other'])
        finally:
            await conn.close()


@pytest.mark.parametrize('contract', [
    'test_same_owner_project_names_preserve_sources_receipts_and_recovery_across_eviction',
    'test_same_project_leases_share_capacity_but_have_independent_lifetimes',
    'test_iterator_lifetime_is_tracked_and_iteration_after_release_rejected',
])
async def test_shared_public_contracts(pg_workspace, tmp_path, contract):
    _, config, _ = pg_workspace
    await getattr(contracts, contract)(tmp_path, config)


@pytest.mark.parametrize('contract', [
    'test_leased_operations_finish_before_eviction_and_saved_methods_expire',
    'test_structured_ingestion_derivations_and_foreign_mutations_stay_in_project',
    'test_cancelled_eviction_finishes_native_close_before_capacity_is_reused',
])
async def test_shared_fault_and_derivation_contracts(pg_workspace, tmp_path, monkeypatch, contract):
    _, config, _ = pg_workspace
    await getattr(contracts, contract)(tmp_path, config, monkeypatch)


async def test_many_projects_share_one_connection_and_bounded_cache(pg_workspace):
    _, _, open_workspace = pg_workspace
    records = {}
    async with open_workspace(max_open=3) as workspace:
        for i in range(20):
            async with workspace.namespace(f'project-{i}') as memory:
                records[i] = await memory.store(f'durable source {i}', user_id='same-owner')
                assert len(workspace._entries) <= 3
                assert workspace._pool.get_size() == 1
        for i in reversed(range(20)):
            async with workspace.namespace(f'project-{i}', create=False) as memory:
                assert (await memory.get_event(records[i], user_id='same-owner')).content == f'durable source {i}'
                assert await memory.get_event(records[(i + 1) % 20], user_id='same-owner') is None
        assert len(await workspace.list_namespaces()) == 20


async def test_independent_workspace_instances_resolve_same_name_atomically(pg_workspace):
    _, _, open_workspace = pg_workspace
    async with open_workspace() as first, open_workspace() as second:
        async def create(workspace):
            async with workspace.namespace('shared') as memory:
                event = await memory.store('concurrent admission', user_id='owner')
                return memory.namespace, event
        left, right = await asyncio.gather(create(first), create(second))
        assert left[0] == right[0]
        async with first.namespace('shared') as memory:
            assert await memory.get_event(right[1], user_id='owner') is not None
        await first.close()
        async with second.namespace('shared') as memory:
            assert await memory.get_event(left[1], user_id='owner') is not None


async def test_schema_loss_after_initialization_never_becomes_empty_memory(pg_workspace):
    _, _, open_workspace = pg_workspace
    async with open_workspace() as workspace:
        async with workspace.namespace('lost') as memory:
            identifier = memory.namespace.id
            await memory.store('must not disappear silently', user_id='owner')
    admin = await asyncpg.connect(os.environ['PRME_TEST_DATABASE_URL'])
    try:
        await admin.execute(f'DROP SCHEMA {quote_identifier(namespace_schema(identifier))} CASCADE')
        async with open_workspace() as workspace:
            with pytest.raises(NamespaceIdentityError, match='missing'):
                async with workspace.namespace('lost'):
                    pytest.fail('Missing schema was recreated')
        assert await admin.fetchval('SELECT oid FROM pg_namespace WHERE nspname=$1', namespace_schema(identifier)) is None
    finally:
        await admin.close()


async def test_catalog_publication_rolls_back_with_initialization(pg_workspace, monkeypatch):
    _, _, open_workspace = pg_workspace
    from prme.storage.pg import namespace as module
    original = module.initialize_pg_database
    async def fail(pool, **kwargs):
        await original(pool, **kwargs)
        raise RuntimeError('injected namespace setup failure')
    async with open_workspace() as workspace:
        monkeypatch.setattr(module, 'initialize_pg_database', fail)
        with pytest.raises(RuntimeError, match='injected'):
            async with workspace.namespace('retry'):
                pass
        info, = await workspace.list_namespaces()
        async with workspace._pool.acquire() as conn:
            assert not await conn.fetchval('SELECT initialized FROM prme_workspace.namespaces WHERE id=$1', info.id)
            assert await conn.fetchval('SELECT oid FROM pg_namespace WHERE nspname=$1', namespace_schema(info.id)) is None
        monkeypatch.setattr(module, 'initialize_pg_database', original)
        async with workspace.namespace('retry') as memory:
            assert memory.namespace == info
            await memory.store('retry success', user_id='owner')


@pytest.mark.parametrize('limits', [{'max_connections': 0}, {'min_connections': True},
                                    {'min_connections': 2, 'max_connections': 1}])
async def test_connection_limits_are_validated_before_opening(pg_workspace, limits):
    _, config, _ = pg_workspace
    with pytest.raises(ValueError):
        async with MemoryWorkspace.open_postgres(config, embedding_provider=MockEmbeddingProvider(), **limits):
            pass


async def test_cancelled_workspace_startup_closes_pool_before_unwinding(pg_workspace, monkeypatch):
    from prme.storage.pg.workspace_catalog import WorkspaceCatalog
    _, config, _ = pg_workspace
    started, finish = asyncio.Event(), asyncio.Event()
    original = WorkspaceCatalog.initialize
    captured = []
    async def pause(self):
        captured.append(self.pool)
        started.set()
        await finish.wait()
        await original(self)
    monkeypatch.setattr(WorkspaceCatalog, 'initialize', pause)
    async def open_it():
        async with MemoryWorkspace.open_postgres(config, embedding_provider=MockEmbeddingProvider()):
            pytest.fail('Cancelled context must not be delivered')
    task = asyncio.create_task(open_it())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert captured[0]._closed


async def test_process_exit_preserves_registry_identity_and_pending_source(pg_workspace, tmp_path):
    import json
    import subprocess
    import inspect
    from tests.test_durable_ingestion import MockEmbeddingProvider
    import sys
    name, _, open_workspace = pg_workspace
    output = tmp_path / 'committed.json'
    script = '''
import asyncio, json, os, sys
from prme import MemoryWorkspace, PRMEConfig
async def main():
    async with MemoryWorkspace.open_postgres(PRMEConfig(database_url=os.environ['PRME_TEST_DATABASE_URL']),
            name=sys.argv[1], embedding_provider=MockEmbeddingProvider(), max_connections=1) as ws:
        async with ws.namespace('abrupt') as memory:
            event = await memory.ingest_fast('durable before process exit', user_id='owner')
            with open(sys.argv[2], 'w') as f:
                json.dump({'namespace': str(memory.namespace.id), 'event': event}, f)
            os._exit(37)
asyncio.run(main())
'''
    script = "import hashlib\n" + inspect.getsource(MockEmbeddingProvider) + "\n" + script
    result = subprocess.run([sys.executable, '-c', script, name, str(output)], capture_output=True, timeout=30)
    assert result.returncode == 37, result.stderr.decode()[-1000:]
    record = json.loads(output.read_text())
    async with open_workspace() as workspace:
        async with workspace.namespace('abrupt', create=False) as memory:
            assert str(memory.namespace.id) == record['namespace']
            assert (await memory.processing_status(record['event'], user_id='owner')).status == 'pending'
            assert (await memory.process_pending(user_id='owner', budget_ms=5000)).processed == 1
            assert (await memory.get_event(record['event'], user_id='owner')).content == 'durable before process exit'


async def test_relocated_extension_supports_ingestion_profiles_and_search(monkeypatch):
    from urllib.parse import urlsplit, urlunsplit
    from unittest.mock import AsyncMock
    from prme.ingestion.schema import ExtractionResult, ExtractedEntity, ExtractedFact
    from prme.types import Scope
    from tests.test_knowledge_profile_scopes import seed, profiles
    admin = await asyncpg.connect(os.environ['PRME_TEST_DATABASE_URL'])
    database = 'prme_ext_' + uuid4().hex
    try:
        try:
            await admin.execute(f'CREATE DATABASE {quote_identifier(database)}')
        except asyncpg.InsufficientPrivilegeError:
            pytest.skip('Relocated-extension workflow requires CREATEDB on a disposable test server')
        parts = urlsplit(os.environ['PRME_TEST_DATABASE_URL'])
        url = urlunsplit((parts.scheme, parts.netloc, '/' + database, parts.query, parts.fragment))
        conn = await asyncpg.connect(url)
        extension_schema = 'vector "extension; schema'
        try:
            await conn.execute(f'CREATE SCHEMA {quote_identifier(extension_schema)}')
            await conn.execute(f'CREATE EXTENSION vector WITH SCHEMA {quote_identifier(extension_schema)}')
        finally:
            await conn.close()
        extraction = ExtractionResult(entities=[ExtractedEntity(name='Alice', entity_type='person')],
            facts=[ExtractedFact(subject='Alice', predicate='uses', object='Rust', evidence_quote='Alice uses Rust.')])
        extractor = type('Extractor', (), {'provider_name': 'authored', 'model_name': 'fixed-extraction'})()
        extractor.extract = AsyncMock(return_value=extraction)
        monkeypatch.setattr('prme.ingestion.extraction.create_extraction_provider', lambda _: extractor)
        config = PRMEConfig(database_url=url, namespace_id=None, organizer={'opportunistic_enabled': False})
        for iteration in range(2):
            async with MemoryWorkspace.open_postgres(config, embedding_provider=MockEmbeddingProvider(),
                                                     max_open=1, max_connections=1) as workspace:
                async with workspace.namespace('first') as memory:
                    if iteration == 0:
                        event = await memory.ingest('Alice uses Rust.', user_id='owner', wait_for_extraction=True)
                        assert (await memory.extraction_status(event, user_id='owner')).status == 'complete'
                        await seed(memory, 'owner')
                    assert await memory.consolidate_knowledge(user_id='owner', entity_names=['Aurora']) == 2
                    actual = await profiles(memory, 'owner')
                    for profile in actual:
                        result = await memory.retrieve('Aurora', user_id='owner', scope=profile.scope)
                        assert profile.id in [r.node.id for r in result.results]
                async with workspace.namespace('second') as memory:
                    assert await memory.get_extraction(event, user_id='owner') is None
                    assert await memory.query_nodes(user_id='owner', scopes=[Scope.PROJECT]) == []
        conn = await asyncpg.connect(url)
        try:
            assert await conn.fetchval("SELECT to_regclass('public.nodes')") is None
            assert await conn.fetchval("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
                                       "WHERE e.extname='vector'") == extension_schema
            await conn.execute('DROP SCHEMA prme_workspace CASCADE')
            with pytest.raises(NamespaceIdentityError, match='registry is missing'):
                async with MemoryWorkspace.open_postgres(config, embedding_provider=MockEmbeddingProvider()):
                    pass
            assert await conn.fetchval("SELECT to_regnamespace('prme_workspace')") is None
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS {quote_identifier(database)} WITH (FORCE)')
        await admin.close()


async def test_workspace_names_are_distinct_keys_and_sql_names_are_not_interpolated(pg_workspace):
    name, config, open_workspace = pg_workspace
    unusual = "project/quote'\"; -- é"
    async with open_workspace() as left, MemoryWorkspace.open_postgres(
            config, name=name + ':other', embedding_provider=MockEmbeddingProvider(), max_connections=1) as right:
        async with left.namespace(unusual) as memory:
            own = memory.namespace
            event = await memory.store('left-only source', user_id='owner')
        assert await right.list_namespaces() == []
        async with right.namespace(unusual) as memory:
            assert memory.namespace.id != own.id
            assert await memory.get_event(event, user_id='owner') is None
        assert [n.name for n in await left.list_namespaces()] == [unusual]
