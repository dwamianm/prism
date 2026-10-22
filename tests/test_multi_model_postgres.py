"""PostgreSQL: a store made with a fixed ``vector(384)`` column moves to model-keyed vectors (dwamianm/prism#368)."""

from __future__ import annotations

import asyncio
import json
import os

import asyncpg
import pytest

from prme import MemoryEngine
from prme.storage.multi_model import ModelIdentity
from tests.multi_model_support import BagProvider, cosine, memory_text
from tests import multi_model_support

store = multi_model_support.store

pytestmark = pytest.mark.skipif(not os.environ.get("PRME_TEST_DATABASE_URL"), reason="PRME_TEST_DATABASE_URL not set")

USER = "pg-mme-user"


def small384() -> BagProvider:
    return BagProvider("test/small-384", 384, seed=1, version="bag-384")


def base768() -> BagProvider:
    return BagProvider("test/base-768", 768, seed=2, version="bag-768")


@pytest.fixture
def pg(store):
    if store.backend != "postgres":
        pytest.skip("PostgreSQL only")
    return store


async def fill(engine, count: int, start: int = 0) -> None:
    for number in range(start, start + count):
        await engine.store(memory_text(number), user_id=USER)


async def query(url: str, sql: str, *args):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def scores(engine, provider, text="jazz hiking weekend", k=8):
    return [round(hit["score"], 4) for hit in await engine._vector_index.search(text, USER, k=k)]


async def test_an_existing_vector_384_store_migrates_without_re_embedding(pg):
    first = small384()
    async with MemoryEngine.open(pg.config(), embedding_provider=first) as engine:
        await fill(engine, 25)
        before = await scores(engine, first)
    column = await query(pg.url, "SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute "
                                 "WHERE attrelid = 'nodes'::regclass AND attname = 'embedding'")
    assert column[0]["t"].endswith("vector(384)")

    reopened = small384()
    async with MemoryEngine.open(pg.multi(), embedding_provider=reopened) as engine:
        assert reopened.embedded == []  # nothing was embedded to adopt the vectors
        assert (await query(pg.url, "SELECT count(*) AS n FROM node_embeddings"))[0]["n"] == 25
        marker = json.loads((await query(pg.url, "SELECT value::text AS v FROM embedding_store_state "
                                                 "WHERE key = 'legacy_adoption'"))[0]["v"])
        assert (marker["adopted"], marker["unknown_provenance"]) == (25, 0)
        # The adopted vectors are the old ones, byte for byte, and the column is as it was.
        same = await query(
            pg.url,
            "SELECT count(*) AS n FROM node_embeddings ne JOIN nodes n ON n.id = ne.node_id "
            "WHERE ne.embedding::text = n.embedding::text AND ne.model_name = n.embedding_model "
            "AND ne.model_version = n.embedding_version AND ne.dimension = 384",
        )
        assert same[0]["n"] == 25
        assert (await query(pg.url, "SELECT count(*) AS n FROM nodes WHERE embedding IS NOT NULL"))[0]["n"] == 25
        status = await engine.embedding_status()
        assert [(m["model"], m["vectors"], m["complete"]) for m in status["models"]] == [("test/small-384", 25, True)]
        assert await scores(engine, reopened) == before
        assert (await engine.retrieve("jazz hiking weekend", user_id=USER)).metadata.embedding_incomplete is None
        await engine.store(memory_text(99), user_id=USER)
    # Idempotent: a third open adopts nothing more.
    async with MemoryEngine.open(pg.multi(), embedding_provider=small384()):
        assert (await query(pg.url, "SELECT count(*) AS n FROM node_embeddings"))[0]["n"] == 26
    # With the option off again, the engine reads the column it always read, as it was when the option went on.
    async with MemoryEngine.open(pg.config(), embedding_provider=small384()) as engine:
        assert len(await engine._vector_index.search("jazz hiking weekend", USER, k=50)) == 25


