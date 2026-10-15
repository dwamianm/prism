"""Compact, migrate and inspect single-file memory packs (issue #247).

A single-file pack is one DuckDB database, and DuckDB never gives freed space
back to the file system: a pack that has seen many rewrites, such as the
vector snapshot row replaced on every save, keeps the size of its largest
past state. :func:`compact_pack` copies a pack into a fresh file and swaps it
in. :func:`migrate_directory_pack` turns a directory pack into a new
single-file pack, and :func:`open_pack_read_only` opens a pack for inspection
without starting an engine.

Single-file packs are attached to DuckDB connections with ``ATTACH``. An
encrypted one is attached with the key from
:func:`prme.storage.encryption.derive_pack_key`, and DuckDB's temporary files
are encrypted too, so its contents never reach the disk in clear text. The
key never appears in a log or error message.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import duckdb
from pydantic import SecretStr

from prme.config import PRMEConfig
from prme.storage._threading import run_to_completion

# Attachment names. None may be "memory", the name of the in-memory database
# of every connection opened with duckdb.connect(":memory:").
_SOURCE = "prme_source"
_TARGET = "prme_target"
_PACK = "prme_pack"


class PackInUseError(RuntimeError):
    """A pack has a write-ahead log beside it, so it may be open elsewhere."""


def _secret(value: str | SecretStr | None) -> str | None:
    """Return the key text of *value*; an empty key means no key."""
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value or None


def _duckdb_key(passphrase: str | None) -> str | None:
    """Return the key string DuckDB takes for *passphrase*, or None without one."""
    if passphrase is None:
        return None
    # Imported only when a key is given: a plain pack never needs it.
    from prme.storage.encryption import derive_pack_key

    return derive_pack_key(passphrase)


def _literal(value: str) -> str:
    """Quote *value* as a SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def _identifier(name: str) -> str:
    """Quote *name* as a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def _remove_empty_directory(path: str) -> None:
    """Remove the directory at *path* if it exists and is empty."""
    try:
        os.rmdir(path)
    except OSError:
        pass  # Missing, or it still holds files.


@contextmanager
def _instance(path: str, key: str | None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open an in-memory DuckDB instance to attach the pack at *path* to.

    As an engine does, it keeps temporary files in ``<path>.tmp`` beside the
    pack rather than in the working directory, and encrypts them for an
    encrypted pack, because DuckDB writes them in clear text otherwise. On
    exit it removes that directory if it created it and left it empty; one
    that was already there may belong to an engine in another process.
    """
    spill = f"{path}.tmp"
    existed = os.path.lexists(spill)
    settings: dict[str, Any] = {"temp_directory": spill}
    if key is not None:
        settings["temp_file_encryption"] = True
    conn = duckdb.connect(":memory:", config=settings)
    try:
        yield conn
    finally:
        conn.close()
        if not existed:
            _remove_empty_directory(spill)


def _attach_error(exc: duckdb.Error, path: str, key: str | None) -> Exception | None:
    """Return the error to raise for a failed ATTACH, or None to keep *exc*.

    Key errors read as an engine reports them. Any other message that quotes
    the key is rewritten without it.
    """
    from prme.storage.encryption import EncryptionError

    message = str(exc)
    if "Wrong encryption key" in message:
        return EncryptionError(f"Wrong encryption key for the memory pack at {path}; the pack was not changed")
    if "Cannot open encrypted database" in message:
        return EncryptionError(f"The memory pack at {path} is encrypted; give its encryption key")
    if key is not None and "is not encrypted" in message:
        return EncryptionError(f"The memory pack at {path} is not encrypted; give no encryption key")
    if key is not None and key in message:
        return type(exc)(message.replace(key, "<key>"))
    return None


