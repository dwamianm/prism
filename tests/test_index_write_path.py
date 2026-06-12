"""Tests for the batched/debounced ingestion index write path (issue #39).

Covers:
- VectorIndex debounced HNSW save (save every N inserts, flush on close).
- LexicalIndex batched tantivy commits, flush-on-search, flush-on-close.
- Failure-safe counter reset so a failed save/commit does not retry-storm.
- IngestionPipeline bounding concurrent LLM extraction with a semaphore.

Uses mock providers and tmp_path for isolation; no LLM or model download.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import duckdb
import pytest
import pytest_asyncio

from prme.ingestion.pipeline import IngestionPipeline
from prme.ingestion.schema import ExtractionResult
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.event_store import EventStore
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
from prme.storage.write_queue import WriteQueue


# --- Mock providers -------------------------------------------------------


class MockEmbeddingProvider:
    """Deterministic mock embedding provider (8-dim)."""

    @property
    def model_name(self) -> str:
        return "mock-embed"

    @property
    def model_version(self) -> str:
        return "mock-1.0"

    @property
    def dimension(self) -> int:
        return 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            base = (hash(t) % 100) / 100.0
            out.append([(base + j * 0.01) % 1.0 for j in range(8)])
        return out


# --- Fixtures -------------------------------------------------------------


@pytest_asyncio.fixture
async def conn(tmp_path: Path):
    db_path = str(tmp_path / "test.duckdb")
    c = duckdb.connect(db_path)
    initialize_database(c)
    yield c
    c.close()


# --- VectorIndex: debounced save -----------------------------------------


async def test_vector_save_debounced_to_interval(tmp_path: Path, conn) -> None:
    """The index file is written only once the save interval is reached."""
    vector_path = tmp_path / "vectors.usearch"
    vidx = VectorIndex(
        conn, str(vector_path), MockEmbeddingProvider(), save_interval=3
    )

    # First two inserts must not write the file to disk.
    await vidx.index("node-1", "alpha", "u1")
    assert not vector_path.exists()
    await vidx.index("node-2", "beta", "u1")
    assert not vector_path.exists()

    # The third insert hits the interval and persists.
    await vidx.index("node-3", "gamma", "u1")
    assert vector_path.exists()


async def test_vector_close_flushes_pending_inserts(
    tmp_path: Path, conn
) -> None:
    """close() persists inserts that have not yet hit the save interval."""
    vector_path = tmp_path / "vectors.usearch"
    vidx = VectorIndex(
        conn, str(vector_path), MockEmbeddingProvider(), save_interval=100
    )

    await vidx.index("node-1", "alpha", "u1")
    assert not vector_path.exists()  # below interval, not yet saved

    await vidx.close()
    assert vector_path.exists()  # flushed on close


async def test_vector_search_after_index_sees_uncommitted_inserts(
    tmp_path: Path, conn
) -> None:
    """In-memory adds are searchable before the debounced disk save."""
    vector_path = tmp_path / "vectors.usearch"
    vidx = VectorIndex(
        conn, str(vector_path), MockEmbeddingProvider(), save_interval=100
    )

    # Insert a node and register it in the graph so the search JOIN matches.
    await vidx.index("11111111-1111-1111-1111-111111111111", "hello world", "u1")
    conn.execute(
        """
        INSERT INTO nodes (id, user_id, node_type, content, lifecycle_state,
            scope, salience, confidence, created_at, updated_at)
        VALUES (?, ?, 'EVENT', ?, 'tentative', 'PERSONAL', 0.5, 0.5,
            current_timestamp, current_timestamp)
        """,
        ["11111111-1111-1111-1111-111111111111", "u1", "hello world"],
    )

    # Not yet saved to disk, but searchable from the in-memory index.
    assert not vector_path.exists()
    results = await vidx.search("hello world", "u1", k=5)
    assert any(
        r["node_id"] == "11111111-1111-1111-1111-111111111111" for r in results
    )


async def test_vector_failed_save_resets_counter(
    tmp_path: Path, conn, monkeypatch
) -> None:
    """A save that raises does not leave the counter stuck (no retry storm)."""
    vector_path = tmp_path / "vectors.usearch"
    vidx = VectorIndex(
        conn, str(vector_path), MockEmbeddingProvider(), save_interval=1
    )

    calls = {"n": 0}
    real_save = vidx._index.save

    def flaky_save(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_save(path)

    monkeypatch.setattr(vidx._index, "save", flaky_save)

    # First insert triggers a save that raises; the error propagates.
    with pytest.raises(OSError):
        await vidx.index("node-1", "alpha", "u1")

    # Counter was reset, so the next insert attempts a fresh save (not a
    # stuck retry of the same failed state) and succeeds.
    await vidx.index("node-2", "beta", "u1")
    assert calls["n"] == 2
    assert vidx._unsaved_inserts == 0


# --- LexicalIndex: batched commits ---------------------------------------


async def test_lexical_search_flushes_pending_docs(tmp_path: Path) -> None:
    """A document indexed below the commit interval is found via search."""
    lex_path = tmp_path / "lexical"
    lex_path.mkdir()
    lidx = LexicalIndex(
        str(lex_path), commit_interval=100, commit_max_delay_s=0.0
    )

    await lidx.index("node-1", "the quick brown fox", "u1", "event", "PERSONAL")
    # Buffered, not yet committed by interval -- but search must flush first.
    results = await lidx.search("quick fox", "u1", limit=5)
    assert any(r["node_id"] == "node-1" for r in results)


async def test_lexical_commit_on_interval(tmp_path: Path) -> None:
    """Reaching the commit interval makes docs visible without a flush."""
    lex_path = tmp_path / "lexical"
    lex_path.mkdir()
    lidx = LexicalIndex(
        str(lex_path), commit_interval=2, commit_max_delay_s=0.0
    )

    await lidx.index("node-1", "apple banana", "u1", "event", "PERSONAL")
    assert lidx._uncommitted == 1  # buffered
    await lidx.index("node-2", "cherry apple", "u1", "event", "PERSONAL")
    assert lidx._uncommitted == 0  # interval reached -> committed


async def test_lexical_close_flushes_buffered_docs(tmp_path: Path) -> None:
    """close() commits documents still buffered in the long-lived writer."""
    lex_path = tmp_path / "lexical"
    lex_path.mkdir()
    lidx = LexicalIndex(
        str(lex_path), commit_interval=100, commit_max_delay_s=0.0
    )
    await lidx.index("node-1", "persisted content", "u1", "event", "PERSONAL")
    assert lidx._uncommitted == 1
    await lidx.close()
    assert lidx._uncommitted == 0

    # Reopen on the same path -- the committed doc is durable and searchable.
    reopened = LexicalIndex(
        str(lex_path), commit_interval=100, commit_max_delay_s=0.0
    )
    results = await reopened.search("persisted content", "u1", limit=5)
    assert any(r["node_id"] == "node-1" for r in results)


async def test_lexical_commit_on_max_delay(tmp_path: Path) -> None:
    """A buffered doc commits when the time bound is observed on next index."""
    lex_path = tmp_path / "lexical"
    lex_path.mkdir()
    lidx = LexicalIndex(
        str(lex_path), commit_interval=1000, commit_max_delay_s=0.05
    )

    await lidx.index("node-1", "first doc", "u1", "event", "PERSONAL")
    assert lidx._uncommitted == 1
    await asyncio.sleep(0.06)
    # The next index call observes the elapsed delay and forces a commit.
    await lidx.index("node-2", "second doc", "u1", "event", "PERSONAL")
    assert lidx._uncommitted == 0


class _FlakyWriter:
    """Wraps a tantivy writer; raises on the first commit, then delegates."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.commit_calls = 0

    def add_document(self, doc) -> None:
        self._inner.add_document(doc)

    def commit(self):
        self.commit_calls += 1
        if self.commit_calls == 1:
            raise RuntimeError("commit boom")
        return self._inner.commit()

    def wait_merging_threads(self):
        return self._inner.wait_merging_threads()


