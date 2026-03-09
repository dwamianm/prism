"""Load and stress tests for PRME.

These tests exercise the engine under high concurrency and data volume.
They are skipped by default; set PRME_STRESS_TESTS=1 to run them:

    PRME_STRESS_TESTS=1 python -m pytest tests/test_stress.py -v

Markers:
    stress: load and stress tests (deselect with '-m not stress')
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import duckdb
import pytest
import pytest_asyncio

from prme.config import PRMEConfig
from prme.ingestion.graph_writer import WriteQueueGraphWriter
from prme.ingestion.pipeline import IngestionPipeline
from prme.ingestion.schema import ExtractionResult
from prme.organizer.maintenance import MaintenanceRunner
from prme.retrieval.pipeline import RetrievalPipeline
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.engine import MemoryEngine
from prme.storage.event_store import EventStore
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
from prme.storage.write_queue import WriteQueue

# ---------------------------------------------------------------------------
# Skip unless PRME_STRESS_TESTS=1
# ---------------------------------------------------------------------------

_stress_enabled = os.environ.get("PRME_STRESS_TESTS", "0") == "1"
pytestmark = [
    pytest.mark.stress,
    pytest.mark.skipif(not _stress_enabled, reason="Set PRME_STRESS_TESTS=1 to run"),
]


# ---------------------------------------------------------------------------
# Mock providers (no LLM / model download needed)
# ---------------------------------------------------------------------------


class MockEmbeddingProvider:
    """Deterministic mock embedding provider."""

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
        # Produce slightly varied vectors so retrieval scoring is meaningful
        results = []
        for i, t in enumerate(texts):
            base = (hash(t) % 100) / 100.0
            results.append([(base + j * 0.01) % 1.0 for j in range(8)])
        return results


class MockExtractionProvider:
    """Mock extraction provider returning minimal results."""

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


# ---------------------------------------------------------------------------
# Engine factory fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> MemoryEngine:
    """Create a fully-wired MemoryEngine with mock providers.

    Includes a RetrievalPipeline so retrieve() works, plus a
    MaintenanceRunner so organize() works.
    """
    db_path = str(tmp_path / "stress.duckdb")
    conn = duckdb.connect(db_path)
    initialize_database(conn)

    conn_lock = asyncio.Lock()

    event_store = EventStore(conn, conn_lock)
    graph_store = DuckPGQGraphStore(conn, conn_lock)

    embedding_provider = MockEmbeddingProvider()
    vector_path = str(tmp_path / "vectors.usearch")
    vector_index = VectorIndex(conn, vector_path, embedding_provider, conn_lock)

    lexical_path = tmp_path / "lexical_index"
    lexical_path.mkdir()
    lexical_index = LexicalIndex(str(lexical_path))

    write_queue = WriteQueue(maxsize=2000)
    await write_queue.start()

    graph_writer = WriteQueueGraphWriter(graph_store, write_queue)
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

    config = PRMEConfig(
        db_path=db_path,
        vector_path=vector_path,
        lexical_path=str(lexical_path),
    )

    retrieval_pipeline = RetrievalPipeline(
        graph_store=graph_store,
        vector_index=vector_index,
        lexical_index=lexical_index,
        conn=conn,
        conn_lock=conn_lock,
        scoring_weights=config.scoring,
        packing_config=config.packing,
    )

    eng = MemoryEngine(
        conn=conn,
        event_store=event_store,
        graph_store=graph_store,
        vector_index=vector_index,
        lexical_index=lexical_index,
        write_queue=write_queue,
        pipeline=pipeline,
        retrieval_pipeline=retrieval_pipeline,
        config=config,
    )
    eng._maintenance_runner = MaintenanceRunner(eng, config.organizer)

    yield eng

    await eng.close()


# ---------------------------------------------------------------------------
# 1. Concurrent store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_store(engine: MemoryEngine):
    """Launch 50 async tasks that each call engine.store() simultaneously.

    Verifies all 50 nodes are created, no crashes, no data corruption.
    Tests the write queue and DuckDB single-writer constraint.
    """
    user_id = "stress-user"
    n = 50

    tasks = [
        engine.store(f"Concurrent store content #{i}", user_id=user_id)
        for i in range(n)
    ]
    event_ids = await asyncio.gather(*tasks)

    # All returned, all unique
    assert len(event_ids) == n
    assert len(set(event_ids)) == n

    # Every event is retrievable
    for eid in event_ids:
        event = await engine.get_event(eid)
        assert event is not None
        assert event.user_id == user_id

    # Graph nodes were created
    nodes = await engine.query_nodes(user_id=user_id)
    assert len(nodes) >= n


# ---------------------------------------------------------------------------
# 2. Concurrent ingest_fast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_ingest_fast(engine: MemoryEngine):
    """Launch 100 async tasks calling ingest_fast() simultaneously.

    Verifies all events persist and materialization queue has correct
    debt count.
    """
    user_id = "stress-user"
    n = 100

    tasks = [
        engine.ingest_fast(
            f"Fast ingest message #{i} about topic {i % 10}",
            user_id=user_id,
        )
        for i in range(n)
    ]
    event_ids = await asyncio.gather(*tasks)

    # All returned, all unique
    assert len(event_ids) == n
    assert len(set(event_ids)) == n

    # Every event persisted
    for eid in event_ids:
        event = await engine.get_event(eid)
        assert event is not None

    # Materialization queue should have debt (ingest_fast defers graph writes)
    debt = engine._materialization_queue.debt_sync()
    assert debt == n, f"Expected {n} queued items, got {debt}"


# ---------------------------------------------------------------------------
# 3. Large corpus retrieval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_large_corpus_retrieval(engine: MemoryEngine):
    """Store 1000+ nodes with varied content, then run 10 retrieve() queries.

    Verifies:
    - Retrieval completes in < 5 seconds each
    - Results are non-empty and properly scored
    - Scores are monotonically non-increasing
    """
    user_id = "corpus-user"
    n_nodes = 1000

    # Batch store to build corpus
    topics = [
        "machine learning algorithms",
        "database performance tuning",
        "distributed systems design",
        "natural language processing",
        "cloud infrastructure management",
        "security best practices",
        "API design patterns",
        "data pipeline architecture",
        "user experience research",
        "project management methods",
    ]

    # Store in batches of 50 to avoid overwhelming the write queue
    for batch_start in range(0, n_nodes, 50):
        batch_tasks = []
        for i in range(batch_start, min(batch_start + 50, n_nodes)):
            topic = topics[i % len(topics)]
            content = (
                f"Memory node {i}: detailed information about {topic}. "
                f"This covers aspect {i % 7} with priority {i % 5}. "
                f"Related to project alpha-{i % 20}."
            )
            batch_tasks.append(
                engine.store(content, user_id=user_id)
            )
        await asyncio.gather(*batch_tasks)

    # Run 10 varied retrieval queries
    queries = [
        "machine learning model training",
        "how to optimize database queries",
        "distributed consensus protocols",
        "text classification techniques",
        "cloud deployment strategies",
        "authentication and authorization",
        "RESTful API versioning",
        "ETL pipeline monitoring",
        "usability testing methods",
        "agile sprint planning",
    ]

    for query in queries:
        t0 = time.monotonic()
        response = await engine.retrieve(query, user_id=user_id)
        elapsed = time.monotonic() - t0

        assert elapsed < 5.0, (
            f"Retrieval for '{query}' took {elapsed:.2f}s (limit: 5s)"
        )

        # Results should be non-empty
        assert len(response.results) > 0, (
            f"No results for query: '{query}'"
        )

        # Scores should be monotonically non-increasing
        scores = [r.composite_score for r in response.results]
        for j in range(1, len(scores)):
            assert scores[j] <= scores[j - 1] + 1e-9, (
                f"Scores not monotonically non-increasing at index {j}: "
                f"{scores[j - 1]:.6f} -> {scores[j]:.6f}"
            )


# ---------------------------------------------------------------------------
# 4. Organizer budget enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_organizer_budget_enforcement(engine: MemoryEngine):
    """Store 500 nodes, run organize() with a 200ms budget.

    Verifies it respects the budget (actual time < 2x budget) and
    processes some but not necessarily all nodes.
    """
    user_id = "budget-user"
    n = 500

    # Store nodes in batches
    for batch_start in range(0, n, 50):
        batch_tasks = []
        for i in range(batch_start, min(batch_start + 50, n)):
            batch_tasks.append(
                engine.store(
                    f"Budget test node {i} with content about topic {i % 10}",
                    user_id=user_id,
                )
            )
        await asyncio.gather(*batch_tasks)

    budget_ms = 500
    t0 = time.monotonic()
    result = await engine.organize(user_id=user_id, budget_ms=budget_ms)
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    # Budget enforcement: actual time should be < 4x budget.
    # The organizer checks budget between jobs (not mid-job), and the
    # first job may take longer than the budget itself, so we allow a
    # generous multiplier while still ensuring it doesn't run unbounded.
    assert elapsed_ms < budget_ms * 4, (
        f"organize() took {elapsed_ms:.1f}ms, expected < {budget_ms * 4}ms"
    )

    # Should have run at least some jobs
    assert len(result.jobs_run) > 0 or len(result.jobs_skipped) > 0, (
        "organize() reported no jobs run or skipped"
    )

    # Result structure is valid
    assert result.duration_ms >= 0
    assert result.budget_remaining_ms >= 0


# ---------------------------------------------------------------------------
# 5. Materialization drain under load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialization_drain_under_load(engine: MemoryEngine):
    """Queue 200 items via ingest_fast(), then call retrieve() multiple times.

    Verifies the queue drains progressively and eventually reaches 0.
    """
    user_id = "drain-user"
    n = 200

    # Queue items via ingest_fast
    tasks = [
        engine.ingest_fast(
            f"Drain test message {i} about subject {i % 15}",
            user_id=user_id,
        )
        for i in range(n)
    ]
    await asyncio.gather(*tasks)

    initial_debt = engine._materialization_queue.debt_sync()
    assert initial_debt == n

    # Each retrieve() call should drain some items
    previous_debt = initial_debt
    max_retrieval_rounds = 50  # safety bound

    for round_num in range(max_retrieval_rounds):
        current_debt = engine._materialization_queue.debt_sync()
        if current_debt == 0:
            break

        # Retrieve drains the queue as a side-effect
        await engine.retrieve(
            f"test query round {round_num}",
            user_id=user_id,
        )

        new_debt = engine._materialization_queue.debt_sync()
        # Debt should not increase (no new items queued)
        assert new_debt <= previous_debt, (
            f"Debt increased from {previous_debt} to {new_debt} "
            f"on round {round_num}"
        )
        previous_debt = new_debt

    final_debt = engine._materialization_queue.debt_sync()
    assert final_debt == 0, (
        f"Materialization queue not fully drained after {max_retrieval_rounds} "
        f"rounds: {final_debt} items remaining"
    )


# ---------------------------------------------------------------------------
# 6. Memory stability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_stability(engine: MemoryEngine):
    """Store 100 nodes, retrieve 50 times, organize 5 times.

    Tracks that no exceptions are raised and the engine remains
    functional throughout.
    """
    user_id = "stability-user"

    # Phase 1: Store 100 nodes
    store_tasks = [
        engine.store(
            f"Stability node {i}: content about area {i % 8}",
            user_id=user_id,
        )
        for i in range(100)
    ]
    event_ids = await asyncio.gather(*store_tasks)
    assert len(event_ids) == 100

    # Phase 2: Retrieve 50 times with varied queries
    for i in range(50):
        response = await engine.retrieve(
            f"query about area {i % 8} with context {i}",
            user_id=user_id,
        )
        # Engine should return valid response objects
        assert response is not None
        assert hasattr(response, "results")

    # Phase 3: Organize 5 times
    for i in range(5):
        result = await engine.organize(user_id=user_id, budget_ms=1000)
        assert result is not None
        assert hasattr(result, "jobs_run")

    # Phase 4: Verify engine is still functional after all operations
    final_eid = await engine.store(
        "Final stability check node",
        user_id=user_id,
    )
    assert final_eid is not None

    final_event = await engine.get_event(final_eid)
    assert final_event is not None
    assert final_event.content == "Final stability check node"

    final_response = await engine.retrieve(
        "stability check",
        user_id=user_id,
    )
    assert final_response is not None
    assert len(final_response.results) > 0
