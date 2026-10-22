"""A store that can switch embedding models (``enable_multi_model_embeddings``, dwamianm/prism#368).

Every test runs on DuckDB and, with ``PRME_TEST_DATABASE_URL`` set, on PostgreSQL (each in its own schema). The
providers are deterministic bag-of-words hashers, so no model is downloaded.
"""

from __future__ import annotations

import asyncio

import pytest

from prme import MemoryEngine
from prme.config import PRMEConfig
from prme.storage.embedding import EmbeddingVersionMismatchError
from prme.storage.multi_model import EmbeddingModelMismatchError, ModelIdentity, MultiModelVectorIndex
from prme.storage.reembed import ReembedBusyError, ReembedDisabledError
from tests.multi_model_support import (
    BagProvider, TOPICS, cosine, large, memory_text, rows_under, small,
)
from tests import multi_model_support

store = multi_model_support.store

USER = "mme-user"


async def fill(engine, count: int, start: int = 0) -> None:
    for number in range(start, start + count):
        await engine.store(memory_text(number), user_id=USER)


async def contents(engine) -> dict[str, str]:
    return {str(node.id): node.content for node in await engine.query_nodes(user_id=USER, limit=1000)}


async def ranked(engine, provider: BagProvider, query: str, k: int = 8) -> list[float]:
    """The scores of what the engine's vector search returns, checked against the model's own cosine order.

    Memories can tie under a hashing model, so the check is on scores: each hit's score is its cosine under
    ``provider``, and the hits are the best ``k`` of them. Returns the scores for comparing two stores.
    """
    texts = await contents(engine)
    hits = await engine._vector_index.search(query, USER, k=k)
    query_vector = provider.vector(query)
    scores = {node_id: cosine(query_vector, provider.vector(text)) for node_id, text in texts.items()}
    best = sorted(scores.values(), reverse=True)[:k]
    assert [hit["score"] for hit in hits] == pytest.approx(best, abs=1e-4)
    for hit in hits:
        assert hit["score"] == pytest.approx(scores[hit["node_id"]], abs=1e-4)
    return [round(hit["score"], 4) for hit in hits]


async def model_counts(engine) -> dict[str, tuple[int, bool | None]]:
    status = await engine.embedding_status()
    return {item["model"]: (item["vectors"], item["complete"]) for item in status["models"]}


