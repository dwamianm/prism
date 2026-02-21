"""Lexical index wrapping tantivy-py for BM25 full-text search.

Provides async LexicalIndex that indexes text content with English
stemming and returns BM25-ranked results scoped by user_id.
"""

from __future__ import annotations

import asyncio

import tantivy


class LexicalIndex:
    """Async full-text search index using tantivy-py with BM25 ranking.

    Indexes content with English stemming tokenization for natural
    language search. All queries are scoped by user_id using tantivy's
    query parser with field-specific terms.

    Documents become searchable immediately after index() returns
    (commit + reload are handled internally).

    Args:
        index_path: Directory path for the persistent tantivy index.
    """

    def __init__(self, index_path: str) -> None:
        self._index_path = index_path
        self._write_lock = asyncio.Lock()

        # Build tantivy schema
        schema_builder = tantivy.SchemaBuilder()
        schema_builder.add_text_field(
            "node_id", stored=True, tokenizer_name="raw"
        )
        schema_builder.add_text_field(
            "content", stored=True, tokenizer_name="en_stem"
        )
        schema_builder.add_text_field(
            "user_id", stored=True, tokenizer_name="raw"
        )
        schema_builder.add_text_field(
            "node_type", stored=True, tokenizer_name="raw"
        )
        schema_builder.add_text_field(
            "scope", stored=True, tokenizer_name="raw"
        )
        self._schema = schema_builder.build()

        # Create or open persistent index
        self._index = tantivy.Index(self._schema, path=index_path)

    def _do_index(
        self,
        node_id: str,
        content: str,
        user_id: str,
        node_type: str,
        scope: str | None,
    ) -> None:
        """Synchronous indexing operation (runs in thread pool)."""
        writer = self._index.writer(heap_size=50_000_000)
        doc_fields: dict = {
            "node_id": [node_id],
            "content": [content],
            "user_id": [user_id],
            "node_type": [node_type],
        }
        if scope is not None:
            doc_fields["scope"] = [scope]
        writer.add_document(tantivy.Document(**doc_fields))
        writer.commit()
        self._index.reload()

    async def index(
        self,
        node_id: str,
        content: str,
        user_id: str,
        node_type: str = "note",
        scope: str | None = None,
    ) -> None:
        """Index a document for full-text search.

        The document becomes searchable immediately after this method
        returns (commit + reload are handled internally).

        Args:
            node_id: Unique identifier for the document.
            content: Text content to index with English stemming.
            user_id: Owner user ID for access scoping.
            node_type: Type classification (e.g., 'fact', 'note', 'event').
            scope: Optional scope value (e.g., 'PERSONAL', 'PROJECT').
                When provided, enables scope-filtered search queries.
        """
        async with self._write_lock:
            await asyncio.to_thread(
                self._do_index, node_id, content, user_id, node_type, scope
            )

    def _do_search(
        self,
        query_text: str,
        user_id: str,
        node_type: str | None,
        limit: int,
        scope: list[str] | None,
    ) -> list[dict]:
        """Synchronous search operation (runs in thread pool)."""
        self._index.reload()
        searcher = self._index.searcher()

        # Build query combining content search with user_id filter
        # tantivy query syntax: content terms AND user_id:exact_match
        query_str = f"{query_text} AND user_id:{user_id}"
        if node_type is not None:
            query_str += f" AND node_type:{node_type}"

        # Scope filtering via tantivy AND clause with OR for multi-scope
        if scope is not None and scope:
            if len(scope) == 1:
                query_str += f" AND scope:{scope[0]}"
            else:
                scope_clause = " OR ".join(f"scope:{s}" for s in scope)
                query_str += f" AND ({scope_clause})"

        query = self._index.parse_query(query_str, ["content"])
        search_result = searcher.search(query, limit)

        results = []
        for score, doc_address in search_result.hits:
            doc = searcher.doc(doc_address)
            results.append({
                "node_id": doc["node_id"][0],
                "content": doc["content"][0],
                "score": float(score),
                "node_type": doc["node_type"][0],
            })

        return results

    async def search(
        self,
        query_text: str,
        user_id: str,
        *,
        node_type: str | None = None,
        limit: int = 10,
        scope: list[str] | None = None,
    ) -> list[dict]:
        """Search for documents by text query, scoped to user_id.

        Uses BM25 ranking with English stemming on the content field.
        Results are filtered to only include documents belonging to
        the specified user_id.

        Args:
            query_text: Natural language search query.
            user_id: Only return results belonging to this user.
            node_type: Optional filter by node type.
            limit: Maximum number of results to return.
            scope: Optional list of scope values to filter by (e.g. ['PERSONAL']).
                When provided, only documents indexed with a matching scope
                are returned. Documents without a scope field will not match
                (safe degradation for pre-migration data).

        Returns:
            List of dicts with keys: node_id, content, score, node_type.
            Results are ordered by descending BM25 score.
        """
        return await asyncio.to_thread(
            self._do_search, query_text, user_id, node_type, limit, scope
        )

    async def delete_by_node_id(self, node_id: str) -> None:
        """Delete a document by node_id (stub for future re-indexing).

        tantivy-py's selective deletion API is not fully documented.
        For Phase 1, this is a stub that logs a warning. Full re-index
        via delete_all + re-add is the recommended approach if needed.

        Args:
            node_id: The node_id of the document to delete.
        """
        import structlog

        logger = structlog.get_logger()
        logger.warning(
            "lexical_index.delete_by_node_id is a stub",
            node_id=node_id,
            hint="Use full re-index if deletion is needed",
        )

    async def close(self) -> None:
        """Clean up resources.

        tantivy-py handles cleanup automatically when the index
        object is garbage collected. This method exists for API
        consistency with other storage backends.
        """
        pass
