"""Crash boundaries between durable vector records and the USearch snapshot."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import threading

import duckdb
import numpy as np
import pytest

from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex


class FixedProvider:
    dimension = 3
    model_name = "recovery-test"
    model_version = "1"

    async def embed(self, texts):
        return [[0.125, 0.5, 0.75] for _ in texts]


class OfflineProvider(FixedProvider):
    async def embed(self, texts):
        raise AssertionError("Recovery must not call the embedding provider")


@pytest.fixture
def database(tmp_path):
    conn = duckdb.connect(str(tmp_path / "memory.duckdb"))
    initialize_database(conn)
    yield conn
    conn.close()


def open_index(database, tmp_path, provider=None, **kwargs):
    return VectorIndex(
        database, str(tmp_path / "vectors.usearch"),
        provider or FixedProvider(), **kwargs,
    )


@pytest.mark.parametrize("legacy", [False, True])
async def test_abrupt_exit_recovers_acknowledged_vector_without_inference(tmp_path, legacy):
    """A real process exit bypasses both engine close and DuckDB checkpoint."""
    if legacy:
        conn = duckdb.connect(str(tmp_path / "memory.duckdb"))
        initialize_database(conn)
        old_index = open_index(conn, tmp_path, save_interval=1)
        await old_index.index("22222222-2222-2222-2222-222222222222", "legacy", "alice")
        conn.execute("DROP TABLE vector_payloads")
        conn.close()
    script = tmp_path / "crash_writer.py"
    script.write_text('''
import asyncio, os, sys
from pathlib import Path
import duckdb
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
class Provider:
    dimension = 3
    model_name = "recovery-test"
    model_version = "1"
    async def embed(self, texts):
        return [[0.125, 0.5, 0.75] for _ in texts]
async def main():
    path = Path(sys.argv[1])
    conn = duckdb.connect(str(path / "memory.duckdb"))
    initialize_database(conn)
    conn.execute("""INSERT INTO nodes
        (id, user_id, node_type, content, lifecycle_state, scope,
         salience, confidence, created_at, updated_at)
        VALUES ('11111111-1111-1111-1111-111111111111', 'alice', 'FACT',
                'source', 'tentative', 'PERSONAL', .5, .5,
                current_timestamp, current_timestamp)""")
    index = VectorIndex(conn, str(path / "vectors.usearch"), Provider(), save_interval=100)
    await index.index("11111111-1111-1111-1111-111111111111", "source", "alice")
    assert index._unsaved_inserts == 1
    os._exit(42)
asyncio.run(main())
''')
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = await asyncio.to_thread(
        subprocess.run, [sys.executable, str(script), str(tmp_path)],
        env=env, capture_output=True, timeout=30,
    )
    assert completed.returncode == 42, completed.stderr.decode()
    conn = duckdb.connect(str(tmp_path / "memory.duckdb"))
    try:
        index = open_index(conn, tmp_path, OfflineProvider())
        results = await index.search_by_vector([0.125, 0.5, 0.75], "alice")
        assert [r["node_id"] for r in results] == ["11111111-1111-1111-1111-111111111111"]
        assert await index.search_by_vector([0.125, 0.5, 0.75], "bob") == []
        assert (tmp_path / "vectors.usearch").exists()
        await index.close()
    finally:
        conn.close()


async def test_recovery_repairs_partial_snapshot_and_is_idempotent(database, tmp_path):
    index = open_index(database, tmp_path, save_interval=100)
    first = await index.index("one", "first", "alice")
    await index.save()
    second = await index.index("two", "second", "alice")
    recovered = open_index(database, tmp_path, OfflineProvider())
    assert set(recovered._index.keys) == {first, second}
    again = open_index(database, tmp_path, OfflineProvider())
    assert set(again._index.keys) == {first, second}
    assert database.execute("SELECT count(*) FROM vector_metadata").fetchone()[0] == 2
    np.testing.assert_array_equal(again._index.get(second), [0.125, 0.5, 0.75])


async def test_recovery_crosses_payload_batch_boundary(database, tmp_path):
    index = open_index(database, tmp_path, save_interval=1000)
    keys = {await index.index(str(i), "source", "alice") for i in range(260)}
    recovered = open_index(database, tmp_path, OfflineProvider())
    assert set(recovered._index.keys) == keys


async def test_failed_native_add_still_has_recoverable_payload(database, tmp_path, monkeypatch):
    index = open_index(database, tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("injected native add failure")
    monkeypatch.setattr(index._index, "add", fail)
    with pytest.raises(RuntimeError, match="injected"):
        await index.index("one", "first", "alice")
    recovered = open_index(database, tmp_path, OfflineProvider())
    assert len(recovered._index) == 1


@pytest.mark.parametrize("operation", ["delete", "clear"])
async def test_old_snapshot_cannot_resurrect_deleted_vectors(database, tmp_path, operation):
    index = open_index(database, tmp_path, save_interval=1)
    first = await index.index("one", "first", "alice")
    second = await index.index("two", "second", "alice")
    snapshot = (tmp_path / "vectors.usearch").read_bytes()
    if operation == "delete":
        await index.delete_by_node_id("one")
        expected = {second}
    else:
        await index.clear()
        expected = set()
    # Simulate termination after DB commit but before snapshot replacement.
    (tmp_path / "vectors.usearch").write_bytes(snapshot)
    recovered = open_index(database, tmp_path, OfflineProvider())
    assert set(recovered._index.keys) == expected
    assert first not in recovered._index.keys
    assert set(open_index(database, tmp_path, OfflineProvider())._index.keys) == expected


async def test_failed_snapshot_write_preserves_previous_complete_file(database, tmp_path, monkeypatch):
    index = open_index(database, tmp_path, save_interval=1)
    await index.index("one", "first", "alice")
    before = (tmp_path / "vectors.usearch").read_bytes()
    def fail(path):
        Path(path).write_bytes(b"incomplete native snapshot")
        raise OSError("injected disk failure")
    monkeypatch.setattr(index._index, "save", fail)
    with pytest.raises(OSError, match="injected"):
        await index.index("two", "second", "alice")
    assert (tmp_path / "vectors.usearch").read_bytes() == before
    assert not list(tmp_path.glob(".prme-vector-*"))
    recovered = open_index(database, tmp_path, OfflineProvider())
    assert len(recovered._index) == 2


async def test_legacy_pack_backfills_numerical_payload_before_future_loss(database, tmp_path):
    index = open_index(database, tmp_path, save_interval=1)
    key = await index.index("one", "first", "alice")
    database.execute("DROP TABLE vector_payloads")
    upgraded = open_index(database, tmp_path, OfflineProvider())
    payload = database.execute("SELECT vector_data FROM vector_payloads").fetchone()[0]
    assert payload is not None
    (tmp_path / "vectors.usearch").unlink()
    recovered = open_index(database, tmp_path, OfflineProvider())
    np.testing.assert_array_equal(recovered._index.get(key), upgraded._index.get(key))


async def test_missing_legacy_vectors_are_reported_without_fabricating_embeddings(database, tmp_path, caplog):
    index = open_index(database, tmp_path)
    await index.index("one", "first", "alice")
    database.execute("DELETE FROM vector_payloads")
    recovered = open_index(database, tmp_path, OfflineProvider())
    assert len(recovered._index) == 0
    assert "1 legacy embeddings" in caplog.text
    assert "prme rebuild" in caplog.text


@pytest.mark.parametrize("vectors", [[], [[1, 2]], [[1, 2, float("nan")]], [[1, 2, float("inf")]], [[1, 2, 3], [1, 2, 3]]])
async def test_invalid_embedding_output_cannot_commit_metadata(database, tmp_path, vectors):
    class BadProvider(FixedProvider):
        async def embed(self, texts):
            return vectors
    index = open_index(database, tmp_path, BadProvider())
    with pytest.raises(ValueError, match="Embedding provider"):
        await index.index("one", "first", "alice")
    assert database.execute("SELECT count(*) FROM vector_metadata").fetchone()[0] == 0
    assert len(index._index) == 0


async def test_invalid_durable_payload_is_reported(database, tmp_path):
    index = open_index(database, tmp_path)
    await index.index("one", "first", "alice")
    database.execute("UPDATE vector_payloads SET vector_data = ?", [np.array([1, 2], dtype="<f4").tobytes()])
    with pytest.raises(ValueError, match="Cannot restore vector payload"):
        open_index(database, tmp_path, OfflineProvider())


async def test_payload_failure_rolls_back_metadata(database, tmp_path):
    index = open_index(database, tmp_path)
    class FailingConnection:
        def execute(self, sql, *args):
            if sql.startswith("INSERT INTO vector_payloads"):
                raise RuntimeError("injected payload failure")
            return database.execute(sql, *args)
    index._conn = FailingConnection()
    with pytest.raises(RuntimeError, match="injected payload failure"):
        await index.index("one", "first", "alice")
    assert database.execute("SELECT count(*) FROM vector_metadata").fetchone()[0] == 0
    assert database.execute("SELECT count(*) FROM vector_payloads").fetchone()[0] == 0
    assert len(index._index) == 0


@pytest.mark.parametrize("worker_fails", [False, True])
async def test_cancelled_native_write_keeps_locks_until_worker_finishes(database, tmp_path, monkeypatch, worker_fails):
    index = open_index(database, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = index._do_index
    def blocked(*args):
        entered.set()
        assert release.wait(timeout=10)
        if worker_fails:
            raise RuntimeError("injected worker failure")
        return original(*args)
    monkeypatch.setattr(index, "_do_index", blocked)
    writing = asyncio.create_task(index.index("one", "first", "alice"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        writing.cancel()
        await asyncio.sleep(0)
        writing.cancel()
        await asyncio.sleep(0)
        assert not writing.done()
        assert index._write_lock.locked() and index._conn_lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await writing
    assert not index._write_lock.locked() and not index._conn_lock.locked()
    assert len(index._index) == (0 if worker_fails else 1)
    await index.close()
