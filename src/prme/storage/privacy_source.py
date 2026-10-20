"""Bounded read-only source snapshots without decrypted filesystem copies.

Only explicitly selected, quiescent local artifacts are supported here. The
filesystem is operation-owned, not fsspec's global memory store. DuckDB reads its
native format through a registered filesystem; no custom database parser or
plaintext temporary file is used. Limits bound file inputs and DuckDB memory,
not the interpreter's total RSS. Heap/swap zeroization is not a guarantee.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import duckdb
from fsspec import AbstractFileSystem

from prme.privacy.portability import PrivacyPortabilityError
from prme.storage.encryption import EncryptionProvider, derive_pack_key


class _Buffer(io.RawIOBase):
    def __init__(self, value: bytes):
        self._value = memoryview(value)
        self._position = 0
        self.size = len(value)

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        self._checkClosed()
        offset += (0, self._position, self.size)[whence]
        if offset < 0:
            raise ValueError("negative offset")
        self._position = offset
        return offset

    def tell(self):
        return self._position

    def read(self, size=-1):
        self._checkClosed()
        end = self.size if size < 0 else min(self.size, self._position + size)
        result = bytes(self._value[self._position:end])
        self._position = end
        return result

    def readinto(self, target):
        value = self.read(len(target))
        target[:len(value)] = value
        return len(value)

    def close(self):
        if not self.closed:
            self._value.release()
        super().close()


class _SourceFileSystem(AbstractFileSystem):
    protocol = "prme_legacy_buffer"
    cachable = False

    def __init__(self, payload: bytes):
        super().__init__(skip_instance_cache=True)
        self.path = f"{uuid4().hex}/memory.duckdb"
        self._payload = payload
        self._handles: list[_Buffer] = []

    def info(self, path, **kwargs):
        if self._strip_protocol(path) != self.path or self._payload is None:
            raise FileNotFoundError
        return {"name": self.path, "size": len(self._payload), "type": "file"}

    def ls(self, path, detail=True, **kwargs):
        item = self.info(path)
        return [item if detail else item["name"]]

    def _open(self, path, mode="rb", **kwargs):
        if mode != "rb":
            raise PermissionError("read-only source")
        self.info(path)
        handle = _Buffer(self._payload)
        self._handles.append(handle)
        return handle

    def release(self):
        for handle in self._handles:
            handle.close()
        self._handles.clear()
        self._payload = None


def _read_bounded(path: Path, limit: int) -> bytes:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise PrivacyPortabilityError("privacy_source_capacity")
    if path.is_symlink() or not path.is_file():
        raise PrivacyPortabilityError("privacy_source_unavailable")
    before = path.stat()
    if before.st_size > limit:
        raise PrivacyPortabilityError("privacy_source_capacity")
    with path.open("rb") as handle:
        value = handle.read(limit + 1)
    if len(value) > limit:
        raise PrivacyPortabilityError("privacy_source_capacity")
    after = path.stat()
    if (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
        before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns,
    ):
        raise PrivacyPortabilityError("privacy_source_changed")
    return value


def _quiescent(path: Path) -> None:
    plain = path.with_suffix("") if path.suffix == ".enc" else path
    if any(candidate.exists() for candidate in (
        Path(str(plain) + ".wal"), Path(str(plain) + ".wal.enc"),
    )):
        raise PrivacyPortabilityError("privacy_source_not_quiescent")
    if path.suffix == ".enc" and plain.exists():
        raise PrivacyPortabilityError("privacy_source_not_quiescent")
    if path.suffix != ".enc" and Path(str(path) + ".enc").exists():
        raise PrivacyPortabilityError("privacy_source_not_quiescent")


def read_legacy_bytes(path: Path, *, encryption_key: str | None = None,
                      max_bytes: int = 64 * 1024 * 1024) -> bytes:
    """Read/authenticate one existing artifact; no missing source is created."""
    try:
        _quiescent(path)
        raw = _read_bounded(path, max_bytes)
        if path.suffix != ".enc":
            return raw
        if not encryption_key:
            raise PrivacyPortabilityError("privacy_source_key_unavailable")
        try:
            return EncryptionProvider(encryption_key).decrypt_bytes(raw)
        except Exception:
            raise PrivacyPortabilityError("privacy_source_key_unavailable") from None
    except PrivacyPortabilityError:
        raise
    except Exception:
        raise PrivacyPortabilityError("privacy_source_unavailable") from None


@contextmanager
def open_privacy_source(path: Path | str, *, encryption_key: str | None = None,
                        max_bytes: int = 64 * 1024 * 1024,
                        memory_limit_mb: int = 128) -> Iterator[duckdb.DuckDBPyConnection]:
    """Attach a quiescent native database snapshot read-only, with spill disabled.

    ``path`` may name a directory, its closed ``memory.duckdb.enc``, or a native
    ``.prme`` pack. Supplying a key never silently decrypts the original files.
    The connection is private and must not escape the context. Only trusted
    engine-defined SELECTs should be issued on it, never source-supplied SQL.
    """
    source = Path(path)
    if source.is_dir():
        source = source / "memory.duckdb"
        if not source.exists() and source.with_suffix(".duckdb.enc").exists():
            source = source.with_suffix(".duckdb.enc")
    if not isinstance(memory_limit_mb, int) or isinstance(memory_limit_mb, bool) or memory_limit_mb <= 0:
        raise PrivacyPortabilityError("privacy_source_capacity")
    connection = None
    filesystem = None
    try:
        _quiescent(source)
        original = _read_bounded(source, max_bytes)
        digest = hashlib.sha256(original).digest()
        if source.suffix == ".enc":
            if not encryption_key:
                raise PrivacyPortabilityError("privacy_source_key_unavailable")
            try:
                original = EncryptionProvider(encryption_key).decrypt_bytes(original)
            except Exception:
                raise PrivacyPortabilityError("privacy_source_key_unavailable") from None
        filesystem = _SourceFileSystem(original)
        del original
        connection = duckdb.connect(":memory:", config={
            "threads": 1, "temp_directory": "", "memory_limit": f"{memory_limit_mb}MB",
            "autoload_known_extensions": False, "autoinstall_known_extensions": False,
            "enable_external_file_cache": False,
        })
        connection.register_filesystem(filesystem)
        options = "TYPE DUCKDB, READ_ONLY"
        if source.suffix == ".prme" and encryption_key:
            options += f", ENCRYPTION_KEY '{derive_pack_key(encryption_key)}'"
        connection.execute(f"ATTACH '{filesystem.protocol}://{filesystem.path}' AS source ({options})")
        connection.execute("USE source")
        # No caller SELECT can install a filesystem/extension or open another URL.
        connection.execute("SET enable_external_access=false")
        yield connection
        _quiescent(source)
        if hashlib.sha256(_read_bounded(source, max_bytes)).digest() != digest:
            raise PrivacyPortabilityError("privacy_source_changed")
    except PrivacyPortabilityError:
        raise
    except (MemoryError, duckdb.OutOfMemoryException):
        raise PrivacyPortabilityError("privacy_source_capacity") from None
    except (OSError, duckdb.Error, ValueError):
        raise PrivacyPortabilityError("privacy_source_unavailable") from None
    finally:
        if connection is not None:
            connection.close()
        if filesystem is not None:
            filesystem.release()
