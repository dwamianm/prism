"""Encrypted single-file memory packs (issue #247, phase 2).

With ``encryption_enabled`` and ``encryption_key`` set, a single-file pack is
encrypted by DuckDB itself: the engine attaches the file to a private
in-memory DuckDB instance with a key from ``derive_pack_key()``. These tests
check that the pack round-trips with its key and opens from a copy, that it
does not open without its key, with a wrong key or as a plain pack with a
key, and that such a failure leaves the file and its WAL unchanged. They also
check that stored text is not in clear text in the file or its WAL while the
engine runs, that rebuilds, ingestion and the derivation stage fences work on
the attached pack, and that the key derivation is stable. Directory packs
keep Fernet file encryption, covered by ``tests/test_encryption.py``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import traceback
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from prme import MemoryClient, MemoryEngine, PRMEConfig
from prme.storage.encryption import (
    _PACK_KEY_SALT,
    _PBKDF2_ITERATIONS,
    EncryptionError,
    EncryptionProvider,
    derive_pack_key,
)
from tests import test_derivation_ingestion, test_durable_ingestion, test_obsolete_derivation_staging
from tests.test_derivation_planning import extraction
from tests.test_durable_ingestion import MockEmbeddingProvider
from tests.test_single_file_pack import (
    MEMORIES,
    USER,
    client_read_back,
    lexical_hits,
    read_back,
    store_all,
    vector_hits,
)

PASSPHRASE = "correct horse battery staple"
MARKER = "ZEBRAPLUM7Q4XLOCKERMARKER"


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    monkeypatch.setattr(
        "prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider(),
    )
    # Opportunistic maintenance could change scores between two reads.
    monkeypatch.setenv("PRME_ORGANIZER_OPPORTUNISTIC_ENABLED", "false")


def encrypted_config(path: Path, key: str = PASSPHRASE, **overrides) -> PRMEConfig:
    return PRMEConfig(pack_path=str(path), encryption_enabled=True, encryption_key=key, **overrides)


def digest(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def node_count(engine: MemoryEngine) -> int:
    return engine._conn.execute("SELECT count(*) FROM nodes WHERE user_id = ?", [USER]).fetchone()[0]


@pytest.fixture
def encrypted_pack(tmp_path) -> Path:
    """A closed encrypted pack that holds MEMORIES."""
    pack = tmp_path / "memories.prme"
    with MemoryClient(config=encrypted_config(pack)) as client:
        for text in MEMORIES:
            client.store(text, user_id=USER)
    return pack


# --- Round trip ------------------------------------------------------------


def test_client_round_trips_an_encrypted_pack(tmp_path):
    pack = tmp_path / "memories.prme"
    with MemoryClient(config=encrypted_config(pack)) as client:
        for text in MEMORIES:
            client.store(text, user_id=USER)
        before = client_read_back(client)
    assert all(results for results, _receipt in before.values())
    # Encryption adds no files: at rest the pack is still one file.
    assert os.listdir(tmp_path) == ["memories.prme"]

    with MemoryClient(config=encrypted_config(pack)) as client:
        assert client_read_back(client) == before
    assert os.listdir(tmp_path) == ["memories.prme"]


async def test_copy_opens_elsewhere_with_the_same_passphrase(tmp_path):
    # The passphrase salt is fixed, so the key does not depend on the file.
    original = tmp_path / "original" / "memories.prme"
    async with MemoryEngine.open(encrypted_config(original)) as engine:
        await store_all(engine)
        before = await read_back(engine)

    copy = tmp_path / "elsewhere" / "copy.prme"
    copy.parent.mkdir()
    shutil.copyfile(original, copy)
    async with MemoryEngine.open(encrypted_config(copy, key="passphrase:" + PASSPHRASE)) as engine:
        assert await read_back(engine) == before


async def test_rebuild_indexes_on_an_encrypted_pack(tmp_path):
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(encrypted_config(pack)) as engine:
        await store_all(engine)
        before = await read_back(engine)

    async with MemoryEngine.open(encrypted_config(pack)) as engine:
        lexical_after_open = await lexical_hits(engine)
        vectors_after_open = await vector_hits(engine)
        summary = await engine.rebuild_indexes()
        assert summary == {"nodes_indexed": len(MEMORIES), "nodes_skipped": 0, "total_active": len(MEMORIES)}
        assert await lexical_hits(engine) == lexical_after_open
        assert await vector_hits(engine) == vectors_after_open
        assert await read_back(engine) == before
    async with MemoryEngine.open(encrypted_config(pack)) as engine:
        assert await read_back(engine) == before


# --- On disk ---------------------------------------------------------------


def pack_files(pack: Path) -> list[Path]:
    return [path for path in (pack, Path(f"{pack}.wal")) if path.exists()]


async def test_stored_text_is_not_in_clear_on_disk_while_open(tmp_path):
    # Control: the same write to a plain pack is readable on disk, so the
    # byte search below would find clear text if there were any.
    plain = tmp_path / "plain" / "memories.prme"
    async with MemoryEngine.open(PRMEConfig(pack_path=str(plain))) as engine:
        await engine.store(f"The locker code is {MARKER}", user_id=USER)
        assert any(MARKER.encode() in path.read_bytes() for path in pack_files(plain))

    pack = tmp_path / "secret" / "memories.prme"
    async with MemoryEngine.open(encrypted_config(pack)) as engine:
        await engine.store(f"The locker code is {MARKER}", user_id=USER)
        # Nothing has been checkpointed since the write: it is in the WAL.
        wal = Path(f"{pack}.wal")
        assert wal.stat().st_size > 0
        for path in (pack, wal):
            assert MARKER.encode() not in path.read_bytes()
        results = (await engine.retrieve("locker code", user_id=USER)).results
        assert any(MARKER in item.node.content for item in results)
    assert os.listdir(pack.parent) == ["memories.prme"]
    assert MARKER.encode() not in pack.read_bytes()


async def test_encrypted_connection_is_set_up_for_the_pack(tmp_path):
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(encrypted_config(pack, duckdb_threads=2)) as engine:
        await engine.store(MEMORIES[0], user_id=USER)
        conn = engine._conn
        assert conn.execute(
            "SELECT encrypted, cipher FROM duckdb_databases() WHERE database_name = current_database()"
        ).fetchone() == (True, "GCM")
        # DuckDB writes temporary files in clear text unless this is on.
        assert conn.execute("SELECT current_setting('temp_file_encryption')").fetchone() == (True,)
        assert conn.execute("SELECT current_setting('temp_directory')").fetchone() == (f"{pack}.tmp",)
        assert conn.execute("SELECT current_setting('threads')").fetchone() == (2,)
        # A cursor is a new connection; it must still land on the pack.
        with conn.cursor() as cursor:
            assert cursor.execute("SELECT current_database()").fetchone() == ("pack",)
            assert cursor.execute("SELECT count(*) FROM nodes").fetchone() == (1,)
        # No file-level encryption is wired for a single-file pack.
        assert engine._encryption_provider is None
        assert not engine._atexit_registered
        for action in (engine.lock, engine.unlock):
            with pytest.raises(RuntimeError, match="single-file pack has no file-level encryption"):
                action()


# --- Refusals ----------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [{}, {"encryption_key": PASSPHRASE}, {"encryption_enabled": True}],
    ids=["no-key", "key-not-enabled", "enabled-without-key"],
)
async def test_encrypted_pack_does_not_open_without_its_key(encrypted_pack, overrides):
    before = digest(encrypted_pack)
    with pytest.raises(EncryptionError, match="is encrypted; set encryption_enabled and encryption_key"):
        await MemoryEngine.create(PRMEConfig(pack_path=str(encrypted_pack), **overrides))
    assert digest(encrypted_pack) == before
    assert os.listdir(encrypted_pack.parent) == [encrypted_pack.name]


def test_client_does_not_open_an_encrypted_pack_without_its_key(encrypted_pack):
    before = digest(encrypted_pack)
    with pytest.raises(EncryptionError):
        MemoryClient(str(encrypted_pack))
    assert digest(encrypted_pack) == before
    assert os.listdir(encrypted_pack.parent) == [encrypted_pack.name]


@pytest.mark.parametrize(
    "wrong_key",
    ["not the passphrase", "raw_key:" + Fernet.generate_key().decode("ascii")],
    ids=["passphrase", "raw-key"],
)
async def test_wrong_key_is_refused_and_leaves_the_pack_unchanged(encrypted_pack, wrong_key):
    before = digest(encrypted_pack)
    with pytest.raises(EncryptionError, match="Wrong encryption key") as raised:
        await MemoryEngine.create(encrypted_config(encrypted_pack, key=wrong_key))
    assert digest(encrypted_pack) == before
    assert os.listdir(encrypted_pack.parent) == [encrypted_pack.name]
    # Neither the configured secrets nor the keys handed to DuckDB are reported.
    reported = "".join(traceback.format_exception(raised.value))
    for secret in (wrong_key, derive_pack_key(wrong_key), PASSPHRASE, derive_pack_key(PASSPHRASE)):
        assert secret not in reported

    # The failed attempt released the file, and the right key still opens it.
    async with MemoryEngine.open(encrypted_config(encrypted_pack)) as engine:
        assert node_count(engine) == len(MEMORIES)


async def test_wrong_key_leaves_an_unclean_pack_and_its_wal_unchanged(tmp_path):
    pack = tmp_path / "live" / "memories.prme"
    crashed = tmp_path / "crashed" / "memories.prme"
    crashed.parent.mkdir()
    async with MemoryEngine.open(encrypted_config(pack)) as engine:
        await store_all(engine)
        # Copy the file and its WAL as a crash would leave them.
        shutil.copyfile(pack, crashed)
        shutil.copyfile(f"{pack}.wal", f"{crashed}.wal")

    before = (digest(crashed), digest(f"{crashed}.wal"))
    with pytest.raises(EncryptionError, match="Wrong encryption key"):
        await MemoryEngine.create(encrypted_config(crashed, key="not the passphrase"))
    assert (digest(crashed), digest(f"{crashed}.wal")) == before

    # The right key replays the WAL.
    async with MemoryEngine.open(encrypted_config(crashed)) as engine:
        assert node_count(engine) == len(MEMORIES)
    assert os.listdir(crashed.parent) == ["memories.prme"]


async def test_plain_pack_with_a_key_is_refused_and_left_unchanged(tmp_path):
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(PRMEConfig(pack_path=str(pack))) as engine:
        await store_all(engine)
    before = digest(pack)
    with pytest.raises(EncryptionError, match="is not encrypted"):
        await MemoryEngine.create(encrypted_config(pack))
    assert digest(pack) == before
    assert os.listdir(tmp_path) == ["memories.prme"]


async def test_malformed_raw_key_is_refused_before_any_file_is_touched(tmp_path):
    pack = tmp_path / "new" / "memories.prme"
    with pytest.raises(ValueError, match="32 url-safe base64-encoded bytes"):
        await MemoryEngine.create(encrypted_config(pack, key="raw_key:too-short"))
    assert not pack.parent.exists()


# --- Key derivation ----------------------------------------------------------


class TestDerivePackKey:
    def test_passphrase_gives_a_stable_pbkdf2_key(self):
        key = derive_pack_key(PASSPHRASE)
        assert len(key) == 64 and key == key.lower()
        assert bytes.fromhex(key) == hashlib.pbkdf2_hmac(
            "sha256", PASSPHRASE.encode("utf-8"), _PACK_KEY_SALT, _PBKDF2_ITERATIONS, dklen=32,
        )
        assert derive_pack_key(PASSPHRASE) == key
        assert derive_pack_key("passphrase:" + PASSPHRASE) == key
        assert derive_pack_key(PASSPHRASE + "!") != key

    def test_derivation_is_pinned(self):
        # Existing encrypted packs open only while this value holds; a new
        # derivation needs a new versioned salt.
        assert _PACK_KEY_SALT == b"prme-single-file-pack-v1"
        assert derive_pack_key("a passphrase") == (
            "a46eea357671c5321d18acbbb86cfa6f334de004d9c510c2bdf9c6d2c74dc8bf"
        )

    def test_raw_keys_give_their_bytes(self):
        fernet_key = Fernet.generate_key().decode("ascii")
        expected = base64.urlsafe_b64decode(fernet_key).hex()
        assert derive_pack_key("raw_key:" + fernet_key) == expected
        # An unprefixed 44-character Fernet key is read as a raw key too.
        assert derive_pack_key(fernet_key) == expected
        # The passphrase prefix derives even a value that looks like a key.
        assert derive_pack_key("passphrase:" + fernet_key) != expected

    def test_key_forms_are_read_as_directory_packs_read_them(self):
        fernet_key = Fernet.generate_key().decode("ascii")
        for key in (PASSPHRASE, "passphrase:" + PASSPHRASE, fernet_key,
                    "raw_key:" + fernet_key, "passphrase:" + fernet_key):
            provider = EncryptionProvider(key)
            if provider._fernet_key is not None:
                expected = base64.urlsafe_b64decode(provider._fernet_key)
            else:
                expected = hashlib.pbkdf2_hmac(
                    "sha256", provider._passphrase.encode("utf-8"), _PACK_KEY_SALT,
                    _PBKDF2_ITERATIONS, dklen=32,
                )
            assert derive_pack_key(key) == expected.hex()

    @pytest.mark.parametrize("key", ["", "passphrase:", "raw_key:", "raw_key:too-short"])
    def test_unusable_keys_are_refused(self, key):
        with pytest.raises(ValueError):
            derive_pack_key(key)


# --- Ingestion and derivation stage fences -----------------------------------

user = test_durable_ingestion.user


@pytest.fixture
def config(tmp_path):
    """An encrypted single-file pack in place of the durable-ingestion pack."""
    return encrypted_config(
        tmp_path / "derivations.prme",
        organizer={"opportunistic_enabled": False},
        materialization_budget_ms=5000,
    )


async def test_ingest_publishes_through_the_stage_fences(config, user):
    source = "Alice switched from Python to Rust. Alice knows Bob."
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=extraction(source, old=None))
        event_id = await engine.ingest(source, user_id=user, wait_for_extraction=True)
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        receipt = await engine._event_store.get_derivation_receipt(event_id, user_id=user)
        assert receipt.plan_id == plan.id and receipt.plan_checksum == plan.checksum
        nodes = {node.id for node in await engine.get_event_nodes(event_id, user_id=user)}
        assert nodes == {node.id for node in plan.nodes}
        lexical = {hit["node_id"] for hit in await engine._lexical_index.search("Rust", user, limit=50)}
        vectors = {hit["node_id"] for hit in await engine._vector_index.search("Rust", user, k=50)}
        assert lexical and lexical <= {str(node_id) for node_id in nodes}
        assert vectors and vectors <= {str(node_id) for node_id in nodes}

    # After a reopen the lexical index is rebuilt from the pack's graph.
    async with MemoryEngine.open(config) as engine:
        assert {node.id for node in await engine.get_event_nodes(event_id, user_id=user)} == nodes
        assert {hit["node_id"] for hit in await engine._lexical_index.search("Rust", user, limit=50)} == lexical
        assert {hit["node_id"] for hit in await engine._vector_index.search("Rust", user, k=50)} == vectors


# Collected here, these contract tests run against the encrypted pack from
# the fixtures above: public ingestion that fails at every derivation
# boundary, including the vector and lexical stage fences, and recovers after
# a reopen, and a native stage that keeps its fence through cancellation
# while another cursor tries to claim the work.
test_public_ingestion_restart_reuses_journaled_inputs = (
    test_derivation_ingestion.test_public_ingestion_restart_reuses_journaled_inputs
)
test_cancellation_does_not_release_native_stage_fence_early = (
    test_obsolete_derivation_staging.test_cancellation_does_not_release_native_stage_fence_early
)
