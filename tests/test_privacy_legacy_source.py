"""Actual native/Fernet sources, no-spill failures, and unchanged originals."""

from __future__ import annotations

import hashlib
import socket
from uuid import uuid4

import duckdb
import numpy as np
import pytest
from usearch.index import Index

from prme import MemoryEngine, PRMEConfig
from prme.privacy.portability import PrivacyPortabilityError
from prme.storage import privacy_source
from prme.storage.privacy_source import open_privacy_source, read_legacy_bytes
from tests.test_durable_ingestion import MockEmbeddingProvider

KEY = "passphrase:task-owned-source-reader-fixture"
TEXT = "The migration control memory is retained for seven days."


def files(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


@pytest.fixture
async def closed_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    path = tmp_path / "legacy"
    config = PRMEConfig(
        database_url=None, db_path=str(path / "memory.duckdb"),
        vector_path=str(path / "vectors.usearch"), lexical_path=str(path / "lexical_index"),
        encryption_enabled=True, encryption_key=KEY, duckdb_threads=1,
    )
    engine = await MemoryEngine.create(config)
    try:
        owner = str(uuid4())
        await engine.store(TEXT, user_id=owner)
        node_id = engine._conn.execute("SELECT id FROM nodes WHERE user_id=? AND content=?", [owner, TEXT]).fetchone()[0]
        assert any(item.node.content == TEXT for item in (await engine.retrieve("migration control", user_id=owner)).results)
        rows = engine._conn.execute("SELECT vector_key, vector_data FROM vector_payloads ORDER BY vector_key").fetchall()
        # An intact legacy index-only record requires no source-writing backfill.
        engine._conn.execute("DELETE FROM vector_payloads")
    finally:
        await engine.close()
    assert (path / "memory.duckdb.enc").is_file()
    assert not (path / "memory.duckdb").exists()
    assert rows
    return path, node_id, rows


async def test_closed_fernet_native_read_and_exact_legacy_vectors(closed_legacy, monkeypatch):
    path, node_id, rows = closed_legacy
    before = files(path)
    def deny_network(*args, **kwargs):
        pytest.fail("source reader attempted network access")
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    with open_privacy_source(path, encryption_key=KEY) as source:
        assert source.execute("SELECT content FROM nodes WHERE id=?", [str(node_id)]).fetchone() == (TEXT,)
        assert source.execute("SELECT count(*) FROM vector_payloads").fetchone() == (0,)
        assert source.execute("SELECT current_setting('temp_directory')").fetchone() == ("",)
        assert source.execute("SELECT current_setting('enable_external_access')").fetchone() == (False,)
        assert source.execute("SELECT current_setting('autoload_known_extensions')").fetchone() == (False,)
        assert source.execute("SELECT current_setting('autoinstall_known_extensions')").fetchone() == (False,)
        with pytest.raises(duckdb.Error):
            source.execute("INSERT INTO nodes (id) VALUES (?)", [str(uuid4())])
        with pytest.raises(duckdb.Error):
            source.execute("SELECT * FROM read_text('https://example.invalid/source')")
    native = Index(ndim=384, metric="cos", dtype="f32")
    native.load(read_legacy_bytes(path / "vectors.usearch.enc", encryption_key=KEY))
    for key, payload in rows:
        np.testing.assert_array_equal(native.get(key), np.frombuffer(payload, dtype="<f4"))
    assert files(path) == before


@pytest.mark.parametrize("variant,code", [
    ("wrong_key", "privacy_source_key_unavailable"),
    ("corrupt", "privacy_source_key_unavailable"),
    ("oversize", "privacy_source_capacity"),
    ("wal", "privacy_source_not_quiescent"),
    ("encrypted_wal", "privacy_source_not_quiescent"),
    ("ambiguous", "privacy_source_not_quiescent"),
])
async def test_source_failure_is_fixed_and_never_mutates(closed_legacy, variant, code):
    path, _, _ = closed_legacy
    options = {"encryption_key": KEY}
    if variant == "wrong_key":
        options["encryption_key"] = "passphrase:wrong"
    if variant == "corrupt":
        encrypted = path / "memory.duckdb.enc"
        encrypted.write_bytes(encrypted.read_bytes()[:-16] + b"invalid fixture!")
    if variant == "oversize":
        options["max_bytes"] = 16
    if variant in ("wal", "encrypted_wal"):
        (path / ("memory.duckdb.wal" + (".enc" if variant == "encrypted_wal" else ""))).write_bytes(b"pending")
    if variant == "ambiguous":
        (path / "memory.duckdb").write_bytes(b"competing plaintext")
    before = files(path)
    with pytest.raises(PrivacyPortabilityError, match=f"^{code}$"):
        with open_privacy_source(path, **options):
            pytest.fail("invalid source was opened")
    assert files(path) == before


async def test_memory_pressure_refuses_without_spill_and_releases_buffers(closed_legacy, monkeypatch):
    path, _, _ = closed_legacy
    before = files(path.parent)
    released = []
    original_release = privacy_source._SourceFileSystem.release
    def release(filesystem):
        original_release(filesystem)
        released.append(filesystem)
    monkeypatch.setattr(privacy_source._SourceFileSystem, "release", release)
    with pytest.raises(PrivacyPortabilityError, match="^privacy_source_capacity$"):
        with open_privacy_source(path, encryption_key=KEY, memory_limit_mb=16) as source:
            source.execute("SELECT list(i) FROM range(2000000) t(i)").fetchone()
    assert released and released[0]._payload is None and released[0]._handles == []
    assert files(path.parent) == before
    # A failed operation cannot leave a stale registered filesystem for the next.
    with open_privacy_source(path, encryption_key=KEY) as source:
        assert source.execute("SELECT count(*) FROM nodes").fetchone()[0] > 0
