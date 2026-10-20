"""PostgreSQL DDL schema for PRME.

Mirrors the DuckDB schema (src/prme/storage/schema.py) with
PostgreSQL-native types: JSONB, pgvector, tsvector GENERATED columns,
and GIN/HNSW indexes.

Called once on startup via ``initialize_pg_database(pool)``.
"""

from __future__ import annotations

import logging
import hashlib
import json

import asyncpg

from prme.storage.pg.vector_sql import VectorSQL, resolve_vector_sql

logger = logging.getLogger(__name__)

# Written only by privileged bootstrap. Deployments grant runtime SELECT, never
# DML, on this descriptor and the catalog/namespace identity authorities.
_RUNTIME_DDL = "CREATE TABLE IF NOT EXISTS prme_runtime_schema(singleton BOOLEAN PRIMARY KEY CHECK(singleton),body TEXT NOT NULL)"
_RUNTIME_TABLES = {
    "events", "nodes", "edges", "operations", "event_materializations", "event_extractions",
    "profile_publication_heads", "profile_work", "profile_preparation_heads",
    "lexical_documents", "derivation_artifact_owners", "derivation_registered_plans",
    "profile_registered_plans", "consolidation_publication_heads",
    "consolidation_registered_plans", "consolidation_stage_fences",
    "privacy_admissions", "privacy_rotation_jobs", "prme_runtime_schema",
}
_PROTECTED_TABLES = {"privacy_format", "privacy_store_state", "privacy_erasure_jobs",
    "privacy_erasure_targets", "privacy_erasure_progress", "privacy_erasure_artifacts", "privacy_erasure_epoch"}


