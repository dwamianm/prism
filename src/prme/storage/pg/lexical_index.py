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

# Distinct query terms a search uses, in query order. Each term costs one
# indexed count and one check per hit, so a pasted paragraph must not multiply
# the work without bound; 32 terms covers any question.
MAX_QUERY_TERMS = 32


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
        *, replace: bool = False,
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
                conflict = (
                    "ON CONFLICT (node_id) DO UPDATE SET content = EXCLUDED.content, "
                    "node_type = EXCLUDED.node_type, scope = EXCLUDED.scope "
                    "WHERE lexical_documents.user_id = EXCLUDED.user_id"
                ) if replace else "ON CONFLICT DO NOTHING"
                await conn.execute(
                    "INSERT INTO lexical_documents (node_id, content, user_id, node_type, scope) "
                    "VALUES ($1, $2, $3, $4, $5) " + conflict,
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
        """Search for documents that match the query's terms.

        Searches across both the ``nodes`` and ``lexical_documents``
        tables, combining results with UNION ALL and deduplicating.

        ``plainto_tsquery`` normalizes the query into terms. When some
        document contains every term, only such documents are returned, as
        before. Otherwise a document matches when it contains any term, so a
        question that no single memory answers word for word still finds the
        memory that shares the most terms with it. With more than one term, a
        term found in at least half of the searched documents (the owner's
        name, say) cannot tell them apart: when some other term matches, such
        terms do not select candidates, so they do not make every document a
        candidate, though they still count when ranking.
        Results rank by the number of distinct terms they match and then by
        ``ts_rank_cd``; a query with a single term keeps the ``ts_rank_cd``
        score. Only the first ``MAX_QUERY_TERMS`` distinct terms are used.

        Args:
            query_text: Natural language search query.
            user_id: Only return results belonging to this user.
            node_type: Optional filter by node type.
            limit: Maximum number of results.
            scope: Optional scope filter values.

        Returns:
            List of dicts with keys: node_id, content, score, node_type.
        """
        # Both sources use the same parameters and enforce isolation before
        # deduplication. Duplicate copies must not consume the candidate limit.
        params: list = [user_id, query_text]
        filters: list[str] = []
        if node_type is not None:
            filters.append(f"node_type = ${len(params) + 1}")
            params.append(node_type)
        if scope is not None and scope:
            placeholders = ", ".join(f"${len(params) + 1 + i}" for i in range(len(scope)))
            filters.append(f"scope IN ({placeholders})")
            params.extend(scope)
        nodes_where = " AND ".join(["n.user_id = $1", *(f"n.{f}" for f in filters)])
        docs_where = " AND ".join(["d.user_id = $1", *(f"d.{f}" for f in filters)])
        params.append(limit)
        limit_idx = len(params)

        def searched_ids(match: str | None = None) -> str:
            """IDs of the searched documents, narrowed by a tsquery when given."""
            nodes = f" AND n.content_tsv @@ {match}" if match else ""
            docs = f" AND d.content_tsv @@ {match}" if match else ""
            return (f"SELECT n.id::text AS node_id FROM nodes n WHERE {nodes_where}{nodes} "
                    f"UNION ALL SELECT d.node_id FROM lexical_documents d "
                    f"WHERE {docs_where}{docs}")

        # plainto_tsquery joins its terms with " & "; a lexeme never contains
        # a space, so splitting its text on that operator yields the terms.
        # With more than one term, a term counts as common when it matches at
        # least half of the searched documents, where the classic BM25
        # (Robertson-Sparck Jones) inverse document frequency is no longer
        # positive. Without another matching term, every term is searched.
        # Common terms still count when ranking the hits: scores add 1 + the
        # best ts_rank_cd per extra matched query term, so a hit that matches
        # more terms always ranks higher and a single-term query keeps its
        # scores. When a hit matches every query term, hits that match fewer
        # are dropped, which keeps the old all-terms result: under rank fusion
        # they would otherwise take the next lexical rank, nearly as strong as
        # the first.
        query = f"""
            WITH query_terms AS (
                SELECT term FROM (
                    SELECT operand AS term, min(position) AS position
                    FROM unnest(string_to_array(
                        plainto_tsquery('english', $2)::text, ' & '))
                        WITH ORDINALITY AS operands (operand, position)
                    GROUP BY operand
                ) AS distinct_terms
                ORDER BY position
                LIMIT {MAX_QUERY_TERMS}
            ), informative_terms AS (
                SELECT t.term
                FROM query_terms t
                CROSS JOIN LATERAL (
                    SELECT count(DISTINCT node_id) AS matches
                    FROM ({searched_ids("t.term::tsquery")}) AS matching
                ) AS f
                WHERE (SELECT count(*) FROM query_terms) > 1
                  AND f.matches > 0
                  AND 2 * f.matches < (
                      SELECT count(DISTINCT node_id)
                      FROM ({searched_ids()}) AS searched)
            ), search_terms AS (
                SELECT term::tsquery AS term FROM informative_terms
                UNION ALL
                SELECT term::tsquery FROM query_terms
                WHERE NOT EXISTS (SELECT 1 FROM informative_terms)
            ), search AS (
                SELECT
                    (SELECT string_agg(term::text, ' | ')::tsquery
                     FROM search_terms) AS query,
                    string_agg(term, ' | ')::tsquery AS ranking,
                    count(*) AS terms
                FROM query_terms
            ), combined AS (
                SELECT
                    n.id::text AS node_id,
                    n.content,
                    n.content_tsv AS tsv,
                    n.node_type,
                    0 AS source_priority
                FROM nodes n
                WHERE {nodes_where}
                  AND n.content_tsv @@ (SELECT query FROM search)

                UNION ALL

                SELECT
                    d.node_id,
                    d.content,
                    d.content_tsv,
                    d.node_type,
                    1 AS source_priority
                FROM lexical_documents d
                WHERE {docs_where}
                  AND d.content_tsv @@ (SELECT query FROM search)
            ), hits AS (
                SELECT
                    c.node_id,
                    c.content,
                    c.node_type,
                    c.source_priority,
                    ts_rank_cd(c.tsv, (SELECT ranking FROM search))::float8 AS rank,
                    (SELECT count(*) FROM query_terms t
                     WHERE c.tsv @@ t.term::tsquery) AS matched_terms
                FROM combined c
            ), unique_hits AS (
                SELECT DISTINCT ON (node_id)
                    node_id, content, node_type, rank, matched_terms
                FROM hits
                ORDER BY node_id, matched_terms DESC, rank DESC, source_priority
            ), scored AS (
                SELECT
                    node_id,
                    content,
                    node_type,
                    matched_terms,
                    max(matched_terms) OVER () AS best_matched,
                    rank + (matched_terms - 1) * (1 + max(rank) OVER ()) AS score
                FROM unique_hits
            )
            SELECT node_id, content, score, node_type FROM scored
            WHERE best_matched < (SELECT terms FROM search)
               OR matched_terms = (SELECT terms FROM search)
            ORDER BY score DESC, node_id COLLATE "C"
            LIMIT ${limit_idx}
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [{"node_id": row["node_id"], "content": row["content"],
                 "score": float(row["score"]), "node_type": row["node_type"]}
                for row in rows]

    async def delete_by_node_id(self, node_id: str) -> None:
        """Delete a document from lexical_documents by node_id."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM lexical_documents WHERE node_id = $1", node_id
            )

    async def close(self) -> None:
        """No-op — pool lifecycle is managed by MemoryEngine."""
