"""P7 prototype part 1: the address key, the index, and the pack migration.

The key is two stored columns on ``nodes``:

    assertion_subject_key     normalize_assertion_value("subject", metadata["subject"])
    assertion_predicate_key   normalize_assertion_value("predicate", metadata["predicate"])

Shape and why each part is what it is:

- Plain stored columns, not generated ones. On the supported DuckDB (1.4.4) a generated column over
  ``json_extract_string`` cannot be indexed (``BinderException: Cannot use json_extract_string in this context``),
  and a generated column cannot be added to a pack that exists (``ParserException: Adding generated columns after
  table creation is not supported yet``). ``DEFAULT`` cannot reference another column either.
- Written in Python by ``normalize_assertion_value`` itself, never by SQL. SQL ``lower()``/``trim()`` is not
  ``str.casefold()`` and SQL has no NFKC, so a SQL-computed key would drift from the lookup's rule on any non-ASCII
  subject. On the 20,500-fact pack every subject is ASCII and the two agree; that agreement is a property of this
  corpus, not a guarantee, so the key is computed where the rule lives.
- The index holds no lifecycle state, for the reason ``idx_nodes_lifecycle`` is dropped in ``schema.py``: an indexed
  lifecycle update is a delete/insert on supported DuckDB and can bypass an in-flight ``updated_at`` column claim.
- The index carries owner, scope and node type ahead of the key, so the same index serves the owner, scope and type
  filters the lookup already applies. Lifecycle is a filter, not an index column.

A node that ``_assertion_values`` refuses stores NULL for both keys. The lookup counts those nodes as
``missing_structured_assertion``, so NULL must mean exactly the same thing in the key as in the count.
"""
from __future__ import annotations

import json

from prme.models.nodes import MemoryNode
from prme.retrieval.aggregation import _assertion_values, normalize_assertion_value

SUBJECT_COLUMN = "assertion_subject_key"
PREDICATE_COLUMN = "assertion_predicate_key"
INDEX_NAME = "idx_nodes_assertion_key"

COLUMNS_DDL = (
    f"ALTER TABLE nodes ADD COLUMN IF NOT EXISTS {SUBJECT_COLUMN} VARCHAR",
    f"ALTER TABLE nodes ADD COLUMN IF NOT EXISTS {PREDICATE_COLUMN} VARCHAR",
)
INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
    f"ON nodes (user_id, scope, node_type, {SUBJECT_COLUMN}, {PREDICATE_COLUMN})"
)


def assertion_key(node: MemoryNode) -> tuple[str | None, str | None]:
    """The stored key of one node, or (None, None) when it has no usable assertion.

    The test is ``_assertion_values`` and the value is ``normalize_assertion_value``: the two steps the lookup takes
    in Python today. A node whose metadata is not a mapping, or that lacks a non-empty string subject, predicate or
    object, or that carries a polarity that is not a non-empty string, has no key.
    """
    values = _assertion_values(node)
    if values is None:
        return None, None
    return (
        normalize_assertion_value("subject", values["subject"]),
        normalize_assertion_value("predicate", values["predicate"]),
    )


def add_schema(conn) -> None:
    """Add the key columns and the index. Safe on a pack that already has them."""
    for statement in COLUMNS_DDL:
        conn.execute(statement)
    conn.execute(INDEX_DDL)


def backfill(conn) -> dict:
    """Fill the key of every node that has none, from that node's own metadata.

    This is the one-time cost an existing pack pays, and it is also what makes bar (c) hold across a reopen: a key
    is derived from stored metadata, so rebuilding it can never change what the lookup returns, only how fast.

    The values pass through the same Python rule as a fresh write. The updates go in through a staging table so the
    whole pass is one statement rather than one round trip per node.
    """
    rows = conn.execute(
        f"SELECT CAST(id AS VARCHAR), metadata FROM nodes "
        f"WHERE {SUBJECT_COLUMN} IS NULL OR {PREDICATE_COLUMN} IS NULL"
    ).fetchall()
    if not rows:
        return {"nodes": 0, "keyed": 0, "unkeyable": 0}
    staged = []
    unkeyable = 0
    for node_id, raw in rows:
        metadata = json.loads(raw) if isinstance(raw, str) else raw
        values = _assertion_values(MemoryNode.model_construct(metadata=metadata))
        if values is None:
            unkeyable += 1
            continue
        staged.append((node_id,
                       normalize_assertion_value("subject", values["subject"]),
                       normalize_assertion_value("predicate", values["predicate"])))
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _p7_keys (id VARCHAR, s VARCHAR, p VARCHAR)")
    conn.execute("DELETE FROM _p7_keys")
    if staged:
        conn.executemany("INSERT INTO _p7_keys VALUES (?, ?, ?)", staged)
        conn.execute(
            f"UPDATE nodes SET {SUBJECT_COLUMN} = _p7_keys.s, {PREDICATE_COLUMN} = _p7_keys.p "
            f"FROM _p7_keys WHERE nodes.id::VARCHAR = _p7_keys.id"
        )
    conn.execute("DROP TABLE _p7_keys")
    return {"nodes": len(rows), "keyed": len(staged), "unkeyable": unkeyable}


