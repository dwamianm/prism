"""PostgreSQL-backed vector index using pgvector.

Embeddings are stored directly on the ``nodes`` table in a ``vector(N)``
column. Exact search materializes the eligible rows before ordering, so a
different owner's closer vectors cannot consume an approximate candidate budget.
Approximate search remains an explicit opt-in; HNSW filters after its index scan
and can return fewer eligible neighbors than requested.

Only existing node rows receive embeddings; indexing an unknown node logs a
debug message and does not create a fallback vector record.
"""

from __future__ import annotations

import logging
from datetime import datetime

import asyncpg

from prme.storage.pg.vector_sql import VectorSQL, resolve_vector_sql

from prme.storage.embedding import EmbeddingProvider, EmbeddingVersionMismatchError, encode_query, encode_texts

logger = logging.getLogger(__name__)


class PgVectorIndex:
    """Async vector index using pgvector on the PostgreSQL nodes table.

    Uses the ``embedding`` column on the ``nodes`` table for vector
    storage and search. Cosine distance (``<=>``) is used for ranking.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_provider: EmbeddingProvider,
        *,
        exact_search: bool = True,
        vector_sql: VectorSQL | None = None,
    ) -> None:
        self._pool = pool
        self._provider = embedding_provider
        self._exact_search = exact_search
        self._vector_sql = vector_sql

    async def _sql(self) -> VectorSQL:
        if self._vector_sql is None:
            async with self._pool.acquire() as conn:
                self._vector_sql = await resolve_vector_sql(conn)
        return self._vector_sql

    async def index(self, node_id: str, content: str, user_id: str, *, replace: bool = False) -> int:
        """Embed content and store the vector on the node row.

        Args:
            node_id: UUID string identifying the source node.
            content: Text content to embed and index.
            user_id: Owner user ID (used for provenance, not filtering here).

        Returns:
            0 (no integer key; pgvector uses the node UUID directly).
        """
        sql = await self._sql()
        embedding = await encode_texts(self._provider, [content])
        vector = embedding[0]
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"

        async with self._pool.acquire() as conn:
            # Try to update the node's embedding column first.
            result = await conn.execute(
                f"UPDATE nodes SET embedding = $1::{sql.type}, embedding_model = $3, "
                "embedding_version = $4 WHERE id = $2 AND user_id = $5",
                vector_str,
                node_id,
                self._provider.model_name,
                self._provider.model_version,
                user_id,
            )

            # Non-node IDs have no vector fallback; raw materialization creates
            # its durable node before indexing it.
            if result == "UPDATE 0":
                logger.debug(
                    "Node %s not found for embedding; content may be non-node",
                    node_id,
                )

        return 0

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
        """Search for nearest neighbors by text query."""
        vector = await encode_query(self._provider, query)
        return await self.search_by_vector(
            vector, user_id, k=k,
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

        Exact mode evaluates all eligible vectors, with stable ID ordering for
        equal distances. Approximate mode allows the planner's HNSW scan and
        may under-return after filters. Neither mode returns another owner's rows.

        Args:
            vector: Pre-computed embedding vector.
            user_id: Only return results belonging to this user.
            k: Maximum number of results to return.
            scope: Optional scope filter values.
            time_from: Optional temporal window start.
            time_to: Optional temporal window end.

        Returns:
            List of dicts with keys: node_id, score, distance.
        """
        if k <= 0:
            return []
        sql = await self._sql()
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"
        conditions: list[str] = [
            "user_id = $1",
            "embedding IS NOT NULL",
            "lifecycle_state IN ('tentative', 'stable', 'contested')",
        ]
        params: list = [user_id]
        idx = 2

        if scope is not None and scope:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(scope)))
            conditions.append(f"scope IN ({placeholders})")
            params.extend(scope)
            idx += len(scope)

        if time_from is not None:
            conditions.append(
                f"(valid_to IS NULL OR valid_to > ${idx} "
                f"OR node_type IN ('entity', 'preference'))"
            )
            params.append(time_from)
            idx += 1

        if time_to is not None:
            conditions.append(
                f"(valid_from <= ${idx} "
                f"OR node_type IN ('entity', 'preference'))"
            )
            params.append(time_to)
            idx += 1

        where = " AND ".join(conditions)

        scored = (
            f"SELECT id::text AS node_id, "
            f"  (embedding {sql.cosine} ${idx}::{sql.type}) AS distance, "
            f"embedding_model, embedding_version, {sql.dimensions}(embedding) AS embedding_dim "
            f"FROM nodes "
            f"WHERE {where} "
        )
        if self._exact_search:
            # The materialization boundary prevents LIMIT/ordering from becoming
            # an ANN scan before eligibility is established. Store only scored
            # rows in the CTE, not another copy of every embedding. Undefined
            # cosine distances (zero-norm vectors) do not become scored results.
            query = (f"WITH eligible AS MATERIALIZED ({scored}) "
                     "SELECT * FROM eligible WHERE distance < 'Infinity'::float8 "
                     f"ORDER BY distance, node_id::uuid LIMIT ${idx + 1}")
        else:
            query = (scored + f"ORDER BY embedding {sql.cosine} ${idx}::{sql.type} LIMIT ${idx + 1}")
        params.extend([vector_str, k])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        results = []
        for row in rows:
            if (row["embedding_model"], row["embedding_version"], row["embedding_dim"]) != (
                self._provider.model_name, self._provider.model_version, self._provider.dimension,
            ):
                raise EmbeddingVersionMismatchError(
                    "Stored embeddings have unknown or incompatible model metadata; run prme rebuild"
                )
            distance = float(row["distance"])
            results.append({
                "node_id": row["node_id"],
                "score": 1.0 - distance,
                "distance": distance,
            })
        return results

    async def delete_by_node_id(self, node_id: str) -> int:
        """Clear the stored vector for a node so it stops surfacing (#41).

        pgvector stores the vector in the ``nodes.embedding`` column, so
        eviction nulls that column rather than removing a row. Mirrors the
        DuckDB ``VectorIndex.delete_by_node_id`` contract (returns the number
        of vectors removed) so lifecycle eviction and the index_compaction
        job work identically across backends.

        Args:
            node_id: UUID string of the node whose vector to clear.

        Returns:
            1 if a stored vector was cleared, 0 otherwise.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE nodes SET embedding = NULL "
                "WHERE id = $1 AND embedding IS NOT NULL",
                node_id,
            )
        # asyncpg returns a status string like "UPDATE 1".
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def save(self) -> None:
        """No-op — PostgreSQL persists automatically."""

    async def close(self) -> None:
        """No-op — pool lifecycle is managed by MemoryEngine."""
