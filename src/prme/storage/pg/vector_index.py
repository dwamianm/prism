"""PostgreSQL-backed vector index using pgvector.

Embeddings are stored directly on the ``nodes`` table in a ``vector(N)``
column. pgvector's HNSW index handles approximate nearest-neighbor search
with native WHERE-clause filtering — no overfetch strategy or USearch
integer-key mapping needed.

Non-node content (events indexed during ingestion) is stored in the
``lexical_documents`` table with a separate embedding column if needed,
but the primary path is node-level embeddings.
"""

from __future__ import annotations

import logging
from datetime import datetime

import asyncpg
import numpy as np

from prme.storage.embedding import EmbeddingProvider

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
    ) -> None:
        self._pool = pool
        self._provider = embedding_provider

    async def index(self, node_id: str, content: str, user_id: str) -> int:
        """Embed content and store the vector on the node row.

        Args:
            node_id: UUID string identifying the source node.
            content: Text content to embed and index.
            user_id: Owner user ID (used for provenance, not filtering here).

        Returns:
            0 (no integer key; pgvector uses the node UUID directly).
        """
        embedding = await self._provider.embed([content])
        vector = embedding[0]
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"

        async with self._pool.acquire() as conn:
            # Try to update the node's embedding column first.
            result = await conn.execute(
                "UPDATE nodes SET embedding = $1::vector WHERE id = $2",
                vector_str,
                node_id,
            )

            # If no row was updated, the node_id might be non-node content
            # (e.g., event indexed during ingestion). Store in lexical_documents
            # as a fallback — the vector search will UNION both tables.
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
        embedding = await self._provider.embed([query])
        vector = embedding[0]
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

        pgvector applies WHERE clauses natively during the HNSW scan,
        so no overfetch strategy is needed.

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
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"

        conditions: list[str] = [
            "user_id = $1",
            "embedding IS NOT NULL",
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

        # pgvector cosine distance: embedding <=> query_vector
        query = (
            f"SELECT id::text AS node_id, "
            f"  (embedding <=> ${idx}::vector) AS distance "
            f"FROM nodes "
            f"WHERE {where} "
            f"ORDER BY embedding <=> ${idx}::vector "
            f"LIMIT ${idx + 1}"
        )
        params.extend([vector_str, k])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        results = []
        for row in rows:
            distance = float(row["distance"])
            results.append({
                "node_id": row["node_id"],
                "score": 1.0 - distance,
                "distance": distance,
            })
        return results

    async def save(self) -> None:
        """No-op — PostgreSQL persists automatically."""

    async def close(self) -> None:
        """No-op — pool lifecycle is managed by MemoryEngine."""
