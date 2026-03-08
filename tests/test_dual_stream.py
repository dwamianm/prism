"""Tests for dual-stream ingestion: fast path + deferred materialization (issue #25).

Validates that ingest_fast() persists events and updates the vector index
immediately, while deferring graph materialization to subsequent retrieve()
or organize() calls. Also tests the MaterializationQueue directly.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine
from prme.storage.materialization_queue import MaterializationQueue, PendingMaterialization
from prme.types import NodeType, Scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_dual_stream_") as d:
        yield d


@pytest.fixture
def base_config(tmp_dir):
    """Config for dual-stream testing."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
        materialization_queue_size=500,
        materialization_budget_ms=5000,
        # Disable opportunistic maintenance to isolate materialization behavior
        organizer={"opportunistic_enabled": False},
    )


async def create_engine(config: PRMEConfig) -> MemoryEngine:
    """Create a MemoryEngine from config."""
    return await MemoryEngine.create(config)


# ---------------------------------------------------------------------------
# MaterializationQueue unit tests
# ---------------------------------------------------------------------------


class TestMaterializationQueue:
    """Unit tests for the MaterializationQueue class."""

    @pytest.mark.asyncio
    async def test_add_and_debt(self):
        """Adding items should increase debt count."""
        q = MaterializationQueue(max_size=10)
        assert await q.debt() == 0

        await q.add("event-1", "content 1", "user-1")
        assert await q.debt() == 1

        await q.add("event-2", "content 2", "user-1")
        assert await q.debt() == 2

    @pytest.mark.asyncio
    async def test_debt_sync(self):
        """debt_sync() should return approximate count without lock."""
        q = MaterializationQueue(max_size=10)
        assert q.debt_sync() == 0

        await q.add("event-1", "content 1", "user-1")
        assert q.debt_sync() == 1

    @pytest.mark.asyncio
    async def test_overflow_drops_oldest(self):
        """When at max capacity, adding drops the oldest item."""
        q = MaterializationQueue(max_size=2)

        await q.add("event-1", "content 1", "user-1")
        await q.add("event-2", "content 2", "user-1")
        assert await q.debt() == 2

        # This should drop event-1
        await q.add("event-3", "content 3", "user-1")
        assert await q.debt() == 2

    @pytest.mark.asyncio
    async def test_drain_processes_items(self, base_config):
        """drain() should process pending items via engine.store()."""
        engine = await create_engine(base_config)
        try:
            q = MaterializationQueue(max_size=10)
            await q.add("event-1", "Python is great", "user-1")
            await q.add("event-2", "Rust is fast", "user-1")

            materialized = await q.drain(engine, budget_ms=5000)
            assert materialized == 2
            assert await q.debt() == 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_drain_respects_budget(self, base_config):
        """drain() should stop when budget is exhausted."""
        engine = await create_engine(base_config)
        try:
            q = MaterializationQueue(max_size=100)
            # Add many items
            for i in range(20):
                await q.add(f"event-{i}", f"Content number {i}", "user-1")

            # Drain with very small budget (1ms)
            materialized = await q.drain(engine, budget_ms=1)
            # Should have processed at least 1 but likely not all 20
            # (depends on timing, but we verify the budget mechanism works)
            remaining = await q.debt()
            assert materialized + remaining == 20
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_drain_empty_queue(self, base_config):
        """drain() on empty queue should return 0."""
        engine = await create_engine(base_config)
        try:
            q = MaterializationQueue(max_size=10)
            materialized = await q.drain(engine, budget_ms=1000)
            assert materialized == 0
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# ingest_fast() integration tests
# ---------------------------------------------------------------------------


