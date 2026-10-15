"""Single-file memory packs (issue #247, phase 1).

A pack opened through ``pack_path``, or through ``MemoryClient`` with a path
ending in ``.prme``, is one DuckDB file holding the event log, the graph, the
vector payloads and snapshot, and the manifest. The lexical index is rebuilt
in memory from the graph on open and shared by the engines of a process that
open the pack. These tests check that the file is all there is at rest, that
reopening or copying it preserves retrieval receipts, that a damaged vector
snapshot is restored from the durable payloads, that vector removals count
toward the snapshot interval and survive a crash, that the lexical index after
open equals a rebuild and, for a pack built with ingest(), the index the write
path built, that engines share the lexical index, that an open that only
reads leaves the file unchanged, and that the configuration guards hold.
Directory packs are covered by their existing tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import duckdb
import pytest
import tantivy

from prme import MemoryClient, MemoryEngine, PRMEConfig, __version__
from prme.client import config_from_directory, config_from_pack
from prme.ingestion.schema import ExtractionResult
from prme.models.nodes import MemoryNode
from prme.storage import lexical_index as lexical_module
from prme.storage.embedding import EmbeddingVersionMismatchError
from prme.storage.lexical_index import LexicalIndex, has_lexical_document
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


def directory_config(directory: Path, **overrides) -> PRMEConfig:
    return PRMEConfig(
        db_path=str(directory / "memory.duckdb"),
        vector_path=str(directory / "vectors.usearch"),
        lexical_path=str(directory / "lexical_index"),
        **overrides,
    )


def layout_config(tmp_path: Path, layout: str, **overrides) -> PRMEConfig:
    if layout == "single-file":
        return pack_config(tmp_path / "memories.prme", **overrides)
    return directory_config(tmp_path / "pack", **overrides)


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


def test_a_pack_path_opens_its_pack_despite_an_ambient_database_url(tmp_path, monkeypatch):
    monkeypatch.setenv("PRME_DATABASE_URL", "postgresql://prme@localhost/prme")
    pack = tmp_path / "memories.prme"
    config = config_from_pack(str(pack))
    assert config.database_url is None and config.backend == "duckdb"
    with MemoryClient(str(pack)) as client:
        client.store(MEMORIES[0], user_id=USER)
    assert os.listdir(tmp_path) == ["memories.prme"]


def test_a_directory_opens_as_a_directory_pack_despite_an_ambient_pack_path(tmp_path, monkeypatch):
    ambient = tmp_path / "ambient.prme"
    monkeypatch.setenv("PRME_PACK_PATH", str(ambient))
    directory = tmp_path / "pack"
    assert config_from_directory(str(directory)).pack_path is None
    with MemoryClient(str(directory)) as client:
        assert not client._config.single_file_pack
        client.store(MEMORIES[0], user_id=USER)
    assert {"memory.duckdb", "vectors.usearch", "lexical_index"} <= set(os.listdir(directory))
    assert not ambient.exists()


async def test_two_spellings_of_a_plain_pack_share_one_database(tmp_path):
    # DuckDB shares a database between engines only when they name it by the
    # same path, so a pack is opened by its real path.
    pack = tmp_path / "memories.prme"
    link = tmp_path / "link.prme"
    link.symlink_to(pack)
    async with MemoryEngine.open(pack_config(pack)) as first:
        async with MemoryEngine.open(pack_config(link)) as second:
            await first.store(MEMORIES[0], user_id=USER)
            assert second._conn.execute("SELECT count(*) FROM nodes").fetchone() == (1,)
    assert sorted(os.listdir(tmp_path)) == ["link.prme", "memories.prme"]


# --- One lexical index per pack in a process ------------------------------


def shared_index_holders(pack: Path) -> int | None:
    entry = lexical_module._pack_indexes.get(os.path.realpath(pack))
    return None if entry is None else entry.holders


async def contents_found(engine: MemoryEngine, query: str) -> set[str]:
    lexical = {hit["content"] for hit in await engine._lexical_index.search(query, USER, limit=50)}
    response = await engine.retrieve(query, user_id=USER, reference_time=REFERENCE_TIME)
    retrieved = {item.node.content for item in response.results}
    return lexical & retrieved


async def test_engines_on_one_plain_pack_share_its_lexical_index(tmp_path):
    pack = tmp_path / "memories.prme"
    link = tmp_path / "link.prme"
    link.symlink_to(pack)
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine, MEMORIES[:2])

    first = await MemoryEngine.create(pack_config(pack))
    second = await MemoryEngine.create(pack_config(link))
    try:
        assert first._lexical_index is second._lexical_index
        assert shared_index_holders(pack) == 2
        # Both engines see what the first one loaded, and a node stored
        # through one engine is found through the other without a reopen.
        assert await contents_found(second, "tea") == {MEMORIES[0]}
        await first.store("Alice adopted a grey cat named Pepper", user_id=USER)
        assert await contents_found(second, "grey cat Pepper") == {"Alice adopted a grey cat named Pepper"}

        # Closing one engine keeps the index of the other.
        await first.close()
        assert shared_index_holders(pack) == 1
        await second.store("Pepper sleeps on the piano", user_id=USER)
        assert await contents_found(second, "piano") == {"Pepper sleeps on the piano"}
    finally:
        await first.close()
        await second.close()
    # The last engine to close releases the pack's index.
    assert shared_index_holders(pack) is None

    async with MemoryEngine.open(pack_config(pack)) as engine:
        assert await contents_found(engine, "piano") == {"Pepper sleeps on the piano"}
    assert shared_index_holders(pack) is None


async def test_engines_on_different_packs_keep_their_own_lexical_index(tmp_path):
    async with MemoryEngine.open(pack_config(tmp_path / "a.prme")) as first:
        async with MemoryEngine.open(pack_config(tmp_path / "b.prme")) as second:
            assert first._lexical_index is not second._lexical_index
            await first.store(MEMORIES[0], user_id=USER)
            assert await first._lexical_index.search("tea", USER)
            assert await second._lexical_index.search("tea", USER) == []
    assert lexical_module._pack_indexes == {}


async def test_a_failed_open_releases_the_shared_lexical_index(tmp_path, monkeypatch):
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine, MEMORIES[:2])

    def broken_load(conn, index):
        raise RuntimeError("load failed")

    with monkeypatch.context() as patch:
        patch.setattr("prme.storage.engine._load_lexical_from_graph", broken_load)
        with pytest.raises(RuntimeError, match="load failed"):
            await MemoryEngine.create(pack_config(pack))
    assert shared_index_holders(pack) is None

    # A later open loads the index afresh. An open that fails after taking
    # the index releases it too, and the engine already holding it keeps it.
    def broken_provider(config):
        raise RuntimeError("provider failed")

    async with MemoryEngine.open(pack_config(pack)) as engine:
        assert await contents_found(engine, "tea") == {MEMORIES[0]}
        with monkeypatch.context() as patch:
            patch.setattr("prme.ingestion.extraction.create_extraction_provider", broken_provider)
            with pytest.raises(RuntimeError, match="provider failed"):
                await MemoryEngine.create(pack_config(pack))
        assert shared_index_holders(pack) == 1
        await engine.store("Pepper sleeps on the piano", user_id=USER)
        assert await contents_found(engine, "piano") == {"Pepper sleeps on the piano"}
    assert shared_index_holders(pack) is None


def test_clients_on_one_pack_share_its_lexical_index_across_event_loops(tmp_path):
    # Each MemoryClient runs its engine on its own event loop, and both
    # write to the shared index at the same time.
    pack = tmp_path / "memories.prme"
    with MemoryClient(str(pack)) as first, MemoryClient(str(pack)) as second:
        assert first._engine._lexical_index is second._engine._lexical_index
        writes = [((first, second)[number % 2], text) for number, text in enumerate(MEMORIES)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda write: write[0].store(write[1], user_id=USER), writes))
        for client in (first, second):
            hits = client._run(client._engine._lexical_index.search("tea Bob violin standup peanuts", USER, limit=50))
            assert {hit["content"] for hit in hits} == set(MEMORIES)
    assert shared_index_holders(pack) is None


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


@pytest.mark.parametrize("layout", ["directory", "single-file"])
async def test_removals_count_toward_the_snapshot_interval_only_in_a_single_file_pack(tmp_path, monkeypatch, layout):
    # A single-file pack rewrites its whole snapshot row on each save, so a
    # removal counts toward vector_save_interval as an addition does. The
    # snapshot file of a directory pack is still saved after every removal.
    single = layout == "single-file"
    async with MemoryEngine.open(layout_config(tmp_path, layout, vector_save_interval=3)) as engine:
        receipts = [await engine.store_with_receipt(text, user_id=USER) for text in MEMORIES]
        vectors = engine._vector_index
        assert vectors._unsaved_inserts == 0
        snapshot = Mock(wraps=vectors._save_snapshot)
        monkeypatch.setattr(vectors, "_save_snapshot", snapshot)

        for receipt in receipts[:2]:
            await engine.archive(str(receipt.node_id), user_id=USER)
        assert snapshot.call_count == (0 if single else 2)
        assert vectors._unsaved_inserts == (2 if single else 0)
        assert vectors._snapshot_dirty

        await engine.archive(str(receipts[2].node_id), user_id=USER)
        assert snapshot.call_count == (1 if single else 3)
        assert vectors._unsaved_inserts == 0
        if single:
            assert engine._conn.execute("SELECT vector_count FROM vector_snapshot").fetchone() == (3,)
        assert {hit["node_id"] for hit in await vectors.search("tea", USER, k=50)} == {
            str(receipt.node_id) for receipt in receipts[3:]
        }


CRASH_AFTER_REMOVALS = '''
import asyncio, hashlib, os, sys
from prme import MemoryEngine, PRMEConfig
import prme.storage.engine as module

class Provider:
    model_name = "durability-test"
    model_version = "1"
    dimension = 384
    async def embed(self, texts):
        return [[hashlib.sha256(t.encode()).digest()[i % 32] / 255 for i in range(384)] for t in texts]

async def main():
    config = PRMEConfig.model_validate_json(sys.argv[1])
    module.create_embedding_provider = lambda _: Provider()
    engine = await MemoryEngine.create(config)
    for node_id in sys.argv[2].split(","):
        await engine.archive(node_id, user_id=sys.argv[3])
    # The removals are below the save interval: nothing saved the snapshot.
    assert engine._vector_index._unsaved_inserts == len(sys.argv[2].split(","))
    os._exit(42)

asyncio.run(main())
'''


async def test_removals_not_yet_in_the_snapshot_survive_a_crash(tmp_path, caplog):
    pack = tmp_path / "memories.prme"
    config = pack_config(pack)
    async with MemoryEngine.open(config) as engine:
        receipts = [await engine.store_with_receipt(text, user_id=USER) for text in MEMORIES]
    assert snapshot_row(pack)[:2] == (MockEmbeddingProvider.dimension, len(MEMORIES))
    removed = [str(receipt.node_id) for receipt in receipts[:3]]
    kept = {str(receipt.node_id) for receipt in receipts[3:]}

    # The process exits without closing the engine, so the stored snapshot
    # still holds the removed vectors while their metadata is gone.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", CRASH_AFTER_REMOVALS, config.model_dump_json(), ",".join(removed), USER],
        capture_output=True, env=env, timeout=60,
    )
    assert completed.returncode == 42, completed.stderr.decode()
    assert snapshot_row(pack)[:2] == (MockEmbeddingProvider.dimension, len(MEMORIES))

    with caplog.at_level(logging.INFO, logger="prme.storage.vector_index"):
        async with MemoryEngine.open(config) as engine:
            assert len(engine._vector_index._index) == len(kept)
            hits = {hit["node_id"] for query in QUERIES
                    for hit in await engine._vector_index.search(query, USER, k=50)}
            assert hits == kept
    assert "vector_index.recovered" in caplog.messages
    assert snapshot_row(pack)[:2] == (MockEmbeddingProvider.dimension, len(kept))


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


def test_an_in_memory_index_lock_serializes_tasks_on_different_event_loops():
    # Engines on several event loops can share an in-memory index, so its
    # lock must make a task on one loop wait for a holder on another.
    index = LexicalIndex(None)
    held, waiting, release = threading.Event(), threading.Event(), threading.Event()
    order = []

    async def holder():
        async with index._write_lock:
            held.set()
            await asyncio.to_thread(release.wait, 5)
            order.append("holder")

    async def waiter():
        await asyncio.to_thread(held.wait, 5)
        assert index._write_lock.locked()
        waiting.set()
        async with index._write_lock:
            order.append("waiter")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(asyncio.run, holder())
        second = pool.submit(asyncio.run, waiter())
        assert waiting.wait(5)
        time.sleep(0.05)
        release.set()
        first.result(5)
        second.result(5)
    assert order == ["holder", "waiter"]
    assert not index._write_lock.locked()


# --- Lexical documents of an ingest() pack --------------------------------

# Two sentences each, so that a claim's text (its own sentence) differs from
# its message and no two documents tie on BM25.
INGESTED = {
    "Caroline paints sunsets over the lake. The weather was mild.": ("paints", "sunsets"),
    "Caroline adopted a puppy named Oscar in May. He sleeps all day.": ("adopted", "a puppy named Oscar"),
}
INGESTED_QUERIES = ("Caroline", "sunsets over the lake", "puppy", "weather")


async def scripted_extraction(content, *, role=None):
    """One person entity and one grounded claim for each known message."""
    predicate, obj = INGESTED[content]
    return ExtractionResult.model_validate({
        "entities": [{"name": "Caroline", "entity_type": "person"}],
        "facts": [{"subject": "Caroline", "subject_entity_type": "person", "predicate": predicate,
                   "object": obj, "object_entity_type": None, "polarity": "positive",
                   "evidence_quote": content}],
    })


async def lexical_state(engine: MemoryEngine) -> tuple:
    """Every document in the lexical index, and the scored hits per query."""
    await engine._lexical_index.flush()
    index = engine._lexical_index._index
    index.reload()
    searcher = index.searcher()
    hits = searcher.search(tantivy.Query.all_query(), limit=max(1, searcher.num_docs)).hits
    documents = sorted(
        tuple(sorted((field, tuple(values)) for field, values in searcher.doc(address).to_dict().items()))
        for _score, address in hits
    )
    scored = {query: sorted((hit["node_id"], hit["score"])
                            for hit in await engine._lexical_index.search(query, USER, limit=50))
              for query in INGESTED_QUERIES}
    return documents, scored


async def ranked_ids(engine: MemoryEngine) -> dict:
    return {
        query: [(str(item.node.id), item.composite_score) for item in (await engine.retrieve(
            query, user_id=USER, reference_time=REFERENCE_TIME,
        )).results]
        for query in INGESTED_QUERIES
    }


@pytest.mark.parametrize("layout", ["directory", "single-file"])
async def test_an_ingested_pack_keeps_its_lexical_index_across_reopen_and_rebuild(tmp_path, layout):
    config = layout_config(tmp_path, layout)
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = scripted_extraction
        for message in INGESTED:
            await engine.ingest(message, user_id=USER, wait_for_extraction=True)
        await engine.process_pending(user_id=USER)
        nodes = await engine.query_nodes(user_id=USER)
        assert sorted(node.node_type.value for node in nodes) == ["entity", "fact", "fact", "note", "note"]
        [entity] = [str(node.id) for node in nodes if node.node_type == NodeType.ENTITY]

        ingested = await lexical_state(engine)
        rankings = await ranked_ids(engine)
        # The write path gives the extracted entity a vector but no lexical
        # document, and every other node both.
        documents, _scored = ingested
        indexed = {dict(document)["node_id"][0] for document in documents}
        assert indexed == {str(node.id) for node in nodes} - {entity}
        assert entity in {hit["node_id"] for hit in await engine._vector_index.search("Caroline", USER, k=50)}
        assert all(rankings.values())

    async with MemoryEngine.open(config) as engine:
        # A single-file pack rebuilds its lexical index on open; it holds the
        # documents the write path built, so rankings do not move.
        assert await lexical_state(engine) == ingested
        assert await ranked_ids(engine) == rankings

        summary = await engine.rebuild_indexes()
        assert summary == {"nodes_indexed": len(nodes), "nodes_skipped": 0, "total_active": len(nodes)}
        assert await lexical_state(engine) == ingested
        assert await ranked_ids(engine) == rankings
        # Rebuilding re-embeds every node, the entity included.
        assert entity in {hit["node_id"] for hit in await engine._vector_index.search("Caroline", USER, k=50)}


async def test_a_directly_stored_entity_keeps_its_lexical_document(tmp_path):
    # store() gives every node a lexical document, an entity included; only
    # the entities that extraction creates have none.
    assert has_lexical_document(NodeType.ENTITY.value, derived=False)
    assert not has_lexical_document(NodeType.ENTITY.value, derived=True)
    assert all(has_lexical_document(node_type.value, derived=True)
               for node_type in NodeType if node_type != NodeType.ENTITY)
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(pack_config(pack)) as engine:
        receipt = await engine.store_with_receipt("Caroline", user_id=USER, node_type=NodeType.ENTITY)
        stored = await lexical_state(engine)
        assert str(receipt.node_id) in {hit["node_id"] for hit in await engine._lexical_index.search("Caroline", USER)}
    async with MemoryEngine.open(pack_config(pack)) as engine:
        assert await lexical_state(engine) == stored
        await engine.rebuild_indexes()
        assert await lexical_state(engine) == stored


# --- Layout equivalence ----------------------------------------------------


async def ranked_contents(engine: MemoryEngine) -> dict:
    """Ranking and scores per query, keyed by text rather than node id.

    Two packs built from the same events have different node ids, so the
    comparison between layouts reads the ranked texts. Scores are rounded
    because the two packs stored their events a few milliseconds apart.
    """
    return {
        query: [
            (item.node.content, round(item.composite_score, 6))
            for item in (await engine.retrieve(
                query, user_id=USER, reference_time=REFERENCE_TIME,
            )).results
        ]
        for query in QUERIES
    }


async def test_directory_and_single_file_packs_rank_the_same_events_alike(tmp_path):
    directory = tmp_path / "directory-pack"
    pack = tmp_path / "single.prme"
    directory_config = PRMEConfig(
        db_path=str(directory / "memory.duckdb"),
        vector_path=str(directory / "vectors.usearch"),
        lexical_path=str(directory / "lexical_index"),
    )

    async with MemoryEngine.open(directory_config) as engine:
        await store_all(engine)
        directory_fresh = await ranked_contents(engine)
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine)
        single_fresh = await ranked_contents(engine)
    assert all(single_fresh.values())
    assert single_fresh == directory_fresh

    # After a reopen the directory pack reads its persisted indexes and the
    # single-file pack rebuilds its lexical index; for a store() pack both
    # hold the same documents, so the ranking does not move.
    async with MemoryEngine.open(directory_config) as engine:
        directory_reopened = await ranked_contents(engine)
    async with MemoryEngine.open(pack_config(pack)) as engine:
        single_reopened = await ranked_contents(engine)
    assert directory_reopened == directory_fresh
    assert single_reopened == single_fresh


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

    # Reopening with the same PRME version leaves the record as it is.
    async with MemoryEngine.open(config):
        pass
    assert manifest_row(pack) == first

    # Another PRME version records itself and the time when it opens the pack.
    with duckdb.connect(str(pack)) as conn:
        conn.execute("UPDATE prme_manifest SET prme_version = '0.0.1'")
    async with MemoryEngine.open(config):
        pass
    second = manifest_row(pack)
    assert second["prme_version"] == __version__
    assert second["updated_at"] > first["updated_at"]
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


async def test_a_pack_without_a_manifest_records_the_model_of_its_vectors(tmp_path, caplog):
    # A directory pack's database has vectors but no manifest. Opened as a
    # single-file pack under another model of the same dimension, it records
    # the model most of its vectors were built with, not the configured one.
    directory = tmp_path / "pack"
    directory_config = PRMEConfig(
        db_path=str(directory / "memory.duckdb"),
        vector_path=str(directory / "vectors.usearch"),
        lexical_path=str(directory / "lexical_index"),
    )
    async with MemoryEngine.open(directory_config) as engine:
        await store_all(engine, MEMORIES[:2])
    async with MemoryEngine.open(directory_config, embedding_provider=OtherModel()) as engine:
        await store_all(engine, MEMORIES[2:3])
    pack = tmp_path / "copy.prme"
    shutil.copyfile(directory / "memory.duckdb", pack)

    with caplog.at_level(logging.WARNING, logger="prme.storage.pack_manifest"):
        async with MemoryEngine.open(pack_config(pack), embedding_provider=OtherModel()):
            pass
    model = MockEmbeddingProvider()
    recorded = manifest_row(pack)
    assert (recorded["embedding_model"], recorded["embedding_version"], recorded["embedding_dim"]) == (
        model.model_name, model.model_version, model.dimension,
    )
    assert f"built with embedding model {model.model_name}" in caplog.text
    assert f"configured model is {OtherModel.model_name}" in caplog.text


# --- An open that only reads ---------------------------------------------


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_an_open_that_only_reads_leaves_the_file_unchanged(tmp_path):
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine)
    # The first open after a session that handed out vector keys moves the
    # key sequence past them once. Later opens that only read write nothing.
    async with MemoryEngine.open(pack_config(pack)):
        pass
    settled = digest(pack)
    for _ in range(2):
        async with MemoryEngine.open(pack_config(pack)) as engine:
            assert all((await lexical_hits(engine)).values())
            assert all((await vector_hits(engine)).values())
        assert digest(pack) == settled

    # A retrieval saves its receipt, which is a real write, and nothing else.
    manifest, snapshot = manifest_row(pack), snapshot_row(pack)
    async with MemoryEngine.open(pack_config(pack)) as engine:
        response = await engine.retrieve("tea", user_id=USER, reference_time=REFERENCE_TIME)
        assert response.metadata.receipt_persisted
    assert (manifest_row(pack), snapshot_row(pack)) == (manifest, snapshot)
    with duckdb.connect(str(pack)) as conn:
        assert conn.execute(
            "SELECT target_id FROM operations WHERE op_type = 'RETRIEVAL_REQUEST'"
        ).fetchall() == [(str(response.metadata.request_id),)]


# --- Configuration guards ------------------------------------------------


def test_pack_path_excludes_database_url(tmp_path):
    with pytest.raises(ValueError, match="cannot be combined with database_url"):
        PRMEConfig(pack_path=str(tmp_path / "memories.prme"),
                   database_url="postgresql://prme@localhost/prme")
    # A blank pack_path means not set, as a blank database_url does.
    assert PRMEConfig(pack_path=" ").pack_path is None
    assert not PRMEConfig(pack_path="").single_file_pack


async def test_key_without_encryption_enabled_requests_no_encryption(tmp_path):
    # Encrypted single-file packs are covered by test_single_file_pack_encryption.
    # A key that is not enabled requests no encryption, as for directory packs,
    # and no file-level encryption or exit handler is wired for the pack.
    pack = tmp_path / "new" / "memories.prme"
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
