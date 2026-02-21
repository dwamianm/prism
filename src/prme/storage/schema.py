"""DuckDB schema initialization for PRME.

Creates all tables (events, nodes, edges) with indexes.
Attempts to install the DuckPGQ extension for SQL/PGQ graph queries;
gracefully degrades to pure SQL if DuckPGQ is unavailable for the
current DuckDB version/platform.

Called once on startup to ensure all tables exist.
"""

import logging

import duckdb

logger = logging.getLogger(__name__)

# Module-level flag indicating whether DuckPGQ is available.
# Set by install_duckpgq() and checked by create_property_graph().
_duckpgq_available: bool = False


def create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all PRME tables and indexes in DuckDB.

    Safe to call multiple times -- uses CREATE TABLE IF NOT EXISTS
    and CREATE INDEX IF NOT EXISTS.

    Args:
        conn: Active DuckDB connection.
    """
    # --- Events table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id UUID PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            role VARCHAR NOT NULL,
            content TEXT NOT NULL,
            content_hash VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            session_id VARCHAR,
            scope VARCHAR NOT NULL DEFAULT 'personal',
            metadata JSON,
            created_at TIMESTAMPTZ DEFAULT current_timestamp
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_session "
        "ON events (user_id, session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_hash ON events (content_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_scope ON events (scope)"
    )

    # --- Nodes table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            id UUID PRIMARY KEY,
            node_type VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            session_id VARCHAR,
            scope VARCHAR NOT NULL DEFAULT 'personal',
            content TEXT NOT NULL,
            metadata JSON,
            confidence FLOAT DEFAULT 0.5,
            salience FLOAT DEFAULT 0.5,
            lifecycle_state VARCHAR NOT NULL DEFAULT 'tentative',
            valid_from TIMESTAMPTZ DEFAULT current_timestamp,
            valid_to TIMESTAMPTZ,
            superseded_by UUID,
            evidence_refs JSON,
            created_at TIMESTAMPTZ DEFAULT current_timestamp,
            updated_at TIMESTAMPTZ DEFAULT current_timestamp,
            epistemic_type VARCHAR NOT NULL DEFAULT 'asserted',
            source_type VARCHAR NOT NULL DEFAULT 'user_stated'
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_user ON nodes (user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes (node_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_lifecycle "
        "ON nodes (lifecycle_state)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_scope ON nodes (scope)"
    )

    # --- Edges table ---
    # Note: source_id and target_id intentionally do NOT use REFERENCES
    # (foreign key constraints). DuckDB treats UPDATE as DELETE+INSERT
    # internally, which causes FK violations when updating a node that
    # is referenced by edges -- even when the primary key is unchanged.
    # Referential integrity is enforced at the application level.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edges (
            id UUID PRIMARY KEY,
            source_id UUID NOT NULL,
            target_id UUID NOT NULL,
            edge_type VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL,
            confidence FLOAT DEFAULT 0.5,
            valid_from TIMESTAMPTZ DEFAULT current_timestamp,
            valid_to TIMESTAMPTZ,
            provenance_event_id UUID,
            metadata JSON,
            created_at TIMESTAMPTZ DEFAULT current_timestamp
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_edges_type ON edges (edge_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_edges_user ON edges (user_id)"
    )

    # --- Operations table ---
    # Stores RETRIEVAL_REQUEST records per RFC-0005 S9 and forward-compatible
    # with Phase 5 TRST-08. Separate from event log per research recommendation
    # (operational metadata, not conversation content).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS operations (
            id VARCHAR PRIMARY KEY,
            op_type VARCHAR NOT NULL,
            target_id VARCHAR,
            payload JSON,
            actor_id VARCHAR,
            namespace_id VARCHAR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operations_op_type "
        "ON operations(op_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operations_created_at "
        "ON operations(created_at)"
    )


def _migrate_events_scope(conn: duckdb.DuckDBPyConnection) -> None:
    """Add scope column to events table if missing (auto-migration).

    For existing databases created before scope was added to the events
    table, this function detects the missing column and adds it with
    a NOT NULL DEFAULT 'personal' constraint. DuckDB backfills all
    existing rows atomically.

    Safe to call on databases that already have the scope column (no-op).

    Args:
        conn: Active DuckDB connection.
    """
    result = conn.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'events' AND column_name = 'scope'
    """).fetchone()
    if result is None:
        # DuckDB 1.4.x does not support ADD COLUMN with NOT NULL
        # constraint, so we add with DEFAULT only. The DEFAULT
        # backfills existing rows as 'personal'. New databases use
        # CREATE TABLE with NOT NULL DEFAULT for full enforcement.
        conn.execute("""
            ALTER TABLE events
            ADD COLUMN scope VARCHAR DEFAULT 'personal'
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_scope ON events (scope)"
        )
        logger.info(
            "Migrated events table: added scope column "
            "(backfilled as 'personal')"
        )


def _verify_nodes_scope(conn: duckdb.DuckDBPyConnection) -> None:
    """Verify existing graph nodes have consistent scope values.

    The nodes table already has a NOT NULL DEFAULT 'personal' constraint
    on the scope column, so this should always be a no-op. This function
    serves as a safety check per user decision to verify existing graph
    nodes at startup.

    Args:
        conn: Active DuckDB connection.
    """
    # Check if the nodes table exists first (may not exist on first run
    # before create_schema completes)
    table_exists = conn.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_name = 'nodes'
    """).fetchone()
    if table_exists is None:
        return

    null_count = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE scope IS NULL"
    ).fetchone()[0]
    if null_count > 0:
        conn.execute(
            "UPDATE nodes SET scope = 'personal' WHERE scope IS NULL"
        )
        logger.info(
            "Fixed %d graph nodes with NULL scope (set to 'personal')",
            null_count,
        )
    else:
        logger.debug("Nodes scope verification passed: no NULL scope values")


