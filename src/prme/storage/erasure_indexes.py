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
from uuid import uuid5
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
        lexical._erasure_known_targets = set()


def refresh_lexical(index):
    """Discard a stale writer before it can commit erased documents."""
    conn = getattr(index, "_erasure_conn", None)
    if conn is None:
        return
    with conn.cursor() as reader:
        rows = reader.execute("SELECT DISTINCT object_id FROM privacy_erasure_targets WHERE kind='node'").fetchall()
    targets = {str(row[0]) for row in rows}
    if targets == index._erasure_known_targets:
        return
    writer, index._writer = index._writer, None
    if writer is not None:
        writer.rollback()
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


@asynccontextmanager
async def _locks(indexes):
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        for index in sorted(indexes, key=id):
            await stack.enter_async_context(index._write_lock)
        yield


