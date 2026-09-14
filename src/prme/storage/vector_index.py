"""Vector index wrapping USearch HNSW with DuckDB metadata tracking.

Provides async VectorIndex that embeds content via an EmbeddingProvider,
indexes vectors in USearch, and maps integer USearch keys to UUIDs in
a DuckDB metadata table. All queries are scoped by user_id.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime

import duckdb
import numpy as np
from usearch.index import Index

from prme.models.derivation import PreparedEmbedding
from prme.storage._threading import run_to_completion
from prme.storage.derivation_staging import DuckDBStageFence
from prme.storage.profile_work import ProfileStageFence
from prme.models.profile import ProfilePublication
from prme.storage.embedding import EmbeddingProvider, EmbeddingVersionMismatchError, encode_query, encode_texts

logger = logging.getLogger(__name__)


class VectorIndex:
    """Async vector index using USearch HNSW with DuckDB metadata.

    Integer USearch keys are mapped to node UUIDs via a vector_metadata
    table in DuckDB. Each vector record stores the embedding model name,
    version, and dimension for re-embedding detection after model switches.

    User_id filtering uses post-filter strategy: retrieve extra candidates
    from USearch, then validate only those matched keys against the
    metadata table (WHERE vector_key IN (...)). The candidate window grows
    until enough distinct eligible nodes are found or the index is exhausted,
    so other tenants and excluded nodes cannot starve a user's results.

    Args:
        conn: DuckDB connection for metadata storage.
        index_path: File path for persisting the USearch index.
        embedding_provider: Provider for generating text embeddings.
    """

    # Initial over-fetch multiplier; expand when filtering leaves fewer than k.
    _OVERFETCH_FACTOR = 3

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        index_path: str,
        embedding_provider: EmbeddingProvider,
        conn_lock: asyncio.Lock | None = None,
        save_interval: int = 64,
        exact_search: bool = True,
    ) -> None:
        self._conn = conn
        self._index_path = index_path
        self._provider = embedding_provider
        self._write_lock = asyncio.Lock()
        self._conn_lock = conn_lock if conn_lock is not None else asyncio.Lock()

        # Exact (brute-force) vs approximate (HNSW) nearest-neighbor search.
        # HNSW traversal is order/thread-dependent, so two rebuilds from the
        # same event log can return different neighbor sets. Exact search is
        # order-independent and makes retrieval reproducible (issue #45).
        self._exact_search = exact_search

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
            # USearch 2.23.0 can segfault when adding to a loaded snapshot
            # whose final vector was removed before it was saved. Discarding
            # that empty native object is lossless; durable payload recovery
            # below repopulates a fresh index when metadata still owns vectors.
            if len(self._index) == 0:
                self._index = Index(
                    ndim=embedding_provider.dimension,
                    metric="cos",
                    dtype="f32",
                )
        self._recover_vectors()

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
        # Keep the numerical output in the same durable transaction as metadata.
        # The USearch file is a debounced snapshot, not the only copy of an
        # embedding. Existing packs are backfilled from their loaded snapshot.
        # A separate table avoids ALTER TABLE replay failures on DuckDB 1.4.x
        # when upgrading packs whose metadata has a function-based default.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS vector_payloads (
                vector_key BIGINT PRIMARY KEY,
                vector_data BLOB NOT NULL
            )
        """)
        # A prepared write owns a fixed node identity and exact source text.
        # Keep this claim in the payload transaction so a failed native add
        # can be retried without allocating another vector key.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS vector_staging (
                node_id VARCHAR PRIMARY KEY,
                vector_key BIGINT NOT NULL UNIQUE,
                content VARCHAR NOT NULL
            )
        """)
        # Indexes for fast lookups
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vector_node
            ON vector_metadata (node_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vector_user
            ON vector_metadata (user_id)
        """)

    def _recover_vectors(self) -> None:
        """Reconcile the snapshot against durable metadata before serving reads.

        Read vector payloads in bounded batches. New records can be recovered
        without inference; old records can only be backfilled when their vector
        is still present in the snapshot. An already missing legacy vector
        requires ``prme rebuild`` and is reported explicitly.
        """
        # Export keys in one native call; scalar Sequence iteration repeatedly
        # asks USearch to locate an offset in its key collection.
        orphan_keys = {int(key) for key in np.asarray(self._index.keys)}
        restored = missing_legacy = max_key = 0
        # A single ordered read avoids re-scanning the remaining payload table
        # for every page. A separate cursor keeps its result alive while legacy
        # backfill writes use the main connection. Both are startup-only here.
        with self._conn.cursor() as reader:
            reader.execute(
                "SELECT vm.vector_key, vm.embedding_dim, vp.vector_key IS NOT NULL "
                "FROM vector_metadata vm LEFT JOIN vector_payloads vp "
                "ON vm.vector_key = vp.vector_key ORDER BY vm.vector_key"
            )
            while rows := reader.fetchmany(256):
                max_key = max(max_key, rows[-1][0])
                backfill = []
                missing = {}
                for key, dimension, has_payload in rows:
                    if key in orphan_keys:
                        orphan_keys.remove(key)
                        if not has_payload:
                            vector = np.asarray(self._index.get(key), dtype="<f4")
                            if vector.shape == (dimension,) and np.isfinite(vector).all():
                                backfill.append((key, vector.tobytes()))
                    elif not has_payload:
                        missing_legacy += 1
                    else:
                        missing[key] = dimension
                # Intact packs need only key metadata. Fetch float payloads
                # solely for missing vectors, avoiding a full embedding read
                # and Python byte allocation on every normal startup.
                if missing:
                    placeholders = ",".join("?" for _ in missing)
                    payloads = self._conn.execute(
                        "SELECT vector_key, vector_data FROM vector_payloads "
                        f"WHERE vector_key IN ({placeholders}) ORDER BY vector_key",
                        list(missing),
                    ).fetchall()
                    if len(payloads) != len(missing):
                        raise ValueError("Vector payloads changed during startup recovery")
                    for key, payload in payloads:
                        dimension = missing[key]
                        vector = np.frombuffer(payload, dtype="<f4")
                        if (vector.shape != (dimension,) or dimension != self._index.ndim
                                or not np.isfinite(vector).all()):
                            raise ValueError(
                                "Cannot restore vector payload: invalid dimensions or values. "
                                "Use the pack's embedding configuration and rebuild its indexes."
                            )
                        self._index.add(key, vector)
                        restored += 1
                if backfill:
                    self._conn.executemany(
                        "INSERT INTO vector_payloads VALUES (?, ?)",
                        backfill,
                    )
        # DuckDB WAL recovery can restore a payload without preserving the
        # corresponding nextval advance. Rebase once at startup, beyond both
        # durable keys and the sequence's recorded position. Otherwise the
        # first new vector after a process exit can collide with a saved key.
        start, last = self._conn.execute(
            "SELECT start_value, last_value FROM duckdb_sequences() "
            "WHERE sequence_name = 'vector_key_seq' AND schema_name = current_schema() "
            "AND database_name = current_database()"
        ).fetchone()
        next_key = max(max_key + 1, start, (last or 0) + 1)
        self._conn.execute(f"CREATE OR REPLACE SEQUENCE vector_key_seq START {int(next_key)}")
        for key in orphan_keys:
            self._index.remove(key)
        if restored or orphan_keys:
            self._save_snapshot()
            logger.info(
                "vector_index.recovered", extra={
                    "restored_vectors": restored, "removed_orphans": len(orphan_keys),
                },
            )
        if missing_legacy:
            logger.warning(
                "Vector snapshot is missing %d legacy embeddings without durable "
                "payloads; run prme rebuild to restore their vector search.", missing_legacy,
            )

    def _save_snapshot(self) -> None:
        """Replace the last complete snapshot only after the new file is written."""
        parent = os.path.dirname(os.path.abspath(self._index_path))
        descriptor, temporary = tempfile.mkstemp(prefix=".prme-vector-", dir=parent)
        os.close(descriptor)
        try:
            self._index.save(temporary)
            os.replace(temporary, self._index_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _fetch_allowed_keys(
        self,
        user_id: str,
        scope: list[str] | None,
        time_from: datetime | None,
        time_to: datetime | None,
        candidate_keys: list[int],
    ) -> dict[int, str]:
        """Fetch allowed vector keys from DuckDB (sync, runs in thread pool).

        Only the HNSW-matched ``candidate_keys`` are checked. Selective
        filters may require expanding this window up to the whole index.
        """
        if not candidate_keys:
            return {}

        # Lifecycle states eligible for retrieval (exclude superseded/archived)
        _ACTIVE_STATES = ("tentative", "stable", "contested")

        key_placeholders = ", ".join("?" for _ in candidate_keys)
        sql = (
            "SELECT vm.vector_key, vm.node_id, vm.embedding_model, "
            "vm.embedding_version, vm.embedding_dim FROM vector_metadata vm "
            "JOIN nodes n ON vm.node_id = n.id "
            f"WHERE vm.vector_key IN ({key_placeholders})"
            " AND vm.user_id = ? AND n.user_id = vm.user_id"
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
                "OR n.node_type IN ('entity', 'preference'))"
            )
            params.append(time_from)

        if time_to is not None:
            sql += (
                " AND (n.valid_from <= ? "
                "OR n.node_type IN ('entity', 'preference'))"
            )
            params.append(time_to)

        rows = self._conn.execute(sql, params).fetchall()
        expected = (self._provider.model_name, self._provider.model_version, self._provider.dimension)
        if any(tuple(row[2:]) != expected for row in rows):
            raise EmbeddingVersionMismatchError(
                "Stored embeddings use a different model, version, or dimension; run prme rebuild"
            )
        return {row[0]: row[1] for row in rows}

    async def index(self, node_id: str, content: str, user_id: str, *, replace: bool = False) -> int:
        """Embed content and add to the vector index.

        With replace=True, publish the new vector before removing previous
        keys for this node, so inference or insertion failures preserve old hits.

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
        embedding = await encode_texts(self._provider, [content])
        if len(embedding) != 1:
            raise ValueError("Embedding provider must return exactly one vector per input")
        vector = np.array(embedding[0], dtype=np.float32)
        if (vector.shape != (self._provider.dimension,) or vector.shape != (self._index.ndim,)
                or not np.isfinite(vector).all()):
            raise ValueError("Embedding provider returned invalid dimensions or non-finite values")

        async with self._write_lock:
            # DuckDB + USearch writes are synchronous; run them off the
            # event loop and serialize DuckDB access through conn_lock to
            # match the other storage backends (event-loop blocking and
            # thread-safety fix, issue #39). The full index save is
            # debounced to avoid O(N^2) disk rewrites during ingestion.
            async with self._conn_lock:
                key = await run_to_completion(
                    self._do_index, node_id, content, user_id, vector
                )
                if replace:
                    await run_to_completion(self._do_delete, node_id, keep_key=key)

        return key

    async def stage(self, embedding: PreparedEmbedding, *, user_id: str, fence: DuckDBStageFence | ProfileStageFence | None = None) -> int:
        """Durably stage a saved embedding without inference or replacement.

        Repeating identical input reuses its key, including after a native
        index failure. Conflicting identities fail without changing existing
        data. Managed ingestion supplies a fence that holds its work generation
        against takeover throughout the native write. Calls without a fence are
        an unmanaged component API; this method does not publish a graph node.
        """
        embedding = PreparedEmbedding.model_validate_json(embedding.model_dump_json())
        if not user_id:
            raise ValueError("Prepared embedding requires an owner")
        if fence is not None:
            if fence.conn is not self._conn or fence.conn_lock is not self._conn_lock:
                raise ValueError("Stage fence and vector index must share their connection and lock")
            fence.verify_embedding(embedding, user_id)
        expected = (self._provider.model_name, self._provider.model_version, self._provider.dimension)
        if ((embedding.model, embedding.version, embedding.dimension) != expected
                or embedding.dimension != self._index.ndim):
            raise EmbeddingVersionMismatchError("Prepared embedding differs from the configured index")
        vector = np.asarray(embedding.values, dtype=np.float32)
        if not np.any(vector):
            raise ValueError("Prepared embedding underflows to a zero float32 vector")
        async with self._write_lock:
            async with self._conn_lock:
                if fence is not None:
                    def guarded():
                        with fence.hold():
                            return self._do_stage(embedding, user_id, vector)
                    return await run_to_completion(guarded)
                return await run_to_completion(self._do_stage, embedding, user_id, vector)

    def _do_stage(self, embedding: PreparedEmbedding, user_id: str, vector: np.ndarray) -> int:
        node_id = str(embedding.node_id)
        rows = self._conn.execute(
            "SELECT vm.vector_key, vm.user_id, vm.embedding_model, vm.embedding_version, "
            "vm.embedding_dim, vp.vector_data, vs.content FROM vector_metadata vm "
            "LEFT JOIN vector_payloads vp USING (vector_key) "
            "LEFT JOIN vector_staging vs ON vm.vector_key = vs.vector_key AND vm.node_id = vs.node_id "
            "WHERE vm.node_id = ?", [node_id],
        ).fetchall()
        if rows:
            expected = (user_id, embedding.model, embedding.version, embedding.dimension,
                        vector.astype("<f4", copy=False).tobytes(), embedding.content)
            if len(rows) != 1 or tuple(rows[0][1:]) != expected:
                raise ValueError("Prepared embedding conflicts with existing vector identity")
            key = rows[0][0]
            if not self._index.contains(key):
                self._add_vector(key, vector)
            return key
        return self._do_index(node_id, embedding.content, user_id, vector,
                              prepared=embedding)

    def _do_index(
        self,
        node_id: str,
        content: str,
        user_id: str,
        vector: np.ndarray,
        *,
        prepared: PreparedEmbedding | None = None,
    ) -> int:
        """Synchronous insert + (debounced) save (runs in thread pool).

        Returns the assigned vector key. Caller must hold both
        ``_write_lock`` and ``_conn_lock``.
        """
        # Get next key from sequence
        key = self._conn.execute(
            "SELECT nextval('vector_key_seq')"
        ).fetchone()[0]

        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._conn.execute(
                """
                INSERT INTO vector_metadata
                    (vector_key, node_id, user_id, embedding_model,
                     embedding_version, embedding_dim)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [key, node_id, user_id,
                 prepared.model if prepared else self._provider.model_name,
                 prepared.version if prepared else self._provider.model_version,
                 prepared.dimension if prepared else self._provider.dimension],
            )
            self._conn.execute(
                "INSERT INTO vector_payloads VALUES (?, ?)",
                [key, vector.astype("<f4", copy=False).tobytes()],
            )
            if prepared is not None:
                self._conn.execute("INSERT INTO vector_staging VALUES (?, ?, ?)",
                                   [node_id, key, content])
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

        self._add_vector(key, vector)
        return key

    def _add_vector(self, key: int, vector: np.ndarray) -> None:
        """Add a durable vector to the native index with debounced snapshots."""
        self._index.add(key, vector)

        # Persist to disk on the debounce interval only. Remaining
        # unsaved inserts are flushed by save()/close(). The counter is
        # reset in a finally so a failed save does not re-trigger on every
        # subsequent insert (the vector is already in the in-memory index
        # and will be persisted by the next interval save or by close()).
        self._unsaved_inserts += 1
        if self._unsaved_inserts >= self._save_interval:
            try:
                self._save_snapshot()
            finally:
                self._unsaved_inserts = 0

    async def delete_profile_stage(self, plan: ProfilePublication, *, fence) -> int:
        """Delete only exact, abandoned, uniquely owned prepared profile vectors."""
        plan = ProfilePublication.model_validate_json(plan.model_dump_json())
        fence.verify_collection_plan(plan)
        if fence.conn is not self._conn or fence.conn_lock is not self._conn_lock:
            raise ValueError('Collection fence must share the vector connection and lock')
        def guarded():
            with fence.hold():
                rows = self._conn.execute(
                    'SELECT vm.user_id,vm.embedding_model,vm.embedding_version,vm.embedding_dim,vs.content,vp.vector_data '
                    'FROM vector_metadata vm LEFT JOIN vector_staging vs ON vs.vector_key=vm.vector_key AND vs.node_id=vm.node_id '
                    'LEFT JOIN vector_payloads vp ON vp.vector_key=vm.vector_key WHERE vm.node_id=?',
                    [str(plan.node.id)]).fetchall()
                expected = (plan.node.user_id, plan.embedding.model, plan.embedding.version, plan.embedding.dimension,
                            plan.node.content, np.asarray(plan.embedding.values, dtype='<f4').tobytes())
                if len(rows) > 1 or any(tuple(row) != expected for row in rows):
                    raise ValueError('Staged vector differs from the abandoned prepared profile')
                return self._do_delete(str(plan.node.id))
        async with self._write_lock:
            async with self._conn_lock:
                return await run_to_completion(guarded)

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
                return await run_to_completion(self._do_delete, node_id)

    def _do_delete(self, node_id: str, *, keep_key: int | None = None) -> int:
        """Synchronous delete from USearch + metadata (runs in thread pool).

        Returns the number of keys removed. Caller must hold both
        ``_write_lock`` and ``_conn_lock``.
        """
        suffix = " AND vector_key <> ?" if keep_key is not None else ""
        params = [node_id, keep_key] if keep_key is not None else [node_id]
        rows = self._conn.execute(
            "SELECT vector_key FROM vector_metadata WHERE node_id = ?" + suffix, params,
        ).fetchall()
        if not rows:
            return 0

        keys = [row[0] for row in rows]
        for key in keys:
            # Missing native keys are harmless drift. A genuine native removal
            # error must preserve the durable retry anchor instead of silently
            # deleting metadata and making the leftover vector undiscoverable.
            if self._index.contains(key):
                self._index.remove(key)

        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._conn.execute("DELETE FROM vector_staging WHERE node_id = ?" + suffix, params)
            self._conn.execute(
                "DELETE FROM vector_payloads WHERE vector_key IN "
                "(SELECT vector_key FROM vector_metadata WHERE node_id = ?" + suffix + ")", params,
            )
            self._conn.execute(
                "DELETE FROM vector_metadata WHERE node_id = ?" + suffix, params
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        # Persist immediately so the removal is not lost if the process
        # exits before the next debounced save.
        self._save_snapshot()
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
        vector = np.array(await encode_query(self._provider, query), dtype=np.float32)

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
        if k <= 0:
            return []

        query_vector = np.array(vector, dtype=np.float32)
        if self._index.ndim != self._provider.dimension:
            raise EmbeddingVersionMismatchError(
                "Stored vector dimension differs from the configured model; run prme rebuild"
            )
        if query_vector.shape != (self._provider.dimension,) or not np.isfinite(query_vector).all():
            raise ValueError("Query embedding has invalid dimensions or non-finite values")

        # Run the USearch read under _write_lock and off the event loop.
        # Index writes now happen in a worker thread (issue #39), so a
        # concurrent full-index save() could otherwise interleave with a
        # search traversal. The lock serializes the USearch index access;
        # to_thread keeps the (CPU-bound) search off the event loop.
        fetch_k = k * self._OVERFETCH_FACTOR
        while True:
            matched_keys = await self._search_index(query_vector, fetch_k)
            if not matched_keys:
                return []

            # Apply the same eligibility filters on every expansion. DuckDB
            # access remains serialized during candidate-path parallelism.
            candidate_keys = [key for key, _distance in matched_keys]
            async with self._conn_lock:
                allowed_keys = await run_to_completion(
                    self._fetch_allowed_keys,
                    user_id,
                    scope,
                    time_from,
                    time_to,
                    candidate_keys,
                )

            # Rebuild from the latest distance-ordered window: approximate
            # search can change its neighbor set when the window grows.
            results = []
            seen_nodes: set[str] = set()
            for key, distance in matched_keys:
                node_id = allowed_keys.get(key)
                if node_id is None or node_id in seen_nodes:
                    continue
                seen_nodes.add(node_id)
                results.append({
                    "node_id": node_id,
                    "score": 1.0 - distance,
                    "distance": distance,
                })
                if len(results) >= k:
                    return results

            if len(matched_keys) < fetch_k:
                return results
            fetch_k *= 2

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
            return await run_to_completion(self._do_search_index, query_vector, k)

    def _do_search_index(
        self, query_vector: np.ndarray, k: int
    ) -> list[tuple[int, float]] | None:
        """Synchronous USearch read (runs in thread pool under _write_lock)."""
        # The caller controls expansion after eligibility filtering.
        fetch_k = min(k, len(self._index))
        if fetch_k == 0:
            return None

        # exact=True forces brute-force cosine over all vectors, which is
        # order-independent and reproducible. exact=False uses the HNSW graph
        # (faster on large corpora, but order/thread-dependent neighbor sets).
        matches = self._index.search(query_vector, fetch_k, exact=self._exact_search)
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
            await run_to_completion(self._save_snapshot)
            self._unsaved_inserts = 0

    async def clear(self) -> None:
        """Drop every vector and its metadata, leaving an empty index.

        Resets the in-memory USearch index and the vector_metadata table to
        a clean slate so the index can be rebuilt from the durable graph
        nodes (issue #45). The empty index is persisted immediately so a
        crash mid-rebuild leaves a consistent (empty) artifact rather than a
        stale one. Caller is responsible for re-indexing afterwards.
        """
        async with self._write_lock:
            async with self._conn_lock:
                await run_to_completion(self._do_clear)

    def _do_clear(self) -> None:
        """Synchronous index + metadata reset (runs in thread pool).

        Caller must hold both ``_write_lock`` and ``_conn_lock``.
        """
        # Recreate an empty USearch index with the same geometry.
        self._index = Index(
            ndim=self._provider.dimension,
            metric="cos",
            dtype="f32",
        )
        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._conn.execute("DELETE FROM vector_staging")
            self._conn.execute("DELETE FROM vector_payloads")
            self._conn.execute("DELETE FROM vector_metadata")
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        # The sequence is intentionally not reset: keys stay monotonic so a
        # rebuild never reuses a key that an in-flight reference might hold.
        self._save_snapshot()
        self._unsaved_inserts = 0

    async def close(self) -> None:
        """Save index and clean up resources."""
        await self.save()