async def test_vectors_without_a_recorded_model_are_left_for_reembed(pg):
    first = small384()
    async with MemoryEngine.open(pg.config(), embedding_provider=first) as engine:
        await fill(engine, 12)
    # Three vectors with no recorded model: their provenance is unknown and is never guessed.
    await query(pg.url, "UPDATE nodes SET embedding_model = NULL, embedding_version = NULL WHERE id IN "
                        "(SELECT id FROM nodes ORDER BY id LIMIT 3)")
    reopened = small384()
    async with MemoryEngine.open(pg.multi(), embedding_provider=reopened) as engine:
        marker = json.loads((await query(pg.url, "SELECT value::text AS v FROM embedding_store_state "
                                                 "WHERE key = 'legacy_adoption'"))[0]["v"])
        assert (marker["adopted"], marker["unknown_provenance"]) == (9, 3)
        response = await engine.retrieve("jazz hiking weekend", user_id=USER)
        assert response.metadata.embedding_incomplete["nodes_without_vector"] == 3
        assert (await engine.reembed()).remaining == 0
        assert len(await engine._vector_index.search("jazz weekend", USER, k=50)) == 12
        assert len([text for text in reopened.embedded if text.startswith("Note")]) == 3  # only the three


async def test_an_interrupted_adoption_resumes(pg):
    first = small384()
    async with MemoryEngine.open(pg.config(), embedding_provider=first) as engine:
        await fill(engine, 30)
    async with MemoryEngine.open(pg.multi(), embedding_provider=small384()):
        pass
    # A crash left half of the rows and no marker.
    await query(pg.url, "DELETE FROM node_embeddings WHERE node_id IN (SELECT node_id FROM node_embeddings ORDER BY node_id LIMIT 14)")
    await query(pg.url, "DELETE FROM embedding_store_state WHERE key = 'legacy_adoption'")
    async with MemoryEngine.open(pg.multi(), embedding_provider=small384()):
        assert (await query(pg.url, "SELECT count(*) AS n FROM node_embeddings"))[0]["n"] == 30


async def test_two_engines_opening_a_legacy_store_together_adopt_it_once(pg):
    async with MemoryEngine.open(pg.config(), embedding_provider=small384()) as engine:
        await fill(engine, 40)

    async def open_and_read():
        async with MemoryEngine.open(pg.multi(), embedding_provider=small384()) as engine:
            return len(await engine._vector_index.search("jazz weekend", USER, k=100))

    assert await asyncio.gather(open_and_read(), open_and_read(), open_and_read()) == [40, 40, 40]
    assert (await query(pg.url, "SELECT count(*) AS n FROM node_embeddings"))[0]["n"] == 40


