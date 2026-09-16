"""Another schema's names cannot satisfy this database installation's DDL."""

import os
from uuid import uuid4

import pytest

asyncpg = pytest.importorskip("asyncpg")
from prme.storage.pg.schema import _NODES_TABLE, initialize_pg_database  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("PRME_TEST_DATABASE_URL"), reason="Live PostgreSQL required")


@pytest.mark.parametrize("collision", ["embedding_column", "index_name"])
async def test_schema_setup_ignores_foreign_catalog_names(collision):
    suffix = uuid4().hex
    target, other = "prme_target_" + suffix, "prme_other_" + suffix
    admin = await asyncpg.connect(os.environ["PRME_TEST_DATABASE_URL"])
    pool = None
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        for name in (target, other):
            await admin.execute(f'CREATE SCHEMA "{name}"')
        await admin.execute(f'CREATE TABLE "{other}".nodes (embedding TEXT)')
        await admin.execute(f'CREATE INDEX idx_nodes_embedding_hnsw ON "{other}".nodes (embedding)')
        pool = await asyncpg.create_pool(os.environ["PRME_TEST_DATABASE_URL"], min_size=1, max_size=1,
                                        server_settings={"search_path": f'"{target}", public'})
        if collision == "index_name":
            async with pool.acquire() as conn:
                await conn.execute(_NODES_TABLE)
                await conn.execute("ALTER TABLE nodes ADD COLUMN embedding vector(384)")
        await initialize_pg_database(pool)
        await initialize_pg_database(pool)
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                                       "WHERE attrelid='nodes'::regclass AND attname='embedding'") == "vector(384)"
            assert await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname=$1 "
                                       "AND tablename='nodes' AND indexname='idx_nodes_embedding_hnsw'", target)
            assert await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname=$1 "
                                       "AND indexname='idx_nodes_embedding_hnsw'", other)
    finally:
        if pool is not None:
            await pool.close()
        # Only these random schemas were created by this test.
        for name in (target, other):
            await admin.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        await admin.close()
