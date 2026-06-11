"""Encryption at rest for PRME memory packs.

Provides transparent file-level encryption using Fernet (AES-128-CBC with
HMAC) from the ``cryptography`` library. Key derivation from passphrases
uses PBKDF2-HMAC-SHA256 with a random 16-byte salt.

Encrypted files use the ``.enc`` extension and store:
    [16-byte salt][ciphertext (Fernet token)]

The salt is needed to re-derive the Fernet key from the passphrase.
Fernet tokens are self-contained (include IV/nonce and HMAC).

Usage is transparent to the rest of PRME: files are decrypted on engine
open and re-encrypted on engine close.

Plaintext window (important)
----------------------------
Files are decrypted to plaintext on disk while the engine is open and are
only re-encrypted on a clean ``close()`` / ``lock()``. The engine registers
a best-effort ``atexit`` handler to re-encrypt on normal interpreter exit,
but a hard crash (``SIGKILL``, power loss, OOM) leaves the pack plaintext on
disk until the next clean ``close()`` re-encrypts it. File writes here are
atomic (temp file + ``os.replace``) so a crash never leaves a truncated or
partial file, but it cannot eliminate the plaintext-at-rest window itself.
Eliminating that window entirely would require native database encryption
(e.g. DuckDB ``ATTACH ... (ENCRYPTION_KEY ...)``); that is a separate,
format-changing effort tracked apart from this lifecycle hardening.

See RFC-0014 Section 10 for security requirements.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# PBKDF2 iteration count -- OWASP 2023 recommendation for SHA-256
_PBKDF2_ITERATIONS = 600_000
_SALT_LENGTH = 16
_ENC_EXTENSION = ".enc"


class EncryptionError(Exception):
    """Raised when encryption or decryption fails."""


class EncryptionProvider:
    """Symmetric file encryption using Fernet (AES-128-CBC + HMAC-SHA256).

    Accepts either a raw Fernet key (32 url-safe base64 bytes) or a
    passphrase from which a key is derived via PBKDF2-HMAC-SHA256.

    Args:
        key: A Fernet key (bytes or base64 str) **or** a passphrase
            string. Key-type resolution, first match wins:

            1. ``bytes`` are always treated as a raw Fernet key.
            2. A string with the ``raw_key:`` prefix is treated as a raw
               Fernet key (the prefix is stripped). Use this to remove any
               ambiguity for keys that look like passphrases, or to force
               passphrases that happen to be valid base64 to be derived —
               see point 4.
            3. A string with the ``passphrase:`` prefix is always run
               through PBKDF2, even if it is valid 44-char base64.
            4. An unprefixed string is auto-detected: exactly 44 characters
               of valid url-safe base64 (32 decoded bytes) is treated as a
               raw Fernet key; anything else is treated as a passphrase.
               This auto-detection is unchanged from prior releases and
               remains the default so existing packs keep decrypting; the
               explicit prefixes above are the recommended way to avoid the
               ambiguity going forward.
    """

    _RAW_KEY_PREFIX = "raw_key:"
    _PASSPHRASE_PREFIX = "passphrase:"

    def __init__(self, key: bytes | str | None = None) -> None:
        if key is None:
            raise ValueError("Encryption key or passphrase must be provided")

        self._passphrase: str | None = None
        self._fernet_key: bytes | None = None

        if isinstance(key, bytes):
            # Raw Fernet key
            self._fernet_key = key
        elif isinstance(key, str):
            if key.startswith(self._RAW_KEY_PREFIX):
                # Explicit raw key -- no derivation, no guessing.
                self._fernet_key = key[len(self._RAW_KEY_PREFIX):].encode("ascii")
            elif key.startswith(self._PASSPHRASE_PREFIX):
                # Explicit passphrase -- always derive, even if it is base64.
                self._passphrase = key[len(self._PASSPHRASE_PREFIX):]
            else:
                # Backward-compatible auto-detection: a 44-char valid
                # url-safe base64 string (32 decoded bytes) is a raw Fernet
                # key; otherwise it is a passphrase.
                try:
                    decoded = base64.urlsafe_b64decode(key)
                    if len(decoded) == 32 and len(key) == 44:
                        self._fernet_key = key.encode("ascii")
                    else:
                        self._passphrase = key
                except Exception:
                    self._passphrase = key

    @staticmethod
    def derive_key(passphrase: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
        """Derive a Fernet key from a passphrase using PBKDF2-HMAC-SHA256.

        Args:
            passphrase: Human-readable passphrase.
            salt: Optional salt bytes. If None, a random 16-byte salt
                is generated.

        Returns:
            Tuple of (fernet_key_bytes, salt_bytes). The salt must be
            stored alongside the ciphertext for decryption.
        """
        if salt is None:
            salt = os.urandom(_SALT_LENGTH)

        raw_key = hashlib.pbkdf2_hmac(
            "sha256",
            passphrase.encode("utf-8"),
            salt,
            _PBKDF2_ITERATIONS,
            dklen=32,
        )
        fernet_key = base64.urlsafe_b64encode(raw_key)
        return fernet_key, salt

    def _get_fernet_and_salt(self, existing_salt: bytes | None = None) -> tuple[Fernet, bytes]:
        """Return a Fernet instance and the salt used.

        For passphrase-based keys, derives the key using the given salt
        (or a fresh random salt when encrypting).

        Args:
            existing_salt: Salt to use for key derivation (for decryption).
                If None, a new random salt is generated (for encryption).

        Returns:
            Tuple of (Fernet instance, salt bytes).
        """
        if self._fernet_key is not None:
            # Raw Fernet key -- salt is a zero-filled placeholder
            return Fernet(self._fernet_key), b"\x00" * _SALT_LENGTH

        assert self._passphrase is not None
        fernet_key, salt = self.derive_key(self._passphrase, existing_salt)
        return Fernet(fernet_key), salt

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        """Write ``data`` to ``target`` atomically (temp file + fsync + rename).

        Writes to a sibling temp file, flushes it to disk, then renames it
        over ``target``. ``os.replace`` is atomic on the same filesystem, so
        a crash mid-write never leaves a partial or truncated file at
        ``target`` -- a reader sees either the old contents (if the rename
        had not happened) or the complete new contents, never a half-written
        mix.

        Args:
            target: Final destination path.
            data: Bytes to write.
        """
        tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except Exception:
            # Never leave a stray temp file behind on failure.
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def encrypt_file(self, path: Path) -> Path:
        """Encrypt a file in-place, producing a ``.enc`` file.

        The original unencrypted file is removed only after the encrypted
        file is fully and durably written, so a crash during encryption can
        never destroy the plaintext without leaving a complete ciphertext.
        File format: ``[16-byte salt][Fernet token]``.

        Args:
            path: Path to the plaintext file.

        Returns:
            Path to the encrypted file (with ``.enc`` suffix).

        Raises:
            EncryptionError: If encryption fails.
            FileNotFoundError: If the source file does not exist.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Cannot encrypt: {path} does not exist")

        if path.suffix == _ENC_EXTENSION:
            logger.debug("File %s is already encrypted, skipping", path)
            return path

        try:
            plaintext = path.read_bytes()
            fernet, salt = self._get_fernet_and_salt()
            ciphertext = fernet.encrypt(plaintext)

            enc_path = path.with_suffix(path.suffix + _ENC_EXTENSION)
            # Atomic write: the ciphertext is fully durable on disk before
            # the plaintext is removed. A crash leaves the plaintext intact
            # (recoverable) rather than a truncated/partial .enc file.
            self._atomic_write(enc_path, salt + ciphertext)

            # Remove original only after the encrypted file is durable.
            path.unlink()
            logger.debug("Encrypted %s -> %s", path, enc_path)
            return enc_path
        except Exception as exc:
            raise EncryptionError(f"Failed to encrypt {path}: {exc}") from exc

    def decrypt_file(self, path: Path) -> Path:
        """Decrypt an ``.enc`` file in-place, restoring the original.

        The encrypted file is removed after successful decryption.

        Args:
            path: Path to the encrypted file (must have ``.enc`` suffix).

        Returns:
            Path to the decrypted file (without ``.enc`` suffix).

        Raises:
            EncryptionError: If decryption fails (wrong key, corrupt data).
            FileNotFoundError: If the encrypted file does not exist.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Cannot decrypt: {path} does not exist")

        if path.suffix != _ENC_EXTENSION:
            logger.debug("File %s is not encrypted, skipping", path)
            return path

        try:
            raw = path.read_bytes()
            if len(raw) < _SALT_LENGTH:
                raise EncryptionError(
                    f"Encrypted file {path} is too short to contain salt"
                )

            salt = raw[:_SALT_LENGTH]
            ciphertext = raw[_SALT_LENGTH:]
            fernet, _ = self._get_fernet_and_salt(existing_salt=salt)
            plaintext = fernet.decrypt(ciphertext)

            # Restore original path (strip .enc suffix)
            dec_path = path.with_suffix("")
            # Atomic write: the plaintext is fully durable before the .enc
            # file is removed, so a crash mid-decrypt leaves the encrypted
            # file intact rather than a truncated plaintext.
            self._atomic_write(dec_path, plaintext)

            # Remove encrypted file only after the plaintext is durable.
            path.unlink()
            logger.debug("Decrypted %s -> %s", path, dec_path)
            return dec_path
        except InvalidToken as exc:
            raise EncryptionError(
                f"Decryption failed for {path}: invalid key or corrupt data"
            ) from exc
        except EncryptionError:
            raise
        except Exception as exc:
            raise EncryptionError(f"Failed to decrypt {path}: {exc}") from exc

    def encrypt_directory(self, dir_path: Path) -> list[Path]:
        """Encrypt all non-encrypted files in a directory (non-recursive).

        Args:
            dir_path: Path to the directory.

        Returns:
            List of encrypted file paths.
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"Directory does not exist: {dir_path}")

        encrypted = []
        for child in sorted(dir_path.iterdir()):
            if child.is_file() and child.suffix != _ENC_EXTENSION:
                enc_path = self.encrypt_file(child)
                encrypted.append(enc_path)
        logger.info("Encrypted %d files in %s", len(encrypted), dir_path)
        return encrypted

    def decrypt_directory(self, dir_path: Path) -> list[Path]:
        """Decrypt all ``.enc`` files in a directory (non-recursive).

        Args:
            dir_path: Path to the directory.

        Returns:
            List of decrypted file paths.
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"Directory does not exist: {dir_path}")

        decrypted = []
        for child in sorted(dir_path.iterdir()):
            if child.is_file() and child.suffix == _ENC_EXTENSION:
                dec_path = self.decrypt_file(child)
                decrypted.append(dec_path)
        logger.info("Decrypted %d files in %s", len(decrypted), dir_path)
        return decrypted


def write_manifest(
    base_dir: Path,
    encrypted_files: list[Path],
    *,
    algorithm: str = "Fernet-AES128-CBC-HMAC-SHA256",
    kdf: str = "PBKDF2-HMAC-SHA256",
    kdf_iterations: int = _PBKDF2_ITERATIONS,
) -> Path:
    """Write a manifest.json alongside the memory pack listing encrypted files.

    Args:
        base_dir: Root directory of the memory pack.
        encrypted_files: List of encrypted file paths.
        algorithm: Encryption algorithm identifier.
        kdf: Key derivation function identifier.
        kdf_iterations: Number of KDF iterations.

    Returns:
        Path to the written manifest.json file.
    """
    manifest = {
        "encryption": {
            "enabled": True,
            "algorithm": algorithm,
            "kdf": kdf,
            "kdf_iterations": kdf_iterations,
            "encrypted_at": datetime.now(timezone.utc).isoformat(),
            "files": [
                str(f.relative_to(base_dir)) if f.is_relative_to(base_dir) else str(f)
                for f in encrypted_files
            ],
        }
    }
    manifest_path = base_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    logger.info("Wrote encryption manifest to %s", manifest_path)
    return manifest_path


def read_manifest(base_dir: Path) -> dict | None:
    """Read manifest.json from a memory pack directory.

    Args:
        base_dir: Root directory of the memory pack.

    Returns:
        Parsed manifest dict, or None if no manifest exists.
    """
    manifest_path = base_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text())
