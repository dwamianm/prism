"""DuckDB: one native index file per model, adopted from the pack's file (dwamianm/prism#368)."""

from __future__ import annotations

import pytest

from prme import MemoryEngine
from prme.storage.multi_model import ModelIdentity
from tests.multi_model_support import BagProvider, cosine, large, memory_text, small
from tests import multi_model_support

store = multi_model_support.store

USER = "duck-mme-user"


@pytest.fixture
def duck(store):
    if store.backend != "duckdb":
        pytest.skip("DuckDB only")
    return store


async def fill(engine, count: int, start: int = 0) -> None:
    for number in range(start, start + count):
        await engine.store(memory_text(number), user_id=USER)


def vector_files(duck) -> list[str]:
    return sorted(path.name for path in duck.tmp_path.iterdir() if path.name.startswith("vectors"))


def registry(engine) -> dict[tuple[str, str], str]:
    return {(row[0], row[1]): row[2] for row in engine._conn.execute(
        "SELECT embedding_model, embedding_version, index_file FROM embedding_models").fetchall()}


async def test_the_packs_file_becomes_the_first_models_index(duck):
    first, second = small(), large()
    async with MemoryEngine.open(duck.config(), embedding_provider=first) as engine:
        await fill(engine, 12)
    assert vector_files(duck) == ["vectors.usearch"]
    size = (duck.tmp_path / "vectors.usearch").stat().st_size
    reopened = small()
    async with MemoryEngine.open(duck.multi(), embedding_provider=reopened) as engine:
        assert registry(engine) == {("test/small", "bag-1"): "vectors.usearch"}
        assert reopened.embedded == []
        assert len(engine._vector_index.active._index) == 12
    assert (duck.tmp_path / "vectors.usearch").stat().st_size == size
    # A second model gets a file named after it, and the first keeps the pack's.
    async with MemoryEngine.open(duck.multi(), embedding_provider=small(), additional_embedding_providers=[second]) as engine:
        await engine.reembed(second)
        files = registry(engine)
        assert files[("test/small", "bag-1")] == "vectors.usearch"
        assert files[("test/large", "bag-1")].startswith("vectors.test-large.") and files[("test/large", "bag-1")].endswith(".usearch")
    assert len(vector_files(duck)) == 2


async def test_a_switched_store_opens_the_new_models_own_file_without_embedding(duck):
    first, second = small(), large()
    async with MemoryEngine.open(duck.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 14)
    reopened = large()
    async with MemoryEngine.open(duck.multi(), embedding_provider=reopened) as engine:
        assert reopened.embedded == []
        assert len(engine._vector_index.active._index) == 14
        hits = await engine._vector_index.search("jazz hiking weekend", USER, k=3)
        expected = sorted((cosine(second.vector("jazz hiking weekend"), second.vector(memory_text(n))) for n in range(14)),
                          reverse=True)[:3]
        assert [h["score"] for h in hits] == pytest.approx(expected, abs=1e-4)
        # The old model is still on disk and in the pack, and its keys do not collide with the new one's.
        keys = engine._conn.execute("SELECT count(*), count(DISTINCT vector_key) FROM vector_metadata").fetchone()
        assert keys == (28, 28)
        await engine.store(memory_text(50), user_id=USER)
        keys = engine._conn.execute("SELECT count(*), count(DISTINCT vector_key) FROM vector_metadata").fetchone()
        assert keys == (29, 29)  # only the active model is written, with a key no other row has
    async with MemoryEngine.open(duck.multi(), embedding_provider=small()) as engine:
        assert len(engine._vector_index.active._index) == 14  # the first model's file is as it was


