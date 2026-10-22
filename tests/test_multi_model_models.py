"""The registered local models end to end: bge-small to bge-base on 100 memories (dwamianm/prism#368, criterion 1).

These use the real fastembed models, which the other tests in this suite also load (bge-small) or download once
(bge-base). No model service is called.
"""

from __future__ import annotations

import pytest

from prme import MemoryEngine
from prme.config import PRMEConfig
from prme.storage.embedding import FastEmbedProvider
from tests.multi_model_support import cosine, memory_text
from tests import multi_model_support

store = multi_model_support.store

SMALL = "BAAI/bge-small-en-v1.5"
BASE = "BAAI/bge-base-en-v1.5"
USER = "real-models-user"
QUERIES = ["who likes sailing", "weekend hobbies with coffee", "playing the piano and chess"]


@pytest.fixture(scope="module")
def providers():
    try:
        small, base = FastEmbedProvider(SMALL), FastEmbedProvider(BASE)
        import asyncio

        asyncio.run(small.embed(["probe"]))
        asyncio.run(base.embed(["probe"]))
    except Exception as exc:  # pragma: no cover - only without the model files and without a network
        pytest.skip(f"the registered local models are not available: {exc}")
    return small, base


def configured(store, **embedding) -> PRMEConfig:
    return store.multi(embedding=embedding)


async def test_a_hundred_memories_move_from_bge_small_to_bge_base(store, providers):
    small, base = providers
    async with MemoryEngine.open(configured(store)) as engine:
        for number in range(100):
            await engine.store(memory_text(number), user_id=USER)
        assert (await engine.embedding_status())["models"][0]["vectors"] == 100

    # bge-base becomes an additional model: written from now on, not yet built for the 100.
    async with MemoryEngine.open(configured(store, additional_models=[BASE])) as engine:
        status = {m["model"]: (m["vectors"], m["complete"]) for m in (await engine.embedding_status())["models"]}
        assert status == {SMALL: (100, True), BASE: (0, False)}
        stopped = await engine.reembed(BASE, limit=40, batch_size=16)
        assert (stopped.done, stopped.remaining) == (40, 60)
        for number in range(100, 105):  # written while the job is stopped
            await engine.store(memory_text(number), user_id=USER)
        resumed = await engine.reembed(BASE, cursor=stopped.cursor, batch_size=16)
        assert (resumed.total, resumed.done, resumed.remaining, resumed.failed) == (105, 105, 0, 0)

    # The switch: bge-base is the active model, and the same retrieval API answers under it.
    async with MemoryEngine.open(configured(store, model_name=BASE)) as engine:
        assert (await engine.embedding_status())["active"].startswith(BASE)
        texts = {str(n.id): n.content for n in await engine.query_nodes(user_id=USER, limit=1000)}
        assert len(texts) == 105
        vectors = dict(zip(texts, await base.embed(list(texts.values()))))
        for query in QUERIES:
            hits = await engine._vector_index.search(query, USER, k=10)
            query_vector = (await base.embed([query]))[0]
            expected = sorted(cosine(query_vector, vector) for vector in vectors.values())[::-1][:10]
            assert [hit["score"] for hit in hits] == pytest.approx(expected, abs=2e-3)
            under_small = (await small.embed([query]))[0]
            assert under_small and len(under_small) == 384
        response = await engine.retrieve(QUERIES[0], user_id=USER)
        assert response.metadata.embedding_mismatch is False and response.metadata.embedding_incomplete is None
        assert len(response.results) > 0
        await engine.store(memory_text(500), user_id=USER)
        # The old index stays until it is dropped.
        status = {m["model"]: m["vectors"] for m in (await engine.embedding_status())["models"]}
        assert status == {BASE: 106, SMALL: 105}
        assert await engine.drop_embeddings(SMALL) == 105
