"""The one graph walk from all seeds on PostgreSQL (``tests/test_seed_walk.py`` runs it on DuckDB).

Skipped unless ``PRME_TEST_DATABASE_URL`` points at a live database; CI runs it.
"""

import os

import pytest

from tests.test_seed_walk import check_candidates, check_equal_candidates, check_links, check_summaries, check_walk

pytestmark = pytest.mark.skipif(
    not os.environ.get("PRME_TEST_DATABASE_URL"), reason="PRME_TEST_DATABASE_URL not set",
)


@pytest.fixture
async def pg_store():
    import asyncpg

    from prme.storage.pg.graph_store import PgGraphStore
    from prme.storage.pg.schema import initialize_pg_database

    pool = await asyncpg.create_pool(os.environ["PRME_TEST_DATABASE_URL"], min_size=1, max_size=3)
    await initialize_pg_database(pool)
    yield PgGraphStore(pool)
    await pool.close()


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
async def test_one_walk_reaches_the_nodes_of_the_per_seed_walks_at_the_same_hops(pg_store, seed):
    # Edges reference nodes on PostgreSQL, so no edge points at a missing node.
    await check_walk(pg_store, seed, dangling=False)


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_the_walk_links_are_the_edges_a_walk_steps_along(pg_store, seed):
    await check_links(pg_store, seed, dangling=False)


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_the_candidate_stage_gives_the_same_candidates_with_one_walk(pg_store, seed):
    await check_candidates(pg_store, seed)


async def test_equal_candidates_keep_the_id_order(pg_store):
    await check_equal_candidates(pg_store)


async def test_the_query_leaves_out_the_summaries_the_stage_leaves_out(pg_store):
    await check_summaries(pg_store)