async def test_a_pack_that_mixed_two_models_is_split_into_their_indexes(duck):
    """Without the option a second model of the same dimension writes beside the first with no warning."""
    first, twin = small(), BagProvider("test/twin", 16, seed=7)
    async with MemoryEngine.open(duck.config(), embedding_provider=first) as engine:
        await fill(engine, 10)
    async with MemoryEngine.open(duck.config(), embedding_provider=twin) as engine:
        await fill(engine, 6, start=100)
    async with MemoryEngine.open(duck.multi(), embedding_provider=small(), additional_embedding_providers=[twin]) as engine:
        status = {m["model"]: m["vectors"] for m in (await engine.embedding_status())["models"]}
        assert status == {"test/small": 10, "test/twin": 6}
        assert len(engine._vector_index.active._index) == 10
        assert len(engine._vector_index.index_for(ModelIdentity.of(twin))._index) == 6
        hits = await engine._vector_index.search("jazz weekend", USER, k=50)
        assert len(hits) == 10  # the twin's nodes are not the active model's


async def test_dropping_a_model_removes_its_file_rows_and_registration(duck):
    first, second = small(), large()
    async with MemoryEngine.open(duck.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 6)
    assert len(vector_files(duck)) == 2
    async with MemoryEngine.open(duck.multi(), embedding_provider=first) as engine:
        # The model is not open here; dropping works on the pack's rows and its file.
        assert await engine.drop_embeddings("test/large") == 6
        assert ("test/large", "bag-1") not in registry(engine)
    assert vector_files(duck) == ["vectors.usearch"]
    async with MemoryEngine.open(duck.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        assert (await engine.reembed(second)).processed == 6  # and a dropped model can be built again


async def test_a_retired_memory_leaves_a_models_file_on_its_next_open(duck):
    first, second = small(), large()
    async with MemoryEngine.open(duck.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 5)
    async with MemoryEngine.open(duck.multi(), embedding_provider=first) as engine:
        node = (await engine.query_nodes(user_id=USER, limit=10))[0]
        await engine.archive(str(node.id), user_id=USER)  # the large model's file is not open to remove it
    async with MemoryEngine.open(duck.multi(), embedding_provider=large()) as engine:
        assert len(engine._vector_index.active._index) == 4
        assert str(node.id) not in [h["node_id"] for h in await engine._vector_index.search("weekend", USER, k=10)]


async def test_rebuild_indexes_rebuilds_every_open_model(duck):
    first, second = small(), large()
    async with MemoryEngine.open(duck.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 8)
        first.embedded.clear()
        second.embedded.clear()
        summary = await engine.rebuild_indexes()
        assert summary["nodes_indexed"] == 8
        assert len(first.embedded) == 8 and len(second.embedded) == 8
        status = {m["model"]: (m["vectors"], m["complete"]) for m in (await engine.embedding_status())["models"]}
        assert status == {"test/small": (8, True), "test/large": (8, True)}


async def test_approximate_search_works_per_model(duck):
    first, second = small(), large()
    async with MemoryEngine.open(duck.multi(vector_exact_search=False), embedding_provider=first,
                                 additional_embedding_providers=[second]) as engine:
        await fill(engine, 20)
        hits = await engine._vector_index.search("jazz hiking weekend", USER, k=3)
        assert len(hits) == 3
    async with MemoryEngine.open(duck.multi(vector_exact_search=False), embedding_provider=second) as engine:
        assert len(await engine._vector_index.search("jazz hiking weekend", USER, k=3)) == 3


async def test_a_pack_of_several_models_does_not_open_without_the_option(duck):
    first, second = small(), large()
    async with MemoryEngine.open(duck.multi(), embedding_provider=first, additional_embedding_providers=[second]) as engine:
        await fill(engine, 4)
    with pytest.raises(ValueError, match="several embedding models.*enable_multi_model_embeddings"):
        await MemoryEngine.create(duck.config(), embedding_provider=first)
    # Dropping the other model leaves a pack the original index class opens as it always did.
    async with MemoryEngine.open(duck.multi(), embedding_provider=first) as engine:
        await engine.drop_embeddings("test/large")
    async with MemoryEngine.open(duck.config(), embedding_provider=small()) as engine:
        assert len(await engine._vector_index.search("jazz weekend", USER, k=10)) == 4
