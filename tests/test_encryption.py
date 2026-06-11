"""Tests for encryption at rest (issue #14, RFC-0014 S10).

Validates:
- Key derivation from passphrase (PBKDF2-HMAC-SHA256)
- Encrypt/decrypt roundtrip for single file
- Encrypt/decrypt for directory
- Encrypted files are not readable as plain text
- Full engine lifecycle with encryption (create -> store -> close -> reopen -> retrieve)
- Wrong passphrase fails gracefully
- Encryption disabled by default (backward compatible)
- Manifest.json generation
- lock()/unlock() convenience methods
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from prme.storage.encryption import (
    EncryptionError,
    EncryptionProvider,
    read_manifest,
    write_manifest,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_enc_") as d:
        yield Path(d)


@pytest.fixture
def passphrase():
    return "test-passphrase-for-prme-encryption"


@pytest.fixture
def provider(passphrase):
    return EncryptionProvider(passphrase)


@pytest.fixture
def sample_file(tmp_dir):
    """Create a sample file with known content."""
    f = tmp_dir / "sample.txt"
    f.write_text("Hello, PRME encryption test!")
    return f


@pytest.fixture
def sample_dir(tmp_dir):
    """Create a directory with multiple files."""
    d = tmp_dir / "test_dir"
    d.mkdir()
    (d / "file1.txt").write_text("Content of file 1")
    (d / "file2.dat").write_bytes(b"\x00\x01\x02\x03" * 100)
    (d / "file3.json").write_text('{"key": "value"}')
    return d


# ---------------------------------------------------------------------------
# Key derivation tests
# ---------------------------------------------------------------------------


class TestKeyDerivation:
    """PBKDF2-HMAC-SHA256 key derivation from passphrase."""

    def test_derive_key_returns_valid_fernet_key(self, passphrase):
        key, salt = EncryptionProvider.derive_key(passphrase)
        # Fernet keys are 44 bytes of url-safe base64
        assert len(key) == 44
        assert isinstance(key, bytes)
        assert isinstance(salt, bytes)
        assert len(salt) == 16

    def test_derive_key_deterministic_with_same_salt(self, passphrase):
        key1, salt = EncryptionProvider.derive_key(passphrase)
        key2, _ = EncryptionProvider.derive_key(passphrase, salt=salt)
        assert key1 == key2

    def test_derive_key_different_with_different_salt(self, passphrase):
        key1, salt1 = EncryptionProvider.derive_key(passphrase)
        key2, salt2 = EncryptionProvider.derive_key(passphrase)
        # Random salts should differ (astronomically unlikely to collide)
        assert salt1 != salt2
        assert key1 != key2

    def test_derive_key_different_passphrases_differ(self):
        key1, salt = EncryptionProvider.derive_key("passphrase-a")
        key2, _ = EncryptionProvider.derive_key("passphrase-b", salt=salt)
        assert key1 != key2

    def test_derive_key_with_explicit_salt(self, passphrase):
        salt = b"\xaa" * 16
        key, returned_salt = EncryptionProvider.derive_key(passphrase, salt=salt)
        assert returned_salt == salt
        assert len(key) == 44


# ---------------------------------------------------------------------------
# Single file encrypt/decrypt
# ---------------------------------------------------------------------------


class TestFileEncryption:
    """Encrypt and decrypt single files."""

    def test_encrypt_file_creates_enc_file(self, provider, sample_file):
        enc_path = provider.encrypt_file(sample_file)
        assert enc_path.suffix == ".enc"
        assert enc_path.exists()
        assert not sample_file.exists()  # Original removed

    def test_encrypt_decrypt_roundtrip(self, provider, sample_file):
        original_content = sample_file.read_text()
        enc_path = provider.encrypt_file(sample_file)
        dec_path = provider.decrypt_file(enc_path)
        assert dec_path.read_text() == original_content
        assert not enc_path.exists()  # Encrypted file removed

    def test_encrypted_file_not_readable_as_plaintext(self, provider, sample_file):
        original_content = sample_file.read_text()
        enc_path = provider.encrypt_file(sample_file)
        encrypted_bytes = enc_path.read_bytes()
        # The encrypted content should not contain the original text
        assert original_content.encode() not in encrypted_bytes

    def test_encrypt_nonexistent_file_raises(self, provider, tmp_dir):
        with pytest.raises(FileNotFoundError):
            provider.encrypt_file(tmp_dir / "nonexistent.txt")

    def test_decrypt_nonexistent_file_raises(self, provider, tmp_dir):
        with pytest.raises(FileNotFoundError):
            provider.decrypt_file(tmp_dir / "nonexistent.enc")

    def test_encrypt_already_encrypted_skips(self, provider, sample_file):
        enc_path = provider.encrypt_file(sample_file)
        # Encrypting an already-encrypted file should be a no-op
        result = provider.encrypt_file(enc_path)
        assert result == enc_path

    def test_decrypt_non_enc_file_skips(self, provider, sample_file):
        # Decrypting a non-.enc file should be a no-op
        result = provider.decrypt_file(sample_file)
        assert result == sample_file

    def test_encrypt_binary_file(self, provider, tmp_dir):
        binary_file = tmp_dir / "data.bin"
        original = bytes(range(256)) * 10
        binary_file.write_bytes(original)
        enc_path = provider.encrypt_file(binary_file)
        dec_path = provider.decrypt_file(enc_path)
        assert dec_path.read_bytes() == original


# ---------------------------------------------------------------------------
# Directory encrypt/decrypt
# ---------------------------------------------------------------------------


class TestDirectoryEncryption:
    """Encrypt and decrypt directories."""

    def test_encrypt_directory(self, provider, sample_dir):
        enc_files = provider.encrypt_directory(sample_dir)
        assert len(enc_files) == 3
        # All original files should be gone
        plain_files = [f for f in sample_dir.iterdir() if f.suffix != ".enc"]
        assert len(plain_files) == 0

    def test_encrypt_decrypt_directory_roundtrip(self, provider, sample_dir):
        # Read original contents
        originals = {}
        for f in sorted(sample_dir.iterdir()):
            originals[f.name] = f.read_bytes()

        # Encrypt and decrypt
        provider.encrypt_directory(sample_dir)
        provider.decrypt_directory(sample_dir)

        # Verify contents match
        for f in sorted(sample_dir.iterdir()):
            assert f.name in originals, f"Unexpected file: {f.name}"
            assert f.read_bytes() == originals[f.name]

    def test_encrypt_nonexistent_dir_raises(self, provider, tmp_dir):
        with pytest.raises(FileNotFoundError):
            provider.encrypt_directory(tmp_dir / "nonexistent_dir")

    def test_decrypt_nonexistent_dir_raises(self, provider, tmp_dir):
        with pytest.raises(FileNotFoundError):
            provider.decrypt_directory(tmp_dir / "nonexistent_dir")

    def test_encrypt_empty_directory(self, provider, tmp_dir):
        empty_dir = tmp_dir / "empty"
        empty_dir.mkdir()
        enc_files = provider.encrypt_directory(empty_dir)
        assert len(enc_files) == 0


# ---------------------------------------------------------------------------
# Wrong passphrase
# ---------------------------------------------------------------------------


class TestWrongPassphrase:
    """Verify that wrong passphrase fails gracefully."""

    def test_wrong_passphrase_decrypt_fails(self, sample_file):
        provider_a = EncryptionProvider("correct-passphrase")
        provider_b = EncryptionProvider("wrong-passphrase")

        enc_path = provider_a.encrypt_file(sample_file)
        with pytest.raises(EncryptionError, match="invalid key or corrupt"):
            provider_b.decrypt_file(enc_path)

    def test_wrong_passphrase_directory_fails(self, sample_dir):
        provider_a = EncryptionProvider("correct-passphrase")
        provider_b = EncryptionProvider("wrong-passphrase")

        provider_a.encrypt_directory(sample_dir)
        with pytest.raises(EncryptionError):
            provider_b.decrypt_directory(sample_dir)


# ---------------------------------------------------------------------------
# Provider initialization
# ---------------------------------------------------------------------------


class TestProviderInit:
    """EncryptionProvider initialization edge cases."""

    def test_none_key_raises(self):
        with pytest.raises(ValueError, match="key or passphrase must be provided"):
            EncryptionProvider(None)

    def test_passphrase_string(self):
        p = EncryptionProvider("my-passphrase")
        assert p._passphrase == "my-passphrase"

    def test_raw_fernet_key_bytes(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        p = EncryptionProvider(key)
        assert p._fernet_key == key
        assert p._passphrase is None

    def test_raw_fernet_key_string(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        p = EncryptionProvider(key)
        assert p._fernet_key is not None
        assert p._passphrase is None


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class TestManifest:
    """manifest.json generation and reading."""

    def test_write_manifest(self, tmp_dir):
        files = [tmp_dir / "a.duckdb.enc", tmp_dir / "b.usearch.enc"]
        manifest_path = write_manifest(tmp_dir, files)
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["encryption"]["enabled"] is True
        assert manifest["encryption"]["algorithm"] == "Fernet-AES128-CBC-HMAC-SHA256"
        assert manifest["encryption"]["kdf"] == "PBKDF2-HMAC-SHA256"
        assert len(manifest["encryption"]["files"]) == 2

    def test_read_manifest(self, tmp_dir):
        files = [tmp_dir / "test.enc"]
        write_manifest(tmp_dir, files)
        manifest = read_manifest(tmp_dir)
        assert manifest is not None
        assert manifest["encryption"]["enabled"] is True

    def test_read_manifest_missing(self, tmp_dir):
        assert read_manifest(tmp_dir) is None


# ---------------------------------------------------------------------------
# Engine lifecycle with encryption
# ---------------------------------------------------------------------------


class TestEngineEncryptionLifecycle:
    """Full engine lifecycle: create -> store -> close -> reopen -> retrieve."""

    @pytest.fixture
    def enc_dir(self, tmp_dir):
        lexical = tmp_dir / "lexical_index"
        lexical.mkdir()
        return tmp_dir

    @pytest.fixture
    def enc_config(self, enc_dir):
        from prme.config import PRMEConfig

        return PRMEConfig(
            db_path=str(enc_dir / "memory.duckdb"),
            vector_path=str(enc_dir / "vectors.usearch"),
            lexical_path=str(enc_dir / "lexical_index"),
            encryption_enabled=True,
            encryption_key="test-engine-passphrase",
        )

    @pytest.fixture
    def plain_config(self, enc_dir):
        from prme.config import PRMEConfig

        return PRMEConfig(
            db_path=str(enc_dir / "memory.duckdb"),
            vector_path=str(enc_dir / "vectors.usearch"),
            lexical_path=str(enc_dir / "lexical_index"),
            encryption_enabled=False,
        )

    @pytest.mark.asyncio
    async def test_engine_create_store_close_encrypts(self, enc_config, enc_dir):
        """After close(), files should be encrypted (.enc extension)."""
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(enc_config)
        await engine.store(
            content="Test content for encryption",
            user_id="user-1",
        )
        await engine.close()

        # DuckDB file should be encrypted
        assert (enc_dir / "memory.duckdb.enc").exists()
        assert not (enc_dir / "memory.duckdb").exists()

    @pytest.mark.asyncio
    async def test_engine_reopen_decrypts(self, enc_config, enc_dir):
        """Creating engine again should decrypt and work normally."""
        from prme.storage.engine import MemoryEngine

        # First cycle: store and close (encrypts)
        engine = await MemoryEngine.create(enc_config)
        await engine.store(
            content="Persistent encrypted content",
            user_id="user-1",
        )
        await engine.close()

        # Verify encrypted
        assert (enc_dir / "memory.duckdb.enc").exists()

        # Second cycle: reopen (decrypts) and verify data exists
        engine2 = await MemoryEngine.create(enc_config)
        results = await engine2.retrieve(
            query="Persistent encrypted content",
            user_id="user-1",
        )
        await engine2.close()

        # Should find the stored content
        assert len(results.results) > 0

    @pytest.mark.asyncio
    async def test_encryption_disabled_by_default(self, plain_config, enc_dir):
        """With encryption disabled, files remain unencrypted."""
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(plain_config)
        await engine.store(
            content="Unencrypted content",
            user_id="user-1",
        )
        await engine.close()

        # DuckDB file should NOT be encrypted
        assert (enc_dir / "memory.duckdb").exists()
        assert not (enc_dir / "memory.duckdb.enc").exists()

    @pytest.mark.asyncio
    async def test_wrong_passphrase_on_reopen_fails(self, enc_config, enc_dir):
        """Opening with wrong passphrase should raise EncryptionError."""
        from prme.config import PRMEConfig
        from prme.storage.encryption import EncryptionError
        from prme.storage.engine import MemoryEngine

        # First cycle: store and close (encrypts)
        engine = await MemoryEngine.create(enc_config)
        await engine.store(
            content="Secret content",
            user_id="user-1",
        )
        await engine.close()

        # Try to reopen with wrong passphrase
        wrong_config = PRMEConfig(
            db_path=str(enc_dir / "memory.duckdb"),
            vector_path=str(enc_dir / "vectors.usearch"),
            lexical_path=str(enc_dir / "lexical_index"),
            encryption_enabled=True,
            encryption_key="wrong-passphrase",
        )
        with pytest.raises(EncryptionError):
            await MemoryEngine.create(wrong_config)

    @pytest.mark.asyncio
    async def test_manifest_written_on_close(self, enc_config, enc_dir):
        """manifest.json should be written after encryption."""
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(enc_config)
        await engine.store(
            content="Content for manifest test",
            user_id="user-1",
        )
        await engine.close()

        manifest_path = enc_dir / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["encryption"]["enabled"] is True
        assert len(manifest["encryption"]["files"]) > 0


# ---------------------------------------------------------------------------
# lock() / unlock() convenience methods
# ---------------------------------------------------------------------------


class TestLockUnlock:
    """lock() and unlock() convenience methods on MemoryEngine."""

    @pytest.mark.asyncio
    async def test_lock_without_encryption_raises(self, tmp_dir):
        """lock() on engine without encryption config should raise."""
        from prme.config import PRMEConfig
        from prme.storage.engine import MemoryEngine

        lexical = tmp_dir / "lexical_index"
        lexical.mkdir()
        config = PRMEConfig(
            db_path=str(tmp_dir / "memory.duckdb"),
            vector_path=str(tmp_dir / "vectors.usearch"),
            lexical_path=str(lexical),
        )
        engine = await MemoryEngine.create(config)
        with pytest.raises(RuntimeError, match="encryption is not configured"):
            engine.lock()
        await engine.close()

    @pytest.mark.asyncio
    async def test_unlock_without_encryption_raises(self, tmp_dir):
        """unlock() on engine without encryption config should raise."""
        from prme.config import PRMEConfig
        from prme.storage.engine import MemoryEngine

        lexical = tmp_dir / "lexical_index"
        lexical.mkdir()
        config = PRMEConfig(
            db_path=str(tmp_dir / "memory.duckdb"),
            vector_path=str(tmp_dir / "vectors.usearch"),
            lexical_path=str(lexical),
        )
        engine = await MemoryEngine.create(config)
        with pytest.raises(RuntimeError, match="encryption is not configured"):
            engine.unlock()
        await engine.close()


# ---------------------------------------------------------------------------
# Atomic writes (issue #37)
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    """encrypt_file / decrypt_file write atomically (temp + os.replace)."""

    def test_encrypt_leaves_no_temp_file_on_success(self, provider, tmp_dir):
        f = tmp_dir / "payload.txt"
        f.write_text("plaintext payload")
        enc = provider.encrypt_file(f)
        # No stray temp file, plaintext gone, ciphertext present.
        leftovers = [c.name for c in tmp_dir.iterdir() if ".tmp-" in c.name]
        assert leftovers == []
        assert enc.exists()
        assert not f.exists()

    def test_decrypt_leaves_no_temp_file_on_success(self, provider, tmp_dir):
        f = tmp_dir / "payload.txt"
        f.write_text("plaintext payload")
        enc = provider.encrypt_file(f)
        dec = provider.decrypt_file(enc)
        leftovers = [c.name for c in tmp_dir.iterdir() if ".tmp-" in c.name]
        assert leftovers == []
        assert dec.read_text() == "plaintext payload"
        assert not enc.exists()

    def test_encrypt_failure_preserves_plaintext_and_cleans_temp(
        self, provider, tmp_dir, monkeypatch
    ):
        """If the atomic rename fails, plaintext survives and no temp lingers."""
        f = tmp_dir / "payload.txt"
        f.write_text("important plaintext")

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", boom)
        with pytest.raises(EncryptionError):
            provider.encrypt_file(f)

        # Plaintext is still intact (never destroyed), and no temp remains.
        assert f.exists()
        assert f.read_text() == "important plaintext"
        leftovers = [c.name for c in tmp_dir.iterdir() if ".tmp-" in c.name]
        assert leftovers == []


# ---------------------------------------------------------------------------
# Explicit key-type prefixes (issue #37)
# ---------------------------------------------------------------------------


class TestKeyTypePrefixes:
    """raw_key: / passphrase: prefixes disambiguate key handling."""

    def test_raw_key_prefix_uses_raw_fernet_key(self):
        from cryptography.fernet import Fernet

        raw = Fernet.generate_key().decode("ascii")
        p = EncryptionProvider("raw_key:" + raw)
        assert p._fernet_key == raw.encode("ascii")
        assert p._passphrase is None

    def test_passphrase_prefix_forces_derivation_on_base64(self):
        """A 44-char base64 value with passphrase: prefix is derived, not raw."""
        from cryptography.fernet import Fernet

        looks_like_key = Fernet.generate_key().decode("ascii")
        assert len(looks_like_key) == 44  # would auto-detect as raw without prefix
        p = EncryptionProvider("passphrase:" + looks_like_key)
        assert p._passphrase == looks_like_key
        assert p._fernet_key is None

    def test_unprefixed_autodetect_unchanged_raw(self):
        from cryptography.fernet import Fernet

        raw = Fernet.generate_key().decode("ascii")
        p = EncryptionProvider(raw)
        assert p._fernet_key == raw.encode("ascii")
        assert p._passphrase is None

    def test_unprefixed_autodetect_unchanged_passphrase(self):
        p = EncryptionProvider("a-normal-human-passphrase")
        assert p._passphrase == "a-normal-human-passphrase"
        assert p._fernet_key is None

    def test_raw_key_prefix_roundtrip(self, sample_file):
        from cryptography.fernet import Fernet

        raw = Fernet.generate_key().decode("ascii")
        original = sample_file.read_text()
        enc = EncryptionProvider("raw_key:" + raw).encrypt_file(sample_file)
        dec = EncryptionProvider("raw_key:" + raw).decrypt_file(enc)
        assert dec.read_text() == original

    def test_passphrase_prefix_roundtrip(self, sample_file):
        original = sample_file.read_text()
        enc = EncryptionProvider("passphrase:secret").encrypt_file(sample_file)
        dec = EncryptionProvider("passphrase:secret").decrypt_file(enc)
        assert dec.read_text() == original


# ---------------------------------------------------------------------------
# SecretStr config fields (issue #37)
# ---------------------------------------------------------------------------


class TestSecretConfigFields:
    """The three secret config fields are SecretStr and never leak."""

    def test_encryption_key_is_secret_and_masked(self):
        from pydantic import SecretStr

        from prme.config import PRMEConfig

        c = PRMEConfig(encryption_enabled=True, encryption_key="topsecret")
        assert isinstance(c.encryption_key, SecretStr)
        assert c.encryption_key.get_secret_value() == "topsecret"
        # Secret must not appear in repr or model_dump string form.
        assert "topsecret" not in repr(c.encryption_key)
        assert "topsecret" not in str(c)
        assert "topsecret" not in str(c.model_dump())

    def test_database_url_is_secret(self):
        from pydantic import SecretStr

        from prme.config import PRMEConfig

        c = PRMEConfig(database_url="postgresql://u:p@host/db")
        assert isinstance(c.database_url, SecretStr)
        assert c.database_url.get_secret_value() == "postgresql://u:p@host/db"
        assert "p@host" not in repr(c.database_url)

    def test_embedding_api_key_is_secret(self):
        from pydantic import SecretStr

        from prme.config import EmbeddingConfig

        c = EmbeddingConfig(api_key="sk-secret")
        assert isinstance(c.api_key, SecretStr)
        assert c.api_key.get_secret_value() == "sk-secret"
        assert "sk-secret" not in repr(c)

    def test_backend_property_with_secret_url(self):
        from prme.config import PRMEConfig

        assert PRMEConfig(database_url="postgresql://localhost/db").backend == "postgres"
        assert PRMEConfig(database_url=None).backend == "duckdb"
        # Empty url string is falsy -> duckdb (semantics preserved under SecretStr).
        assert PRMEConfig(database_url="").backend == "duckdb"


# ---------------------------------------------------------------------------
# Fail-closed close() and atexit safety net (issue #37)
# ---------------------------------------------------------------------------


class TestFailClosedClose:
    """close() surfaces encryption failures instead of silently leaving plaintext."""

    @pytest.fixture
    def enc_dir(self, tmp_dir):
        lexical = tmp_dir / "lexical_index"
        lexical.mkdir()
        return tmp_dir

    @pytest.fixture
    def enc_config(self, enc_dir):
        from prme.config import PRMEConfig

        return PRMEConfig(
            db_path=str(enc_dir / "memory.duckdb"),
            vector_path=str(enc_dir / "vectors.usearch"),
            lexical_path=str(enc_dir / "lexical_index"),
            encryption_enabled=True,
            encryption_key="fail-closed-passphrase",
        )

    @pytest.mark.asyncio
    async def test_close_reraises_on_encryption_failure(self, enc_config):
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(enc_config)
        await engine.store(content="data", user_id="user-1")

        # Force encryption to fail at close time.
        def boom():
            raise RuntimeError("simulated encrypt failure")

        engine._encrypt_memory_pack = boom  # type: ignore[method-assign]
        with pytest.raises(EncryptionError, match="plaintext on disk"):
            await engine.close()

    @pytest.mark.asyncio
    async def test_atexit_registered_when_encryption_enabled(self, enc_config):
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(enc_config)
        try:
            assert engine._atexit_registered is True
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_atexit_unregistered_after_clean_close(self, enc_config):
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(enc_config)
        await engine.store(content="data", user_id="user-1")
        await engine.close()
        assert engine._atexit_registered is False

    @pytest.mark.asyncio
    async def test_atexit_handler_noop_after_close(self, enc_config):
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(enc_config)
        await engine.store(content="data", user_id="user-1")
        await engine.close()
        # Re-encryption already happened; calling the handler must not raise
        # or double-encrypt (early-returns on _closed).
        engine._atexit_encrypt()

    @pytest.mark.asyncio
    async def test_not_registered_without_encryption(self, tmp_dir):
        from prme.config import PRMEConfig
        from prme.storage.engine import MemoryEngine

        lexical = tmp_dir / "lexical_index"
        lexical.mkdir()
        config = PRMEConfig(
            db_path=str(tmp_dir / "memory.duckdb"),
            vector_path=str(tmp_dir / "vectors.usearch"),
            lexical_path=str(lexical),
        )
        engine = await MemoryEngine.create(config)
        try:
            assert engine._atexit_registered is False
        finally:
            await engine.close()
