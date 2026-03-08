"""PostgreSQL DDL schema for PRME.

Mirrors the DuckDB schema (src/prme/storage/schema.py) with
PostgreSQL-native types: JSONB, pgvector, tsvector GENERATED columns,
and GIN/HNSW indexes.

Called once on startup via ``initialize_pg_database(pool)``.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

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
) -> None:
    """Create all PRME tables, indexes and extensions in PostgreSQL.

    Safe to call multiple times (idempotent via IF NOT EXISTS).

    Args:
        pool: asyncpg connection pool.
        embedding_dim: Dimension for the pgvector embedding column on
            the nodes table. Must match the configured embedding model.
    """
    async with pool.acquire() as conn:
        # Extensions
        for ext_sql in _EXTENSIONS:
            await conn.execute(ext_sql)

        # Events
        await conn.execute(_EVENTS_TABLE)
        for idx in _EVENTS_INDEXES:
            await conn.execute(idx)

        # Nodes (without embedding column initially)
        await conn.execute(_NODES_TABLE)
        for idx in _NODES_INDEXES:
            await conn.execute(idx)

        # Add embedding vector column if it doesn't exist.
        has_embedding = await conn.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'nodes' AND column_name = 'embedding'"
        )
        if has_embedding is None:
            await conn.execute(
                f"ALTER TABLE nodes ADD COLUMN embedding vector({embedding_dim})"
            )
            logger.info(
                "Added embedding column to nodes (dim=%d)", embedding_dim
            )

        # Create HNSW index on embedding column (if not exists).
        # Use a DO block to conditionally create since IF NOT EXISTS
        # isn't supported for HNSW indexes on all PG versions.
        await conn.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE indexname = 'idx_nodes_embedding_hnsw'
                ) THEN
                    CREATE INDEX idx_nodes_embedding_hnsw
                    ON nodes USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64);
                END IF;
            END
            $$
        """)

        # Edges
        await conn.execute(_EDGES_TABLE)
        for idx in _EDGES_INDEXES:
            await conn.execute(idx)

        # Operations
        await conn.execute(_OPERATIONS_TABLE)
        for idx in _OPERATIONS_INDEXES:
            await conn.execute(idx)

        # Lexical documents
        await conn.execute(_LEXICAL_DOCUMENTS_TABLE)
        for idx in _LEXICAL_DOCUMENTS_INDEXES:
            await conn.execute(idx)

    logger.info("PostgreSQL schema initialized (embedding_dim=%d)", embedding_dim)