def _migrate_nodes_epistemic_type(conn: duckdb.DuckDBPyConnection) -> None:
    """Add epistemic_type and source_type columns to nodes table if missing.

    For existing databases created before these columns were added, this
    function detects the missing columns and adds them with sensible defaults.
    DuckDB 1.4.x ALTER TABLE ADD COLUMN does not support NOT NULL, so we
    use DEFAULT only. New databases use CREATE TABLE with NOT NULL DEFAULT.

    Safe to call on databases that already have the columns (no-op).

    Args:
        conn: Active DuckDB connection.
    """
    result = conn.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'nodes' AND column_name = 'epistemic_type'
    """).fetchone()
    if result is None:
        conn.execute("""
            ALTER TABLE nodes
            ADD COLUMN epistemic_type VARCHAR DEFAULT 'asserted'
        """)
        conn.execute("""
            ALTER TABLE nodes
            ADD COLUMN source_type VARCHAR DEFAULT 'user_stated'
        """)
        logger.info(
            "Migrated nodes table: added epistemic_type and source_type "
            "columns (backfilled as 'asserted'/'user_stated')"
        )


def install_duckpgq(conn: duckdb.DuckDBPyConnection) -> bool:
    """Attempt to install and load the DuckPGQ community extension.

    DuckPGQ provides SQL/PGQ graph query syntax. If the extension is
    not available for the current DuckDB version/platform, this function
    logs a warning and returns False. All graph operations fall back to
    standard SQL (JOINs, recursive CTEs) when DuckPGQ is unavailable.

    Args:
        conn: Active DuckDB connection.

    Returns:
        True if DuckPGQ was loaded successfully, False otherwise.
    """
    global _duckpgq_available

    try:
        conn.execute("INSTALL duckpgq FROM community")
    except (duckdb.IOException, duckdb.HTTPException, duckdb.Error):
        # Extension not available for this version/platform, or
        # already installed -- try loading anyway
        pass

    try:
        conn.execute("LOAD duckpgq")
        _duckpgq_available = True
        logger.info("DuckPGQ extension loaded successfully")
        return True
    except (duckdb.IOException, duckdb.Error) as exc:
        _duckpgq_available = False
        logger.warning(
            "DuckPGQ extension not available for DuckDB %s on this platform. "
            "Graph queries will use standard SQL fallback. Error: %s",
            duckdb.__version__,
            exc,
        )
        return False


def is_duckpgq_available() -> bool:
    """Check whether the DuckPGQ extension is loaded and available.

    Returns:
        True if DuckPGQ was successfully loaded during initialization.
    """
    return _duckpgq_available


def create_property_graph(conn: duckdb.DuckDBPyConnection) -> bool:
    """Create the DuckPGQ property graph over nodes and edges tables.

    If DuckPGQ is not available, this is a no-op and returns False.
    Uses CREATE OR REPLACE so it's safe to call multiple times.

    Args:
        conn: Active DuckDB connection.

    Returns:
        True if the property graph was created, False if DuckPGQ
        is not available.
    """
    if not _duckpgq_available:
        logger.info(
            "Skipping property graph creation -- DuckPGQ not available. "
            "Graph operations will use standard SQL on nodes/edges tables."
        )
        return False

    conn.execute("""
        CREATE OR REPLACE PROPERTY GRAPH memory_graph
        VERTEX TABLES (nodes)
        EDGE TABLES (
            edges SOURCE KEY (source_id) REFERENCES nodes (id)
                  DESTINATION KEY (target_id) REFERENCES nodes (id)
        )
    """)
    logger.info("Property graph 'memory_graph' created successfully")
    return True


def initialize_database(conn: duckdb.DuckDBPyConnection) -> bool:
    """Initialize the full PRME database schema.

    Convenience function that calls install_duckpgq, create_schema,
    and create_property_graph in order. Tables and indexes are always
    created; DuckPGQ property graph is best-effort.

    Args:
        conn: Active DuckDB connection.

    Returns:
        True if DuckPGQ property graph was created, False if operating
        in SQL-only fallback mode (tables still created successfully).
    """
    pgq_available = install_duckpgq(conn)
    create_schema(conn)
    _migrate_events_scope(conn)
    _verify_nodes_scope(conn)
    _migrate_nodes_epistemic_type(conn)
    pgq_graph = create_property_graph(conn)
    return pgq_available and pgq_graph
