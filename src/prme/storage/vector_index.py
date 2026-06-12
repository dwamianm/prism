"""Vector index wrapping USearch HNSW with DuckDB metadata tracking.

Provides async VectorIndex that embeds content via an EmbeddingProvider,
indexes vectors in USearch, and maps integer USearch keys to UUIDs in
a DuckDB metadata table. All queries are scoped by user_id.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import duckdb
import numpy as np
from usearch.index import Index

from prme.storage.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)


class VectorIndex:
    """Async vector index using USearch HNSW with DuckDB metadata.

    Integer USearch keys are mapped to node UUIDs via a vector_metadata
    table in DuckDB. Each vector record stores the embedding model name,
    version, and dimension for re-embedding detection after model switches.

    User_id filtering uses post-filter strategy: retrieve extra candidates
    from USearch, then validate only those matched keys against the
    metadata table (WHERE vector_key IN (...)), so the lookup is bounded
    by the over-fetch size rather than the user's total vector count.

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
        conn_lock: asyncio.Lock | None = None,
        save_interval: int = 64,
    ) -> None:
        self._conn = conn
        self._index_path = index_path
        self._provider = embedding_provider
        self._write_lock = asyncio.Lock()
        self._conn_lock = conn_lock if conn_lock is not None else asyncio.Lock()

        # Debounced save: the full USearch index file is rewritten on each
        # save, so saving on every insert is O(N^2) total write volume.
        # We save only once every ``save_interval`` inserts and flush any
        # remaining pending writes on close(). Set to 1 for save-per-insert.
        self._save_interval = max(1, save_interval)
        self._unsaved_inserts = 0

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

    def _fetch_allowed_keys(
        self,
        user_id: str,
        scope: list[str] | None,
        time_from: datetime | None,
        time_to: datetime | None,
        candidate_keys: list[int],
    ) -> dict[int, str]:
        """Fetch allowed vector keys from DuckDB (sync, runs in thread pool).

        Only the HNSW-matched ``candidate_keys`` are checked (inverted
        filter): the query is bounded at O(k) rows instead of scanning
        every vector_metadata row for the user.
        """
        if not candidate_keys:
            return {}

        # Lifecycle states eligible for retrieval (exclude superseded/archived)
        _ACTIVE_STATES = ("tentative", "stable", "contested")

        key_placeholders = ", ".join("?" for _ in candidate_keys)
        sql = (
            "SELECT vm.vector_key, vm.node_id FROM vector_metadata vm "
            "JOIN nodes n ON vm.node_id = n.id "
            f"WHERE vm.vector_key IN ({key_placeholders})"
            " AND vm.user_id = ?"
            " AND COALESCE(n.lifecycle_state, 'tentative') IN (?, ?, ?)"
        )
        params: list = [*candidate_keys, user_id, *_ACTIVE_STATES]

        if scope is not None and scope:
            placeholders = ", ".join("?" for _ in scope)
            sql += f" AND n.scope IN ({placeholders})"
            params.extend(scope)

        if time_from is not None:
            sql += (
                " AND (n.valid_to IS NULL OR n.valid_to > ? "
                "OR n.node_type IN ('ENTITY', 'PREFERENCE'))"
            )
            params.append(time_from)

        if time_to is not None:
            sql += (
                " AND (n.valid_from <= ? "
                "OR n.node_type IN ('ENTITY', 'PREFERENCE'))"
            )
            params.append(time_to)

        rows = self._conn.execute(sql, params).fetchall()
        return {row[0]: row[1] for row in rows}

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
            # DuckDB + USearch writes are synchronous; run them off the
            # event loop and serialize DuckDB access through conn_lock to
            # match the other storage backends (event-loop blocking and
            # thread-safety fix, issue #39). The full index save is
            # debounced to avoid O(N^2) disk rewrites during ingestion.
            async with self._conn_lock:
                key = await asyncio.to_thread(
                    self._do_index, node_id, content, user_id, vector
                )

        return key

    def _do_index(
        self,
        node_id: str,
        content: str,
        user_id: str,
        vector: np.ndarray,
    ) -> int:
        """Synchronous insert + (debounced) save (runs in thread pool).

        Returns the assigned vector key. Caller must hold both
        ``_write_lock`` and ``_conn_lock``.
        """
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

        # Persist to disk on the debounce interval only. Remaining
        # unsaved inserts are flushed by save()/close(). The counter is
        # reset in a finally so a failed save does not re-trigger on every
        # subsequent insert (the vector is already in the in-memory index
        # and will be persisted by the next interval save or by close()).
        self._unsaved_inserts += 1
        if self._unsaved_inserts >= self._save_interval:
            try:
                self._index.save(self._index_path)
            finally:
                self._unsaved_inserts = 0

        return key

    async def delete_by_node_id(self, node_id: str) -> int:
        """Remove all vectors for a node from USearch and DuckDB metadata.

        Used to evict superseded, archived, or rolled-back content so the
        vector keys no longer inflate the per-search candidate scan and the
        HNSW index does not grow without bound. The index is persisted after
        a removal so the deletion survives a restart.

        Args:
            node_id: UUID string of the node whose vectors to remove.

        Returns:
            The number of vector keys removed (0 if the node had none).
        """
        async with self._write_lock:
            async with self._conn_lock:
                return await asyncio.to_thread(self._do_delete, node_id)

    def _do_delete(self, node_id: str) -> int:
        """Synchronous delete from USearch + metadata (runs in thread pool).

        Returns the number of keys removed. Caller must hold both
        ``_write_lock`` and ``_conn_lock``.
        """
        rows = self._conn.execute(
            "SELECT vector_key FROM vector_metadata WHERE node_id = ?",
            [node_id],
        ).fetchall()
        if not rows:
            return 0

        keys = [row[0] for row in rows]
        for key in keys:
            # USearch raises if the key is absent; tolerate that so a
            # metadata/index drift (key in metadata but missing from the
            # HNSW index) does not abort the whole eviction. Logged at
            # debug for observability rather than silently swallowed.
            try:
                self._index.remove(key)
            except (KeyError, ValueError, RuntimeError) as exc:
                logger.debug(
                    "vector_index.remove_missing_key",
                    extra={"vector_key": key, "node_id": node_id, "error": str(exc)},
                )

        self._conn.execute(
            "DELETE FROM vector_metadata WHERE node_id = ?", [node_id]
        )
        # Persist immediately so the removal is not lost if the process
        # exits before the next debounced save.
        self._index.save(self._index_path)
        self._unsaved_inserts = 0
        return len(keys)

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

        # Run the USearch read under _write_lock and off the event loop.
        # Index writes now happen in a worker thread (issue #39), so a
        # concurrent full-index save() could otherwise interleave with a
        # search traversal. The lock serializes the USearch index access;
        # to_thread keeps the (CPU-bound) search off the event loop.
        matched_keys = await self._search_index(query_vector, k)
        if matched_keys is None:
            return []

        # Check only the matched keys against DuckDB (inverted filter:
        # WHERE vector_key IN (...) bounded at fetch_k rows, instead of
        # loading every vector_metadata row for the user). Scope/temporal
        # filters JOIN with the nodes table. All DuckDB access is
        # serialized through conn_lock to prevent concurrent thread
        # access during asyncio.gather parallelism.
        candidate_keys = [key for key, _distance in matched_keys]
        async with self._conn_lock:
            allowed_keys = await asyncio.to_thread(
                self._fetch_allowed_keys,
                user_id,
                scope,
                time_from,
                time_to,
                candidate_keys,
            )

        # Filter and map results
        results = []
        for key, distance in matched_keys:
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

    async def _search_index(
        self, query_vector: np.ndarray, k: int
    ) -> list[tuple[int, float]] | None:
        """Run the USearch nearest-neighbor read under the write lock.

        Returns a list of ``(key, distance)`` tuples ordered by ascending
        distance, or ``None`` when the index is empty. Held under
        ``_write_lock`` and executed off the event loop so it cannot
        interleave with a concurrent index ``add``/``save`` (issue #39).
        """
        async with self._write_lock:
            return await asyncio.to_thread(self._do_search_index, query_vector, k)

    def _do_search_index(
        self, query_vector: np.ndarray, k: int
    ) -> list[tuple[int, float]] | None:
        """Synchronous USearch read (runs in thread pool under _write_lock)."""
        # Over-fetch to compensate for post-filtering
        fetch_k = min(k * self._OVERFETCH_FACTOR, len(self._index))
        if fetch_k == 0:
            return None

        matches = self._index.search(query_vector, fetch_k)
        return [
            (int(matches.keys[i]), float(matches.distances[i]))
            for i in range(len(matches.keys))
        ]

    async def save(self) -> None:
        """Persist the USearch index to disk, flushing pending inserts.

        Always writes the index regardless of the debounce counter, then
        resets it. Runs off the event loop under the write lock.
        """
        async with self._write_lock:
            await asyncio.to_thread(self._index.save, self._index_path)
            self._unsaved_inserts = 0

    async def close(self) -> None:
        """Save index and clean up resources."""
        await self.save()
