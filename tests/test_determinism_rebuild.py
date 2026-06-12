"""Determinism and rebuildability tests (issue #45).

Covers the two least-true documented claims and their fixes:

- Exact (brute-force) vector search makes retrieval order-independent and
  reproducible, where HNSW traversal is order/thread-dependent.
- ``MemoryEngine.rebuild_indexes()`` reconstructs the derived vector and
  lexical indexes from the durable graph nodes without touching the event
  log or graph, restoring retrieval after the index files are wiped.
- The determinism validation harness: rebuild the indexes twice from the
  same pack and assert identical retrieval results for a query suite.

Uses a deterministic hash-based mock embedding (no model download) and
tmp_path for isolation. All assertions run on the DuckDB backend.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest
import pytest_asyncio

from prme.config import PRMEConfig
from prme.models.nodes import MemoryNode
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.engine import MemoryEngine
from prme.storage.event_store import EventStore
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
from prme.storage.write_queue import WriteQueue
from prme.types import LifecycleState, NodeType, Scope

pytestmark = pytest.mark.asyncio


class MockEmbeddingProvider:
    """Deterministic, order-independent embedding from a content hash.

    The same text always maps to the same vector regardless of when or in
    what order it is embedded, so any non-determinism observed in retrieval
    comes from the index search strategy rather than the embedding.
    """

    model_name = "mock-embed"
    model_version = "mock-1.0"
    dimension = 16

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append([digest[i] / 255.0 for i in range(self.dimension)])
        return out


# --- Fixtures -------------------------------------------------------------


@pytest_asyncio.fixture
async def conn(tmp_path: Path):
    db_path = str(tmp_path / "memory.duckdb")
    c = duckdb.connect(db_path)
    initialize_database(c)
    yield c
    c.close()


async def _make_engine(
    tmp_path: Path,
    conn: duckdb.DuckDBPyConnection,
    *,
    exact: bool = True,
) -> MemoryEngine:
    """Build a DuckDB MemoryEngine wired with the mock embedding provider."""
    import asyncio

    conn_lock = asyncio.Lock()
    event_store = EventStore(conn, conn_lock)
    graph_store = DuckPGQGraphStore(conn, conn_lock)
    vector_index = VectorIndex(
        conn,
        str(tmp_path / "vectors.usearch"),
        MockEmbeddingProvider(),
        conn_lock,
        save_interval=1,
        exact_search=exact,
    )
    lexical_dir = tmp_path / "lexical_index"
    lexical_dir.mkdir(exist_ok=True)
    lexical_index = LexicalIndex(str(lexical_dir))
    write_queue = WriteQueue(maxsize=256)
    await write_queue.start()

    return MemoryEngine(
        conn=conn,
        event_store=event_store,
        graph_store=graph_store,
        vector_index=vector_index,
        lexical_index=lexical_index,
        write_queue=write_queue,
        config=PRMEConfig(),
    )


async def _add_node(
    engine: MemoryEngine,
    content: str,
    user_id: str,
    *,
    node_type: NodeType = NodeType.FACT,
    index: bool = True,
) -> str:
    """Create a graph node and (optionally) index it like store() would."""
    node = MemoryNode(
        user_id=user_id,
        node_type=node_type,
        scope=Scope.PERSONAL,
        content=content,
    )
    await engine._graph_store.create_node(node)
    node_id = str(node.id)
    if index:
        await engine._vector_index.index(node_id, content, user_id)
        await engine._lexical_index.index(
            node_id, content, user_id, node_type.value, Scope.PERSONAL.value
        )
    return node_id


# --- Config plumbing ------------------------------------------------------


async def test_exact_search_config_default_on() -> None:
    """The determinism knob defaults to exact search."""
    assert PRMEConfig().vector_exact_search is True


async def test_exact_flag_threads_into_vector_index(tmp_path: Path, conn) -> None:
    """The config flag reaches the VectorIndex search strategy."""
    on = VectorIndex(
        conn, str(tmp_path / "a.usearch"), MockEmbeddingProvider(), exact_search=True
    )
    off = VectorIndex(
        conn, str(tmp_path / "b.usearch"), MockEmbeddingProvider(), exact_search=False
    )
    assert on._exact_search is True
    assert off._exact_search is False


# --- Exact search determinism --------------------------------------------


async def test_exact_search_is_order_independent(tmp_path: Path) -> None:
    """Inserting the same vectors in different orders yields identical results."""
    contents = [f"fact number {i} about topic alpha" for i in range(12)]

    async def build_and_query(order: list[int], tag: str) -> list[tuple[str, float]]:
        """Index the same contents in ``order`` and return the ranked result.

        Node ids are fresh uuids per run, so results are mapped back to their
        content and rounded score: that projection is what must be stable
        across insertion orders if exact search is order-independent.
        """
        c = duckdb.connect(str(tmp_path / f"db-{tag}.duckdb"))
        initialize_database(c)
        import asyncio

        lock = asyncio.Lock()
        graph = DuckPGQGraphStore(c, lock)
        vidx = VectorIndex(
            c,
            str(tmp_path / f"vec-{tag}.usearch"),
            MockEmbeddingProvider(),
            lock,
            save_interval=1,
            exact_search=True,
        )
        id_to_content: dict[str, str] = {}
        for i in order:
            node = MemoryNode(
                user_id="u1",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
                content=contents[i],
            )
            await graph.create_node(node)
            await vidx.index(str(node.id), contents[i], "u1")
            id_to_content[str(node.id)] = contents[i]
        results = await vidx.search("topic alpha facts", "u1", k=8)
        c.close()
        return [(id_to_content[r["node_id"]], round(r["score"], 6)) for r in results]

    forward = await build_and_query(list(range(12)), "fwd")
    backward = await build_and_query(list(reversed(range(12))), "bwd")

    # Exact search ranks purely by distance, so the same content set returns
    # the same (content, score) ranking regardless of insertion order.
    assert forward
    assert forward == backward


# --- clear() --------------------------------------------------------------


async def test_vector_clear_empties_index(tmp_path: Path, conn) -> None:
    """clear() drops every vector and its metadata."""
    engine = await _make_engine(tmp_path, conn)
    try:
        await _add_node(engine, "the sky is blue", "u1")
        await _add_node(engine, "grass is green", "u1")
        before = conn.execute("SELECT COUNT(*) FROM vector_metadata").fetchone()[0]
        assert before == 2

        await engine._vector_index.clear()

        after = conn.execute("SELECT COUNT(*) FROM vector_metadata").fetchone()[0]
        assert after == 0
        assert len(engine._vector_index._index) == 0
    finally:
        await engine.close()


async def test_lexical_clear_empties_index(tmp_path: Path, conn) -> None:
    """clear() removes every lexical document."""
    engine = await _make_engine(tmp_path, conn)
    try:
        await _add_node(engine, "quick brown fox", "u1")
        assert await engine._lexical_index.search("fox", "u1", limit=5)

        await engine._lexical_index.clear()

        assert await engine._lexical_index.search("fox", "u1", limit=5) == []
    finally:
        await engine.close()


# --- rebuild_indexes() ----------------------------------------------------


async def test_rebuild_restores_retrieval_after_index_wipe(
    tmp_path: Path, conn
) -> None:
    """Rebuild reconstructs both indexes from the graph after a wipe."""
    engine = await _make_engine(tmp_path, conn)
    try:
        await _add_node(engine, "Paris is the capital of France", "u1")
        await _add_node(engine, "Tokyo is the capital of Japan", "u1")

        # Simulate index corruption by wiping both indexes.
        await engine._vector_index.clear()
        await engine._lexical_index.clear()
        assert await engine._vector_index.search("capital of France", "u1", k=5) == []

        summary = await engine.rebuild_indexes()

        assert summary["nodes_indexed"] == 2
        assert summary["total_active"] == 2
        vec = await engine._vector_index.search("capital of France", "u1", k=5)
        assert len(vec) == 2
        lex = await engine._lexical_index.search("capital", "u1", limit=5)
        assert len(lex) == 2
    finally:
        await engine.close()


async def test_rebuild_excludes_superseded_and_archived(
    tmp_path: Path, conn
) -> None:
    """Only active-lifecycle nodes are re-indexed (visibility contract)."""
    engine = await _make_engine(tmp_path, conn)
    try:
        active = await _add_node(engine, "active fact about widgets", "u1")
        superseded = await _add_node(engine, "stale fact about widgets", "u1")
        archived = await _add_node(engine, "archived fact about widgets", "u1")

        # Move two nodes out of active states directly in the graph.
        await engine._graph_store.update_node(
            superseded, lifecycle_state=LifecycleState.SUPERSEDED
        )
        await engine._graph_store.update_node(
            archived, lifecycle_state=LifecycleState.ARCHIVED
        )

        summary = await engine.rebuild_indexes()

        # Three nodes exist, but only the active one is indexed.
        assert summary["nodes_indexed"] == 1
        assert summary["total_active"] == 1

        keys = conn.execute(
            "SELECT node_id FROM vector_metadata ORDER BY node_id"
        ).fetchall()
        indexed_ids = {row[0] for row in keys}
        assert active in indexed_ids
        assert superseded not in indexed_ids
        assert archived not in indexed_ids
    finally:
        await engine.close()


async def test_rebuild_is_nondestructive(tmp_path: Path, conn) -> None:
    """Rebuild leaves the event log and graph nodes untouched."""
    engine = await _make_engine(tmp_path, conn)
    try:
        await _add_node(engine, "fact one", "u1")
        await _add_node(engine, "fact two", "u1")

        nodes_before = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges_before = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        await engine.rebuild_indexes()

        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == nodes_before
        assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == edges_before
        assert (
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
        )
    finally:
        await engine.close()


async def test_rebuild_empty_graph(tmp_path: Path, conn) -> None:
    """Rebuild on a pack with no nodes is a clean no-op."""
    engine = await _make_engine(tmp_path, conn)
    try:
        summary = await engine.rebuild_indexes()
        assert summary == {
            "nodes_indexed": 0,
            "nodes_skipped": 0,
            "total_active": 0,
        }
    finally:
        await engine.close()


async def test_rebuild_skips_empty_content(tmp_path: Path, conn) -> None:
    """Active nodes with blank content are skipped and counted."""
    engine = await _make_engine(tmp_path, conn)
    try:
        await _add_node(engine, "a real fact", "u1", index=False)
        # Whitespace-only content cannot be embedded.
        await _add_node(engine, "   ", "u1", index=False)

        summary = await engine.rebuild_indexes()

        assert summary["total_active"] == 2
        assert summary["nodes_indexed"] == 1
        assert summary["nodes_skipped"] == 1
    finally:
        await engine.close()


async def test_rebuild_is_idempotent(tmp_path: Path, conn) -> None:
    """Running rebuild repeatedly does not duplicate index entries."""
    engine = await _make_engine(tmp_path, conn)
    try:
        await _add_node(engine, "alpha fact", "u1")
        await _add_node(engine, "beta fact", "u1")

        await engine.rebuild_indexes()
        first = conn.execute("SELECT COUNT(*) FROM vector_metadata").fetchone()[0]
        await engine.rebuild_indexes()
        second = conn.execute("SELECT COUNT(*) FROM vector_metadata").fetchone()[0]

        assert first == 2
        assert second == 2
    finally:
        await engine.close()


# --- Determinism validation harness --------------------------------------


async def test_rebuild_twice_yields_identical_retrieval(
    tmp_path: Path, conn
) -> None:
    """Two rebuilds from the same pack produce identical retrieval results.

    This is the validation harness from the issue: with exact vector search
    and deterministic downstream scoring, rebuilding the derived indexes
    twice from the same durable graph must return the same neighbors in the
    same order for a query suite.
    """
    engine = await _make_engine(tmp_path, conn, exact=True)
    try:
        for i in range(20):
            await _add_node(
                engine, f"document {i} discussing machine learning topic {i % 4}", "u1"
            )

        query_suite = [
            "machine learning topic 0",
            "document discussing topic 3",
            "learning",
        ]

        await engine.rebuild_indexes()
        first_run = {
            q: [r["node_id"] for r in await engine._vector_index.search(q, "u1", k=8)]
            for q in query_suite
        }

        await engine.rebuild_indexes()
        second_run = {
            q: [r["node_id"] for r in await engine._vector_index.search(q, "u1", k=8)]
            for q in query_suite
        }

        assert first_run == second_run
        # And every query returned something to make the assertion meaningful.
        assert all(first_run[q] for q in query_suite)
    finally:
        await engine.close()
