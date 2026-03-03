"""PostgreSQL-backed full-text search index using tsvector/tsquery.

Uses PostgreSQL's built-in full-text search with ``tsvector`` GENERATED
columns on the ``nodes`` and ``lexical_documents`` tables. The ``nodes``
table's ``content_tsv`` column is auto-maintained by PostgreSQL — no
explicit indexing step is needed for graph nodes. Non-node content
(events, summaries) is stored in the ``lexical_documents`` table.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


class PgLexicalIndex:
    """Async full-text search index using PostgreSQL tsvector/tsquery.

    The ``nodes`` table has a ``content_tsv`` GENERATED ALWAYS column
    that auto-indexes content. This class handles:
    - Indexing non-node content into ``lexical_documents``
    - Searching across both ``nodes`` and ``lexical_documents``
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def index(
        self,
        node_id: str,
        content: str,
        user_id: str,
        node_type: str = "note",
        scope: str | None = None,
    ) -> None:
        """Index a document for full-text search.

        For graph nodes: this is mostly a no-op because the ``nodes``
        table's GENERATED ``content_tsv`` column handles it. However,
        we insert into ``lexical_documents`` as well so that content
        indexed at ingestion time (which may not have a matching node
        row yet, or may be non-node content) is also searchable.

        Args:
            node_id: Unique identifier for the document.
            content: Text content to index.
            user_id: Owner user ID for access scoping.
            node_type: Type classification.
            scope: Optional scope value.
        """
        async with self._pool.acquire() as conn:
            # Check if a node with this ID exists — if so, the GENERATED
            # column already handles tsvector. Only insert into
            # lexical_documents for non-node content.
            exists = await conn.fetchval(
                "SELECT 1 FROM nodes WHERE id = $1::uuid", node_id
            )
            if exists is None:
                await conn.execute(
                    """
                    INSERT INTO lexical_documents (node_id, content, user_id, node_type, scope)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT DO NOTHING
                    """,
                    node_id,
                    content,
                    user_id,
                    node_type,
                    scope,
                )

    async def search(
        self,
        query_text: str,
        user_id: str,
        *,
        node_type: str | None = None,
        limit: int = 10,
        scope: list[str] | None = None,
    ) -> list[dict]:
        """Search for documents by text query using ts_rank_cd.

        Searches across both the ``nodes`` and ``lexical_documents``
        tables, combining results with UNION ALL and deduplicating.

        Args:
            query_text: Natural language search query.
            user_id: Only return results belonging to this user.
            node_type: Optional filter by node type.
            limit: Maximum number of results.
            scope: Optional scope filter values.

        Returns:
            List of dicts with keys: node_id, content, score, node_type.
        """
        # Build WHERE clauses for both tables
        # Nodes query
        n_conditions = ["n.user_id = $1", "n.content_tsv @@ plainto_tsquery('english', $2)"]
        n_params: list = [user_id, query_text]
        n_idx = 3

        if node_type is not None:
            n_conditions.append(f"n.node_type = ${n_idx}")
            n_params.append(node_type)
            n_idx += 1

        if scope is not None and scope:
            placeholders = ", ".join(f"${n_idx + i}" for i in range(len(scope)))
            n_conditions.append(f"n.scope IN ({placeholders})")
            n_params.extend(scope)
            n_idx += len(scope)

        n_where = " AND ".join(n_conditions)

        # lexical_documents query — same parameter positions offset
        d_conditions = ["d.user_id = $1", "d.content_tsv @@ plainto_tsquery('english', $2)"]
        if node_type is not None:
            # Reuse the same parameter index as nodes query for node_type
            d_conditions.append(f"d.node_type = ${3 if node_type else n_idx}")

        # Build the UNION ALL query using a single parameter set.
        # We rebuild with unified parameters to keep it simple.
        params: list = [user_id, query_text]
        idx = 3

        # Nodes WHERE
        nodes_conds = [
            "n.user_id = $1",
            "n.content_tsv @@ plainto_tsquery('english', $2)",
        ]
        if node_type is not None:
            nodes_conds.append(f"n.node_type = ${idx}")
            params.append(node_type)
            idx += 1
        if scope is not None and scope:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(scope)))
            nodes_conds.append(f"n.scope IN ({placeholders})")
            params.extend(scope)
            idx += len(scope)

        # Docs WHERE (reuses same parameter positions)
        docs_conds = [
            "d.user_id = $1",
            "d.content_tsv @@ plainto_tsquery('english', $2)",
        ]
        if node_type is not None:
            # node_type param was already added at position 3
            docs_conds.append(f"d.node_type = $3")
        if scope is not None and scope:
            # scope params follow node_type. Compute correct indices.
            scope_start = 3 + (1 if node_type is not None else 0)
            placeholders = ", ".join(
                f"${scope_start + i}" for i in range(len(scope))
            )
            docs_conds.append(f"d.scope IN ({placeholders})")

        nodes_where = " AND ".join(nodes_conds)
        docs_where = " AND ".join(docs_conds)

        limit_idx = idx
        params.append(limit)

        query = f"""
            SELECT node_id, content, score, node_type FROM (
                SELECT
                    n.id::text AS node_id,
                    n.content,
                    ts_rank_cd(n.content_tsv, plainto_tsquery('english', $2)) AS score,
                    n.node_type
                FROM nodes n
                WHERE {nodes_where}

                UNION ALL

                SELECT
                    d.node_id,
                    d.content,
                    ts_rank_cd(d.content_tsv, plainto_tsquery('english', $2)) AS score,
                    d.node_type
                FROM lexical_documents d
                WHERE {docs_where}
            ) combined
            ORDER BY score DESC
            LIMIT ${limit_idx}
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        # Deduplicate by node_id (keep highest score)
        seen: dict[str, dict] = {}
        for row in rows:
            nid = row["node_id"]
            score = float(row["score"])
            if nid not in seen or score > seen[nid]["score"]:
                seen[nid] = {
                    "node_id": nid,
                    "content": row["content"],
                    "score": score,
                    "node_type": row["node_type"],
                }

        results = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
        return results[:limit]

    async def delete_by_node_id(self, node_id: str) -> None:
        """Delete a document from lexical_documents by node_id."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM lexical_documents WHERE node_id = $1", node_id
            )

    async def close(self) -> None:
        """No-op — pool lifecycle is managed by MemoryEngine."""
