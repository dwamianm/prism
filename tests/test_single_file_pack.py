"""Single-file memory packs (issue #247, phase 1).

A pack opened through ``pack_path``, or through ``MemoryClient`` with a path
ending in ``.prme``, is one DuckDB file holding the event log, the graph, the
vector payloads and snapshot, and the manifest. The lexical index is rebuilt
in memory from the graph on open. These tests check that the file is all
there is at rest, that reopening or copying it preserves retrieval receipts,
that a damaged vector snapshot is restored from the durable payloads, that
the lexical index after open equals a rebuild, and that the configuration
guards hold. Directory packs are covered by their existing tests.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb
import pytest

from prme import MemoryClient, MemoryEngine, PRMEConfig, __version__
from prme.client import config_from_pack
from prme.models.nodes import MemoryNode
from prme.storage.embedding import EmbeddingVersionMismatchError
from prme.storage.lexical_index import LexicalIndex
from prme.storage.namespace_identity import NamespaceIdentityError
from prme.storage.pack_manifest import PACK_FORMAT_VERSION
from prme.types import NodeType, Scope
from tests.test_durable_ingestion import MockEmbeddingProvider

USER = "alice"
REFERENCE_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)
MEMORIES = (
    "Alice drinks green tea every morning",
    "Alice's brother Bob drives a red pickup truck",
    "The team standup moved to 9:30 on Tuesdays",
    "Alice is allergic to peanuts and carries an epinephrine pen",
    "Carol recommended the violin teacher on Elm Street",
    "Alice prefers tea over coffee in the afternoon",
)
QUERIES = ("tea", "who drives a truck", "peanut allergy", "violin teacher")


class OtherModel(MockEmbeddingProvider):
    """Same geometry as the default test model, under another name."""

    model_name = "other-test-model"


class SmallModel(MockEmbeddingProvider):
    """A model with another vector dimension."""

    model_name = "small-test-model"
    dimension = 16


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    monkeypatch.setattr(
        "prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider(),
    )
    # Opportunistic maintenance could change scores between two reads.
    monkeypatch.setenv("PRME_ORGANIZER_OPPORTUNISTIC_ENABLED", "false")


def pack_config(path: Path, **overrides) -> PRMEConfig:
    return PRMEConfig(pack_path=str(path), **overrides)


async def store_all(engine: MemoryEngine, memories=MEMORIES) -> None:
    for text in memories:
        await engine.store(text, user_id=USER)


async def read_back(engine: MemoryEngine) -> dict:
    """Results and receipts for the query suite, without per-request IDs."""
    out = {}
    for query in QUERIES:
        response = await engine.retrieve(query, user_id=USER, reference_time=REFERENCE_TIME)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
        out[query] = (
            [(str(item.node.id), item.composite_score) for item in response.results],
            receipt.model_dump(mode="json", exclude={"request_id"}),
        )
    return out


def client_read_back(client: MemoryClient) -> dict:
    out = {}
    for query in QUERIES:
        response = client.retrieve(query, user_id=USER, reference_time=REFERENCE_TIME)
        receipt = client.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
        out[query] = (
            [(str(item.node.id), item.composite_score) for item in response.results],
            receipt.model_dump(mode="json", exclude={"request_id"}),
        )
    return out


async def lexical_hits(engine: MemoryEngine) -> dict:
    return {query: [(hit["node_id"], hit["score"])
                    for hit in await engine._lexical_index.search(query, USER, limit=50)]
            for query in QUERIES}


async def vector_hits(engine: MemoryEngine) -> dict:
    return {query: [(hit["node_id"], hit["score"])
                    for hit in await engine._vector_index.search(query, USER, k=50)]
            for query in QUERIES}


def snapshot_row(path: Path) -> tuple:
    with duckdb.connect(str(path)) as conn:
        return conn.execute("SELECT ndim, vector_count, data FROM vector_snapshot").fetchone()


def manifest_row(path: Path) -> dict:
    with duckdb.connect(str(path)) as conn:
        cursor = conn.execute("SELECT * EXCLUDE (singleton) FROM prme_manifest")
        rows = cursor.fetchall()
        assert len(rows) == 1
        return dict(zip([column[0] for column in cursor.description], rows[0]))


# --- One file -------------------------------------------------------------


def test_client_pack_is_one_file_at_rest(tmp_path, monkeypatch):
    # Directory-pack defaults are relative paths; none may appear here.
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    pack = tmp_path / "packs" / "memories.prme"

    with MemoryClient(str(pack)) as client:
        assert client._config.pack_path == str(pack)
        for text in MEMORIES:
            client.store(text, user_id=USER)
        # DuckDB keeps its write-ahead log beside the file only while open.
        assert set(os.listdir(pack.parent)) <= {"memories.prme", "memories.prme.wal"}
        before = client_read_back(client)
    assert os.listdir(pack.parent) == ["memories.prme"]
    assert os.listdir(workdir) == []
    assert all(results for results, _receipt in before.values())

    with MemoryClient(str(pack)) as client:
        assert client_read_back(client) == before
    assert os.listdir(pack.parent) == ["memories.prme"]
    assert os.listdir(workdir) == []


async def test_reopen_and_copy_give_identical_receipts(tmp_path):
    original = tmp_path / "original" / "memories.prme"
    async with MemoryEngine.open(pack_config(original)) as engine:
        await store_all(engine)
        before = await read_back(engine)
    assert all(results for results, _receipt in before.values())

    copy = tmp_path / "elsewhere" / "copy.prme"
    copy.parent.mkdir()
    shutil.copyfile(original, copy)
    async with MemoryEngine.open(pack_config(copy)) as engine:
        assert await read_back(engine) == before
    async with MemoryEngine.open(pack_config(original)) as engine:
        assert await read_back(engine) == before


def test_directory_pack_keeps_its_layout(tmp_path):
    directory = tmp_path / "pack"
    with MemoryClient(str(directory)) as client:
        assert client._config.pack_path is None
        client.store(MEMORIES[0], user_id=USER)
    assert {"memory.duckdb", "vectors.usearch", "lexical_index"} <= set(os.listdir(directory))
    with duckdb.connect(str(directory / "memory.duckdb")) as conn:
        tables = {row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert "vector_payloads" in tables
    assert not {"vector_snapshot", "prme_manifest"} & tables


def test_existing_directory_with_pack_suffix_stays_a_directory_pack(tmp_path):
    legacy = tmp_path / "legacy.prme"
    legacy.mkdir()
    with MemoryClient(str(legacy)) as client:
        assert client._config.pack_path is None
        assert client._config.db_path == str(legacy / "memory.duckdb")


def test_config_from_pack_creates_only_the_parent(tmp_path):
    pack = tmp_path / "a" / "b" / "memories.prme"
    config = config_from_pack(str(pack))
    assert config.pack_path == str(pack)
    assert config.single_file_pack
    assert pack.parent.is_dir()
    assert not pack.exists()


# --- Vector snapshot ------------------------------------------------------


@pytest.mark.parametrize("damage", ["missing", "stale", "unreadable", "miscounted"])
async def test_damaged_vector_snapshot_is_restored_from_payloads(tmp_path, caplog, damage):
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine, MEMORIES[:3])
    stale = snapshot_row(pack)
    assert stale[:2] == (MockEmbeddingProvider.dimension, 3)
    with caplog.at_level(logging.INFO, logger="prme.storage.vector_index"):
        async with MemoryEngine.open(pack_config(pack)) as engine:
            # An intact snapshot row is loaded as is, with nothing to restore.
            assert len(engine._vector_index._index) == 3
            await store_all(engine, MEMORIES[3:])
            expected = await vector_hits(engine)
    assert "vector_index.recovered" not in caplog.messages
    assert snapshot_row(pack)[:2] == (MockEmbeddingProvider.dimension, len(MEMORIES))

    with duckdb.connect(str(pack)) as conn:
        if damage == "missing":
            conn.execute("DELETE FROM vector_snapshot")
        elif damage == "stale":
            conn.execute("UPDATE vector_snapshot SET ndim = ?, vector_count = ?, data = ?", list(stale))
        elif damage == "unreadable":
            conn.execute("UPDATE vector_snapshot SET data = ?", [b"not a usearch index"])
        else:
            conn.execute("UPDATE vector_snapshot SET vector_count = vector_count + 1")

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="prme.storage.vector_index"):
        async with MemoryEngine.open(pack_config(pack)) as engine:
            # Startup recovery wrote a complete snapshot row before any read.
            ndim, count, _data = engine._conn.execute(
                "SELECT ndim, vector_count, data FROM vector_snapshot",
            ).fetchone()
            assert (ndim, count) == (MockEmbeddingProvider.dimension, len(MEMORIES))
            assert await vector_hits(engine) == expected
    assert "vector_index.recovered" in caplog.messages
    if damage in ("unreadable", "miscounted"):
        assert "restoring vectors from their saved payloads" in caplog.text
    assert snapshot_row(pack)[:2] == (MockEmbeddingProvider.dimension, len(MEMORIES))


async def test_model_with_another_dimension_opens_and_rebuilds(tmp_path, caplog):
    # As with a snapshot file, the stored snapshot keeps its own dimension,
    # so the pack opens and a rebuild can move it to the new model.
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine)
    with caplog.at_level(logging.WARNING):
        async with MemoryEngine.open(pack_config(pack), embedding_provider=SmallModel()) as engine:
            assert "run prme rebuild" in caplog.text
            with pytest.raises(EmbeddingVersionMismatchError):
                await engine._vector_index.search("tea", USER)
            summary = await engine.rebuild_indexes()
            assert summary["nodes_indexed"] == len(MEMORIES)
            assert len(await engine._vector_index.search("tea", USER, k=50)) == len(MEMORIES)
    assert snapshot_row(pack)[:2] == (SmallModel.dimension, len(MEMORIES))
    assert manifest_row(pack)["embedding_dim"] == SmallModel.dimension


# --- Lexical index and rebuild -------------------------------------------


async def test_lexical_index_after_open_equals_rebuild(tmp_path):
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine)
        retired = await engine.store_with_receipt("Alice once preferred coffee to tea", user_id=USER)
        await engine.archive(str(retired.node_id), user_id=USER)
        await engine.store("Bob keeps his own tea notes", user_id="bob")
        # A blank node cannot be indexed; it would change every BM25 score.
        await engine._graph_store.create_node(MemoryNode(
            user_id=USER, node_type=NodeType.NOTE, scope=Scope.PERSONAL, content="   ",
        ))

    async with MemoryEngine.open(pack_config(pack)) as engine:
        after_open = await lexical_hits(engine)
        summary = await engine.rebuild_indexes()
        after_rebuild = await lexical_hits(engine)
    assert (summary["nodes_indexed"], summary["nodes_skipped"]) == (len(MEMORIES) + 1, 1)
    assert after_open == after_rebuild
    assert all(after_open.values())
    assert str(retired.node_id) not in {node_id for hits in after_open.values() for node_id, _ in hits}


async def test_rebuild_indexes_keeps_receipts_and_snapshot(tmp_path):
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine)
        before = await read_back(engine)

        # Clearing resets the stored snapshot along with the vectors.
        await engine._vector_index.clear()
        assert engine._conn.execute("SELECT vector_count FROM vector_snapshot").fetchone() == (0,)

        summary = await engine.rebuild_indexes()
        assert summary == {"nodes_indexed": len(MEMORIES), "nodes_skipped": 0, "total_active": len(MEMORIES)}
        assert await read_back(engine) == before
    assert snapshot_row(pack)[:2] == (MockEmbeddingProvider.dimension, len(MEMORIES))
    async with MemoryEngine.open(pack_config(pack)) as engine:
        assert await read_back(engine) == before


async def test_in_memory_lexical_index_supports_every_write_path():
    index = LexicalIndex(None, commit_interval=2)
    rows = ((f"n{i}", f"note {i} about tea", USER, "note", "personal") for i in range(3))
    assert index.load_documents(rows) == 3
    assert {hit["node_id"] for hit in await index.search("tea", USER, limit=10)} == {"n0", "n1", "n2"}

    await index.index("n3", "more tea", USER, "note", "personal")
    await index.index("n0", "replaced tea", USER, "note", "personal", replace=True)
    await index.delete_by_node_id("n1")
    hits = await index.search("tea", USER, limit=10)
    assert sorted(hit["node_id"] for hit in hits) == ["n0", "n2", "n3"]
    assert next(hit for hit in hits if hit["node_id"] == "n0")["content"] == "replaced tea"

    await index.clear()
    assert await index.search("tea", USER, limit=10) == []
    assert index.load_documents(iter(())) == 0
    await index.close()


# --- Manifest -------------------------------------------------------------


async def test_manifest_describes_the_pack(tmp_path, caplog):
    pack = tmp_path / "memories.prme"
    config = pack_config(pack)
    async with MemoryEngine.open(config) as engine:
        await store_all(engine, MEMORIES[:2])
    first = manifest_row(pack)
    model = MockEmbeddingProvider()
    assert first["format_version"] == PACK_FORMAT_VERSION == 1
    assert first["prme_version"] == __version__
    assert (first["embedding_model"], first["embedding_version"], first["embedding_dim"]) == (
        model.model_name, model.model_version, model.dimension,
    )
    assert first["scoring_version"] == config.scoring.version_id
    assert first["created_at"] <= first["updated_at"]

    # Reopening keeps the record and refreshes the update time.
    async with MemoryEngine.open(config):
        pass
    second = manifest_row(pack)
    assert second["updated_at"] >= first["updated_at"]
    assert {**second, "updated_at": None} == {**first, "updated_at": None}

    # Another model still opens the pack, with a warning naming both models,
    # and a rebuild with it makes it the recorded model.
    other = OtherModel()
    with caplog.at_level(logging.WARNING, logger="prme.storage.pack_manifest"):
        async with MemoryEngine.open(config, embedding_provider=other) as engine:
            await engine.rebuild_indexes()
    assert model.model_name in caplog.text and other.model_name in caplog.text
    assert manifest_row(pack)["embedding_model"] == other.model_name

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="prme.storage.pack_manifest"):
        async with MemoryEngine.open(config, embedding_provider=OtherModel()):
            pass
    assert "Memory pack was built with" not in caplog.text


# --- Configuration guards ------------------------------------------------


def test_pack_path_excludes_database_url(tmp_path):
    with pytest.raises(ValueError, match="cannot be combined with database_url"):
        PRMEConfig(pack_path=str(tmp_path / "memories.prme"),
                   database_url="postgresql://prme@localhost/prme")
    # A blank pack_path means not set, as a blank database_url does.
    assert PRMEConfig(pack_path=" ").pack_path is None
    assert not PRMEConfig(pack_path="").single_file_pack


async def test_encryption_is_refused_before_opening(tmp_path):
    pack = tmp_path / "new" / "memories.prme"
    config = pack_config(pack, encryption_enabled=True, encryption_key="a passphrase")
    with pytest.raises(ValueError) as raised:
        await MemoryEngine.create(config)
    assert str(raised.value) == "Single-file packs do not support encryption yet; see issue #247"
    assert not pack.parent.exists()

    # A key that is not enabled requests no encryption, as for directory packs,
    # and no file-level encryption or exit handler is wired for the pack.
    async with MemoryEngine.open(pack_config(pack, encryption_key="a passphrase")) as engine:
        await engine.store(MEMORIES[0], user_id=USER)
        assert engine._encryption_provider is None
        assert not engine._atexit_registered
    assert os.listdir(pack.parent) == ["memories.prme"]


async def test_namespace_identity_binds_a_new_pack(tmp_path):
    pack = tmp_path / "memories.prme"
    namespace = uuid4()
    async with MemoryEngine.open(pack_config(pack, namespace_id=namespace)):
        pass
    async with MemoryEngine.open(pack_config(pack, namespace_id=namespace)):
        pass
    with pytest.raises(NamespaceIdentityError):
        await MemoryEngine.create(pack_config(pack, namespace_id=uuid4()))