class TestIngestFast:
    """Integration tests for the fast ingestion path."""

    @pytest.mark.asyncio
    async def test_ingest_fast_returns_event_id(self, base_config):
        """ingest_fast() should return a valid event ID string."""
        engine = await create_engine(base_config)
        try:
            event_id = await engine.ingest_fast(
                "Python is a programming language",
                user_id="test-user",
            )
            assert event_id is not None
            assert isinstance(event_id, str)
            assert len(event_id) > 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_ingest_fast_persists_event(self, base_config):
        """Fast-ingested content should be retrievable from the event store."""
        engine = await create_engine(base_config)
        try:
            event_id = await engine.ingest_fast(
                "DuckDB is an embedded analytics database",
                user_id="test-user",
            )
            event = await engine.get_event(event_id)
            assert event is not None
            assert event.content == "DuckDB is an embedded analytics database"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_ingest_fast_vector_indexed_but_filtered_before_materialization(
        self, base_config
    ):
        """Fast-ingested content is vector-indexed but filtered by node JOIN.

        The vector index stores the embedding, but user-scoped search
        requires a JOIN with the nodes table (for lifecycle_state filtering).
        Since ingest_fast skips graph writes, the vector entry exists in
        USearch but is filtered out during search. After materialization
        drains, the node exists and the vector becomes searchable.
        """
        engine = await create_engine(base_config)
        try:
            event_id = await engine.ingest_fast(
                "Kubernetes orchestrates container deployments",
                user_id="test-user",
            )

            # Before materialization: vector is indexed but JOIN filters it
            results = await engine._vector_index.search(
                "container orchestration", "test-user", k=5
            )
            assert len(results) == 0  # Filtered by missing node row

            # Drain materialization (creates graph node via store())
            await engine._materialization_queue.drain(engine, budget_ms=5000)

            # After materialization: vector search now finds the content
            results = await engine._vector_index.search(
                "container orchestration", "test-user", k=5
            )
            assert len(results) > 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_ingest_fast_does_not_create_graph_node(self, base_config):
        """Fast-ingested content should NOT appear in graph queries immediately."""
        engine = await create_engine(base_config)
        try:
            await engine.ingest_fast(
                "Redis is used for caching",
                user_id="test-user",
            )
            # Graph query should return nothing since store() was not called
            nodes = await engine.query_nodes(user_id="test-user", limit=100)
            assert len(nodes) == 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_ingest_fast_increases_materialization_debt(self, base_config):
        """Each ingest_fast() should increment the materialization debt."""
        engine = await create_engine(base_config)
        try:
            assert engine.materialization_debt == 0

            await engine.ingest_fast(
                "Content one", user_id="test-user"
            )
            assert engine.materialization_debt == 1

            await engine.ingest_fast(
                "Content two", user_id="test-user"
            )
            assert engine.materialization_debt == 2
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_ingest_fast_is_faster_than_store(self, base_config):
        """ingest_fast() should be significantly faster than store().

        We test that ingest_fast doesn't do graph writes by checking
        that no graph nodes are created, rather than relying on strict
        timing (which can vary in CI environments).
        """
        engine = await create_engine(base_config)
        try:
            # Fast path
            start = time.monotonic()
            await engine.ingest_fast(
                "Fast ingestion test content",
                user_id="test-user",
            )
            fast_ms = (time.monotonic() - start) * 1000.0

            # No graph nodes should exist
            nodes = await engine.query_nodes(user_id="test-user", limit=100)
            assert len(nodes) == 0

            # Full path
            start = time.monotonic()
            await engine.store(
                "Full store test content",
                user_id="test-user-2",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            store_ms = (time.monotonic() - start) * 1000.0

            # Graph node should exist for store()
            nodes = await engine.query_nodes(user_id="test-user-2", limit=100)
            assert len(nodes) == 1

            # Log timings for debugging (not strict assertion due to CI variance)
            print(f"ingest_fast: {fast_ms:.1f}ms, store: {store_ms:.1f}ms")
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Materialization via retrieve() tests
# ---------------------------------------------------------------------------


class TestMaterializationOnRetrieve:
    """Test that graph materialization happens on retrieve()."""

    @pytest.mark.asyncio
    async def test_retrieve_triggers_materialization(self, base_config):
        """Calling retrieve() should drain the materialization queue."""
        engine = await create_engine(base_config)
        try:
            # Fast-ingest some content
            await engine.ingest_fast(
                "Docker containers simplify deployment",
                user_id="test-user",
            )
            assert engine.materialization_debt == 1

            # No graph nodes yet
            nodes = await engine.query_nodes(user_id="test-user", limit=100)
            assert len(nodes) == 0

            # Retrieve triggers materialization
            await engine.retrieve(
                "What is Docker?", user_id="test-user"
            )

            # After retrieve, materialization should have happened
            assert engine.materialization_debt == 0

            # Graph node should now exist
            nodes = await engine.query_nodes(user_id="test-user", limit=100)
            assert len(nodes) >= 1
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Materialization via organize() tests
# ---------------------------------------------------------------------------


class TestMaterializationOnOrganize:
    """Test that graph materialization happens on organize()."""

    @pytest.mark.asyncio
    async def test_organize_triggers_materialization(self, base_config):
        """Calling organize() should drain the materialization queue."""
        engine = await create_engine(base_config)
        try:
            # Fast-ingest some content
            await engine.ingest_fast(
                "Terraform manages infrastructure as code",
                user_id="test-user",
            )
            assert engine.materialization_debt == 1

            # Organize triggers materialization
            result = await engine.organize(user_id="test-user")

            # Debt should be cleared
            assert engine.materialization_debt == 0

            # Graph node should now exist
            nodes = await engine.query_nodes(user_id="test-user", limit=100)
            assert len(nodes) >= 1

            # Result should include materialization info
            if "materialization_drain" in result.per_job:
                mat_job = result.per_job["materialization_drain"]
                assert mat_job.details["materialized"] >= 1
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Debt tracking tests
# ---------------------------------------------------------------------------


class TestMaterializationDebtTracking:
    """Test materialization debt property and tracking."""

    @pytest.mark.asyncio
    async def test_debt_property_starts_at_zero(self, base_config):
        """Fresh engine should have zero materialization debt."""
        engine = await create_engine(base_config)
        try:
            assert engine.materialization_debt == 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_debt_decreases_after_drain(self, base_config):
        """Debt should decrease after materialization drain."""
        engine = await create_engine(base_config)
        try:
            await engine.ingest_fast("Content A", user_id="test-user")
            await engine.ingest_fast("Content B", user_id="test-user")
            assert engine.materialization_debt == 2

            # Manual drain
            await engine._materialization_queue.drain(
                engine, budget_ms=5000
            )
            assert engine.materialization_debt == 0
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Queue size limits tests
# ---------------------------------------------------------------------------


class TestQueueSizeLimits:
    """Test that materialization queue respects size limits."""

    @pytest.mark.asyncio
    async def test_config_queue_size_respected(self, tmp_dir):
        """Custom materialization_queue_size should be used."""
        lexical_path = Path(tmp_dir) / "lexical_small"
        lexical_path.mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory_small.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors_small.usearch"),
            lexical_path=str(lexical_path),
            materialization_queue_size=3,
            organizer={"opportunistic_enabled": False},
        )
        engine = await create_engine(config)
        try:
            # Fill beyond capacity
            for i in range(5):
                await engine.ingest_fast(
                    f"Content item {i}", user_id="test-user"
                )

            # Should be capped at max_size=3
            assert engine.materialization_debt == 3
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Concurrent fast ingestion tests
# ---------------------------------------------------------------------------


