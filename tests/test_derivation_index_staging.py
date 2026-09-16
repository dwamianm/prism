"""Saved derivation inputs can be staged repeatedly without inference or loss."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import threading
from uuid import uuid4

import numpy as np
import pytest

from prme.models import Event, MemoryNode
from prme.models.derivation import DerivationPlan, PreparedEmbedding, PreparedLexicalDocument
from prme.storage.embedding import EmbeddingVersionMismatchError
from prme.storage.lexical_index import LexicalIndex
from prme.types import NodeType
from tests import test_vector_recovery

database = test_vector_recovery.database
open_index = test_vector_recovery.open_index
OfflineProvider = test_vector_recovery.OfflineProvider


def make_plan(count=2):
    event = Event(content="Alice uses Rust", role="user", user_id="alice")
    nodes = tuple(MemoryNode(content=f"Rust compiler {i}", node_type=NodeType.NOTE, user_id=event.user_id,
                             evidence_refs=[event.id]) for i in range(count))
    return DerivationPlan(
        event_id=event.id, user_id=event.user_id, scope=event.scope, content_hash=event.content_hash,
        nodes=nodes,
        embeddings=tuple(PreparedEmbedding(node_id=node.id, content=node.content,
                         model="recovery-test", version="1", dimension=3,
                         values=(0.125, 0.5, 0.75)) for node in nodes),
        lexical_documents=tuple(PreparedLexicalDocument(node_id=node.id, content=node.content)
                                for node in nodes),
    )


async def test_vector_concurrent_retry_restart_and_eviction(database, tmp_path):
    embedding = make_plan().embeddings[0]
    index = open_index(database, tmp_path, OfflineProvider())
    keys = await asyncio.gather(*(index.stage(embedding, user_id="alice") for _ in range(8)))
    assert len(set(keys)) == 1
    assert database.execute("SELECT count(*) FROM vector_metadata").fetchone()[0] == 1
    assert index._index.size == 1
    # No graph node exists yet, so ordinary retrieval cannot expose staging.
    assert await index.search_by_vector(list(embedding.values), "alice") == []
    await index.close()
    index = open_index(database, tmp_path, OfflineProvider())
    assert await index.stage(embedding, user_id="alice") == keys[0]
    await index.delete_by_node_id(str(embedding.node_id))
    assert database.execute("SELECT count(*) FROM vector_staging").fetchone()[0] == 0
    assert await index.stage(embedding, user_id="alice") != keys[0]
    await index.clear()
    assert database.execute("SELECT count(*) FROM vector_staging").fetchone()[0] == 0
    await index.stage(embedding, user_id="alice")
    await index.close()


@pytest.mark.parametrize("after_add", [False, True])
async def test_vector_retry_after_native_failure_reuses_durable_key(database, tmp_path, monkeypatch, after_add):
    index = open_index(database, tmp_path, OfflineProvider())
    embedding = make_plan().embeddings[0]
    original = index._add_vector

    def fail(key, vector):
        if after_add:
            original(key, vector)
        raise RuntimeError("native failure")

    with monkeypatch.context() as fault:
        fault.setattr(index, "_add_vector", fail)
        with pytest.raises(RuntimeError, match="native failure"):
            await index.stage(embedding, user_id="alice")
    key = database.execute("SELECT vector_key FROM vector_metadata").fetchone()[0]
    assert await index.stage(embedding, user_id="alice") == key
    assert index._index.size == 1
    assert database.execute("SELECT count(*) FROM vector_payloads").fetchone()[0] == 1
    assert np.allclose(index._index.get(key), embedding.values)
    await index.close()


@pytest.mark.parametrize("field,value", [
    ("user_id", "bob"), ("content", "different text"), ("values", (0.75, 0.5, 0.125)),
    ("model", "other-model"), ("version", "2"),
])
async def test_vector_identity_conflict_preserves_original(database, tmp_path, field, value):
    index = open_index(database, tmp_path, OfflineProvider())
    embedding = make_plan().embeddings[0]
    key = await index.stage(embedding, user_id="alice")
    changed = embedding if field == "user_id" else embedding.model_copy(update={field: value})
    with pytest.raises((ValueError, EmbeddingVersionMismatchError)):
        await index.stage(changed, user_id=value if field == "user_id" else "alice")
    assert await index.stage(embedding, user_id="alice") == key
    assert index._index.size == 1
    await index.close()


async def test_vector_does_not_adopt_unverifiable_ordinary_entry(database, tmp_path):
    index = open_index(database, tmp_path)
    embedding = make_plan().embeddings[0]
    key = await index.index(str(embedding.node_id), embedding.content, "alice")
    with pytest.raises(ValueError, match="conflicts"):
        await index.stage(embedding, user_id="alice")
    assert index._index.contains(key)
    assert index._index.size == 1
    await index.close()


async def test_vector_rejects_float32_underflow(database, tmp_path):
    index = open_index(database, tmp_path, OfflineProvider())
    embedding = make_plan().embeddings[0].model_copy(update={"values": (1e-100, 0, 0)})
    with pytest.raises(ValueError, match="underflows"):
        await index.stage(embedding, user_id="alice")
    assert index._index.size == 0
    await index.close()


@pytest.fixture
def lexical_path(tmp_path):
    path = tmp_path / "lexical"
    path.mkdir()
    return str(path)


async def test_lexical_concurrent_retry_restart_and_writer_release(lexical_path):
    plan = make_plan()
    index = LexicalIndex(lexical_path)
    await index.index(str(uuid4()), "unrelated Rust document", "alice")
    await asyncio.gather(*(index.stage(plan) for _ in range(6)))
    assert len(await index.search("Rust", "alice")) == 3
    assert await index.search("Rust", "bob") == []
    await index.close()
    index = LexicalIndex(lexical_path)
    await index.stage(plan)
    # A no-op stage must release the writer, too.
    other = LexicalIndex(lexical_path)
    await other.stage(plan)
    assert len(await other.search("Rust", "alice")) == 3
    await other.close()
    await index.close()


@pytest.mark.parametrize("field,value", [
    ("user_id", "bob"), ("content", "different Rust content"),
    ("scope", "project"), ("scope", None), ("node_type", "fact"),
])
async def test_lexical_rejects_conflicting_batch_before_adding_any_document(lexical_path, field, value):
    plan = make_plan()
    doc = plan.lexical_documents[-1]
    node = plan.nodes[-1]
    original = dict(node_id=str(doc.node_id), content=doc.content, user_id=plan.user_id,
                    scope=node.scope.value, node_type=node.node_type.value)
    original[field] = value
    index = LexicalIndex(lexical_path)
    await index.index(**original)
    await index.flush()
    with pytest.raises(ValueError, match="conflicts"):
        await index.stage(plan)
    assert index._index.searcher().num_docs == 1
    # The conflicting document remains intact and the writer was released.
    other = LexicalIndex(lexical_path)
    assert len(await other.search("Rust", original["user_id"])) == 1
    await other.close()
    await index.close()


async def test_lexical_duplicate_identity_is_rejected_without_deleting_documents(lexical_path):
    plan = make_plan(1)
    doc = plan.lexical_documents[0]
    index = LexicalIndex(lexical_path)
    for _ in range(2):
        await index.index(str(doc.node_id), doc.content, plan.user_id,
                          plan.nodes[0].node_type.value, plan.scope.value)
    await index.flush()
    with pytest.raises(ValueError, match="duplicate"):
        await index.stage(plan)
    assert len(await index.search("Rust", "alice")) == 2
    await index.close()


class WriterFault:
    def __init__(self, writer, boundary):
        self.writer, self.boundary, self.adds = writer, boundary, 0

    def add_document(self, document):
        result = self.writer.add_document(document)
        self.adds += 1
        if self.boundary == "add" and self.adds == 1:
            raise RuntimeError("staging failure")
        return result

    def commit(self):
        if self.boundary == "before_commit":
            raise RuntimeError("staging failure")
        result = self.writer.commit()
        if self.boundary == "after_commit":
            raise RuntimeError("staging failure")
        return result

    def __getattr__(self, name):
        return getattr(self.writer, name)


@pytest.mark.parametrize("boundary", ["add", "before_commit", "after_commit"])
async def test_lexical_failed_stage_preserves_committed_data_and_retries_once(lexical_path, monkeypatch, boundary):
    plan = make_plan()
    index = LexicalIndex(lexical_path)
    await index.index(str(uuid4()), "existing Rust document", "alice")
    await index.flush()
    ensure = index._ensure_writer
    with monkeypatch.context() as fault:
        fault.setattr(index, "_ensure_writer", lambda: WriterFault(ensure(), boundary))
        with pytest.raises(RuntimeError, match="staging failure"):
            await index.stage(plan)
    results = await index.search("Rust", "alice")
    assert len(results) == (3 if boundary == "after_commit" else 1)
    await index.close()
    index = LexicalIndex(lexical_path)
    await index.stage(plan)
    await index.stage(plan)
    assert len(await index.search("Rust", "alice")) == 3
    await index.close()


@pytest.mark.parametrize("boundary", ["vector_payload", "vector_add", "lexical_commit"])
async def test_process_exit_replays_saved_index_inputs_without_inference(tmp_path, boundary):
    plan = make_plan()
    (tmp_path / "plan.json").write_text(plan.model_dump_json())
    script = """
