"""Strict managed native cleanup and stale-snapshot serialization.

Durable SQL ownership decides which vectors survive. A changing DuckDB row
fences native persistence against erasure; no-op updates are not sufficient.
Physical filesystem pages, WAL, backups and unmanaged handles remain outside the
logical live-copy report.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
import os
from pathlib import Path
import shutil
from uuid import UUID, uuid4, uuid5
import weakref

from prme.privacy.policy import PrivacyDenied
from prme.storage._threading import run_to_completion

_ENGINES: dict[str, weakref.WeakSet] = {}


def enabled(conn):
    return bool(conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_name='privacy_erasure_epoch'").fetchone())


@contextmanager
def native_fence(conn):
    # Independent cursor owns the real transaction; callers retain existing
    # write_lock -> conn_lock ordering through completion of native work.
    with conn.cursor() as fence:
        fence.execute("BEGIN TRANSACTION")
        try:
            fence.execute("UPDATE privacy_erasure_epoch SET epoch=epoch+1 WHERE singleton=1")
            yield fence
            fence.execute("COMMIT")
        except BaseException:
            fence.execute("ROLLBACK")
            raise


def reconcile_vector(index, reader):
    import numpy as np
    live = {}
    reader.execute("SELECT m.vector_key,m.embedding_dim,p.vector_data FROM vector_metadata m LEFT JOIN vector_payloads p ON p.vector_key=m.vector_key ORDER BY m.vector_key")
    current = {int(key) for key in np.asarray(index._index.keys)}
    while rows := reader.fetchmany(128):
        for key, dimension, payload in rows:
            if payload is None:
                # Never recover protected ownership from an old native file.
                raise PrivacyDenied("privacy_ledger_unavailable")
            live[key] = True
            if key not in current:
                vector = np.frombuffer(payload, dtype="<f4")
                if vector.shape != (dimension,) or dimension != index._index.ndim or not np.isfinite(vector).all():
                    raise PrivacyDenied("privacy_invalid_result")
                index._index.add(key, vector)
    for key in current - live.keys():
        index._index.remove(key)
    index._snapshot_dirty = True


async def attach(engine):
    if engine._privacy.runtime is None:
        return
    _, head = await engine._privacy.erasure.repository.state()
    key = str(head.store_id)
    _ENGINES.setdefault(key, weakref.WeakSet()).add(engine)
    engine._erasure_store_id = key
    # A lexical handle must not flush its old buffered documents after another
    # managed handle has replaced the persistent directory.
    lexical = engine._lexical_index
    if hasattr(lexical, "_index"):
        lexical._erasure_conn = engine._conn
        lexical._erasure_store_id = key
        lexical._erasure_known_targets = set()
        # A directory may carry an independently restored old native snapshot.
        # The current ledger/SQL were applied before native opening; reconcile
        # loaded handles as well, before the factory exposes the engine.
        async with engine._privacy.erasure.guard():
            intents = await engine._privacy.erasure.repository.intents()
            memory = [intent for intent in intents if intent.request.mode == "memory"]
            if memory:
                from prme.storage.erasure import _discover, _purge_sql
                for intent in memory:
                    await _discover(engine._privacy.erasure.repository, intent)
                    await _purge_sql(engine._privacy.erasure.repository, intent)
                await purge_indexes(engine, memory[-1])


def refresh_lexical(index):
    """Remove erased additions while preserving unrelated buffered work."""
    conn = getattr(index, "_erasure_conn", None)
    if conn is None:
        return
    # Single-file handles share one index. Its most recently attached engine
    # can close before the last owner, so resolve a currently open connection.
    # Engine connection close holds this same lexical write lock.
    import duckdb
    reader = None
    for handle in _ENGINES.get(index._erasure_store_id, ()):
        if handle._lexical_index is index and handle._conn is not None:
            try:
                reader = handle._conn.cursor()
                break
            except duckdb.ConnectionException:
                continue
    if reader is None:
        raise PrivacyDenied("privacy_ledger_unavailable")
    with reader:
        rows = reader.execute("SELECT DISTINCT object_id FROM privacy_erasure_targets WHERE kind='node'").fetchall()
    targets = {str(row[0]) for row in rows}
    if targets == index._erasure_known_targets:
        return
    writer, index._writer = index._writer, None
    if writer is not None:
        try:
            # A delete after a buffered add removes that add in the same native
            # commit. Rolling the entire writer back would lose unrelated
            # admitted documents or staged work in the batch.
            for identifier in targets:
                writer.delete_documents_by_term("node_id", identifier)
            writer.commit()
            index._index.reload()
        except BaseException:
            writer.rollback()
            raise
        finally:
            writer.wait_merging_threads()
    index._uncommitted = 0
    index._oldest_uncommitted_at = None
    if index._index_path is not None:
        import tantivy
        index._index = tantivy.Index(index._schema, path=index._index_path)
        index._index.config_reader(reload_policy="Manual")
    index._erasure_known_targets = targets


def _documents(index, excluded):
    import tantivy
    index._index.reload()
    searcher = index._index.searcher()
    documents = {}
    for _, address in searcher.search(tantivy.Query.all_query(), limit=max(1, searcher.num_docs)).hits:
        fields = searcher.doc(address).to_dict()
        if fields["node_id"][0] not in excluded:
            documents[fields["node_id"][0]] = fields
    return documents


def compact_lexical(indexes, excluded, artifact_id):
    """Replace every known handle with a fresh index, propagating every failure."""
    import tantivy
    indexes = list(dict.fromkeys(indexes))
    documents = {}
    for index in indexes:
        index._commit_locked()
        documents.update(_documents(index, excluded))
    first = indexes[0]
    path = Path(first._index_path) if first._index_path is not None else None
    fresh_path = path.with_name(path.name + ".prme-erasure-fresh-" + artifact_id.hex) if path else None
    retired = path.with_name(path.name + ".prme-erasure-retired-" + artifact_id.hex) if path else None
    if fresh_path:
        if fresh_path.exists():
            if fresh_path.is_symlink():
                raise PrivacyDenied("privacy_invalid_result")
            shutil.rmtree(fresh_path)
        fresh_path.mkdir()
    fresh = tantivy.Index(first._schema, path=str(fresh_path)) if fresh_path else tantivy.Index(first._schema)
    fresh.config_reader(reload_policy="Manual")
    writer = fresh.writer(heap_size=15_000_000, num_threads=1)
    try:
        for identifier in sorted(documents):
            writer.add_document(tantivy.Document(**documents[identifier]))
        writer.commit()
        fresh.reload()
    finally:
        writer.wait_merging_threads()
    del writer
    if path:
        # Only exact generated paths are removed; unknown sibling files are
        # never glob-deleted. A failed swap is visible and remains retryable.
        os.replace(path, retired)
        try:
            os.replace(fresh_path, path)
        except BaseException:
            os.replace(retired, path)
            raise
        fresh = tantivy.Index(first._schema, path=str(path))
        fresh.config_reader(reload_policy="Manual")
    for index in indexes:
        index._index = fresh
        index._writer = None
        index._uncommitted = 0
        index._oldest_uncommitted_at = None
        index._stale_markers = False
        index._erasure_known_targets = set(excluded)
    if retired:
        shutil.rmtree(retired)


def recover_lexical_swaps(conn, path):
    """Replay exact registered artifact names before opening a native index."""
    from uuid import UUID
    if not enabled(conn):
        return
    rows = conn.execute("SELECT artifact_id FROM privacy_erasure_artifacts WHERE state<>'complete'").fetchall()
    base = Path(path)
    for row in rows:
        identifier = UUID(str(row[0]))
        fresh = base.with_name(base.name + ".prme-erasure-fresh-" + identifier.hex)
        retired = base.with_name(base.name + ".prme-erasure-retired-" + identifier.hex)
        if any(candidate.is_symlink() for candidate in (base, fresh, retired)):
            raise PrivacyDenied("privacy_invalid_result")
        if retired.exists():
            if not base.exists():
                os.replace(fresh if fresh.exists() else retired, base)
            if retired.exists():
                shutil.rmtree(retired)


async def purge_indexes(engine, intent):
    if intent.request.mode != "memory":
        return
    if getattr(engine._vector_index, "_pool", None) is not None:
        # Both PG native indexes are SQL columns/documents removed atomically
        # with the graph; WAL/base-page residue is reported separately.
        return
    repository = engine._privacy.erasure.repository
    async with repository.session() as session:
        rows = await session.fetch("SELECT DISTINCT object_id FROM privacy_erasure_targets WHERE kind='node'")
    excluded = {str(row["object_id"]) for row in rows}
    handles = list(_ENGINES.get(engine._erasure_store_id, ()))
    handles = [handle for handle in handles if getattr(handle, "_conn", None) is not None and not handle._closed]
    # Save each independently loaded vector object from current durable SQL;
    # its own changing-epoch fence rejects a concurrent purge or stale save.
    for handle in handles:
        await handle._vector_index.save()
    indexes = list(dict.fromkeys(handle._lexical_index for handle in handles))
    artifact_id = uuid5(intent.request.request_id, "prme:lexical-erasure:v1")
    async with repository.session(transaction=True) as session:
        await session.execute("INSERT INTO privacy_erasure_artifacts VALUES($1,$2,'prepared') ON CONFLICT DO NOTHING",
                              str(intent.request.request_id), str(artifact_id))
    async with _locks(indexes):
        async with engine._event_store._conn_lock:
            def compact():
                with native_fence(engine._conn):
                    if indexes[0]._index_path is not None:
                        recover_lexical_swaps(engine._conn, indexes[0]._index_path)
                    compact_lexical(indexes, excluded, artifact_id)
            await run_to_completion(compact)
    async with repository.session(transaction=True) as session:
        await session.execute("UPDATE privacy_erasure_artifacts SET state='complete' WHERE request_id=$1", str(intent.request.request_id))


async def rebuild_protected(engine, *, user_id, batch_size):
    """Recover saved vectors and admitted graph text, preserving other work.

    This protected rebuild does not migrate embedding models or reconstruct
    missing vector payloads from evidence. Missing durable payloads deny.
    """
    from prme.privacy.reads import ReadContext
    from prme.storage.erasure import _discover, _purge_sql
    from prme.storage.lexical_index import has_lexical_document
    from usearch.index import Index
    if engine._conn is None:
        raise RuntimeError("rebuild_indexes is only supported on the DuckDB backend")
    if not user_id or type(batch_size) is not int or not 1 <= batch_size <= 512:
        raise PrivacyDenied("privacy_configuration_invalid")
    repository = engine._privacy.erasure.repository
    counts = {"nodes_indexed": 0, "nodes_skipped": 0, "total_active": 0}
    async with engine._privacy.erasure.guard():
        for intent in await repository.intents():
            await _discover(repository, intent)
            await _purge_sql(repository, intent)
        context = await ReadContext.create(engine._privacy, user_id=user_id)
        cursor = str(UUID(int=0))
        while True:
            async with repository.session() as session:
                rows = await session.fetch("SELECT id FROM nodes WHERE user_id=$1 AND id>$2 AND lifecycle_state IN ('tentative','stable','contested') ORDER BY id LIMIT $3", user_id, cursor, batch_size)
            if not rows:
                break
            for row in rows:
                cursor = str(row["id"])
                counts["total_active"] += 1
                node = await context.project_node(await engine._graph_store.get_node(cursor))
                if node is None or not node.content:
                    counts["nodes_skipped"] += 1
                    continue
                records = await engine._privacy.repository.records_for_objects([("nodes", cursor)], owner=user_id)
                async with engine._privacy.publication_guards(records, node_ids=(node.id,)):
                    async with repository.session() as session:
                        vectors = await session.fetch("SELECT 1 FROM vector_metadata m JOIN vector_payloads p USING(vector_key) WHERE m.node_id=$1", cursor)
                        derived = await session.fetch("SELECT 1 FROM derivation_artifact_owners WHERE node_id=$1", cursor)
                    if not vectors:
                        raise PrivacyDenied("privacy_unavailable")
                    if has_lexical_document(node.node_type.value, derived=bool(derived)):
                        await engine._lexical_index.replace_many(((cursor, node.content, user_id, node.node_type.value, node.scope.value),))
                    counts["nodes_indexed"] += 1
        # Real native reconstruction from current durable numeric payloads.
        vector = engine._vector_index
        async with vector._write_lock:
            async with vector._conn_lock:
                def rebuild_vector():
                    with native_fence(engine._conn) as fence:
                        vector._index = Index(ndim=vector._provider.dimension, metric="cos", dtype="f32")
                        reconcile_vector(vector, fence)
                        vector._persist_snapshot()
                await run_to_completion(rebuild_vector)
        artifact = uuid4()
        async with repository.session(transaction=True) as session:
            await session.execute("INSERT INTO privacy_erasure_artifacts VALUES($1,$1,'prepared')", str(artifact))
            targets = await session.fetch("SELECT DISTINCT object_id FROM privacy_erasure_targets WHERE kind='node'")
        excluded = {str(row["object_id"]) for row in targets}
        indexes = list(dict.fromkeys(handle._lexical_index for handle in _ENGINES[engine._erasure_store_id] if not handle._closed))
        async with _locks(indexes):
            async with engine._event_store._conn_lock:
                def rebuild_lexical():
                    with native_fence(engine._conn):
                        compact_lexical(indexes, excluded, artifact)
                await run_to_completion(rebuild_lexical)
        async with repository.session(transaction=True) as session:
            await session.execute("UPDATE privacy_erasure_artifacts SET state='complete' WHERE request_id=$1", str(artifact))
        return await context.disclose(counts)


@asynccontextmanager
async def _locks(indexes):
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        for index in sorted(indexes, key=id):
            await stack.enter_async_context(index._write_lock)
        yield


