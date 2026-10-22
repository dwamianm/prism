"""The DuckDB side of ``enable_multi_model_embeddings`` (dwamianm/prism#368).

The pack's ``vector_metadata`` and ``vector_payloads`` tables already record each vector's model, version and
dimension, and hold one row per node and model. What a second model needs is its own native index, so the pack
keeps one USearch file per model, named in the ``embedding_models`` table. The first model's file is the
pack's existing ``vector_path``: turning the option on adopts it, with no copy and no re-embedding, and the
vectors of any model the file did not hold are restored from their durable payloads like any missing key.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import duckdb

from prme.storage._threading import run_to_completion
from prme.storage.embedding import EmbeddingProvider
from prme.storage.multi_model import ModelIdentity, MultiModelVectorIndex
from prme.storage.vector_index import VectorIndex

if TYPE_CHECKING:
    import asyncio

    from prme.config import PRMEConfig

logger = logging.getLogger(__name__)

_REGISTRY_DDL = """
    CREATE TABLE IF NOT EXISTS embedding_models (
        embedding_model VARCHAR NOT NULL,
        embedding_version VARCHAR NOT NULL,
        embedding_dim INTEGER NOT NULL,
        index_file VARCHAR NOT NULL,
        created_at TIMESTAMPTZ DEFAULT current_timestamp,
        PRIMARY KEY (embedding_model, embedding_version)
    )
"""


def _register(conn: duckdb.DuckDBPyConnection, identity: ModelIdentity, index_file: str) -> None:
    conn.execute(
        "INSERT INTO embedding_models (embedding_model, embedding_version, embedding_dim, index_file) "
        "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [identity.name, identity.version, identity.dimension, index_file],
    )


def index_path_for(conn: duckdb.DuckDBPyConnection, vector_path: str, identity: ModelIdentity) -> str:
    """The native index file of one model, registering the model when the pack has not seen it.

    A pack that already holds vectors registers every model they came from the first time, and the model with
    the most vectors keeps the pack's existing file. A fresh pack gives its file to the first model it opens.
    Every other model gets a file named after it.
    """
    conn.execute(_REGISTRY_DDL)
    directory = os.path.dirname(os.path.abspath(vector_path))
    legacy = os.path.basename(vector_path)
    if conn.execute("SELECT count(*) FROM embedding_models").fetchone()[0] == 0:
        for position, (name, version, dimension) in enumerate(conn.execute(
            "SELECT embedding_model, embedding_version, embedding_dim FROM vector_metadata "
            "GROUP BY 1, 2, 3 ORDER BY count(*) DESC, 1, 2, 3"
        ).fetchall()):
            held = ModelIdentity(name, version, int(dimension))
            _register(conn, held, legacy if position == 0 else _model_file(legacy, held))
    row = conn.execute(
        "SELECT index_file FROM embedding_models WHERE embedding_model = ? AND embedding_version = ?",
        [identity.name, identity.version],
    ).fetchone()
    if row is None:
        legacy_taken = conn.execute(
            "SELECT 1 FROM embedding_models WHERE index_file = ?", [legacy]).fetchone() is not None
        _register(conn, identity, _model_file(legacy, identity) if legacy_taken else legacy)
        row = conn.execute(
            "SELECT index_file FROM embedding_models WHERE embedding_model = ? AND embedding_version = ?",
            [identity.name, identity.version],
        ).fetchone()
    return os.path.join(directory, row[0])


def _model_file(legacy: str, identity: ModelIdentity) -> str:
    stem, extension = os.path.splitext(legacy)
    return f"{stem}.{identity.slug()}{extension}"


def forget_model(conn: duckdb.DuckDBPyConnection, vector_path: str, identity: ModelIdentity) -> None:
    """Drop a model's registration and delete its native index file (the rows are removed by the caller)."""
    conn.execute(_REGISTRY_DDL)
    row = conn.execute(
        "SELECT index_file FROM embedding_models WHERE embedding_model = ? AND embedding_version = ?",
        [identity.name, identity.version],
    ).fetchone()
    conn.execute(
        "DELETE FROM embedding_models WHERE embedding_model = ? AND embedding_version = ?",
        [identity.name, identity.version],
    )
    if row is not None:
        path = os.path.join(os.path.dirname(os.path.abspath(vector_path)), row[0])
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def open_multi_model_index(
    conn: duckdb.DuckDBPyConnection,
    config: PRMEConfig,
    embedding_provider: EmbeddingProvider,
    conn_lock: asyncio.Lock,
    additional_providers: list[EmbeddingProvider],
) -> MultiModelVectorIndex:
    """Open the active model's index and each additional model's, and put the composite in front of them."""
    vector_path = config.vector_path

    def open_one(provider: EmbeddingProvider) -> VectorIndex:
        return VectorIndex(
            conn,
            index_path_for(conn, vector_path, ModelIdentity.of(provider)),
            provider,
            conn_lock,
            save_interval=config.vector_save_interval,
            exact_search=config.vector_exact_search,
            model_scoped=True,
        )

    async def make_index(provider: EmbeddingProvider) -> VectorIndex:
        # Opening reads the pack and may restore vectors from their payloads, so it holds the pack's lock.
        async with conn_lock:
            return await run_to_completion(open_one, provider)

    async def drop_files(identity: ModelIdentity) -> None:
        async with conn_lock:
            forget_model(conn, vector_path, identity)

    active = open_one(embedding_provider)
    extras = [open_one(provider) for provider in additional_providers]
    return MultiModelVectorIndex(active, extras, make_index=make_index, drop_model_files=drop_files)
