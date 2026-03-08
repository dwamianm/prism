"""Concurrency integration tests for WriteQueue contract.

Verifies that parallel ingestion requests complete without data loss
or transaction conflicts. Uses mock extraction and embedding providers
for CI speed -- no LLM or model download required.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from prme.ingestion.graph_writer import WriteQueueGraphWriter
from prme.ingestion.pipeline import IngestionPipeline
from prme.ingestion.schema import ExtractionResult
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.event_store import EventStore
from prme.storage.engine import MemoryEngine
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
from prme.storage.write_queue import WriteQueue


# --- Mock providers for fast testing ---


class MockEmbeddingProvider:
    """Mock embedding provider returning deterministic vectors."""

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
        return [[0.1] * 8 for _ in texts]


class MockExtractionProvider:
    """Mock extraction provider returning minimal (empty) results."""

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-extract"

    async def extract(self, content: str, *, role: str = "user"):
        return ExtractionResult(
            entities=[], facts=[], relationships=[], summary=None
        )


# --- Fixtures ---


@pytest_asyncio.fixture
async def engine(tmp_path):
    """Create a MemoryEngine with mock providers for fast testing.

    Uses tmp_path for all storage artifacts so tests are isolated.
    Yields the engine and closes it on teardown.
    """
    import asyncio
    import duckdb

    # Create DuckDB connection in tmp_path
    db_path = str(tmp_path / "test.duckdb")
    conn = duckdb.connect(db_path)
    initialize_database(conn)

    # Shared connection lock for DuckDB thread-safety (issue #19)
    conn_lock = asyncio.Lock()

    # Create real backend stores
    event_store = EventStore(conn, conn_lock)
    graph_store = DuckPGQGraphStore(conn, conn_lock)

    # Create mock embedding provider and real vector index
    embedding_provider = MockEmbeddingProvider()
    vector_path = str(tmp_path / "vectors.usearch")
    vector_index = VectorIndex(conn, vector_path, embedding_provider, conn_lock)

    # Create real lexical index in tmp_path (tantivy requires dir to exist)
    lexical_path = tmp_path / "lexical_index"
    lexical_path.mkdir()
    lexical_index = LexicalIndex(str(lexical_path))

    # Create and start write queue
    write_queue = WriteQueue(maxsize=1000)
    await write_queue.start()

    # Create WriteQueueGraphWriter for pipeline injection
    graph_writer = WriteQueueGraphWriter(graph_store, write_queue)

    # Create ingestion pipeline with mock extraction provider
    extraction_provider = MockExtractionProvider()
    pipeline = IngestionPipeline(
        event_store=event_store,
        graph_store=graph_store,
        vector_index=vector_index,
        lexical_index=lexical_index,
        extraction_provider=extraction_provider,
        write_queue=write_queue,
        graph_writer=graph_writer,
    )

    # Create the engine with all components
    eng = MemoryEngine(
        conn=conn,
        event_store=event_store,
        graph_store=graph_store,
        vector_index=vector_index,
        lexical_index=lexical_index,
        write_queue=write_queue,
        pipeline=pipeline,
    )

    yield eng

    # Teardown
    await eng.close()


# --- Concurrency Tests ---


@pytest.mark.asyncio
async def test_parallel_ingestion_no_data_loss(engine):
    """5 parallel ingestion calls complete without data loss."""
    user_id = "test-user"
    n_parallel = 5
    messages = [
        f"Test message {i}: some content about topic {i}"
        for i in range(n_parallel)
    ]

    tasks = [
        engine.ingest(msg, user_id=user_id, wait_for_extraction=True)
        for msg in messages
    ]
    event_ids = await asyncio.gather(*tasks)

    assert len(event_ids) == n_parallel
    assert len(set(event_ids)) == n_parallel  # all unique

    for eid in event_ids:
        event = await engine.get_event(eid)
        assert event is not None
        assert event.user_id == user_id


@pytest.mark.asyncio
async def test_parallel_store_no_data_loss(engine):
    """5 parallel store() calls complete without conflicts."""
    user_id = "test-user"
    n_parallel = 5
    contents = [f"Store content {i}" for i in range(n_parallel)]

    tasks = [engine.store(c, user_id=user_id) for c in contents]
    event_ids = await asyncio.gather(*tasks)

    assert len(event_ids) == n_parallel
    assert len(set(event_ids)) == n_parallel

    for eid in event_ids:
        event = await engine.get_event(eid)
        assert event is not None


@pytest.mark.asyncio
async def test_write_queue_serializes_under_load(engine):
    """WriteQueue processes all submitted jobs without dropping any.

    Verifies both events AND graph nodes are created for each store() call.
    """
    user_id = "test-user"
    n_parallel = 5

    tasks = [
        engine.store(f"Content {i}", user_id=user_id) for i in range(n_parallel)
    ]
    event_ids = await asyncio.gather(*tasks)

    assert len(event_ids) == n_parallel
    assert len(set(event_ids)) == n_parallel

    # Verify nodes were created for each store()
    nodes = await engine.query_nodes(user_id=user_id)
    assert len(nodes) >= n_parallel  # at least one node per store()
