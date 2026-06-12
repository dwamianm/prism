"""Lexical index wrapping tantivy-py for BM25 full-text search.

Provides async LexicalIndex that indexes text content with English
stemming and returns BM25-ranked results scoped by user_id.
"""

from __future__ import annotations

import asyncio
import time

import tantivy


class LexicalIndex:
    """Async full-text search index using tantivy-py with BM25 ranking.

    Indexes content with English stemming tokenization for natural
    language search. All queries are scoped by user_id using tantivy's
    query parser with field-specific terms.

    Writes are buffered in a per-batch writer and committed in batches.
    The writer is created on the first add of a batch and released on the
    commit, so the exclusive tantivy directory lock is held only while a
    batch is uncommitted -- another instance can open the same index path
    when this one is idle. A commit is triggered when ``commit_interval``
    documents have accumulated, when ``commit_max_delay_s`` seconds have
    elapsed since the oldest buffered document, when ``search()`` runs with
    pending writes, or on ``close()``. The time bound is evaluated lazily on
    the next
    ``index()`` call (there is no background timer), so a lone buffered
    document with no following writes stays uncommitted until the next
    ``index()``/``search()``/``close()``. ``search()`` always flushes
    pending writes first, so a query never misses its own writes.
    Per-document commits cause tantivy segment explosion and merge churn,
    so batching is the recommended pattern. Set ``commit_interval=1`` and
    ``commit_max_delay_s=0`` for commit-per-document (legacy
    immediate-searchability behavior).

    Args:
        index_path: Directory path for the persistent tantivy index.
        commit_interval: Documents to buffer before committing (>=1).
        commit_max_delay_s: Max seconds a document may stay uncommitted
            before a commit is forced on the next index/search call. 0
            disables the time bound.
    """

    def __init__(
        self,
        index_path: str,
        commit_interval: int = 64,
        commit_max_delay_s: float = 2.0,
    ) -> None:
        self._index_path = index_path
        self._write_lock = asyncio.Lock()
        self._commit_interval = max(1, commit_interval)
        self._commit_max_delay_s = max(0.0, commit_max_delay_s)

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

        # A writer is held only while a batch has uncommitted documents:
        # created lazily on the first add of a batch and released on commit.
        # This keeps one commit per batch (instead of per document) while
        # NOT holding tantivy's exclusive directory lock between batches, so
        # other readers/writers (e.g. a CLI engine on the same path) can
        # open the index when this instance is idle.
        self._writer: tantivy.IndexWriter | None = None
        self._uncommitted = 0
        self._oldest_uncommitted_at: float | None = None

    def _ensure_writer(self) -> "tantivy.IndexWriter":
        """Return the batch writer, creating it on the first add of a batch."""
        if self._writer is None:
            self._writer = self._index.writer(heap_size=50_000_000)
        return self._writer

    def _do_index(
        self,
        node_id: str,
        content: str,
        user_id: str,
        node_type: str,
        scope: str | None,
    ) -> None:
        """Synchronous indexing operation (runs in thread pool).

        Adds the document to the long-lived writer and commits only when
        the batch interval or time delay threshold is reached.
        """
        writer = self._ensure_writer()
        doc_fields: dict = {
            "node_id": [node_id],
            "content": [content],
            "user_id": [user_id],
            "node_type": [node_type],
        }
        if scope is not None:
            doc_fields["scope"] = [scope]
        writer.add_document(tantivy.Document(**doc_fields))

        self._uncommitted += 1
        if self._oldest_uncommitted_at is None:
            self._oldest_uncommitted_at = time.monotonic()

        delay_exceeded = (
            self._commit_max_delay_s > 0.0
            and (time.monotonic() - self._oldest_uncommitted_at)
            >= self._commit_max_delay_s
        )
        if self._uncommitted >= self._commit_interval or delay_exceeded:
            self._commit_locked()

    def _commit_locked(self) -> None:
        """Commit buffered documents and reset the batch counters.

        Must be called with ``_write_lock`` held (or before any concurrent
        access). The post-commit ``reload`` makes committed docs visible to
        new searchers; ``_do_search`` also reloads, so this keeps already
        committed data current without an extra reload per add.
        """
        if self._uncommitted == 0 or self._writer is None:
            return
        # Reset the batch counters and release the writer in a finally so a
        # failed commit does not re-trigger on every subsequent add (which
        # would turn a transient failure into a per-document retry storm)
        # and never leaks the exclusive directory lock. The error still
        # propagates to the caller. wait_merging_threads consumes the writer
        # and releases the directory lock; the next add recreates it.
        try:
            self._writer.commit()
            self._index.reload()
        finally:
            self._uncommitted = 0
            self._oldest_uncommitted_at = None
            writer = self._writer
            self._writer = None
            if writer is not None:
                writer.wait_merging_threads()

    async def index(
        self,
        node_id: str,
        content: str,
        user_id: str,
        node_type: str = "note",
        scope: str | None = None,
    ) -> None:
        """Index a document for full-text search.

        The document is buffered and becomes searchable once the next
        batch commit runs (on the commit interval, after the max delay is
        observed on a later index call, on the next ``search`` call, or on
        ``close``). Any ``search()`` flushes pending writes first, so a
        query always sees documents indexed before it; for guaranteed
        immediate visibility to other readers configure ``commit_interval=1``.

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

    async def flush(self) -> None:
        """Commit any buffered documents so they become searchable."""
        async with self._write_lock:
            await asyncio.to_thread(self._commit_locked)

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

        # Parse content query leniently — tolerates apostrophes, parens,
        # colons, and other special characters in user query text.
        content_q, _unconsumed = self._index.parse_query_lenient(
            query_text, ["content"]
        )

        # Build exact-match filters via term_query (no parser involved)
        subqueries = [
            (tantivy.Occur.Must, content_q),
            (tantivy.Occur.Must, tantivy.Query.term_query(
                self._schema, "user_id", user_id
            )),
        ]
        if node_type is not None:
            subqueries.append((tantivy.Occur.Must, tantivy.Query.term_query(
                self._schema, "node_type", node_type
            )))
        if scope is not None and scope:
            if len(scope) == 1:
                subqueries.append((tantivy.Occur.Must, tantivy.Query.term_query(
                    self._schema, "scope", scope[0]
                )))
            else:
                scope_q = tantivy.Query.boolean_query([
                    (tantivy.Occur.Should, tantivy.Query.term_query(
                        self._schema, "scope", s
                    ))
                    for s in scope
                ])
                subqueries.append((tantivy.Occur.Must, scope_q))

        query = tantivy.Query.boolean_query(subqueries)
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
        # Flush buffered writes so search reflects everything indexed so
        # far (batched commits would otherwise hide recent documents).
        if self._uncommitted > 0:
            await self.flush()
        return await asyncio.to_thread(
            self._do_search, query_text, user_id, node_type, limit, scope
        )

    def _do_delete(self, node_id: str) -> None:
        """Synchronous delete + commit (runs in thread pool).

        Removes every document whose ``node_id`` term matches and commits
        immediately so the deletion is durable and visible to subsequent
        searches. The ``node_id`` field uses the ``raw`` tokenizer, so the
        term matches the stored id exactly (no stemming/punctuation
        surprises). Any documents still buffered in the current batch are
        committed first so the delete cannot miss a not-yet-committed add
        for the same node_id.
        """
        # Flush any buffered adds so a delete cannot race an uncommitted
        # add of the same document.
        if self._uncommitted > 0:
            self._commit_locked()
        writer = self._index.writer(heap_size=50_000_000)
        try:
            writer.delete_documents_by_term("node_id", node_id)
            writer.commit()
            self._index.reload()
        finally:
            writer.wait_merging_threads()

    async def delete_by_node_id(self, node_id: str) -> None:
        """Delete all documents for ``node_id`` from the index.

        Used to evict superseded, archived, or rolled-back content so it
        no longer surfaces in search and the index does not grow without
        bound. The delete is committed immediately (its own short-lived
        writer), so it is durable and visible to the next ``search``.

        Args:
            node_id: The node_id of the document(s) to delete.
        """
        async with self._write_lock:
            await asyncio.to_thread(self._do_delete, node_id)

    async def close(self) -> None:
        """Flush buffered documents and release the writer.

        Commits any documents still buffered in the batch writer so no
        indexed content is lost; the commit also releases the writer's
        exclusive directory lock. A subsequent ``index()`` call
        transparently recreates the writer.
        """
        await self.flush()
