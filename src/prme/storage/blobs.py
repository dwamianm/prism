"""Small binary evidence kept inside a memory pack (RFC-0018).

A pack keeps one JPEG thumbnail per observed keyframe so a fact can show the
frame it came from. The thumbnails live in a ``blobs`` table of the pack's
DuckDB database in both layouts, a directory pack's ``memory.duckdb`` and a
single-file pack alike, rather than in a ``frames/`` directory beside the
database. One code path then covers both layouts, the blobs inherit the
pack's encryption at rest (file-level Fernet for a directory pack, DuckDB's
native encryption for a single-file pack), ``prme migrate`` and
``prme compact`` copy them with every other table, and ``prme rebuild``
never touches them. Keys name their content: a thumbnail is
``frames/<sha256 of the keyframe>.jpg``, so the same frame observed twice
stores one row. The table is created on first use, so a pack that never
observes keeps its schema byte for byte.
"""

from __future__ import annotations

import duckdb

BLOBS_TABLE = "blobs"
"""Name of the table that holds pack-resident binary evidence."""

THUMBNAIL_KEY_PREFIX = "frames/"
"""Key prefix of observation thumbnails; the rest is the frame's SHA-256 and ``.jpg``."""


def thumbnail_key(frame_sha256: str) -> str:
    """Return the blob key of the thumbnail for the keyframe with *frame_sha256*."""
    return f"{THUMBNAIL_KEY_PREFIX}{frame_sha256}.jpg"


def has_blobs_table(conn: duckdb.DuckDBPyConnection) -> bool:
    """Return True when the pack has stored at least one blob table row ever."""
    (count,) = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name = ?",
        [BLOBS_TABLE],
    ).fetchone()
    return bool(count)


def ensure_blobs_table(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the blobs table if the pack has none. Called before the first write only."""
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {BLOBS_TABLE} ("
        "key VARCHAR PRIMARY KEY, "
        "media_type VARCHAR NOT NULL, "
        "size INTEGER NOT NULL, "
        "data BLOB NOT NULL, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp)"
    )


def put_blob(conn: duckdb.DuckDBPyConnection, key: str, media_type: str, data: bytes) -> bool:
    """Store *data* under *key*; return True when the row is new.

    Keys name their content, so an existing row under the same key is kept
    as it is and the call reports False.
    """
    if not key:
        raise ValueError("A blob key must not be empty")
    ensure_blobs_table(conn)
    if conn.execute(f"SELECT 1 FROM {BLOBS_TABLE} WHERE key = ?", [key]).fetchone() is not None:
        return False
    # Two engines of one process can share a DuckDB instance; the first row
    # written under a key stands.
    conn.execute(
        f"INSERT OR IGNORE INTO {BLOBS_TABLE} (key, media_type, size, data) VALUES (?, ?, ?, ?)",
        [key, media_type, len(data), data],
    )
    return True


def get_blob(conn: duckdb.DuckDBPyConnection, key: str) -> tuple[str, bytes] | None:
    """Return ``(media_type, data)`` for *key*, or None when the pack has no such blob."""
    if not has_blobs_table(conn):
        return None
    row = conn.execute(f"SELECT media_type, data FROM {BLOBS_TABLE} WHERE key = ?", [key]).fetchone()
    if row is None:
        return None
    return str(row[0]), bytes(row[1])


def blob_count(conn: duckdb.DuckDBPyConnection) -> int:
    """Return how many blobs the pack holds; zero for a pack without the table."""
    if not has_blobs_table(conn):
        return 0
    (count,) = conn.execute(f"SELECT count(*) FROM {BLOBS_TABLE}").fetchone()
    return int(count)
