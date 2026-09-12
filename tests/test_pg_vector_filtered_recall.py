"""Exact PostgreSQL retrieval must not depend on ineligible ANN neighbors."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from uuid import UUID, uuid4

import pytest

asyncpg = pytest.importorskip("asyncpg")
from prme.storage.pg.schema import initialize_pg_database  # noqa: E402
from prme.storage.pg.vector_index import PgVectorIndex  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("PRME_TEST_DATABASE_URL"), reason="Live PostgreSQL required")


class Provider:
    model_name = "filtered-pg"
    model_version = "1"
    dimension = 2

    async def embed(self, texts):
        return [[1., 0.] for _ in texts]


class TracedPool:
    def __init__(self, pool):
        self.pool = pool
        self.query = None

    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as connection:
            parent = self
            class Connection:
                def __getattr__(self, name):
                    return getattr(connection, name)

                async def fetch(self, query, *args):
                    parent.query = (query, args)
                    return await connection.fetch(query, *args)
            yield Connection()


def plan_indexes(value):
    if isinstance(value, dict):
        return ([value["Index Name"]] if "Index Name" in value else []) + [
            name for child in value.values() for name in plan_indexes(child)]
    if isinstance(value, list):
        return [name for child in value for name in plan_indexes(child)]
    return []


@pytest.fixture
async def corpus():
    schema = "prme_vector_filter_" + uuid4().hex
    admin = await asyncpg.connect(os.environ["PRME_TEST_DATABASE_URL"])
    pool = None
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(os.environ["PRME_TEST_DATABASE_URL"], min_size=1, max_size=1,
            server_settings={"search_path": f'"{schema}", public', "enable_seqscan": "off",
                             "enable_bitmapscan": "off", "hnsw.ef_search": "40", "hnsw.iterative_scan": "off"})
        await initialize_pg_database(pool, embedding_dim=2)
        async with pool.acquire() as conn:
            indexes = await conn.fetch("SELECT indexname FROM pg_indexes WHERE schemaname=$1 AND tablename='nodes'", schema)
            for row in indexes:
                if row["indexname"] not in {"nodes_pkey", "idx_nodes_embedding_hnsw"}:
                    await conn.execute(f'DROP INDEX "{row["indexname"]}"')
        traced = TracedPool(pool)
        yield pool, traced
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def populate(pool, excluded_by):
    before, cutoff, after = [datetime(y, 1, 1, tzinfo=timezone.utc) for y in [2024, 2025, 2026]]
    rows = []
    for i in range(512):
        rows.append((uuid4(), "other" if excluded_by == "user" else "owner",
                     "project" if excluded_by == "scope" else "personal",
                     "archived" if excluded_by == "archived" else "tentative",
                     after if excluded_by == "future" else before,
                     cutoff if excluded_by == "expired" else None,
                     f"[1,{i / 1_000_000}]") )
    expected = [UUID(int=i) for i in [30, 20, 10]]
    for identifier, vector in zip(expected, ["[0.8,0.6]", "[0.6,0.8]", "[0,1]"]):
        rows.append((identifier, "owner", "personal", "tentative", before, None, vector))
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO nodes (id,node_type,user_id,scope,lifecycle_state,valid_from,valid_to,content,"
            "embedding,embedding_model,embedding_version) VALUES ($1,'fact',$2,$3,$4,$5,$6,'authored',$7::vector,'filtered-pg','1')",
            rows,
        )
        await conn.execute("ANALYZE nodes")
    filters = {"scope": {"scope": ["personal"]}, "expired": {"time_from": cutoff},
               "future": {"time_to": cutoff}}.get(excluded_by, {})
    return list(map(str, expected)), filters


@pytest.mark.parametrize("excluded_by", ["user", "scope", "expired", "future", "archived"])
async def test_default_search_preserves_eligible_neighbors_when_hnsw_would_starve(corpus, excluded_by):
    pool, traced = corpus
    expected, filters = await populate(pool, excluded_by)
    index = PgVectorIndex(traced, Provider())
    found = await index.search("query", "owner", k=2, **filters)
    query, args = traced.query
    async with pool.acquire() as conn:
        plan = json.loads(await conn.fetchval("EXPLAIN (FORMAT JSON) " + query, *args))
    assert [row["node_id"] for row in found] == expected[:2], {"found": found, "indexes": plan_indexes(plan)}
    assert "idx_nodes_embedding_hnsw" not in json.dumps(plan)


async def test_default_exact_ties_have_stable_ids_and_undefined_cosine_is_excluded(corpus):
    pool, traced = corpus
    async with pool.acquire() as conn:
        for i in [50, 40, 30, 20, 10]:
            await conn.execute("INSERT INTO nodes (id,node_type,user_id,content,embedding,embedding_model,embedding_version) "
                               "VALUES ($1,'fact','owner','tie','[1,0]','filtered-pg','1')", UUID(int=i))
        await conn.execute("INSERT INTO nodes (id,node_type,user_id,content,embedding,embedding_model,embedding_version) "
                           "VALUES ($1,'fact','owner','zero','[0,0]','filtered-pg','1')", uuid4())
    results = await PgVectorIndex(traced, Provider()).search("query", "owner", k=10)
    assert [r["node_id"] for r in results] == [str(UUID(int=i)) for i in [10, 20, 30, 40, 50]]
    assert await PgVectorIndex(traced, Provider()).search_by_vector([0., 0.], "owner", k=10) == []


async def test_approximate_mode_remains_explicit_and_can_underreturn(corpus):
    pool, traced = corpus
    await populate(pool, "user")
    index = PgVectorIndex(traced, Provider(), exact_search=False)
    assert await index.search("query", "owner", k=2) == []
    query, args = traced.query
    async with pool.acquire() as conn:
        plan = json.loads(await conn.fetchval("EXPLAIN (FORMAT JSON) " + query, *args))
    assert "idx_nodes_embedding_hnsw" in plan_indexes(plan)


@pytest.mark.parametrize("exact", [True, False])
async def test_engine_honors_vector_mode_and_records_it_in_receipts(corpus, monkeypatch, exact):
    from prme import MemoryEngine, PRMEConfig
    pool, _ = corpus
    await populate(pool, "user")
    async def reuse(*args, **kwargs):
        return pool
    monkeypatch.setattr("prme.storage.pg.create_pool", reuse)
    config = PRMEConfig(database_url=os.environ["PRME_TEST_DATABASE_URL"], vector_exact_search=exact,
                        organizer={"opportunistic_enabled": False})
    async with MemoryEngine.open(config, embedding_provider=Provider()) as memory:
        assert memory._vector_index._exact_search is exact
        response = await memory.retrieve("query", user_id="owner")
        receipt = await memory.get_retrieval_receipt(str(response.metadata.request_id), user_id="owner")
        assert receipt.execution.features["vector_search"]["exact"] is exact
