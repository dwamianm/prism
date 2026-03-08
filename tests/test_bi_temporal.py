"""Tests for bi-temporal data model (issue #21).

Validates that:
- Events can be stored with explicit event_time
- Events without event_time default to None (ingestion time semantics)
- knowledge_at point-in-time queries filter by ingestion time (created_at)
- event_time_from/event_time_to queries filter by when events happened
- Existing functionality is not broken (backward compatibility)
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prme.config import PRMEConfig
from prme.models import Event, MemoryNode
from prme.storage.engine import MemoryEngine
from prme.types import NodeType, Scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_bitemporal_") as d:
        yield d


@pytest.fixture
def config(tmp_dir):
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
    )


async def create_engine(config: PRMEConfig) -> MemoryEngine:
    return await MemoryEngine.create(config)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _store_and_get_node(
    engine: MemoryEngine,
    content: str,
    user_id: str = "test-user",
    event_time: datetime | None = None,
) -> MemoryNode:
    """Store content and return the created MemoryNode."""
    await engine.store(
        content,
        user_id=user_id,
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        event_time=event_time,
    )
    nodes = await engine.query_nodes(user_id=user_id, limit=100)
    for n in nodes:
        if n.content == content:
            return n
    raise RuntimeError(f"Could not find stored node with content {content!r}")


async def _store_and_get_event(
    engine: MemoryEngine,
    content: str,
    user_id: str = "test-user",
    event_time: datetime | None = None,
) -> Event:
    """Store content and return the created Event."""
    event_id = await engine.store(
        content,
        user_id=user_id,
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        event_time=event_time,
    )
    event = await engine.get_event(event_id)
    assert event is not None
    return event


# ---------------------------------------------------------------------------
# Tests: Event model
# ---------------------------------------------------------------------------


class TestEventModel:
    """Test Event model with bi-temporal fields."""

    def test_event_without_event_time(self):
        """Event without event_time defaults to None."""
        event = Event(
            content="test content",
            role="user",
            user_id="u1",
        )
        assert event.event_time is None
        assert event.timestamp is not None  # ingestion time always set

    def test_event_with_explicit_event_time(self):
        """Event can carry an explicit event_time."""
        past = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        event = Event(
            content="happened last month",
            role="user",
            user_id="u1",
            event_time=past,
        )
        assert event.event_time == past
        # timestamp (ingestion) is independent of event_time
        assert event.timestamp != past


# ---------------------------------------------------------------------------
# Tests: MemoryNode model
# ---------------------------------------------------------------------------


class TestMemoryNodeModel:
    """Test MemoryNode model with bi-temporal fields."""

    def test_node_without_event_time(self):
        """MemoryNode without event_time defaults to None."""
        node = MemoryNode(
            content="test",
            user_id="u1",
            node_type=NodeType.FACT,
        )
        assert node.event_time is None
        assert node.created_at is not None  # ingestion time

    def test_node_with_event_time(self):
        """MemoryNode can carry event_time."""
        past = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
        node = MemoryNode(
            content="test",
            user_id="u1",
            node_type=NodeType.FACT,
            event_time=past,
        )
        assert node.event_time == past


# ---------------------------------------------------------------------------
# Tests: Store with event_time
# ---------------------------------------------------------------------------


class TestStoreWithEventTime:
    """Integration tests: store() with event_time parameter."""

    @pytest.mark.asyncio
    async def test_store_with_explicit_event_time(self, config):
        """Store with event_time persists it on both Event and Node."""
        engine = await create_engine(config)
        try:
            past = datetime(2025, 3, 1, 9, 0, tzinfo=timezone.utc)

            # Store with explicit event_time
            event_id = await engine.store(
                "Team moved from Slack to Discord last week",
                user_id="test-user",
                node_type=NodeType.FACT,
                event_time=past,
            )

            # Verify Event has event_time
            event = await engine.get_event(event_id)
            assert event is not None
            assert event.event_time == past

            # Verify Node has event_time
            node = await _store_and_get_node(
                engine,
                "Another fact with event time",
                event_time=past,
            )
            assert node.event_time == past
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_store_without_event_time(self, config):
        """Store without event_time defaults to None (backward compatible)."""
        engine = await create_engine(config)
        try:
            event_id = await engine.store(
                "Normal content without event time",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            # Event should have event_time=None
            event = await engine.get_event(event_id)
            assert event is not None
            assert event.event_time is None

            # Node should have event_time=None
            nodes = await engine.query_nodes(user_id="test-user", limit=10)
            matching = [n for n in nodes if n.content == "Normal content without event time"]
            assert len(matching) == 1
            assert matching[0].event_time is None
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Tests: knowledge_at point-in-time queries
# ---------------------------------------------------------------------------


class TestKnowledgeAtQueries:
    """Test knowledge_at parameter for point-in-time knowledge snapshots."""

    @pytest.mark.asyncio
    async def test_knowledge_at_filters_by_ingestion_time(self, config):
        """knowledge_at filters nodes by created_at (ingestion time)."""
        engine = await create_engine(config)
        try:
            now = datetime.now(timezone.utc)

            # Store two facts
            await engine.store(
                "Python is the primary language",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Rust is being evaluated for performance",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            # Backdate the first node to simulate it being stored earlier
            conn = engine._conn
            conn.execute(
                "UPDATE nodes SET created_at = ?::TIMESTAMPTZ "
                "WHERE content = ? AND user_id = ?",
                [
                    (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S.%f+00"),
                    "Python is the primary language",
                    "test-user",
                ],
            )

            # Query with knowledge_at = 5 days ago: only Python should appear
            knowledge_cutoff = now - timedelta(days=5)
            response = await engine.retrieve(
                "What programming languages are used?",
                user_id="test-user",
                knowledge_at=knowledge_cutoff,
            )

            result_contents = [r.node.content for r in response.results]
            assert any("Python" in c for c in result_contents), (
                "Python fact (ingested 10 days ago) should be visible at 5 days ago"
            )
            assert not any("Rust" in c for c in result_contents), (
                "Rust fact (ingested just now) should NOT be visible at 5 days ago"
            )
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Tests: event_time_range filtering
# ---------------------------------------------------------------------------


class TestEventTimeRangeQueries:
    """Test event_time_from/event_time_to parameters."""

    @pytest.mark.asyncio
    async def test_event_time_range_filters(self, config):
        """event_time_from/event_time_to filter by when events happened."""
        engine = await create_engine(config)
        try:
            # Store facts about events that happened at different times
            jan = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
            mar = datetime(2025, 3, 15, 12, 0, tzinfo=timezone.utc)
            jun = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)

            await engine.store(
                "Company founded in January",
                user_id="test-user",
                node_type=NodeType.FACT,
                event_time=jan,
            )
            await engine.store(
                "Series A funding closed in March",
                user_id="test-user",
                node_type=NodeType.FACT,
                event_time=mar,
            )
            await engine.store(
                "Product launched in June",
                user_id="test-user",
                node_type=NodeType.FACT,
                event_time=jun,
            )

            # Query for events that happened in Q1 (Jan-Mar)
            q1_start = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
            q1_end = datetime(2025, 3, 31, 23, 59, 59, tzinfo=timezone.utc)

            response = await engine.retrieve(
                "What happened in the company?",
                user_id="test-user",
                event_time_from=q1_start,
                event_time_to=q1_end,
            )

            result_contents = [r.node.content for r in response.results]
            assert any("January" in c for c in result_contents), (
                "January fact should appear in Q1 range"
            )
            assert any("March" in c for c in result_contents), (
                "March fact should appear in Q1 range"
            )
            assert not any("June" in c for c in result_contents), (
                "June fact should NOT appear in Q1 range"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_event_time_fallback_to_created_at(self, config):
        """Nodes without event_time fall back to created_at for range filtering."""
        engine = await create_engine(config)
        try:
            now = datetime.now(timezone.utc)

            # Store a fact without event_time (will use created_at)
            await engine.store(
                "Regular fact without event time",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            # Query with event_time range that includes now
            response = await engine.retrieve(
                "What facts are stored?",
                user_id="test-user",
                event_time_from=now - timedelta(minutes=5),
                event_time_to=now + timedelta(minutes=5),
            )

            result_contents = [r.node.content for r in response.results]
            assert any("Regular fact" in c for c in result_contents), (
                "Fact without event_time should match via created_at fallback"
            )
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Tests: Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Verify existing functionality is not broken."""

    @pytest.mark.asyncio
    async def test_store_without_new_params(self, config):
        """store() works exactly as before when event_time is not provided."""
        engine = await create_engine(config)
        try:
            event_id = await engine.store(
                "Test backward compatibility",
                user_id="test-user",
                role="user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            assert event_id is not None

            # Retrieve works without bi-temporal params
            response = await engine.retrieve(
                "Test backward compatibility",
                user_id="test-user",
            )
            assert response is not None
            assert len(response.results) > 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_retrieve_without_bi_temporal_params(self, config):
        """retrieve() works exactly as before without bi-temporal params."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Existing retrieval test",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            # All existing parameters still work
            response = await engine.retrieve(
                "Existing retrieval test",
                user_id="test-user",
                scope=Scope.PERSONAL,
                time_from=datetime.now(timezone.utc) - timedelta(hours=1),
                time_to=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            assert response is not None
        finally:
            await engine.close()
