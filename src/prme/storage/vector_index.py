"""Vector index wrapping USearch HNSW with DuckDB metadata tracking.

Provides async VectorIndex that embeds content via an EmbeddingProvider,
indexes vectors in USearch, and maps integer USearch keys to UUIDs in
a DuckDB metadata table. All queries are scoped by user_id.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

import duckdb
import numpy as np
from usearch.index import Index

from prme.storage.embedding import EmbeddingProvider


class VectorIndex:
    """Async vector index using USearch HNSW with DuckDB metadata.

    Integer USearch keys are mapped to node UUIDs via a vector_metadata
    table in DuckDB. Each vector record stores the embedding model name,
    version, and dimension for re-embedding detection after model switches.

    User_id filtering uses post-filter strategy: retrieve extra candidates
    from USearch, then filter by user_id via metadata lookup. This is
    simpler and sufficient for Phase 1 scale.

    Args:
        conn: DuckDB connection for metadata storage.
        index_path: File path for persisting the USearch index.
        embedding_provider: Provider for generating text embeddings.
    """

    # Over-fetch multiplier for post-filtering by user_id
    _OVERFETCH_FACTOR = 3

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        index_path: str,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._conn = conn
        self._index_path = index_path
        self._provider = embedding_provider
        self._write_lock = asyncio.Lock()

        # Create metadata table and sequence in DuckDB
        self._init_metadata_table()

        # Create or load USearch index
        self._index = Index(
            ndim=embedding_provider.dimension,
            metric="cos",
            dtype="f32",
        )
        if os.path.exists(index_path):
            self._index.load(index_path)

    def _init_metadata_table(self) -> None:
        """Create the vector_metadata table and sequence if they don't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS vector_metadata (
                vector_key BIGINT PRIMARY KEY,
                node_id VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                embedding_model VARCHAR NOT NULL,
                embedding_version VARCHAR NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at TIMESTAMPTZ DEFAULT current_timestamp
            )
        """)
        self._conn.execute(
            "CREATE SEQUENCE IF NOT EXISTS vector_key_seq START 1"
        )
        # Indexes for fast lookups
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vector_node
            ON vector_metadata (node_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vector_user
            ON vector_metadata (user_id)
        """)

    async def index(self, node_id: str, content: str, user_id: str) -> int:
        """Embed content and add to the vector index.

        Generates an embedding via the provider, stores the vector in
        USearch, and records metadata (model info, user_id, node_id)
        in DuckDB.

        Args:
            node_id: UUID string identifying the source node.
            content: Text content to embed and index.
            user_id: Owner user ID for access scoping.

        Returns:
            The integer key assigned to the vector in USearch.
        """
        # Generate embedding (provider handles async internally)
        embedding = await self._provider.embed([content])
        vector = np.array(embedding[0], dtype=np.float32)

        async with self._write_lock:
            # Get next key from sequence
            key = self._conn.execute(
                "SELECT nextval('vector_key_seq')"
            ).fetchone()[0]

            # Insert metadata
            self._conn.execute(
                """
                INSERT INTO vector_metadata
                    (vector_key, node_id, user_id, embedding_model,
                     embedding_version, embedding_dim)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    key,
                    node_id,
                    user_id,
                    self._provider.model_name,
                    self._provider.model_version,
                    self._provider.dimension,
                ],
            )

            # Add to USearch index
            self._index.add(key, vector)

            # Persist to disk
            self._index.save(self._index_path)

        return key

    async def search(
        self,
        query: str,
        user_id: str,
        *,
        k: int = 10,
        scope: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[dict]:
        """Search for nearest neighbors by text query, scoped to user_id.

        Embeds the query text, searches USearch for candidates (over-fetching
        to compensate for user_id filtering), then filters results to only
        include vectors belonging to the specified user.

        Args:
            query: Text to search for.
            user_id: Only return results belonging to this user.
            k: Maximum number of results to return.
            scope: Optional list of scope values to filter by (e.g. ['PERSONAL', 'PROJECT']).
                When provided, only vectors for nodes matching these scopes are returned.
            time_from: Optional temporal window start. Excludes nodes with
                valid_to <= time_from (except ENTITY and PREFERENCE types).
            time_to: Optional temporal window end. Excludes nodes with
                valid_from > time_to (except ENTITY and PREFERENCE types).

        Returns:
            List of dicts with keys: node_id, score, distance.
            Score is 1 - distance (cosine similarity). Results are
            ordered by descending score (most similar first).
        """
        # Generate query embedding (provider handles async internally)
        embedding = await self._provider.embed([query])
        vector = np.array(embedding[0], dtype=np.float32)

        return await self.search_by_vector(
            vector.tolist(), user_id, k=k,
            scope=scope, time_from=time_from, time_to=time_to,
        )

    async def search_by_vector(
        self,
        vector: list[float],
        user_id: str,
        *,
        k: int = 10,
        scope: list[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> list[dict]:
        """Search for nearest neighbors by pre-computed vector.

        Args:
            vector: Pre-computed embedding vector.
            user_id: Only return results belonging to this user.
            k: Maximum number of results to return.
            scope: Optional list of scope values to filter by via JOIN
                with nodes table.
            time_from: Optional temporal window start. Excludes nodes with
                valid_to <= time_from (except ENTITY and PREFERENCE types).
            time_to: Optional temporal window end. Excludes nodes with
                valid_from > time_to (except ENTITY and PREFERENCE types).

        Returns:
            List of dicts with keys: node_id, score, distance.
        """
        query_vector = np.array(vector, dtype=np.float32)

        # Over-fetch to compensate for post-filtering
        fetch_k = min(k * self._OVERFETCH_FACTOR, len(self._index))
        if fetch_k == 0:
            return []

        # Search USearch
        matches = self._index.search(query_vector, fetch_k)

        # Build set of allowed keys via DuckDB query with scope/temporal filtering.
        # When scope or temporal filters are active, JOIN with nodes table.
        if scope is not None or time_from is not None or time_to is not None:
            # JOIN vector_metadata with nodes for scope/temporal filtering
            sql = (
                "SELECT vm.vector_key, vm.node_id FROM vector_metadata vm "
                "JOIN nodes n ON vm.node_id = n.id "
                "WHERE vm.user_id = ?"
            )
            params: list = [user_id]

            if scope is not None and scope:
                placeholders = ", ".join("?" for _ in scope)
                sql += f" AND n.scope IN ({placeholders})"
                params.extend(scope)

            if time_from is not None:
                # Exclude nodes whose validity ended before time_from,
                # but exempt ENTITY and PREFERENCE types (persistent knowledge).
                sql += (
                    " AND (n.valid_to IS NULL OR n.valid_to > ? "
                    "OR n.node_type IN ('ENTITY', 'PREFERENCE'))"
                )
                params.append(time_from)

            if time_to is not None:
                # Exclude nodes created after time_to,
                # but exempt ENTITY and PREFERENCE types.
                sql += (
                    " AND (n.valid_from <= ? "
                    "OR n.node_type IN ('ENTITY', 'PREFERENCE'))"
                )
                params.append(time_to)

            rows = self._conn.execute(sql, params).fetchall()
            allowed_keys = {row[0]: row[1] for row in rows}  # key -> node_id
        else:
            # No scope/temporal filter -- just filter by user_id (original path)
            user_keys_rows = self._conn.execute(
                "SELECT vector_key, node_id FROM vector_metadata WHERE user_id = ?",
                [user_id],
            ).fetchall()
            allowed_keys = {row[0]: row[1] for row in user_keys_rows}

        # Filter and map results
        results = []
        for i in range(len(matches.keys)):
            key = int(matches.keys[i])
            distance = float(matches.distances[i])

            if key not in allowed_keys:
                continue

            results.append({
                "node_id": allowed_keys[key],
                "score": 1.0 - distance,  # cosine similarity
                "distance": distance,
            })

            if len(results) >= k:
                break

        return results

    async def save(self) -> None:
        """Persist the USearch index to disk."""
        async with self._write_lock:
            self._index.save(self._index_path)

    async def close(self) -> None:
        """Save index and clean up resources."""
        await self.save()