async def test_a_384_store_moves_to_a_768_model_and_keeps_its_column(pg):
    first, second = small384(), base768()
    async with MemoryEngine.open(pg.config(), embedding_provider=first) as engine:
        await fill(engine, 20)
    async with MemoryEngine.open(pg.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        assert (await engine.reembed(second)).remaining == 0
    async with MemoryEngine.open(pg.multi(), embedding_provider=second) as engine:
        hits = await engine._vector_index.search("jazz hiking weekend", USER, k=5)
        nodes = {str(n.id): n.content for n in await engine.query_nodes(user_id=USER, limit=100)}
        expected = sorted((cosine(second.vector("jazz hiking weekend"), second.vector(t)) for t in nodes.values()),
                          reverse=True)[:5]
        assert [h["score"] for h in hits] == pytest.approx(expected, abs=1e-4)
        await engine.store(memory_text(77), user_id=USER)
        assert (await engine.retrieve("jazz", user_id=USER)).metadata.embedding_incomplete is None
    # The column is still vector(384) and holds what the first model made; a store with the option off and
    # the first model still reads it.
    column = await query(pg.url, "SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute "
                                 "WHERE attrelid = 'nodes'::regclass AND attname = 'embedding'")
    assert column[0]["t"].endswith("vector(384)")
    assert (await query(pg.url, "SELECT count(*) AS n FROM nodes WHERE embedding IS NOT NULL"))[0]["n"] == 20


async def test_each_model_has_its_own_partial_hnsw_index_and_approximate_search_uses_it(pg):
    first, second = small384(), base768()
    config = pg.multi(vector_exact_search=False)
    async with MemoryEngine.open(config, embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 30)
        definitions = {row["indexname"]: row["indexdef"] for row in await query(
            pg.url, "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'node_embeddings' "
            "AND schemaname = current_schema()")}
        hnsw = [d for d in definitions.values() if "hnsw" in d]
        assert len(hnsw) == 2
        assert any("vector(384)" in d and "test/small-384" in d for d in hnsw)
        assert any("vector(768)" in d and "test/base-768" in d for d in hnsw)
        exact = await engine._vector_index.search_by_vector(first.vector("jazz hiking"), USER, k=3)
        assert exact and exact[0]["score"] == pytest.approx(
            max(cosine(first.vector("jazz hiking"), first.vector(memory_text(n))) for n in range(30)), abs=1e-4)
        # The planner uses the model's partial index for the statement the approximate search sends.
        conn = await asyncpg.connect(pg.url)
        try:
            await conn.execute("SET enable_seqscan = off")
            plan = "\n".join(r[0] for r in await conn.fetch(
                "EXPLAIN SELECT ne.node_id FROM node_embeddings ne "
                "WHERE ne.model_name = 'test/small-384' AND ne.model_version = 'bag-384' "
                "ORDER BY ne.embedding::public.vector(384) OPERATOR(public.<=>) $1::public.vector(384) LIMIT 5",
                "[" + ",".join(str(v) for v in first.vector("jazz")) + "]"))
        finally:
            await conn.close()
        assert "idx_node_embeddings_hnsw_" in plan


async def test_an_index_left_invalid_by_a_failed_build_is_rebuilt(pg):
    first = small384()
    async with MemoryEngine.open(pg.multi(), embedding_provider=first) as engine:
        await fill(engine, 5)
    name = (await query(pg.url, "SELECT indexname FROM pg_indexes WHERE tablename = 'node_embeddings' "
                                "AND schemaname = current_schema() AND indexdef LIKE '%hnsw%'"))[0]["indexname"]
    await query(pg.url, "UPDATE pg_index SET indisvalid = false WHERE indexrelid = $1::regclass", name)
    async with MemoryEngine.open(pg.multi(), embedding_provider=first):
        pass
    valid = await query(pg.url, "SELECT indisvalid FROM pg_index WHERE indexrelid = $1::regclass", name)
    assert valid[0]["indisvalid"] is True


async def test_dropping_a_model_drops_its_index(pg):
    first, second = small384(), base768()
    async with MemoryEngine.open(pg.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 5)
        assert await engine.drop_embeddings("test/base-768") == 5
    indexes = await query(pg.url, "SELECT indexdef FROM pg_indexes WHERE tablename = 'node_embeddings' "
                           "AND schemaname = current_schema() AND indexdef LIKE '%hnsw%'")
    assert len(indexes) == 1 and "test/small-384" in indexes[0]["indexdef"]


async def test_rebuild_is_refused_on_postgresql_with_a_way_forward(pg):
    async with MemoryEngine.open(pg.config(), embedding_provider=small384()) as engine:
        with pytest.raises(RuntimeError, match="reembed"):
            await engine.rebuild_indexes()
    assert ModelIdentity("a", "b", 1).slug()


async def test_the_option_off_on_a_model_keyed_store_warns(pg, caplog):
    async with MemoryEngine.open(pg.multi(), embedding_provider=small384()) as engine:
        await fill(engine, 3)
    with caplog.at_level("WARNING"):
        async with MemoryEngine.open(pg.config(), embedding_provider=small384()):
            pass
    assert "enable_multi_model_embeddings is off" in caplog.text
