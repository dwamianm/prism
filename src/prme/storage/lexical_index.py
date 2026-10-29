"""Lexical index wrapping tantivy-py for BM25 full-text search.

Provides async LexicalIndex that indexes text content with English
stemming and returns BM25-ranked results scoped by user_id. Also defines
which graph nodes get a lexical document, and shares one in-memory index
among the engines of a process that open the same single-file pack.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable

import tantivy
from prme.models.derivation import DerivationPlan
from prme.models.profile import ProfilePublication
from prme.storage._threading import run_to_completion

from prme.storage.derivation_staging import DuckDBStageFence
from prme.types import NodeType

from prme.privacy.diagnostics import ScopedLogger

logger = logging.getLogger(__name__)
logger = ScopedLogger(logger)

# Every Tantivy writer indexes on one thread, so a batch's documents land in
# one segment in the order they were added. With several threads the split
# between segments depends on scheduling, and tied BM25 hits come back in
# segment order, so two builds of the same documents could cut differently
# at lexical_k. One thread keeps every batch, startup load and purge
# deterministic; a batch holds at most a few hundred documents, and the
# single-threaded startup load of 100,000 documents is measured in
# RFC-0014 (#247).
_WRITER_THREADS = 1
_WRITER_HEAP = 50_000_000

# Which graph nodes get a lexical document (issue #247). Extraction gives
# each entity node it creates a vector but no lexical document. Every other
# node gets both, an entity stored directly with store() included. The
# ingestion write path asks has_lexical_document(), and rebuild_indexes()
# and the startup load of a single-file pack select their documents with
# LEXICAL_DOCUMENT_SQL, so all three build the same index.
_DERIVED_TYPES_WITHOUT_LEXICAL_DOCUMENT = frozenset({NodeType.ENTITY.value})


def has_lexical_document(node_type: str, *, derived: bool) -> bool:
    """Whether a node of ``node_type`` gets a lexical document.

    ``derived`` means a journaled derivation, such as ``ingest()``
    extraction, created the node and reserved its identity in
    ``derivation_artifact_owners``. A protected store's direct ``store()``
    plan reserves its node too, but that node is not derived
    (``derived_artifact_sql``).
    """
    return not (derived and node_type in _DERIVED_TYPES_WITHOUT_LEXICAL_DOCUMENT)


# Whether the node a ``derivation_artifact_owners`` row reserves was derived.
# A protected store publishes a node stored directly with store() through a
# journaled plan too, so it also owns an artifact row; that plan's event has a
# direct-store request, and the node keeps the lexical document store() gave
# it. ``artifact`` is the owner row and ``node_id`` the SQL for its node's ID.
def derived_artifact_sql(node_id: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM derivation_artifact_owners artifact "
        f"WHERE artifact.node_id = {node_id} AND NOT EXISTS ("
        "SELECT 1 FROM derivation_registered_plans registered JOIN operations request "
        "ON request.target_id = registered.event_id AND request.op_type = 'DIRECT_STORE_REQUESTED' "
        "WHERE registered.operation_id = artifact.operation_id))"
    )


# has_lexical_document() as a SQL condition on a row of the ``nodes`` table.
LEXICAL_DOCUMENT_SQL = (
    "NOT (nodes.node_type IN ("
    + ", ".join(f"'{node_type}'" for node_type in sorted(_DERIVED_TYPES_WITHOUT_LEXICAL_DOCUMENT))
    + ") AND " + derived_artifact_sql("CAST(nodes.id AS VARCHAR)") + ")"
)


class _AnyLoopLock:
    """An async lock that tasks on any event loop in the process can share.

    ``asyncio.Lock`` serves a single event loop, but an in-memory index can
    be shared by engines on several loops (each ``MemoryClient`` runs its
    own). A waiter here polls a ``threading.Lock``, sleeping briefly between
    attempts, so it never blocks its loop or occupies a worker thread. A
    holder runs one bounded index operation, so waits are short.
    """

    _POLL_SECONDS = 0.001

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def locked(self) -> bool:
        return self._lock.locked()

    async def __aenter__(self) -> None:
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(self._POLL_SECONDS)

    async def __aexit__(self, *exc_info: object) -> None:
        self._lock.release()


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
        index_path: Directory path for the persistent tantivy index, or None
            for an index held only in memory. A single-file pack keeps no
            tantivy state and fills the in-memory index from its graph with
            ``load_documents()`` on open; see
            ``acquire_pack_lexical_index()``.
        commit_interval: Documents to buffer before committing (>=1).
        commit_max_delay_s: Max seconds a document may stay uncommitted
            before a commit is forced on the next index/search call. 0
            disables the time bound.
    """

    def __init__(
        self,
        index_path: str | None,
        commit_interval: int = 64,
        commit_max_delay_s: float = 2.0,
    ) -> None:
        self._index_path = index_path
        # Engines on different event loops can share an in-memory index.
        self._write_lock = _AnyLoopLock() if index_path is None else asyncio.Lock()
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

        # Create or open persistent index, or create an in-memory one
        self._index = (
            tantivy.Index(self._schema) if index_path is None
            else tantivy.Index(self._schema, path=index_path)
        )
        # All searches explicitly reload below. Tantivy's default OnCommit
        # reader adds a delayed callback that can recreate .tantivy-meta.lock
        # after close(), racing pack removal/move/encryption (#71). Manual
        # reload preserves cross-instance visibility without that callback.
        self._index.config_reader(reload_policy="Manual")

        # A writer is held only while a batch has uncommitted documents:
        # created lazily on the first add of a batch and released on commit.
        # This keeps one commit per batch (instead of per document) while
        # NOT holding tantivy's exclusive directory lock between batches, so
        # other readers/writers (e.g. a CLI engine on the same path) can
        # open the index when this instance is idle.
        self._writer: tantivy.IndexWriter | None = None
        self._uncommitted = 0
        self._oldest_uncommitted_at: float | None = None
        # Whether a deleted document still counts in an in-memory index's
        # BM25 statistics. Tantivy keeps a deleted document in its segment's
        # term frequencies and length totals until a merge rewrites the
        # segment, so a session that evicted documents would score the
        # survivors differently from the fresh index the next open of a
        # single-file pack builds from the graph. ``_purge_locked`` rebuilds
        # the in-memory index from its live documents before the next search
        # once a deletion left such a marker (#247). A directory index keeps
        # its markers on disk, as any Tantivy index does.
        self._stale_markers = False

    def _ensure_writer(self) -> "tantivy.IndexWriter":
        """Return the batch writer, creating it on the first add of a batch."""
        if self._writer is None:
            self._writer = self._index.writer(heap_size=_WRITER_HEAP, num_threads=_WRITER_THREADS)
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
        doc_fields: dict = {
            "node_id": [node_id],
            "content": [content],
            "user_id": [user_id],
            "node_type": [node_type],
        }
        if scope is not None:
            doc_fields["scope"] = [scope]
        # Build the document first, so a bad field leaves no idle writer behind.
        document = tantivy.Document(**doc_fields)
        writer = self._ensure_writer()
        writer.add_document(document)

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
        from prme.storage.erasure_indexes import refresh_lexical
        refresh_lexical(self)
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

    def _do_replace(self, node_id: str, content: str, user_id: str, node_type: str, scope: str | None) -> None:
        """Publish replacement in one commit; failure preserves the old document."""
        self._do_replace_many(((node_id, content, user_id, node_type, scope),))

    def _do_replace_many(self, replacements: tuple[tuple[str, str, str, str, str | None], ...]) -> None:
        # Construct and validate all documents before deleting any old version.
        documents = []
        identities = set()
        for node_id, content, user_id, node_type, scope in replacements:
            if node_id in identities:
                raise ValueError("Replacement batch contains duplicate node identities")
            identities.add(node_id)
            fields = {"node_id": [node_id], "content": [content], "user_id": [user_id], "node_type": [node_type]}
            if scope is not None:
                fields["scope"] = [scope]
            documents.append((node_id, tantivy.Document(**fields)))
        if not documents:
            return
        self._commit_locked()
        writer = self._ensure_writer()
        try:
            for node_id, document in documents:
                writer.delete_documents("node_id", node_id)
                writer.add_document(document)
            writer.commit()
            self._index.reload()
        except BaseException:
            writer.rollback()
            raise
        finally:
            self._writer = None
            self._uncommitted = 0
            self._oldest_uncommitted_at = None
            writer.wait_merging_threads()
        # A replaced document's old version counts beside the new one until
        # the next flush or search rebuilds an in-memory index.
        if self._index_path is None:
            searcher = self._index.searcher()
            if any(searcher.doc_freq("node_id", node_id) > 1 for node_id, _document in documents):
                self._stale_markers = True

    async def replace_many(self, replacements: tuple[tuple[str, str, str, str, str | None], ...]) -> None:
        """Atomically publish a bounded batch of exact replacement documents.

        Fields are node ID, content, owner, node type and optional scope. Duplicate
        identities are rejected. A failure before commit preserves every prior
        document; cancellation waits for the native transaction to finish before
        releasing the index lock. Returns only after the commit is durable.
        """
        snapshot = tuple((node_id, content, owner, node_type, scope)
                         for node_id, content, owner, node_type, scope in replacements)
        async with self._write_lock:
            await run_to_completion(self._do_replace_many, snapshot)

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

        With replace=True, atomically replace this node's documents and commit
        immediately. Failure before commit retains the previous document.

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
            await run_to_completion(
                self._do_replace if replace else self._do_index, node_id, content, user_id, node_type, scope
            )

    def _marker_remains(self, node_id: str) -> bool:
        """Whether an in-memory index still counts a deleted ``node_id`` in its statistics.

        ``doc_freq`` reads the segment's term dictionary, which a deletion
        does not rewrite, so it is positive exactly while the marker stays.
        A segment whose every document is deleted is dropped at the commit,
        which is why a document that was alone in its segment leaves none.
        """
        return self._index_path is None and self._index.searcher().doc_freq("node_id", node_id) > 0

    def _purge_locked(self) -> None:
        """Rebuild an in-memory index from its live documents, dropping deleted-document markers.

        Must be called with ``_write_lock`` held. The documents are re-added
        in ``node_id`` order, the order a single-file pack's open and
        ``rebuild_indexes`` use, into a fresh index that replaces the old one
        in one assignment, so a concurrent search sees either the old index
        or the complete new one. Every field is stored, so the rebuilt index
        holds the same documents; its statistics then count live documents
        alone, as a fresh build's do. The cost is one pass over the live
        documents (RFC-0014 records the measurements), paid once before the
        first search after a deletion left a marker. A rebuild that fails
        is logged and the old index serves on with its stale statistics,
        which beats failing every search until a restart.
        """
        if not self._stale_markers:
            return
        self._commit_locked()
        # An idle writer left on the old index would add later documents to
        # an index nothing searches, so none survives the swap.
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.rollback()
            writer.wait_merging_threads()
        self._index.reload()
        try:
            searcher = self._index.searcher()
            hits = searcher.search(tantivy.Query.all_query(), limit=max(1, searcher.num_docs)).hits
            documents = sorted((searcher.doc(address).to_dict() for _score, address in hits),
                               key=lambda fields: fields["node_id"][0])
            fresh = tantivy.Index(self._schema)
            fresh.config_reader(reload_policy="Manual")
            writer = fresh.writer(heap_size=_WRITER_HEAP, num_threads=_WRITER_THREADS)
            try:
                for fields in documents:
                    writer.add_document(tantivy.Document(**fields))
                writer.commit()
                fresh.reload()
            finally:
                writer.wait_merging_threads()
        except Exception:
            logger.warning("lexical.purge_failed", exc_info=True)
            self._stale_markers = False
            return
        self._index = fresh
        self._stale_markers = False

    def _prepare_search_locked(self) -> None:
        """Commit buffered documents, then drop any deleted-document markers of an in-memory index."""
        self._commit_locked()
        self._purge_locked()

    async def flush(self) -> None:
        """Commit any buffered documents so they become searchable."""
        async with self._write_lock:
            await run_to_completion(self._commit_locked)

    async def settle(self) -> None:
        """Rebuild an in-memory index into one segment in ``node_id`` order.

        ``rebuild_indexes`` adds documents in batches, which leaves several
        segments whose order Tantivy's merges decide, so tied scores could
        come back in another order than after an open, which loads every
        document in one batch. Settling makes a rebuilt single-file pack
        read exactly like a reopened one. A directory index keeps its
        segments, so this does nothing for it.
        """
        if self._index_path is not None:
            return
        async with self._write_lock:
            self._stale_markers = True
            await run_to_completion(self._prepare_search_locked)

    def load_documents(self, rows: Iterable[tuple[str, str, str, str, str | None]]) -> int:
        """Add many documents with one writer and a single commit.

        Fields are node ID, content, owner, node type and optional scope, as
        for ``index()``. This is synchronous and takes no lock: it is meant
        for startup, before the index serves any read, such as opening a
        single-file pack. ``rows`` may be a generator, so a large graph can
        be read in pages. Returns the number of documents added.
        """
        self._commit_locked()
        writer = self._ensure_writer()
        added = 0
        try:
            for node_id, content, user_id, node_type, scope in rows:
                fields = {"node_id": [node_id], "content": [content],
                          "user_id": [user_id], "node_type": [node_type]}
                if scope is not None:
                    fields["scope"] = [scope]
                writer.add_document(tantivy.Document(**fields))
                added += 1
            if added:
                writer.commit()
                self._index.reload()
        except BaseException:
            writer.rollback()
            raise
        finally:
            self._writer = None
            self._uncommitted = 0
            self._oldest_uncommitted_at = None
            writer.wait_merging_threads()
        return added

    async def stage(self, plan: DerivationPlan, *, fence: DuckDBStageFence | None = None) -> None:
        """Commit missing prepared documents once, rejecting identity conflicts.

        The directory writer lock covers comparison and publication, so a
        retry never deletes another attempt's documents. Managed ingestion
        supplies a fence covering the work generation and native write; calls
        without one remain an unmanaged component API.
        """
        plan = DerivationPlan.model_validate_json(plan.model_dump_json())
        if fence is not None:
            fence.verify_plan(plan)
        nodes = {node.id: node for node in plan.nodes}
        documents = tuple({
            "node_id": [str(doc.node_id)], "content": [doc.content],
            "user_id": [plan.user_id], "node_type": [nodes[doc.node_id].node_type.value],
            "scope": [nodes[doc.node_id].scope.value],
        } for doc in plan.lexical_documents)
        async with self._write_lock:
            if fence is not None:
                async with fence.conn_lock:
                    def guarded():
                        with fence.hold():
                            self._do_stage(documents)
                    await run_to_completion(guarded)
            else:
                await run_to_completion(self._do_stage, documents)

    async def stage_profile(self, plan: ProfilePublication, *, fence=None) -> None:
        """Durably stage one prepared profile before graph publication.

        As with unmanaged derivation staging, an interrupted preparation is
        retained until an explicit rebuild; it is not an active graph memory.
        """
        plan = ProfilePublication.model_validate_json(plan.model_dump_json())
        if fence is not None:
            fence.verify_plan(plan)
        node = plan.node
        documents = ({
            "node_id": [str(node.id)], "content": [node.content],
            "user_id": [node.user_id], "node_type": [node.node_type.value],
            "scope": [node.scope.value],
        },)
        async with self._write_lock:
            if fence is None:
                await run_to_completion(self._do_stage, documents)
            else:
                async with fence.conn_lock:
                    def guarded():
                        with fence.hold():
                            self._do_stage(documents)
                    await run_to_completion(guarded)

    async def stage_consolidation(self, plan, *, fence=None) -> None:
        """Durably stage one journaled consolidation document idempotently."""
        from prme.models.consolidation import ConsolidationPublication

        plan = ConsolidationPublication.model_validate_json(plan.model_dump_json())
        if fence is not None:
            fence.verify_plan(plan)
        node = plan.node
        documents = ({
            "node_id": [str(node.id)], "content": [node.content],
            "user_id": [node.user_id], "node_type": [node.node_type.value],
            "scope": [node.scope.value],
        },)
        async with self._write_lock:
            if fence is None:
                await run_to_completion(self._do_stage, documents)
            else:
                async with fence.conn_lock:
                    def guarded():
                        with fence.hold():
                            self._do_stage(documents)
                    await run_to_completion(guarded)

    def _do_stage(self, documents: tuple[dict, ...]) -> None:
        # Preserve unrelated normal writes before starting this isolated batch.
        self._commit_locked()
        if not documents:
            return
        writer = self._ensure_writer()
        try:
            self._index.reload()
            searcher = self._index.searcher()
            missing = []
            for fields in documents:
                query = tantivy.Query.term_query(self._schema, "node_id", fields["node_id"][0])
                found = searcher.search(query, limit=1)
                if found.count > 1:
                    raise ValueError("Prepared document conflicts with duplicate lexical identity")
                if found.hits:
                    existing = searcher.doc(found.hits[0][1])
                    if any(existing[name] != value for name, value in fields.items()):
                        raise ValueError("Prepared document conflicts with existing lexical identity")
                else:
                    missing.append(fields)
            # Validate the whole batch before any add. One commit avoids the
            # segment/merge overhead of committing every individual document.
            for fields in missing:
                writer.add_document(tantivy.Document(**fields))
            if missing:
                writer.commit()
                self._index.reload()
        except BaseException:
            # Rollback only changes since the last commit. A lost commit
            # acknowledgement must preserve the already committed documents.
            writer.rollback()
            raise
        finally:
            self._writer = None
            self._uncommitted = 0
            self._oldest_uncommitted_at = None
            writer.wait_merging_threads()

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
        # far (batched commits would otherwise hide recent documents), and
        # drop deleted-document markers so the scores are a fresh build's.
        if self._uncommitted > 0 or self._stale_markers:
            async with self._write_lock:
                await run_to_completion(self._prepare_search_locked)
        return await run_to_completion(
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
        writer = self._index.writer(heap_size=_WRITER_HEAP, num_threads=_WRITER_THREADS)
        try:
            writer.delete_documents_by_term("node_id", node_id)
            writer.commit()
            self._index.reload()
        finally:
            writer.wait_merging_threads()
        # The next flush or search rebuilds an in-memory index whose
        # statistics still count the deleted document.
        if self._marker_remains(node_id):
            self._stale_markers = True

    async def delete_profile_stage(self, plan: ProfilePublication, *, fence) -> None:
        """Delete one exact abandoned document under its preparation fence."""
        plan = ProfilePublication.model_validate_json(plan.model_dump_json())
        fence.verify_collection_plan(plan)
        node = plan.node
        expected = {'node_id': [str(node.id)], 'content': [node.content], 'user_id': [node.user_id],
                    'node_type': [node.node_type.value], 'scope': [node.scope.value]}
        def guarded():
            with fence.hold():
                self._commit_locked()
                self._index.reload()
                searcher = self._index.searcher()
                query = tantivy.Query.term_query(self._schema, 'node_id', str(node.id))
                found = searcher.search(query, limit=1)
                if found.count > 1:
                    raise ValueError('Abandoned profile has ambiguous lexical documents')
                if found.hits:
                    document = searcher.doc(found.hits[0][1])
                    if any(document[name] != value for name, value in expected.items()):
                        raise ValueError('Staged document differs from the abandoned prepared profile')
                self._do_delete(str(node.id))
        async with self._write_lock:
            async with fence.conn_lock:
                await run_to_completion(guarded)

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
            await run_to_completion(self._do_delete, node_id)

    def _do_clear(self) -> None:
        """Synchronous delete-all + commit (runs in thread pool).

        Drops every document so the index can be rebuilt from the durable
        graph nodes (issue #45). Any buffered adds are discarded first, then
        a short-lived writer deletes all documents and commits so the empty
        state is durable and visible to subsequent searches.
        """
        # Drop any buffered adds: they are about to be re-indexed from the
        # graph, so committing them first would be wasted work.
        self._writer = None
        self._uncommitted = 0
        self._oldest_uncommitted_at = None
        writer = self._index.writer(heap_size=_WRITER_HEAP, num_threads=_WRITER_THREADS)
        try:
            writer.delete_all_documents()
            writer.commit()
            self._index.reload()
        finally:
            writer.wait_merging_threads()
        # Segments with no live document are dropped at the commit.
        self._stale_markers = False

    async def clear(self) -> None:
        """Delete every document, leaving an empty index.

        Resets the lexical index to a clean slate so it can be rebuilt from
        the durable graph nodes (issue #45). The deletion is committed
        immediately so a crash mid-rebuild leaves a consistent (empty) index.
        Caller is responsible for re-indexing afterwards.
        """
        async with self._write_lock:
            await run_to_completion(self._do_clear)

    async def close(self) -> None:
        """Flush buffered documents and release the writer.

        Commits any documents still buffered in the batch writer so no
        indexed content is lost; the commit also releases the writer's
        exclusive directory lock. A subsequent ``index()`` call
        transparently recreates the writer.
        """
        await self.flush()


class _PackIndex:
    """The shared in-memory lexical index of one open single-file pack."""

    __slots__ = ("key", "index", "holders", "commit_interval", "commit_max_delay_s", "load_lock")

    def __init__(self, key: str, commit_interval: int, commit_max_delay_s: float) -> None:
        self.key = key
        self.index: LexicalIndex | None = None
        self.holders = 0
        self.commit_interval = commit_interval
        self.commit_max_delay_s = commit_max_delay_s
        # Held by the worker thread that loads the index, so an engine that
        # opens the pack meanwhile waits for that load instead of repeating it.
        self.load_lock = threading.Lock()


# Open single-file packs by real path. The lock guards the registry and the
# holder counts; it is never held while an index loads or closes.
_pack_indexes: dict[str, _PackIndex] = {}
_pack_indexes_lock = threading.Lock()


def _load_pack_index(entry: _PackIndex, load: Callable[[LexicalIndex], object]) -> LexicalIndex:
    with entry.load_lock:
        if entry.index is None:
            index = LexicalIndex(
                None, commit_interval=entry.commit_interval, commit_max_delay_s=entry.commit_max_delay_s,
            )
            # A failed load leaves no index, so the next engine loads afresh.
            load(index)
            entry.index = index
        return entry.index


async def _release_pack_index(entry: _PackIndex) -> None:
    with _pack_indexes_lock:
        entry.holders -= 1
        last = entry.holders == 0
        if last and _pack_indexes.get(entry.key) is entry:
            del _pack_indexes[entry.key]
    if last and entry.index is not None:
        await entry.index.close()


async def acquire_pack_lexical_index(
    pack_path: str,
    load: Callable[[LexicalIndex], object],
    *,
    commit_interval: int = 64,
    commit_max_delay_s: float = 2.0,
) -> LexicalIndex:
    """Return the in-memory lexical index of a single-file pack.

    Every engine in this process that opens the pack shares one index, so a
    document indexed through one engine is found through the others at once,
    as with a directory pack's on-disk index. Packs are told apart by their
    real path, so two spellings of one pack share an index. The first engine
    creates the index with its commit settings and fills it with
    ``load(index)``; an engine that opens the pack during that load waits
    for it, and if the load fails, the next engine loads a fresh index. Each
    acquisition must be matched by one ``release_pack_lexical_index()``.
    """
    key = os.path.realpath(pack_path)
    with _pack_indexes_lock:
        entry = _pack_indexes.get(key)
        if entry is None:
            entry = _pack_indexes[key] = _PackIndex(key, commit_interval, commit_max_delay_s)
        entry.holders += 1
    try:
        return await run_to_completion(_load_pack_index, entry, load)
    except BaseException:
        await _release_pack_index(entry)
        raise


async def release_pack_lexical_index(index: LexicalIndex) -> None:
    """Release one engine's hold on a pack's shared lexical index.

    The last release removes the pack from the registry and closes the index.
    """
    with _pack_indexes_lock:
        entry = next((entry for entry in _pack_indexes.values() if entry.index is index), None)
    if entry is None:
        raise ValueError("This lexical index is not held for an open single-file pack")
    await _release_pack_index(entry)