def _attach(
    conn: duckdb.DuckDBPyConnection, path: str, alias: str, *,
    key: str | None, read_only: bool, options: tuple[str, ...] = (),
) -> None:
    """Attach the DuckDB file at *path* as *alias*, with *key* when encrypted.

    ``TYPE DUCKDB`` keeps DuckDB from guessing another file format and
    loading an extension for it. ``ATTACH`` takes no query parameters, so the
    path and key are quoted literals; see :func:`_attach_error` for errors.
    """
    settings = ["TYPE DUCKDB", *options]
    if read_only:
        settings.append("READ_ONLY")
    if key is not None:
        settings.append(f"ENCRYPTION_KEY {_literal(key)}")
    try:
        conn.execute(f"ATTACH {_literal(path)} AS {_identifier(alias)} ({', '.join(settings)})")
    except duckdb.Error as exc:
        error = _attach_error(exc, path, key)
        if error is None:
            raise
        # Chaining would print DuckDB's message, which may quote the key.
        raise error from None


def _refuse_write_ahead_log(path: str) -> None:
    """Refuse a pack whose write-ahead log shows it open or not closed cleanly."""
    if os.path.lexists(path + ".wal"):
        raise PackInUseError(
            f"{path} has a write-ahead log beside it: the pack is open in another "
            "process or was not closed cleanly. Close every program that uses it, "
            "or open and close it once (for example with prme info) to fold the "
            "log into the file, then try again"
        )


def _remove_database(path: str) -> None:
    """Delete a database file this module created, with its write-ahead log."""
    for name in (path, path + ".wal"):
        try:
            os.remove(name)
        except FileNotFoundError:
            pass


@contextmanager
def _read_only(path: str, key: str | None) -> Iterator[duckdb.DuckDBPyConnection]:
    with _instance(path, key) as conn:
        # Show stored instants in UTC, as an engine does.
        conn.execute("SET TimeZone = 'UTC'")
        _attach(conn, path, _PACK, key=key, read_only=True)
        conn.execute(f"USE {_identifier(_PACK)}")
        yield conn