async def test_store_switches_models_without_losing_a_write(store):
    """Criterion 1: store, add a model, re-embed (stopped and resumed), switch, rank under the new model."""
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        await fill(engine, 30)
        assert (await model_counts(engine))["test/small"] == (30, True)
    # The second model is added to the configuration: it is written from now on, and nothing of it exists yet.
    async with MemoryEngine.open(store.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        assert (await model_counts(engine))["test/large"] == (0, False)
        before = await engine.reembed("test/large", dry_run=True)
        assert (before.total, before.done, before.remaining, before.selected, before.processed) == (30, 0, 30, 30, 0)

        # A run that stops after 12, a write while it is stopped, and a run that resumes.
        stopped = await engine.reembed("test/large", limit=12, batch_size=5)
        assert (stopped.total, stopped.done, stopped.remaining, stopped.processed) == (30, 12, 18, 12)
        assert stopped.cursor is not None and stopped.failed == 0
        await fill(engine, 3, start=100)
        resumed = await engine.reembed("test/large", cursor=stopped.cursor, batch_size=5)
        assert (resumed.total, resumed.remaining, resumed.cursor) == (33, 0, None)
        assert resumed.done == 33
        assert (await model_counts(engine))["test/large"] == (33, True)
        assert (await engine.reembed("test/large")).processed == 0  # nothing left, and nothing is embedded twice
        await fill(engine, 2, start=200)  # written after the job: the additional model takes it as well
        assert (await model_counts(engine))["test/large"] == (35, True)
        assert await ranked(engine, first, "jazz hiking weekend") != []

    # The switch: the configuration names the new model, and searches read its index.
    async with MemoryEngine.open(store.multi(), embedding_provider=second) as engine:
        assert isinstance(engine._vector_index, MultiModelVectorIndex)
        under_second = await ranked(engine, second, "jazz hiking weekend")
        response = await engine.retrieve("jazz hiking weekend", user_id=USER)
        assert response.metadata.embedding_mismatch is False
        assert response.metadata.embedding_incomplete is None
        assert len(response.results) > 0
        # The old index stays until it is dropped, and the store keeps writing under the new model.
        assert (await model_counts(engine))["test/small"][0] == 35
        await engine.store(memory_text(300), user_id=USER)
        assert (await model_counts(engine))["test/large"] == (36, True)
        assert under_second
        assert await engine.drop_embeddings("test/small") == 35
        with pytest.raises(ValueError, match="active"):
            await engine.drop_embeddings("test/large")
        assert "test/small" not in await model_counts(engine)
    assert await rows_under(store, "test/small") == 0
    assert await rows_under(store, "test/large") == 36


async def test_writes_that_arrive_while_the_job_runs_are_not_lost(store):
    """New memories stored during a re-embed end up with a vector, ahead of the cursor or behind it."""
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        await fill(engine, 12)
    async with MemoryEngine.open(store.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        index = engine._vector_index
        attached = index.index_for(ModelIdentity.of(second))
        injected = {"done": False}

        async def write_behind_the_job(texts):
            if injected["done"]:
                return
            injected["done"] = True
            # Take the model out of the dual-write set, so these writes need the job's own catch-up. Twenty
            # random ids put some of them behind the cursor.
            index._extras.remove(attached)
            await fill(engine, 20, start=500)
            index._extras.append(attached)

        second.on_embed = write_behind_the_job
        result = await engine.reembed("test/large", batch_size=4)
        assert injected["done"]
        assert result.remaining == 0 and result.total == 32 and result.done == 32
        assert (await model_counts(engine))["test/large"] == (32, True)


async def test_a_model_in_the_dual_write_set_never_falls_behind(store):
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 5)
        assert (await model_counts(engine))["test/large"] == (5, True)
        # A failing additional model does not fail the write; the job fills the gap.
        second.fail_on = {memory_text(99)}
        await engine.store(memory_text(99), user_id=USER)
        await engine.store(memory_text(100), user_id=USER)
        assert (await model_counts(engine))["test/small"] == (7, True)
        assert (await model_counts(engine))["test/large"] == (6, False)
        second.fail_on = set()
        assert (await engine.reembed("test/large")).remaining == 0
        assert (await model_counts(engine))["test/large"] == (7, True)


async def test_a_failed_text_is_counted_and_the_rest_is_stored(store):
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        await fill(engine, 8)
    async with MemoryEngine.open(store.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        second.fail_on = {memory_text(3)}
        result = await engine.reembed("test/large", batch_size=4)
        # fail_on makes embed() raise for the batch that holds the text, and the job retries its texts one by one.
        assert (result.total, result.processed, result.failed, result.remaining) == (8, 7, 1, 1)
        second.fail_on = set()
        again = await engine.reembed("test/large")
        assert (again.processed, again.remaining, again.failed) == (1, 0, 0)


async def test_progress_is_queryable_and_a_cursor_is_validated(store):
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        await fill(engine, 10)
        status = await engine.embedding_status()
        assert status["active"].startswith("test/small")
        assert [(m["model"], m["role"], m["complete"]) for m in status["models"]] == [("test/small", "active", True)]
        partial = await engine.reembed(second, limit=4)
        assert (partial.done, partial.remaining, partial.total) == (4, 6, 10)
        status = await engine.embedding_status()
        by_model = {m["model"]: m for m in status["models"]}
        assert by_model["test/large"]["role"] == "additional"  # attached by the job
        assert (by_model["test/large"]["vectors"], by_model["test/large"]["complete"]) == (4, False)
        assert by_model["test/small"]["complete"] is True
        with pytest.raises(ValueError, match="cursor"):
            await engine.reembed(second, cursor="not-a-cursor")
        with pytest.raises(ValueError):
            await engine.reembed(second, limit=-1)
        with pytest.raises(ValueError):
            await engine.reembed(second, batch_size=0)
        only_user = await engine.reembed(second, user_id="somebody-else", dry_run=True)
        assert (only_user.total, only_user.remaining) == (0, 0)
        scoped = await engine.reembed(second, user_id=USER)
        assert scoped.remaining == 0 and scoped.user_id == USER


async def test_a_second_run_of_the_same_model_is_refused_while_one_runs(store):
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        await fill(engine, 4)
        second.gate = asyncio.Event()
        running = asyncio.create_task(engine.reembed(second))
        await asyncio.sleep(0.2)
        with pytest.raises(ReembedBusyError):
            await engine.reembed(second)
        second.gate.set()
        assert (await running).remaining == 0


async def test_a_store_of_another_model_is_reported_and_refuses_writes(store):
    """Criterion 3: the error names the stored and the configured model; retrieval says what is missing."""
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        await fill(engine, 6)
    async with MemoryEngine.open(store.multi(), embedding_provider=second) as engine:
        with pytest.raises(EmbeddingModelMismatchError) as caught:
            await engine.store("one more", user_id=USER)
        message = str(caught.value)
        assert "test/small" in message and "test/large" in message
        assert "16 dimensions" in message and "32 dimensions" in message
        assert isinstance(caught.value, EmbeddingVersionMismatchError)
        with pytest.raises(EmbeddingModelMismatchError):
            await engine.ingest("another", user_id=USER)
        with pytest.raises(EmbeddingModelMismatchError):
            await engine.ingest_fast("quick", user_id=USER)
        # Nothing was written by the refused calls.
        assert len(await contents(engine)) == 6
        response = await engine.retrieve("jazz weekend", user_id=USER)
        assert response.metadata.embedding_mismatch is True
        assert response.metadata.backend_failures["VECTOR"] == "embedding_mismatch"
        incomplete = response.metadata.embedding_incomplete
        assert (incomplete["model"], incomplete["dimension"]) == ("test/large", 32)
        assert (incomplete["nodes_total"], incomplete["nodes_without_vector"]) == (6, 6)
        assert incomplete["stored_models"] == [
            {"model": "test/small", "version": "bag-1", "dimension": 16, "vectors": 6}]
        # Lexical and graph results still come back.
        assert len(response.results) > 0
        # The job is the way out, and it is not refused.
        assert (await engine.reembed()).remaining == 0
        assert (await engine.retrieve("jazz weekend", user_id=USER)).metadata.embedding_mismatch is False
        await engine.store("one more", user_id=USER)


async def test_the_same_dimension_under_another_model_is_not_mixed(store):
    first = small()
    twin = BagProvider("test/twin", 16, seed=9)
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        await fill(engine, 3)
    async with MemoryEngine.open(store.multi(), embedding_provider=twin) as engine:
        with pytest.raises(EmbeddingModelMismatchError, match="test/small.*test/twin"):
            await engine.store("one more", user_id=USER)
    # A new version of the same model is another model too.
    newer = BagProvider("test/small", 16, seed=1, version="bag-2")
    async with MemoryEngine.open(store.multi(), embedding_provider=newer) as engine:
        with pytest.raises(EmbeddingModelMismatchError, match="bag-1.*bag-2"):
            await engine.store("one more", user_id=USER)


async def test_a_partly_built_active_model_reports_what_it_lacks(store):
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        await fill(engine, 10)
        await engine.reembed(second, limit=6)
    async with MemoryEngine.open(store.multi(), embedding_provider=second) as engine:
        # It has vectors, so writes are allowed and searches read what exists, and the response says it is short.
        await engine.store(memory_text(77), user_id=USER)
        response = await engine.retrieve("jazz hiking weekend", user_id=USER)
        assert response.metadata.embedding_mismatch is False
        incomplete = response.metadata.embedding_incomplete
        assert (incomplete["nodes_total"], incomplete["nodes_without_vector"]) == (11, 4)
        hits = await engine._vector_index.search("jazz hiking weekend", USER, k=50)
        assert len(hits) == 7
        assert (await engine.reembed()).remaining == 0
        assert (await engine.retrieve("jazz", user_id=USER)).metadata.embedding_incomplete is None


async def test_retiring_a_memory_removes_it_from_every_model(store):
    first, second = small(), large()
    async with MemoryEngine.open(store.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 4)
        node = (await engine.query_nodes(user_id=USER, limit=10))[0]
        await engine.archive(str(node.id), user_id=USER)
        counts = await model_counts(engine)
        assert counts["test/small"][0] == 3 and counts["test/large"][0] == 3
        # And a model that is stored but not open here.
    async with MemoryEngine.open(store.multi(), embedding_provider=first) as engine:
        node = (await engine.query_nodes(user_id=USER, limit=10))[0]
        await engine.archive(str(node.id), user_id=USER)
        assert (await model_counts(engine))["test/small"][0] == 2
    assert await rows_under(store, "test/large") == 2


async def test_embedding_calls_need_the_option(store):
    async with MemoryEngine.open(store.config(), embedding_provider=small()) as engine:
        await fill(engine, 2)
        assert not isinstance(engine._vector_index, MultiModelVectorIndex)
        with pytest.raises(ReembedDisabledError):
            await engine.reembed("test/large")
        with pytest.raises(ReembedDisabledError):
            await engine.embedding_status()
        with pytest.raises(ReembedDisabledError):
            await engine.drop_embeddings("test/large")
        response = await engine.retrieve("jazz", user_id=USER)
        assert response.metadata.embedding_incomplete is None
        assert "embedding_incomplete" not in response.metadata.model_dump(mode="json")


async def test_the_option_off_leaves_the_storage_as_it_was(store):
    async with MemoryEngine.open(store.config(), embedding_provider=small()) as engine:
        await fill(engine, 3)
    if store.backend == "duckdb":
        import duckdb

        conn = duckdb.connect(str(store.tmp_path / "memory.duckdb"), read_only=True)
        try:
            tables = {row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        finally:
            conn.close()
        assert "embedding_models" not in tables
        assert sorted(path.name for path in store.tmp_path.iterdir() if path.name.startswith("vectors")) == ["vectors.usearch"]
    else:
        import asyncpg

        conn = await asyncpg.connect(store.url)
        try:
            assert await conn.fetchval("SELECT to_regclass('node_embeddings')") is None
            assert await conn.fetchval("SELECT to_regclass('embedding_store_state')") is None
            assert await conn.fetchval("SELECT count(*) FROM nodes WHERE embedding IS NOT NULL") == 3
        finally:
            await conn.close()


async def test_a_store_made_without_the_option_is_adopted_with_no_re_embedding(store):
    """Criterion 2 (DuckDB side here, PostgreSQL's column in test_multi_model_postgres.py)."""
    first = small()
    async with MemoryEngine.open(store.config(), embedding_provider=first) as engine:
        await fill(engine, 15)
        expected = await ranked_plain(engine, first)
    reopened = small()
    async with MemoryEngine.open(store.multi(), embedding_provider=reopened) as engine:
        assert reopened.embedded == []  # nothing was embedded to adopt the vectors
        assert (await model_counts(engine))["test/small"] == (15, True)
        assert await ranked(engine, reopened, "jazz hiking weekend") == expected
        await engine.store(memory_text(40), user_id=USER)
        assert (await model_counts(engine))["test/small"] == (16, True)
    # Idempotent: a third open adopts nothing more and still serves the same store.
    again = small()
    async with MemoryEngine.open(store.multi(), embedding_provider=again) as engine:
        assert again.embedded == []
        assert (await model_counts(engine))["test/small"] == (16, True)


async def ranked_plain(engine, provider: BagProvider) -> list[float]:
    return await ranked(engine, provider, "jazz hiking weekend")


async def test_additional_models_come_from_the_configuration(store):
    """The configured names open as local models; the list needs the option and cannot name the active model."""
    with pytest.raises(ValueError, match="enable_multi_model_embeddings"):
        PRMEConfig(embedding={"additional_models": ["BAAI/bge-base-en-v1.5"]})
    with pytest.raises(ValueError, match="active"):
        PRMEConfig(enable_multi_model_embeddings=True, embedding={"additional_models": ["BAAI/bge-small-en-v1.5"]})
    with pytest.raises(ValueError, match="single-file"):
        PRMEConfig(enable_multi_model_embeddings=True, pack_path="x.prme")
    config = PRMEConfig(enable_multi_model_embeddings=True,
                        embedding={"additional_models": "BAAI/bge-base-en-v1.5, BAAI/bge-base-en-v1.5"})
    assert config.embedding.additional_models == ["BAAI/bge-base-en-v1.5"]
    assert PRMEConfig(enable_multi_model_embeddings=True,
                      embedding={"additional_models": '["BAAI/bge-base-en-v1.5"]'}).embedding.additional_models == [
                          "BAAI/bge-base-en-v1.5"]


def test_the_default_configuration_dump_is_unchanged():
    dump = PRMEConfig().model_dump(mode="json")
    assert "enable_multi_model_embeddings" not in dump
    assert "additional_models" not in dump["embedding"]
    on = PRMEConfig(enable_multi_model_embeddings=True).model_dump(mode="json")
    assert on["enable_multi_model_embeddings"] is True
    assert PRMEConfig(enable_multi_model_embeddings=False).model_dump_json() == PRMEConfig().model_dump_json()
    assert TOPICS  # the topics the other tests rank by
