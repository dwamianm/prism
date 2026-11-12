"""The stored claim address and the stored privacy marker, and how a pack gains them.

Two kinds of column live here, both written in Python and never by SQL.

The claim address. ``assertion_subject_key`` and ``assertion_predicate_key`` hold
``normalize_assertion_value`` of a node's stored subject and predicate, so a lookup by
address reads the claims at one address instead of every claim of one owner. The value
must be computed by that function and not by SQL: SQL ``lower()`` and ``trim()`` are not
``str.casefold()`` and SQL has no NFKC, so a SQL-computed key would silently drift from
the lookup's own rule on any non-ASCII subject. A node that ``_assertion_values`` refuses
stores NULL for both keys, which is exactly what ``missing_structured_assertion`` counts.

The privacy marker. ``has_privacy_marker`` says whether ``metadata`` carries the privacy
key. The range check in ``prme.storage.privacy`` asks a question with the same answer, but
it asks it by running ``json_extract`` over every row of ``events`` and ``nodes``, and that
scan is most of what an assertion-state read costs. The column turns the question into a
comparison of one stored boolean. It is a cache of a fact about ``metadata``, so it is written wherever
``metadata`` is written and it is NULL and never FALSE when that fact is not known: NULL
means "ask the exact question", which keeps the check exact on every row this module did
not write, including rows a protected export or a migration inserted.

Why the columns are plain and not generated. On the supported DuckDB (1.4.4) a generated
column cannot be added to a table that exists, and this change has to reach packs that
already exist. A generated column also cannot hold ``json_extract`` over a JSON column at
all. A plain column written at every write site is what is left, and the cost is that each
site has to remember it.

Why the columns carry no index. Two measurements on a 20,500 fact pack decided it. First,
the supported DuckDB never chose an index scan for the address read or the marker check:
the plan stayed a sequential scan with or without a composite index on (user_id, scope,
node_type, subject key, predicate key), and the timings matched (address page 3.69 ms with
the index, 3.70 ms without; marker check 0.645 ms and 0.655 ms). The saving comes from
comparing two short stored strings and one boolean in SQL instead of decoding JSON per row
in SQL and Python. Second, an index on a column that ``update_node`` rewrites turns that
UPDATE into a delete and an insert, and that write then skips an in-flight ``updated_at``
conflict claim, which is the reason ``idx_nodes_lifecycle`` is dropped in ``schema.py``.
Every metadata update rewrites these columns, so an index on them would bring that hazard
back for no read gain. ``tests/test_address_lookup.py`` keeps the indexes out.
"""

from __future__ import annotations

import json
from typing import Any

from prme.models.nodes import MemoryNode

SUBJECT_COLUMN = "assertion_subject_key"
PREDICATE_COLUMN = "assertion_predicate_key"
PRIVACY_COLUMN = "has_privacy_marker"
COLUMNS = (SUBJECT_COLUMN, PREDICATE_COLUMN, PRIVACY_COLUMN)


