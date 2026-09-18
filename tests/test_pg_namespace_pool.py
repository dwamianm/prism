"""Adversarial schema selection and connection reuse against native PostgreSQL."""

import asyncio
import os
from uuid import uuid4

import pytest

asyncpg = pytest.importorskip('asyncpg')
from prme.storage.namespace_identity import NamespaceIdentityError  # noqa: E402
from prme.storage.pg.namespace import namespace_schema, prepare_namespace  # noqa: E402
from prme.storage.pg.vector_index import PgVectorIndex  # noqa: E402
from prme.storage.pg.vector_sql import quote_identifier  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get('PRME_TEST_DATABASE_URL'), reason='Live PostgreSQL required')


class Provider:
    model_name = 'namespace-test'
    model_version = '1'
    dimension = 2

    async def embed(self, texts):
        return [[1., 0.] for _ in texts]


@pytest.fixture
async def namespaces():
    pool = await asyncpg.create_pool(os.environ['PRME_TEST_DATABASE_URL'], min_size=1, max_size=1)
    identifiers = [uuid4(), uuid4()]
    async with pool.acquire() as conn:
        await conn.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
        await conn.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public')
    try:
        yield pool, identifiers
    finally:
        async with pool.acquire() as conn:
            for identifier in identifiers:
                await conn.execute(f'DROP SCHEMA IF EXISTS {quote_identifier(namespace_schema(identifier))} CASCADE')
        await pool.close()


async def prepared(namespaces):
    pool, identifiers = namespaces
    return [await prepare_namespace(pool, identifier, embedding_dim=2, create=True) for identifier in identifiers]


async def test_reused_connection_keeps_identical_sql_and_ids_in_distinct_namespaces(namespaces):
    left, right = await prepared(namespaces)
    node_id = uuid4()
    for ns, content in [(left, 'left'), (right, 'right')]:
        async with ns.acquire() as conn:
            assert await conn.fetchval('SHOW search_path') == quote_identifier(ns.schema)
            await conn.execute("INSERT INTO nodes (id, node_type, content, user_id) VALUES ($1, 'fact', $2, 'same-owner')", node_id, content)
        await PgVectorIndex(ns, Provider()).index(str(node_id), content, 'same-owner')
    for _ in range(8):
        for ns, expected in [(left, 'left'), (right, 'right')]:
            async with ns.acquire() as conn:
                assert await conn.fetchval('SELECT content FROM nodes WHERE id=$1', node_id) == expected
            for exact in [True, False]:
                found = await PgVectorIndex(ns, Provider(), exact_search=exact).search('query', 'same-owner')
                assert [r['node_id'] for r in found] == [str(node_id)]
    await left.close()
    async with right.acquire() as conn:
        assert await conn.fetchval('SELECT content FROM nodes') == 'right'
    with pytest.raises(RuntimeError, match='closed'):
        async with left.acquire():
            pytest.fail('Closed namespace accepted a checkout')


async def test_missing_table_never_falls_back_to_public_or_temp(namespaces):
    left, _ = await prepared(namespaces)
    pool, _ = namespaces
    async with pool.acquire() as conn:
        created_public = await conn.fetchval("SELECT to_regclass('public.nodes')") is None
        if created_public:
            await conn.execute('CREATE TABLE public.nodes (content TEXT)')
        # A temporary table survives asyncpg's normal release/reset.
        await conn.execute('CREATE TEMP TABLE nodes (content TEXT)')
        await conn.execute("INSERT INTO nodes VALUES ('foreign temp')")
    try:
        async with left.acquire() as conn:
            assert await conn.fetchval('SELECT count(*) FROM nodes') == 0
            assert await conn.fetchval("SELECT to_regclass('public.nodes')") is not None
            await conn.execute('DROP TABLE nodes CASCADE')
        async with left.acquire() as conn:
            with pytest.raises(asyncpg.UndefinedTableError):
                await conn.fetch('SELECT * FROM nodes')
        with pytest.raises(NamespaceIdentityError, match='relations are missing'):
            await prepare_namespace(pool, left.identifier, embedding_dim=2)
    finally:
        if created_public:
            async with pool.acquire() as conn:
                await conn.execute('DROP TABLE public.nodes')


async def test_identity_is_checked_on_every_checkout(namespaces):
    left, right = await prepared(namespaces)
    pool, _ = namespaces
    async with pool.acquire() as conn:
        await conn.execute(f'UPDATE {quote_identifier(left.schema)}.prme_namespace_identity SET namespace_id=$1', right.identifier)
    with pytest.raises(NamespaceIdentityError, match='does not match'):
        async with left.acquire():
            pytest.fail('Wrong identity was accepted')


async def test_missing_initialized_schema_is_not_recreated(namespaces):
    left, _ = await prepared(namespaces)
    pool, _ = namespaces
    async with pool.acquire() as conn:
        await conn.execute(f'DROP SCHEMA {quote_identifier(left.schema)} CASCADE')
    with pytest.raises(NamespaceIdentityError, match='missing'):
        async with left.acquire():
            pytest.fail('Missing namespace was accepted')
    with pytest.raises(NamespaceIdentityError, match='missing'):
        await prepare_namespace(pool, left.identifier, embedding_dim=2)
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT oid FROM pg_namespace WHERE nspname=$1', left.schema) is None


async def test_existing_unbound_schema_is_not_adopted(namespaces):
    pool, identifiers = namespaces
    schema = namespace_schema(identifiers[0])
    async with pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA {quote_identifier(schema)}')
    with pytest.raises(NamespaceIdentityError, match='no namespace identity'):
        await prepare_namespace(pool, identifiers[0], embedding_dim=2, create=True)


async def test_failed_initialization_rolls_back_schema_and_can_retry(namespaces, monkeypatch):
    pool, identifiers = namespaces
    from prme.storage.pg import namespace as module
    original = module.initialize_pg_database
    async def fail(pool, **kwargs):
        await original(pool, **kwargs)
        raise RuntimeError('injected post-DDL failure')
    monkeypatch.setattr(module, 'initialize_pg_database', fail)
    with pytest.raises(RuntimeError, match='injected'):
        await prepare_namespace(pool, identifiers[0], embedding_dim=2, create=True)
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT oid FROM pg_namespace WHERE nspname=$1', namespace_schema(identifiers[0])) is None
    monkeypatch.setattr(module, 'initialize_pg_database', original)
    ns = await prepare_namespace(pool, identifiers[0], embedding_dim=2, create=True)
    async with ns.acquire() as conn:
        assert await conn.fetchval('SELECT count(*) FROM events') == 0


async def test_close_waits_for_active_checkout_without_closing_root_pool(namespaces):
    left, _ = await prepared(namespaces)
    pool, _ = namespaces
    async with left.acquire():
        closer = asyncio.create_task(left.close())
        await asyncio.sleep(0)
        assert not closer.done()
    await asyncio.wait_for(closer, 2)
    async with pool.acquire() as conn:
        assert await conn.fetchval('SELECT 1') == 1


async def test_cancelled_waiting_checkout_does_not_leak_close_ownership(namespaces):
    left, _ = await prepared(namespaces)
    pool, _ = namespaces
    async def wait():
        async with left.acquire():
            pytest.fail('Root pool should still be occupied')
    async with pool.acquire():
        task = asyncio.create_task(wait())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.wait_for(left.close(), 2)
