"""Describe a single-file memory pack inside the pack itself (issue #247).

A directory pack carries no self-description beyond its per-vector rows. A
single-file pack keeps a singleton ``prme_manifest`` row instead: the pack
format version, the PRME version that last opened it, the embedding model its
vectors were built with, and the scoring version of the configuration that
created it.
"""

from __future__ import annotations

import logging

import duckdb

from prme import __version__
from prme.storage.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

PACK_FORMAT_VERSION = 1
"""Version of the single-file pack layout recorded in ``prme_manifest``."""


def ensure_pack_manifest(
    conn: duckdb.DuckDBPyConnection,
    embedding_provider: EmbeddingProvider,
    *,
    scoring_version: str,
) -> None:
    """Create the manifest row of a new pack, or refresh it on reopen.

    A new pack records the configured embedding model. Reopening keeps that
    record, updates ``updated_at`` and ``prme_version``, and logs a warning
    when the configured model name or dimension differs from the recorded
    one; opening is never blocked here. Startup only, on the main connection.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS prme_manifest ("
        "singleton BOOLEAN PRIMARY KEY CHECK (singleton), "
        "format_version INTEGER NOT NULL, prme_version VARCHAR NOT NULL, "
        "embedding_model VARCHAR NOT NULL, embedding_version VARCHAR NOT NULL, "
        "embedding_dim INTEGER NOT NULL, scoring_version VARCHAR NOT NULL, "
        "created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)"
    )
    row = conn.execute(
        "SELECT embedding_model, embedding_dim FROM prme_manifest WHERE singleton"
    ).fetchone()
    if row is None:
        # Two engines in one process can open the same new pack through one
        # DuckDB instance; the first row written stands.
        conn.execute(
            "INSERT OR IGNORE INTO prme_manifest VALUES "
            "(true, ?, ?, ?, ?, ?, ?, current_timestamp, current_timestamp)",
            [PACK_FORMAT_VERSION, __version__, embedding_provider.model_name,
             embedding_provider.model_version, embedding_provider.dimension, scoring_version],
        )
        return
    recorded_model, recorded_dim = row
    if (recorded_model, recorded_dim) != (embedding_provider.model_name, embedding_provider.dimension):
        logger.warning(
            "Memory pack was built with embedding model %s (dimension %d), but the "
            "configured model is %s (dimension %d); run prme rebuild to re-embed it",
            recorded_model, recorded_dim,
            embedding_provider.model_name, embedding_provider.dimension,
        )
    conn.execute(
        "UPDATE prme_manifest SET prme_version = ?, updated_at = current_timestamp WHERE singleton",
        [__version__],
    )


def record_pack_embedding(conn: duckdb.DuckDBPyConnection, embedding_provider: EmbeddingProvider) -> None:
    """Record the model that rebuilt every vector of the pack.

    ``prme rebuild`` re-embeds the whole pack with the configured provider,
    so the manifest names that model afterwards. Caller holds the engine's
    connection lock.
    """
    conn.execute(
        "UPDATE prme_manifest SET embedding_model = ?, embedding_version = ?, "
        "embedding_dim = ?, updated_at = current_timestamp WHERE singleton",
        [embedding_provider.model_name, embedding_provider.model_version,
         embedding_provider.dimension],
    )