def install_write_path(graph) -> None:
    """Keep the key correct on every write the graph already makes.

    ``_create_node_sync`` is the only ``INSERT INTO nodes`` site in the graph, so wrapping it covers every path that
    stores a node: ``store()``, ``ingest()`` and the organizer. ``_update_node_sync`` is the only site that can change
    ``metadata`` afterwards, so it is wrapped too: a claim whose subject or predicate is rewritten must take its new
    key with it, or an address would silently stop finding it.

    Both wrappers call the original first and then write the key on the same connection, so an insert that fails
    leaves no key behind and a key write can never happen without its row.
    """
    original_create = graph._create_node_sync
    original_update = graph._update_node_sync

    def keyed_create(node: MemoryNode) -> None:
        original_create(node)
        subject_key, predicate_key = assertion_key(node)
        if subject_key is None:
            return
        graph._conn.execute(
            f"UPDATE nodes SET {SUBJECT_COLUMN} = ?, {PREDICATE_COLUMN} = ? WHERE id = ?",
            [subject_key, predicate_key, str(node.id)],
        )  # one extra statement on the writer's own connection

    def keyed_update(node_id, updates: dict) -> None:
        original_update(node_id, updates)
        if "metadata" not in updates:
            return
        row = graph._conn.execute("SELECT metadata FROM nodes WHERE id = ?", [node_id]).fetchone()
        if row is None:
            return
        metadata = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        subject_key, predicate_key = assertion_key(MemoryNode.model_construct(metadata=metadata))
        graph._conn.execute(
            f"UPDATE nodes SET {SUBJECT_COLUMN} = ?, {PREDICATE_COLUMN} = ? WHERE id = ?",
            [subject_key, predicate_key, str(node_id)],
        )

    graph._create_node_sync = keyed_create
    graph._update_node_sync = keyed_update


def write_key(conn_or_graph, node: MemoryNode) -> None:
    """Write one node's key on the connection that just wrote the node."""
    conn = getattr(conn_or_graph, "_conn", conn_or_graph)
    subject_key, predicate_key = assertion_key(node)
    conn.execute(
        f"UPDATE nodes SET {SUBJECT_COLUMN} = ?, {PREDICATE_COLUMN} = ? WHERE id = ?",
        [subject_key, predicate_key, str(node.id)],
    )


INSERT_WITH_KEY_SQL = f"""
    INSERT INTO nodes (
        id, node_type, user_id, session_id, scope, content,
        metadata, confidence, salience, lifecycle_state,
        valid_from, valid_to, superseded_by, evidence_refs,
        created_at, updated_at, epistemic_type, source_type,
        decay_profile, last_reinforced_at, reinforcement_boost,
        salience_base, confidence_base, pinned, ttl_days, event_time,
        {SUBJECT_COLUMN}, {PREDICATE_COLUMN}
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def insert_with_key(conn, node: MemoryNode) -> None:
    """Insert one node with its key in the same statement.

    This is the shape a real change should carry: the key is one more pair of values in the insert that already
    writes the row, not a second statement that rewrites it. DuckDB treats an UPDATE as a delete and insert, so a
    second write costs a row rewrite plus two index updates.

    Nothing here parses JSON a second time: the key comes from ``node.metadata``, which the caller already holds.
    """
    subject_key, predicate_key = assertion_key(node)
    conn.execute(INSERT_WITH_KEY_SQL, [
        str(node.id), node.node_type.value, node.user_id, node.session_id, node.scope.value, node.content,
        json.dumps(node.metadata) if node.metadata is not None else None,
        node.confidence, node.salience, node.lifecycle_state.value,
        node.valid_from, node.valid_to,
        str(node.superseded_by) if node.superseded_by else None,
        json.dumps([str(ref) for ref in node.evidence_refs]) if node.evidence_refs else None,
        node.created_at, node.updated_at, node.epistemic_type.value, node.source_type.value,
        node.decay_profile.value, node.last_reinforced_at, node.reinforcement_boost,
        node.salience_base, node.confidence_base, node.pinned, node.ttl_days, node.event_time,
        subject_key, predicate_key,
    ])