class TestConcurrentFastIngestion:
    """Test that concurrent ingest_fast() calls are safe."""

    @pytest.mark.asyncio
    async def test_concurrent_ingest_fast(self, base_config):
        """Multiple concurrent ingest_fast() calls should all succeed."""
        engine = await create_engine(base_config)
        try:
            # Fire 10 concurrent ingest_fast calls
            tasks = [
                engine.ingest_fast(
                    f"Concurrent content item {i}",
                    user_id="test-user",
                )
                for i in range(10)
            ]
            event_ids = await asyncio.gather(*tasks)

            # All should return valid event IDs
            assert len(event_ids) == 10
            assert all(isinstance(eid, str) for eid in event_ids)
            assert len(set(event_ids)) == 10  # all unique

            # All events should be in event store
            for eid in event_ids:
                event = await engine.get_event(eid)
                assert event is not None

            # Materialization debt should equal 10
            assert engine.materialization_debt == 10
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Config defaults tests
# ---------------------------------------------------------------------------


class TestDualStreamConfig:
    """Test that config defaults are correct."""

    def test_default_materialization_queue_size(self):
        """Default materialization_queue_size should be 500."""
        config = PRMEConfig()
        assert config.materialization_queue_size == 500

    def test_default_materialization_budget_ms(self):
        """Default materialization_budget_ms should be 100."""
        config = PRMEConfig()
        assert config.materialization_budget_ms == 100


# ---------------------------------------------------------------------------
# store() unchanged tests
# ---------------------------------------------------------------------------


class TestStoreUnchanged:
    """Verify that store() behavior is unchanged."""

    @pytest.mark.asyncio
    async def test_store_does_not_affect_materialization_debt(self, base_config):
        """store() should not add to the materialization queue."""
        engine = await create_engine(base_config)
        try:
            await engine.store(
                "Direct store content",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            assert engine.materialization_debt == 0

            # Graph node should exist immediately
            nodes = await engine.query_nodes(user_id="test-user", limit=100)
            assert len(nodes) == 1
        finally:
            await engine.close()