async def test_lexical_failed_commit_resets_counter(tmp_path: Path) -> None:
    """A commit that raises does not leave the counter stuck."""
    lex_path = tmp_path / "lexical"
    lex_path.mkdir()
    lidx = LexicalIndex(
        str(lex_path), commit_interval=1, commit_max_delay_s=0.0
    )
    # The batch writer is created lazily on first add; wrap it so the first
    # commit raises. _ensure_writer returns the existing (wrapped) writer.
    lidx._writer = _FlakyWriter(lidx._index.writer(heap_size=50_000_000))

    with pytest.raises(RuntimeError):
        await lidx.index("node-1", "alpha", "u1", "event", "PERSONAL")
    # Counter reset despite the failure -> next add does not re-fire the
    # same failed commit on top of a stuck count, and the writer was
    # released (no leaked directory lock).
    assert lidx._uncommitted == 0
    assert lidx._writer is None


# --- IngestionPipeline: bounded extraction concurrency -------------------


class _GatedExtractionProvider:
    """Extraction provider that tracks peak concurrency under a gate."""

    def __init__(self, release: asyncio.Event) -> None:
        self._release = release
        self.active = 0
        self.peak = 0

    @property
    def provider_name(self) -> str:
        return "gated"

    @property
    def model_name(self) -> str:
        return "gated-extract"

    async def extract(self, content: str, *, role: str = "user"):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await self._release.wait()
        finally:
            self.active -= 1
        return ExtractionResult(
            entities=[], facts=[], relationships=[], summary=None
        )