async def _schema_fingerprint(conn, tables):
    columns = await conn.fetch(
        "SELECT c.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,"
        "pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS default_value FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
        "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
        "WHERE n.nspname=current_schema() AND c.relkind='r' AND c.relname=ANY($1::text[]) "
        "ORDER BY c.relname,a.attnum", tables)
    indexes = await conn.fetch(
        "SELECT t.relname,i.relname,x.indisvalid,x.indisready,pg_catalog.pg_get_indexdef(i.oid) "
        "FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid "
        "JOIN pg_catalog.pg_class t ON t.oid=x.indrelid JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname=current_schema() AND t.relname=ANY($1::text[]) ORDER BY t.relname,i.relname", tables)
    constraints = await conn.fetch(
        "SELECT t.relname,c.conname,c.convalidated,pg_catalog.pg_get_constraintdef(c.oid) "
        "FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class t ON t.oid=c.conrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname=current_schema() AND t.relname=ANY($1::text[]) ORDER BY t.relname,c.conname", tables)
    if {row[0] for row in columns} != set(tables) or any(not row[2] or not row[3] for row in indexes):
        raise ValueError("postgres_schema_bootstrap_required")
    body = [[list(row) for row in group] for group in (columns, indexes, constraints)]
    return hashlib.sha256(json.dumps(body, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


async def _record_runtime_schema(conn, *, embedding_dim, protected):
    await conn.execute(_RUNTIME_DDL)
    tables = sorted(_RUNTIME_TABLES | (_PROTECTED_TABLES if protected else set()))
    body = json.dumps({"version": 1, "embedding_dim": embedding_dim, "protected": protected,
        "tables": tables, "fingerprint": await _schema_fingerprint(conn, tables)}, sort_keys=True)
    await conn.execute("INSERT INTO prme_runtime_schema VALUES(true,$1) ON CONFLICT(singleton) DO UPDATE SET body=excluded.body", body)


async def validate_pg_runtime(pool, *, embedding_dim, protected):
    """Read-only startup attestation; no implicit bootstrap or registry writes."""
    from prme.storage.derivation_registry import _PENDING
    from prme.storage.profile_registry import PENDING as PROFILE_PENDING
    from prme.storage.consolidation_publication import PENDING as CONSOLIDATION_PENDING
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch("SELECT body FROM prme_runtime_schema WHERE singleton")
            if len(rows) != 1:
                raise ValueError()
            saved = json.loads(rows[0][0])
            tables = sorted(_RUNTIME_TABLES | (_PROTECTED_TABLES if protected else set()))
            if (set(saved) != {"version", "embedding_dim", "protected", "tables", "fingerprint"}
                    or saved["version"] != 1 or saved["embedding_dim"] != embedding_dim
                    or saved["protected"] is not protected or saved["tables"] != tables
                    or saved["fingerprint"] != await _schema_fingerprint(conn, tables)):
                raise ValueError()
            # A stored claim about dimension cannot override the native type.
            dimension = await conn.fetchval("SELECT a.atttypmod FROM pg_catalog.pg_attribute a "
                "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND c.relname='nodes' AND a.attname='embedding'")
            if dimension != embedding_dim:
                raise ValueError()
        except (ValueError, KeyError, TypeError, asyncpg.UndefinedTableError):
            raise ValueError("postgres_schema_bootstrap_required") from None
        for query in (_PENDING, PROFILE_PENDING, CONSOLIDATION_PENDING,
                "SELECT 1 FROM profile_work WHERE collection_operation_id IS NULL",
                "SELECT 1 FROM operations o WHERE o.op_type='CONSOLIDATION_PREPARED' AND NOT EXISTS "
                "(SELECT 1 FROM consolidation_stage_fences s WHERE s.plan_id=o.target_id)"):
            if await conn.fetchrow(query + " LIMIT 1"):
                raise ValueError("postgres_recovery_required")
        # Reconstructing these heads changes publication authority. A runtime
        # login checks them and asks the bootstrap role to repair missing state.
        from prme.storage.profile_registry import decode
        from prme.storage.consolidation_publication import _unpack
        for kind, table, key in (("PROFILE_PUBLISHED", "profile_publication_heads", "profile_key"),
                                 ("CONSOLIDATION_PUBLISHED", "consolidation_publication_heads", "consolidation_key")):
            cursor = ""
            while True:
                batch = await conn.fetch("SELECT id,op_type,target_id,payload,actor_id,namespace_id FROM operations "
                    "WHERE op_type=$1 AND id>$2 ORDER BY id LIMIT 128", kind, cursor)
                if not batch:
                    break
                for row in batch:
                    cursor = row[0]
                    try:
                        plan = decode(row) if kind == "PROFILE_PUBLISHED" else _unpack(row[3])
                        generation = await conn.fetchval(f"SELECT generation FROM {table} WHERE {key}=$1", plan.key)
                        if generation is None or generation < plan.generation + 1:
                            raise ValueError()
                    except (ValueError, KeyError, TypeError):
                        raise ValueError("postgres_recovery_required") from None
        return await resolve_vector_sql(conn)

# DDL split into individual statements for asyncpg (no multi-statement execute).
_EXTENSIONS = [
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    "CREATE EXTENSION IF NOT EXISTS vector",
]

_EVENTS_TABLE = """\
CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    role VARCHAR NOT NULL,
    content TEXT NOT NULL,
    content_hash VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    session_id VARCHAR,
    scope VARCHAR NOT NULL DEFAULT 'personal',
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
)"""

_EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_user ON events (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_session ON events (user_id, session_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_events_hash ON events (content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_events_scope ON events (scope)",
]

# The embedding column dimension is set dynamically via ALTER TABLE
# after the initial CREATE TABLE, so we start without a vector column.
_NODES_TABLE = """\
CREATE TABLE IF NOT EXISTS nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_type VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    session_id VARCHAR,
    scope VARCHAR NOT NULL DEFAULT 'personal',
    content TEXT NOT NULL,
    metadata JSONB,
    confidence REAL DEFAULT 0.5,
    salience REAL DEFAULT 0.5,
    lifecycle_state VARCHAR NOT NULL DEFAULT 'tentative',
    valid_from TIMESTAMPTZ DEFAULT now(),
    valid_to TIMESTAMPTZ,
    superseded_by UUID,
    evidence_refs JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    epistemic_type VARCHAR NOT NULL DEFAULT 'asserted',
    source_type VARCHAR NOT NULL DEFAULT 'user_stated',
    decay_profile VARCHAR DEFAULT 'medium',
    last_reinforced_at TIMESTAMPTZ DEFAULT now(),
    reinforcement_boost REAL DEFAULT 0.0,
    salience_base REAL DEFAULT 0.5,
    confidence_base REAL DEFAULT 0.5,
    pinned BOOLEAN DEFAULT FALSE,
    content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', content)
    ) STORED
)"""

_NODES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_nodes_user ON nodes (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes (node_type)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_lifecycle ON nodes (lifecycle_state)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_scope ON nodes (scope)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_session "
    "ON nodes (user_id, session_id, scope, created_at, id)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_content_tsv ON nodes USING GIN (content_tsv)",
]

_EDGES_TABLE = """\
CREATE TABLE IF NOT EXISTS edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    edge_type VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    confidence REAL DEFAULT 0.5,
    valid_from TIMESTAMPTZ DEFAULT now(),
    valid_to TIMESTAMPTZ,
    provenance_event_id UUID,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
)"""

_EDGES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (source_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (target_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_type ON edges (edge_type)",
    "CREATE INDEX IF NOT EXISTS idx_edges_user ON edges (user_id)",
]

_OPERATIONS_TABLE = """\
CREATE TABLE IF NOT EXISTS operations (
    id VARCHAR PRIMARY KEY,
    op_type VARCHAR NOT NULL,
    target_id VARCHAR,
    payload JSONB,
    actor_id VARCHAR,
    namespace_id VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

_OPERATIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_operations_op_type ON operations(op_type)",
    "CREATE INDEX IF NOT EXISTS idx_operations_created_at ON operations(created_at)",
]

_LEXICAL_DOCUMENTS_TABLE = """\
CREATE TABLE IF NOT EXISTS lexical_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id VARCHAR NOT NULL,
    content TEXT NOT NULL,
    user_id VARCHAR NOT NULL,
    node_type VARCHAR NOT NULL DEFAULT 'note',
    scope VARCHAR,
    content_tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', content)
    ) STORED,
    created_at TIMESTAMPTZ DEFAULT now()
)"""

_LEXICAL_DOCUMENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_lexdocs_node ON lexical_documents (node_id)",
    "CREATE INDEX IF NOT EXISTS idx_lexdocs_user ON lexical_documents (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_lexdocs_tsv ON lexical_documents USING GIN (content_tsv)",
]


async def initialize_pg_database(
    pool: asyncpg.Pool,
    *,
    embedding_dim: int = 384,
    protected: bool = False,
) -> VectorSQL:
    """Create all PRME tables, indexes and extensions in PostgreSQL.

    Safe to call multiple times (idempotent via IF NOT EXISTS).

    Args:
        pool: asyncpg connection pool.
        embedding_dim: Dimension for the pgvector embedding column on
            the nodes table. Must match the configured embedding model.
    """
    if type(embedding_dim) is not int or embedding_dim < 1:
        raise ValueError("embedding_dim must be a positive integer")
    async with pool.acquire() as conn:
        # Extensions
        for ext_sql in _EXTENSIONS:
            await conn.execute(ext_sql)

        vector_sql = await resolve_vector_sql(conn)

        # Events
        await conn.execute(_EVENTS_TABLE.replace("    content_hash VARCHAR NOT NULL,\n", "") if protected else _EVENTS_TABLE)
        for idx in _EVENTS_INDEXES:
            if not (protected and "idx_events_hash" in idx):
                await conn.execute(idx)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS event_materializations (
                event_id UUID PRIMARY KEY REFERENCES events(id),
                status VARCHAR NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_materializations_pending "
            "ON event_materializations (status, attempts, event_id)"
        )
        await conn.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS event_time TIMESTAMPTZ")

        # Nodes (without embedding column initially)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS event_extractions (
                event_id UUID PRIMARY KEY REFERENCES events(id),
                work_order BIGSERIAL NOT NULL UNIQUE,
                status VARCHAR NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                generation BIGINT NOT NULL DEFAULT 0,
                plan_id UUID,
                lease_expires_at TIMESTAMPTZ,
                next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_error VARCHAR,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_extractions_status ON event_extractions(status, work_order)")
        await conn.execute("ALTER TABLE event_extractions ADD COLUMN IF NOT EXISTS plan_revision INTEGER DEFAULT 1")

        await conn.execute(_NODES_TABLE)
        await conn.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS event_time TIMESTAMPTZ")
        await conn.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS ttl_days INTEGER")
        # Nullable on migration: legacy vectors have unknown provenance until
        # explicitly re-embedded; never guess their model from today's config.
        await conn.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS embedding_model VARCHAR")
        await conn.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS embedding_version VARCHAR")
        for idx in _NODES_INDEXES:
            await conn.execute(idx)

        # Resolve the same table as the other DDL, never a same-named relation
        # elsewhere in the database. PostgreSQL places an index in its parent
        # table's schema; native IF NOT EXISTS handles this for HNSW too.
        await conn.execute(
            f"ALTER TABLE nodes ADD COLUMN IF NOT EXISTS embedding {vector_sql.type}({embedding_dim})"
        )
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_nodes_embedding_hnsw
            ON nodes USING hnsw (embedding {vector_sql.cosine_ops})
            WITH (m = 16, ef_construction = 64)
        """)

        # Edges
        await conn.execute(_EDGES_TABLE)
        for idx in _EDGES_INDEXES:
            await conn.execute(idx)

        # Operations
        from prme.storage.profile_publication import HEADS_DDL
        await conn.execute(HEADS_DDL)
        from prme.storage.profile_work import PROFILE_WORK_DDL
        await conn.execute(PROFILE_WORK_DDL)
        await conn.execute(_OPERATIONS_TABLE)
        for idx in _OPERATIONS_INDEXES:
            await conn.execute(idx)

        # Lexical documents
        await conn.execute(_LEXICAL_DOCUMENTS_TABLE)
        for idx in _LEXICAL_DOCUMENTS_INDEXES:
            await conn.execute(idx)

        from prme.storage.derivation_registry import initialize_pg
        await initialize_pg(conn)
        from prme.storage.profile_registry import initialize_pg as initialize_profiles
        await initialize_profiles(conn)
        from prme.storage.consolidation_publication import initialize_pg as initialize_consolidations
        await initialize_consolidations(conn)
        from prme.storage.privacy import initialize_pg as initialize_privacy
        await initialize_privacy(conn)
        if protected:
            from prme.storage.privacy_format import install_pg_format
            await install_pg_format(conn)
        await _record_runtime_schema(conn, embedding_dim=embedding_dim, protected=protected)

    logger.info("PostgreSQL schema initialized (embedding_dim=%d)", embedding_dim)

    return vector_sql