@contextmanager
def open_pack_read_only(
    path: str | os.PathLike[str], *, encryption_key: str | SecretStr | None = None,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a single-file pack for reading, without an engine.

    Yields an in-memory DuckDB connection with the pack attached read only
    and selected, so queries can name its tables directly. DuckDB's file lock
    keeps other processes from writing to the pack until the block exits. An
    encrypted pack needs its *encryption_key*, the passphrase or raw key an
    engine would use.
    """
    with _read_only(os.path.abspath(os.fspath(path)), _duckdb_key(_secret(encryption_key))) as conn:
        yield conn


def _layout_options(conn: duckdb.DuckDBPyConnection, source: str, key: str | None) -> tuple[str, ...]:
    """Return ATTACH options that give a copy the storage layout of *source*.

    A copy keeps the source's storage version, and an encrypted copy of an
    encrypted source keeps its cipher. An encrypted copy of a plain source
    takes DuckDB's defaults, because encryption needs storage version 1.4.0
    or later, which DuckDB then chooses itself.
    """
    encrypted, cipher, tags = conn.execute(
        "SELECT encrypted, cipher, tags FROM duckdb_databases() WHERE database_name = ?", [source],
    ).fetchone()
    # The tag names the oldest DuckDB release that reads the file, as "v1.0.0+".
    version = (tags or {}).get("storage_version", "").rstrip("+")
    options = []
    if version and (key is None or encrypted):
        options.append(f"STORAGE_VERSION {_literal(version)}")
    if key is not None and encrypted and cipher:
        options.append(f"ENCRYPTION_CIPHER {_literal(cipher)}")
    return tuple(options)


def _copy_database(conn: duckdb.DuckDBPyConnection, source: str, destination: str, key: str | None) -> None:
    """Copy every table, index and sequence of attached *source* into a new file.

    The new file at *destination* is encrypted with *key* when one is given.
    It is complete on return: a write-ahead log left beside it would hold
    part of the copy, so one is refused rather than left behind.
    """
    _attach(conn, destination, _TARGET, key=key, read_only=False,
            options=_layout_options(conn, source, key))
    try:
        conn.execute(f"COPY FROM DATABASE {_identifier(source)} TO {_identifier(_TARGET)}")
        conn.execute(f"CHECKPOINT {_identifier(_TARGET)}")
    finally:
        conn.execute(f"DETACH {_identifier(_TARGET)}")
    if os.path.lexists(destination + ".wal"):
        raise RuntimeError(f"DuckDB left a write-ahead log beside {destination}; the copy is incomplete")


def compact_pack(
    path: str | os.PathLike[str], *, encryption_key: str | SecretStr | None = None,
) -> dict[str, Any]:
    """Rewrite a closed memory pack into a fresh file to give back unused space.

    DuckDB never shrinks a database file. This copies every table, index and
    sequence into a new file in the same directory with ``COPY FROM DATABASE``
    and then replaces the pack with it in one rename, so a failure at any
    point leaves the original untouched. Rows, node IDs and vector keys are
    unchanged, so the same works for the ``memory.duckdb`` of a directory
    pack, whose other files stay valid.

    The pack must be closed. A write-ahead log beside it is refused, and
    DuckDB's file lock refuses a pack that another process holds open; the
    read-only source keeps that lock until the rename, so no other process
    can write to the pack in between. Within one process, close every engine
    that uses the pack first. An encrypted pack needs its *encryption_key*,
    and the copy is encrypted with the same key and cipher.

    Returns:
        The pack's absolute path, its size in bytes before and after, and
        whether it is encrypted.

    Raises:
        FileNotFoundError: No file exists at *path*.
        PackInUseError: A write-ahead log is beside the pack.
    """
    target = os.path.abspath(os.fspath(path))
    if not os.path.isfile(target):
        raise FileNotFoundError(f"Memory pack not found: {target}")
    _refuse_write_ahead_log(target)
    key = _duckdb_key(_secret(encryption_key))
    folder, name = os.path.split(target)
    temporary = os.path.join(folder, f".{name}.{uuid.uuid4().hex}.compact")
    bytes_before = os.path.getsize(target)
    try:
        with _instance(target, key) as conn:
            _attach(conn, target, _SOURCE, key=key, read_only=True)
            _copy_database(conn, _SOURCE, temporary, key)
            if os.name == "nt":
                # Windows cannot rename a file over one that is still open.
                conn.execute(f"DETACH {_identifier(_SOURCE)}")
            os.replace(temporary, target)
    except BaseException:
        _remove_database(temporary)
        raise
    return {
        "path": target,
        "bytes_before": bytes_before,
        "bytes_after": os.path.getsize(target),
        "encrypted": key is not None,
    }


def _directory_pack_bytes(directory: str) -> int:
    """Return the size of the files that make up the directory pack in *directory*."""
    names = ("memory.duckdb", "memory.duckdb.enc", "vectors.usearch", "vectors.usearch.enc", "manifest.json")
    total = sum(os.path.getsize(path) for path in (os.path.join(directory, name) for name in names)
                if os.path.isfile(path))
    for root, _directories, files in os.walk(os.path.join(directory, "lexical_index")):
        total += sum(os.path.getsize(os.path.join(root, name)) for name in files)
    return total


def _refuse_existing(path: str) -> None:
    if os.path.lexists(path) or os.path.lexists(path + ".wal"):
        raise FileExistsError(f"Destination already exists: {path}")


def _publish(temporary: str, target: str) -> None:
    """Move a finished pack to *target* without replacing anything already there."""
    try:
        # A hard link fails if the target exists, so this cannot overwrite it.
        os.link(temporary, target)
    except FileExistsError:
        raise FileExistsError(f"Destination already exists: {target}") from None
    except OSError:
        # Some file systems have no hard links: check again and rename.
        _refuse_existing(target)
        os.replace(temporary, target)
    else:
        os.remove(temporary)


def _copy_open_database(conn: duckdb.DuckDBPyConnection, destination: str, key: str | None) -> None:
    """Copy the database an open engine uses through its own connection."""
    if key is not None:
        # DuckDB writes temporary files in clear text unless asked not to.
        # This changes the source engine's instance, which closes right after.
        conn.execute("SET temp_file_encryption = true")
    (source,) = conn.execute("SELECT current_database()").fetchone()
    _copy_database(conn, source, destination, key)


async def migrate_directory_pack(
    directory: str | os.PathLike[str], destination: str | os.PathLike[str], *,
    encryption_key: str | SecretStr | None = None,
) -> dict[str, Any]:
    """Copy a directory pack into a new single-file pack.

    Opens the directory pack once with an engine, which folds pending writes
    into ``memory.duckdb`` and backfills vector payloads from a legacy
    ``vectors.usearch``, and copies its database with ``COPY FROM DATABASE``
    through that engine's connection. Copying while the engine is open is
    what lets an encrypted directory pack migrate: its close encrypts the
    files again and removes the clear copy. The new file is then opened once
    as a single-file pack, which restores every vector from its payload and
    writes the vector snapshot and the manifest, and only then moved to
    *destination*, so a failure never leaves a partial pack there. The source
    keeps its layout and contents; the only changes are those of a normal
    open and close.

    A directory pack encrypted with ``encryption_enabled`` needs
    *encryption_key*, its passphrase or raw key. When a key is given, the new
    pack is encrypted with DuckDB's native encryption under the same key;
    without one, it is not encrypted.

    Returns:
        The source and destination paths; the node, edge, event and vector
        counts of the new pack; the size in bytes of the directory pack's
        files (``bytes_before``) and of the new pack (``bytes_after``); and
        whether the new pack is encrypted.

    Raises:
        FileNotFoundError: *directory* holds no memory pack.
        FileExistsError: *destination* already exists.
        ValueError: The source is encrypted and no key was given.
    """
    from prme.storage.engine import MemoryEngine

    source = os.path.abspath(os.fspath(directory))
    target = os.path.abspath(os.fspath(destination))
    database = os.path.join(source, "memory.duckdb")
    vectors = os.path.join(source, "vectors.usearch")
    encrypted_source = os.path.isfile(database + ".enc") or os.path.isfile(vectors + ".enc")
    if not (os.path.isfile(database) or encrypted_source):
        raise FileNotFoundError(f"No memory pack in {source}: memory.duckdb is missing")
    passphrase = _secret(encryption_key)
    if encrypted_source and passphrase is None:
        raise ValueError(f"The memory pack in {source} is encrypted; give its encryption key")
    _refuse_existing(target)
    key = _duckdb_key(passphrase)

    # Every setting that decides which storage an engine opens, and how, is
    # named here, so ambient PRME_ settings can neither point an engine at
    # other storage nor encrypt a plain source when it closes.
    source_config = PRMEConfig(
        database_url=None, pack_path=None, namespace_id=None,
        db_path=database, vector_path=vectors, lexical_path=os.path.join(source, "lexical_index"),
        encryption_enabled=encrypted_source, encryption_key=passphrase if encrypted_source else None,
    )
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    temporary = os.path.join(folder, f".{os.path.basename(target)}.{uuid.uuid4().hex}.migrate")
    pack_config = PRMEConfig(
        database_url=None, namespace_id=None, pack_path=temporary,
        encryption_enabled=key is not None, encryption_key=passphrase,
    )
    # Where the source engine's DuckDB instance keeps temporary files.
    source_spill = database + ".tmp"
    source_spill_existed = os.path.lexists(source_spill)
    try:
        async with MemoryEngine.open(source_config) as engine:
            async with engine._event_store._conn_lock:
                await run_to_completion(_copy_open_database, engine._conn, temporary, key)
        bytes_before = _directory_pack_bytes(source)

        # Each engine closes before the file is attached again, because
        # DuckDB attaches an encrypted file to one instance per process.
        async with MemoryEngine.open(pack_config):
            pass

        with _read_only(temporary, key) as conn:
            counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                      for table in ("nodes", "edges", "events")}
            snapshot = conn.execute("SELECT vector_count FROM vector_snapshot WHERE singleton").fetchone()
            (manifests,) = conn.execute("SELECT count(*) FROM prme_manifest").fetchone()
        if snapshot is None or manifests != 1:
            raise RuntimeError("The new pack has no vector snapshot or manifest; it was not published")
        _publish(temporary, target)
    except BaseException:
        _remove_database(temporary)
        raise
    finally:
        if not source_spill_existed:
            _remove_empty_directory(source_spill)
        _remove_empty_directory(temporary + ".tmp")
    return {
        "source": source,
        "destination": target,
        **counts,
        "vectors": snapshot[0],
        "bytes_before": bytes_before,
        "bytes_after": os.path.getsize(target),
        "encrypted": key is not None,
    }