def assertion_columns(conn) -> bool:
    """Whether this store carries both address columns.

    A pack opened by this build does. A pack that predates them and has not been opened
    since does not, and a read falls back to the full scan rather than failing.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info('nodes')").fetchall()}
    return SUBJECT_COLUMN in columns and PREDICATE_COLUMN in columns


def needs_backfill(conn) -> bool:
    """Whether any row still has no stored marker.

    The marker is the one column that is non-NULL for every row after a pass, so it is the
    only one worth probing: a node with no usable claim keeps a NULL address forever, and
    asking whether any address is NULL would be true on every store that holds a note.
    """
    column = PRIVACY_COLUMN
    if not _has_column(conn, "nodes", column):
        return True
    if conn.execute(f"SELECT 1 FROM nodes WHERE {column} IS NULL LIMIT 1").fetchone() is not None:
        return True
    if not _has_column(conn, "events", column):
        return False
    return conn.execute(f"SELECT 1 FROM events WHERE {column} IS NULL LIMIT 1").fetchone() is not None


def _has_column(conn, table: str, column: str) -> bool:
    return column in {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}


def assertion_key(node: MemoryNode) -> tuple[str | None, str | None]:
    """The stored address of one node, or (None, None) when it has no usable claim.

    The test is ``_assertion_values`` and the value is ``normalize_assertion_value``, the
    same two steps a lookup takes today in Python over every row. A node whose metadata is
    not a mapping, or that lacks a non-empty string subject, predicate or object, or whose
    polarity is not a non-empty string, has no address.
    """
    from prme.retrieval.aggregation import _assertion_values, normalize_assertion_value

    values = _assertion_values(node)
    if values is None:
        return None, None
    return (
        normalize_assertion_value("subject", values["subject"]),
        normalize_assertion_value("predicate", values["predicate"]),
    )


def privacy_marker(metadata: Any) -> bool:
    """Whether this metadata carries a privacy marker, by the range check's own test.

    ``require_legacy_range`` asks ``json_extract(metadata,'$.<key>') IS NOT NULL``, so a key
    that is present but null does not count. The JSON round trip below asks the same
    question of the value the writer holds.
    """
    from prme.privacy.records import PRIVACY_KEY

    if not isinstance(metadata, dict):
        return False
    return json.loads(json.dumps(metadata, default=str)).get(PRIVACY_KEY) is not None


def key_values(node: MemoryNode) -> tuple[str | None, str | None, bool]:
    """Every stored column this module writes, for one node being inserted."""
    subject_key, predicate_key = assertion_key(node)
    return subject_key, predicate_key, privacy_marker(node.metadata)


def add_columns(conn, *, protected: bool) -> bool:
    """Add the columns a store is missing, and say whether any was added. No index is created.

    A store created by this build declares them in ``CREATE TABLE``, so this adds nothing
    there; only a pack that predates them runs an ``ALTER``, and the caller checkpoints right
    after it. ``protected`` keeps the ``events`` table exactly as the protected format
    declares it: a protected store refuses any other ``events`` column set, and its events
    all carry the marker anyway, so its range check never reaches the branch that reads the
    stored marker.
    """
    added = False
    for column in COLUMNS:
        if _has_column(conn, "nodes", column):
            continue
        kind = "BOOLEAN" if column == PRIVACY_COLUMN else "VARCHAR"
        conn.execute(f"ALTER TABLE nodes ADD COLUMN {column} {kind}")
        added = True
    if not protected and not _has_column(conn, "events", PRIVACY_COLUMN):
        conn.execute(f"ALTER TABLE events ADD COLUMN {PRIVACY_COLUMN} BOOLEAN")
        added = True
    return added


def backfill(conn, *, batch_size: int = 5000) -> dict[str, int]:
    """Fill the address and the marker of every row that has neither yet.

    This is the one-time cost an existing pack pays on its next open. Both values are
    derived from stored bytes, so filling them can never change what a lookup returns, only
    how fast it returns it. A row whose address stays NULL is one ``_assertion_values``
    refuses, which is the same set the lookup counts as ``missing_structured_assertion``.

    The updates go through a staging table so the whole pass is one statement per batch
    rather than one round trip per row.
    """
    counts = _backfill_nodes(conn, batch_size=batch_size)
    counts.update(_backfill_events(conn, batch_size=batch_size))
    return counts


def _backfill_nodes(conn, *, batch_size: int) -> dict[str, int]:
    total = 0
    keyed = 0
    while True:
        # The loop condition is the marker alone, because the marker is the only one of the
        # three that is non-NULL for every row after this pass: a node with no usable claim
        # keeps a NULL address forever, and a condition that included it would never finish.
        rows = conn.execute(
            f"SELECT CAST(id AS VARCHAR), metadata FROM nodes "
            f"WHERE {PRIVACY_COLUMN} IS NULL LIMIT ?",
            [batch_size],
        ).fetchall()
        if not rows:
            return {"nodes_backfilled": total, "nodes_keyed": keyed}
        staged = []
        for node_id, raw in rows:
            metadata = _metadata(raw)
            node = MemoryNode.model_construct(metadata=metadata)
            subject_key, predicate_key = assertion_key(node)
            staged.append((node_id, subject_key, predicate_key, privacy_marker(metadata)))
            keyed += 1 if subject_key is not None else 0
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _prme_keys "
            "(id VARCHAR, s VARCHAR, p VARCHAR, m BOOLEAN)"
        )
        conn.execute("DELETE FROM _prme_keys")
        conn.executemany("INSERT INTO _prme_keys VALUES (?, ?, ?, ?)", staged)
        conn.execute(
            f"UPDATE nodes SET {SUBJECT_COLUMN} = _prme_keys.s, "
            f"{PREDICATE_COLUMN} = _prme_keys.p, {PRIVACY_COLUMN} = _prme_keys.m "
            f"FROM _prme_keys WHERE CAST(nodes.id AS VARCHAR) = _prme_keys.id"
        )
        conn.execute("DROP TABLE _prme_keys")
        total += len(rows)


def _backfill_events(conn, *, batch_size: int) -> dict[str, int]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info('events')").fetchall()}
    if PRIVACY_COLUMN not in columns:
        # The protected format declares its own events columns, and this change leaves it
        # exactly as declared. Its range check keeps the exact marker predicate.
        return {}
    total = 0
    while True:
        rows = conn.execute(
            f"SELECT CAST(id AS VARCHAR), metadata FROM events "
            f"WHERE {PRIVACY_COLUMN} IS NULL LIMIT ?",
            [batch_size],
        ).fetchall()
        if not rows:
            return {"events_backfilled": total}
        staged = [(node_id, privacy_marker(_metadata(raw))) for node_id, raw in rows]
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _prme_markers (id VARCHAR, m BOOLEAN)"
        )
        conn.execute("DELETE FROM _prme_markers")
        conn.executemany("INSERT INTO _prme_markers VALUES (?, ?)", staged)
        conn.execute(
            f"UPDATE events SET {PRIVACY_COLUMN} = _prme_markers.m "
            f"FROM _prme_markers WHERE CAST(events.id AS VARCHAR) = _prme_markers.id"
        )
        conn.execute("DROP TABLE _prme_markers")
        total += len(rows)


def _metadata(raw) -> dict:
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return raw if isinstance(raw, dict) else {}