async def test_extraction_concurrency_is_bounded(tmp_path: Path, conn) -> None:
    """ingest of N messages runs at most max_concurrent_extractions at once."""
    from prme.ingestion.graph_writer import WriteQueueGraphWriter

    conn_lock = asyncio.Lock()
    event_store = EventStore(conn, conn_lock)
    graph_store = DuckPGQGraphStore(conn, conn_lock)
    vidx = VectorIndex(
        conn, str(tmp_path / "v.usearch"), MockEmbeddingProvider(), conn_lock
    )
    lex_path = tmp_path / "lex"
    lex_path.mkdir()
    lidx = LexicalIndex(str(lex_path))
    wq = WriteQueue(maxsize=1000)
    await wq.start()

    release = asyncio.Event()
    provider = _GatedExtractionProvider(release)
    pipeline = IngestionPipeline(
        event_store=event_store,
        graph_store=graph_store,
        vector_index=vidx,
        lexical_index=lidx,
        extraction_provider=provider,
        write_queue=wq,
        graph_writer=WriteQueueGraphWriter(graph_store, wq),
        max_concurrent_extractions=3,
    )

    # Launch more messages than the limit; none complete until released.
    for i in range(10):
        await pipeline.ingest(f"message {i}", user_id="u1")

    # Let the gated extractions pile up against the semaphore.
    await asyncio.sleep(0.1)
    assert provider.peak <= 3
    assert provider.active <= 3

    release.set()
    await pipeline.shutdown()
    await wq.stop()
    assert provider.peak <= 3


# --- Index deletion (issue #41) ------------------------------------------


async def test_vector_delete_by_node_id_removes_vector(
    tmp_path: Path, conn
) -> None:
    """delete_by_node_id drops the vector and its metadata; search misses it."""
    vidx = VectorIndex(
        conn, str(tmp_path / "v.usearch"), MockEmbeddingProvider(), save_interval=1
    )
    await vidx.index("keep", "alpha content", "u1")
    await vidx.index("drop", "beta content", "u1")

    # Two vectors are present in the raw HNSW index before deletion.
    assert len(vidx._index) == 2

    removed = await vidx.delete_by_node_id("drop")
    assert removed == 1

    # Metadata row for the dropped node is gone; the kept one remains.
    dropped = conn.execute(
        "SELECT COUNT(*) FROM vector_metadata WHERE node_id = ?", ["drop"]
    ).fetchone()[0]
    assert dropped == 0
    kept = conn.execute(
        "SELECT COUNT(*) FROM vector_metadata WHERE node_id = ?", ["keep"]
    ).fetchone()[0]
    assert kept == 1

    # The key is removed from the HNSW index itself, not just the metadata.
    assert len(vidx._index) == 1

    await vidx.close()


async def test_vector_delete_missing_node_is_noop(tmp_path: Path, conn) -> None:
    """Deleting a node with no vectors returns 0 and does not raise."""
    vidx = VectorIndex(
        conn, str(tmp_path / "v.usearch"), MockEmbeddingProvider(), save_interval=1
    )
    removed = await vidx.delete_by_node_id("never-indexed")
    assert removed == 0
    await vidx.close()


async def test_lexical_delete_by_node_id_removes_doc(tmp_path: Path) -> None:
    """delete_by_node_id removes a document so it no longer surfaces."""
    lex_path = tmp_path / "lexical"
    lex_path.mkdir()
    lidx = LexicalIndex(str(lex_path), commit_interval=100, commit_max_delay_s=0.0)

    await lidx.index("keep", "shared keyword here", "u1", "fact", "PERSONAL")
    await lidx.index("drop", "shared keyword too", "u1", "fact", "PERSONAL")

    await lidx.delete_by_node_id("drop")

    results = await lidx.search("shared keyword", "u1", limit=10)
    ids = {r["node_id"] for r in results}
    assert "keep" in ids
    assert "drop" not in ids

    await lidx.close()


async def test_lexical_delete_then_reindex_same_node(tmp_path: Path) -> None:
    """A node can be re-indexed after deletion (delete flushes buffered adds)."""
    lex_path = tmp_path / "lexical"
    lex_path.mkdir()
    lidx = LexicalIndex(str(lex_path), commit_interval=100, commit_max_delay_s=0.0)

    await lidx.index("n1", "first version", "u1", "fact", "PERSONAL")
    await lidx.delete_by_node_id("n1")
    assert await lidx.search("first version", "u1", limit=5) == []

    await lidx.index("n1", "second version", "u1", "fact", "PERSONAL")
    results = await lidx.search("second version", "u1", limit=5)
    assert any(r["node_id"] == "n1" for r in results)

    await lidx.close()