import asyncio, os, sys
from pathlib import Path
import duckdb
from prme.models.derivation import DerivationPlan
from prme.storage.schema import initialize_database
from prme.storage.lexical_index import LexicalIndex
from prme.storage.vector_index import VectorIndex
class Offline:
    dimension = 3
    model_name = 'recovery-test'
    model_version = '1'
    async def embed(self, texts):
        raise AssertionError('No inference during replay')
async def main():
    path, boundary = Path(sys.argv[1]), sys.argv[2]
    plan = DerivationPlan.model_validate_json((path / 'plan.json').read_text())
    conn = duckdb.connect(str(path / 'memory.duckdb'))
    initialize_database(conn)
    vector = VectorIndex(conn, str(path / 'vectors.usearch'), Offline())
    if boundary == 'vector_payload':
        vector._add_vector = lambda *args: os._exit(42)
    for embedding in plan.embeddings:
        await vector.stage(embedding, user_id=plan.user_id)
    if boundary == 'vector_add':
        os._exit(42)
    (path / 'lexical').mkdir(exist_ok=True)
    lexical = LexicalIndex(str(path / 'lexical'))
    await lexical.stage(plan)
    os._exit(42)
asyncio.run(main())
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, str(tmp_path), boundary],
        capture_output=True, timeout=30, env=env,
    )
    assert completed.returncode == 42, completed.stderr.decode()
    import duckdb
    conn = duckdb.connect(str(tmp_path / "memory.duckdb"))
    try:
        index = open_index(conn, tmp_path, OfflineProvider())
        for embedding in plan.embeddings:
            await index.stage(embedding, user_id=plan.user_id)
        assert conn.execute("SELECT count(*) FROM vector_metadata").fetchone()[0] == 2
        assert index._index.size == 2
        (tmp_path / "lexical").mkdir(exist_ok=True)
        lexical = LexicalIndex(str(tmp_path / "lexical"))
        await lexical.stage(plan)
        await lexical.stage(plan)
        assert len(await lexical.search("Rust", "alice")) == 2
        await lexical.close()
        await index.close()
    finally:
        conn.close()


@pytest.mark.parametrize("backend", ["vector", "lexical"])
async def test_cancelled_stage_finishes_before_unlocking_and_retry_does_not_duplicate(
    database, tmp_path, lexical_path, monkeypatch, backend,
):
    plan = make_plan()
    index = (open_index(database, tmp_path, OfflineProvider()) if backend == "vector"
             else LexicalIndex(lexical_path))
    started, release = threading.Event(), threading.Event()
    original = index._do_stage

    def blocked(*args):
        result = original(*args)
        started.set()
        if not release.wait(5):
            raise TimeoutError("Test did not release the staging thread")
        return result

    def stage():
        return (index.stage(plan.embeddings[0], user_id=plan.user_id) if backend == "vector"
                else index.stage(plan))

    with monkeypatch.context() as fault:
        fault.setattr(index, "_do_stage", blocked)
        task = asyncio.create_task(stage())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert index._write_lock.locked()
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    await stage()
    if backend == "vector":
        assert index._index.size == 1
        assert database.execute("SELECT count(*) FROM vector_metadata").fetchone()[0] == 1
    else:
        assert len(await index.search("Rust", "alice")) == 2
    await index.close()
