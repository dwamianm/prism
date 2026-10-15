"""Describe a single-file memory pack inside the pack itself (issue #247).

A directory pack carries no self-description beyond its per-vector rows. A
single-file pack keeps a singleton ``prme_manifest`` row instead: the pack
format version, the PRME version that last opened it, the embedding model its
vectors were built with, and the scoring version of the configuration that
created it. The PRME version and the update time change only when another
PRME version opens the pack.
"""

from __future__ import annotations

import logging

import duckdb

from prme import __version__
from prme.storage.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

PACK_FORMAT_VERSION = 1
"""Version of the single-file pack layout recorded in ``prme_manifest``."""


def _stored_vector_model(conn: duckdb.DuckDBPyConnection) -> tuple[str, str, int] | None:
    """Return the embedding model of the pack's stored vectors, or None without any.

    When the vectors name several models, the one most of them name is
    returned, with ties broken by name, version and dimension.
    """
    (tables,) = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name = 'vector_metadata'"
    ).fetchone()
    if not tables:
        return None
    return conn.execute(
        "SELECT embedding_model, embedding_version, embedding_dim FROM vector_metadata "
        "GROUP BY embedding_model, embedding_version, embedding_dim "
        "ORDER BY count(*) DESC, embedding_model, embedding_version, embedding_dim LIMIT 1"
    ).fetchone()


def _warn_on_model_change(model: str, dimension: int, embedding_provider: EmbeddingProvider) -> None:
    """Warn when the configured model differs from *model* in name or dimension."""
    if (model, dimension) != (embedding_provider.model_name, embedding_provider.dimension):
        logger.warning(
            "Memory pack was built with embedding model %s (dimension %d), but the "
            "configured model is %s (dimension %d); run prme rebuild to re-embed it",
            model, dimension, embedding_provider.model_name, embedding_provider.dimension,
        )


def ensure_pack_manifest(
    conn: duckdb.DuckDBPyConnection,
    embedding_provider: EmbeddingProvider,
    *,
    scoring_version: str,
) -> None:
    """Create the manifest row of a pack that has none, or check it on reopen.

    A pack without a manifest row records the embedding model its stored
    vectors were built with, the one most of them name if there are
    several, so a pack migrated or copied from a directory pack names the
    model of its vectors; only a pack without vectors records the configured
    model. Reopening keeps the record and updates ``prme_version`` and
    ``updated_at`` only when another PRME version opens the pack, so an open
    that writes nothing else leaves the file unchanged. A warning is logged
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
        "SELECT prme_version, embedding_model, embedding_dim FROM prme_manifest WHERE singleton"
    ).fetchone()
    if row is None:
        model = _stored_vector_model(conn) or (
            embedding_provider.model_name, embedding_provider.model_version, embedding_provider.dimension,
        )
        # Two engines in one process can open the same new pack through one
        # DuckDB instance; the first row written stands.
        conn.execute(
            "INSERT OR IGNORE INTO prme_manifest VALUES "
            "(true, ?, ?, ?, ?, ?, ?, current_timestamp, current_timestamp)",
            [PACK_FORMAT_VERSION, __version__, *model, scoring_version],
        )
        _warn_on_model_change(model[0], model[2], embedding_provider)
        return
    recorded_version, recorded_model, recorded_dim = row
    _warn_on_model_change(recorded_model, recorded_dim, embedding_provider)
    if recorded_version != __version__:
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
