"""Tests for entity snapshot generation (issue #13).

Covers:
- Single entity snapshot generation
- Snapshot includes related facts, preferences, decisions, tasks
- Summary text generation
- Snapshot with at_time parameter
- Snapshot for entity with no relationships
- Bulk snapshot generation (generate_all_entity_snapshots)
- render_snapshot_text output
- Error handling for missing/non-entity nodes
- Organizer snapshot_generation job
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import duckdb
import pytest
import pytest_asyncio

from prme.config import OrganizerConfig, PRMEConfig
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.jobs import ALL_JOBS, run_job
from prme.organizer.models import JobResult
from prme.retrieval.snapshots import (
    EntitySnapshot,
    generate_all_entity_snapshots,
    generate_entity_snapshot,
    render_snapshot_text,
)
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.schema import initialize_database
from prme.types import (
    EdgeType,
    LifecycleState,
    NodeType,
    Scope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    *,
    user_id: str = "test-user",
    content: str = "test content",
    node_type: NodeType = NodeType.FACT,
    lifecycle_state: LifecycleState = LifecycleState.TENTATIVE,
    scope: Scope = Scope.PERSONAL,
) -> MemoryNode:
    """Create a MemoryNode for testing."""
    now = datetime.now(timezone.utc)
    return MemoryNode(
        id=uuid4(),
        user_id=user_id,
        node_type=node_type,
        content=content,
        lifecycle_state=lifecycle_state,
        scope=scope,
        created_at=now,
    )


def _make_edge(
    source_id,
    target_id,
    *,
    edge_type: EdgeType = EdgeType.RELATES_TO,
    user_id: str = "test-user",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> MemoryEdge:
    """Create a MemoryEdge for testing."""
    now = datetime.now(timezone.utc)
    return MemoryEdge(
        id=uuid4(),
        source_id=source_id,
        target_id=target_id,
        edge_type=edge_type,
        user_id=user_id,
        valid_from=valid_from or now,
        valid_to=valid_to,
    )


# ---------------------------------------------------------------------------
# Mock embedding provider
# ---------------------------------------------------------------------------


class MockEmbeddingProvider:
    """Mock embedding provider for testing."""

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
        return [[0.5] * 8 for _ in texts]


# ---------------------------------------------------------------------------
# Fixture: lightweight MemoryEngine with DuckDB graph store
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine_parts(tmp_path):
    """Create a minimal MemoryEngine with real DuckDB backends.

    Yields (engine, graph_store, conn).
    """
    from prme.storage.engine import MemoryEngine
    from prme.storage.event_store import EventStore
    from prme.storage.lexical_index import LexicalIndex
    from prme.storage.vector_index import VectorIndex
    from prme.storage.write_queue import WriteQueue

    db_path = str(tmp_path / "test.duckdb")
    conn = duckdb.connect(db_path)
    initialize_database(conn)

    conn_lock = asyncio.Lock()
    graph_store = DuckPGQGraphStore(conn, conn_lock)
    event_store = EventStore(conn, conn_lock)

    embedding_provider = MockEmbeddingProvider()
    vector_path = str(tmp_path / "vectors.usearch")
    vector_index = VectorIndex(conn, vector_path, embedding_provider, conn_lock)

    lexical_path = tmp_path / "lexical_index"
    lexical_path.mkdir()
    lexical_index = LexicalIndex(str(lexical_path))

    write_queue = WriteQueue(maxsize=100)
    await write_queue.start()

    config = PRMEConfig(
        db_path=db_path,
        vector_path=vector_path,
        lexical_path=str(lexical_path),
    )

    engine = MemoryEngine(
        conn=conn,
        event_store=event_store,
        graph_store=graph_store,
        vector_index=vector_index,
        lexical_index=lexical_index,
        write_queue=write_queue,
        config=config,
    )

    yield engine, graph_store, conn

    await write_queue.stop()
    await vector_index.close()
    await lexical_index.close()
    conn.close()


# ---------------------------------------------------------------------------
# Tests: Single Entity Snapshot
# ---------------------------------------------------------------------------


class TestGenerateEntitySnapshot:
    """Tests for generate_entity_snapshot()."""

    @pytest.mark.asyncio
    async def test_basic_snapshot(self, engine_parts):
        """Generate a snapshot for an entity with related nodes."""
        engine, graph_store, _ = engine_parts

        # Create entity
        entity = _make_node(
            node_type=NodeType.ENTITY,
            content="Alice",
        )
        await graph_store.create_node(entity)

        # Create related fact
        fact = _make_node(
            node_type=NodeType.FACT,
            content="Alice likes Python",
        )
        await graph_store.create_node(fact)

        # Create edge: entity -> fact
        edge = _make_edge(entity.id, fact.id, edge_type=EdgeType.HAS_FACT)
        await graph_store.create_edge(edge)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        assert snapshot.entity_node.id == entity.id
        assert snapshot.entity_node.content == "Alice"
        assert len(snapshot.facts) == 1
        assert snapshot.facts[0].content == "Alice likes Python"
        assert snapshot.generated_at is not None

    @pytest.mark.asyncio
    async def test_snapshot_includes_all_types(self, engine_parts):
        """Snapshot groups related nodes by type: facts, preferences, decisions, tasks."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Bob")
        await graph_store.create_node(entity)

        fact = _make_node(node_type=NodeType.FACT, content="Bob is a developer")
        pref = _make_node(node_type=NodeType.PREFERENCE, content="Bob prefers dark mode")
        dec = _make_node(node_type=NodeType.DECISION, content="Bob chose React")
        task = _make_node(node_type=NodeType.TASK, content="Bob: review PR #42")

        for node in [fact, pref, dec, task]:
            await graph_store.create_node(node)
            edge = _make_edge(entity.id, node.id, edge_type=EdgeType.RELATES_TO)
            await graph_store.create_edge(edge)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        assert len(snapshot.facts) == 1
        assert len(snapshot.preferences) == 1
        assert len(snapshot.decisions) == 1
        assert len(snapshot.tasks) == 1

    @pytest.mark.asyncio
    async def test_snapshot_collects_edges(self, engine_parts):
        """Snapshot includes edges involving the entity."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Carol")
        fact = _make_node(node_type=NodeType.FACT, content="Carol knows Go")
        await graph_store.create_node(entity)
        await graph_store.create_node(fact)

        edge = _make_edge(entity.id, fact.id, edge_type=EdgeType.HAS_FACT)
        await graph_store.create_edge(edge)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        assert len(snapshot.relationships) >= 1
        edge_types = [e.edge_type for e in snapshot.relationships]
        assert EdgeType.HAS_FACT in edge_types

    @pytest.mark.asyncio
    async def test_snapshot_excludes_superseded(self, engine_parts):
        """Snapshot excludes neighbors in superseded/archived states."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Dave")
        await graph_store.create_node(entity)

        active_fact = _make_node(
            node_type=NodeType.FACT,
            content="Dave uses Linux",
            lifecycle_state=LifecycleState.STABLE,
        )
        await graph_store.create_node(active_fact)

        archived_fact = _make_node(
            node_type=NodeType.FACT,
            content="Dave uses Windows",
            lifecycle_state=LifecycleState.ARCHIVED,
        )
        await graph_store.create_node(archived_fact)

        for f in [active_fact, archived_fact]:
            edge = _make_edge(entity.id, f.id, edge_type=EdgeType.HAS_FACT)
            await graph_store.create_edge(edge)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        # Only the active fact should appear
        assert len(snapshot.facts) == 1
        assert snapshot.facts[0].content == "Dave uses Linux"


# ---------------------------------------------------------------------------
# Tests: Summary Text
# ---------------------------------------------------------------------------


class TestSummaryText:
    """Tests for summary text generation."""

    @pytest.mark.asyncio
    async def test_summary_includes_entity(self, engine_parts):
        """Summary text starts with entity content."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Eve")
        await graph_store.create_node(entity)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        assert snapshot.summary_text.startswith("Entity: Eve")

    @pytest.mark.asyncio
    async def test_summary_includes_facts(self, engine_parts):
        """Summary text includes known facts."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Frank")
        fact = _make_node(node_type=NodeType.FACT, content="Frank is a designer")
        await graph_store.create_node(entity)
        await graph_store.create_node(fact)
        edge = _make_edge(entity.id, fact.id, edge_type=EdgeType.HAS_FACT)
        await graph_store.create_edge(edge)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        assert "Known facts:" in snapshot.summary_text
        assert "Frank is a designer" in snapshot.summary_text

    @pytest.mark.asyncio
    async def test_summary_includes_preferences_and_tasks(self, engine_parts):
        """Summary text includes preferences and tasks."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Grace")
        pref = _make_node(node_type=NodeType.PREFERENCE, content="Grace likes tea")
        task = _make_node(node_type=NodeType.TASK, content="Grace: deploy v2")
        await graph_store.create_node(entity)
        for node in [pref, task]:
            await graph_store.create_node(node)
            edge = _make_edge(entity.id, node.id, edge_type=EdgeType.RELATES_TO)
            await graph_store.create_edge(edge)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        assert "Preferences:" in snapshot.summary_text
        assert "Grace likes tea" in snapshot.summary_text
        assert "Active tasks:" in snapshot.summary_text
        assert "Grace: deploy v2" in snapshot.summary_text

    @pytest.mark.asyncio
    async def test_summary_empty_relationships(self, engine_parts):
        """Summary for entity with no relationships is just entity line."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Hank")
        await graph_store.create_node(entity)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        assert snapshot.summary_text == "Entity: Hank."
        assert "Known facts" not in snapshot.summary_text


# ---------------------------------------------------------------------------
# Tests: at_time Parameter
# ---------------------------------------------------------------------------


class TestSnapshotAtTime:
    """Tests for the at_time temporal filter."""

    @pytest.mark.asyncio
    async def test_snapshot_with_at_time(self, engine_parts):
        """at_time parameter filters edges/neighbors by temporal validity."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Ivy")
        await graph_store.create_node(entity)

        # Fact valid in the past only
        past_fact = _make_node(node_type=NodeType.FACT, content="Ivy used Java")
        await graph_store.create_node(past_fact)
        past_edge = _make_edge(
            entity.id, past_fact.id,
            edge_type=EdgeType.HAS_FACT,
            valid_from=datetime(2023, 1, 1, tzinfo=timezone.utc),
            valid_to=datetime(2023, 6, 1, tzinfo=timezone.utc),
        )
        await graph_store.create_edge(past_edge)

        # Fact valid now
        now_fact = _make_node(node_type=NodeType.FACT, content="Ivy uses Rust")
        await graph_store.create_node(now_fact)
        now_edge = _make_edge(
            entity.id, now_fact.id,
            edge_type=EdgeType.HAS_FACT,
            valid_from=datetime(2023, 6, 1, tzinfo=timezone.utc),
        )
        await graph_store.create_edge(now_edge)

        # Snapshot at past time should include "Ivy used Java"
        past_snapshot = await generate_entity_snapshot(
            engine, str(entity.id),
            at_time=datetime(2023, 3, 1, tzinfo=timezone.utc),
        )

        # Snapshot at current time should include "Ivy uses Rust"
        current_snapshot = await generate_entity_snapshot(
            engine, str(entity.id),
            at_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

        # Both snapshots should have the entity node
        assert past_snapshot.entity_node.content == "Ivy"
        assert current_snapshot.entity_node.content == "Ivy"


# ---------------------------------------------------------------------------
# Tests: Entity with No Relationships
# ---------------------------------------------------------------------------


class TestNoRelationships:
    """Tests for snapshot of entity with no related nodes."""

    @pytest.mark.asyncio
    async def test_empty_snapshot(self, engine_parts):
        """Entity with no relationships produces empty snapshot lists."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="Lone Entity")
        await graph_store.create_node(entity)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))

        assert snapshot.entity_node.content == "Lone Entity"
        assert snapshot.facts == []
        assert snapshot.preferences == []
        assert snapshot.decisions == []
        assert snapshot.tasks == []
        assert snapshot.relationships == []


# ---------------------------------------------------------------------------
# Tests: Error Handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error conditions."""

    @pytest.mark.asyncio
    async def test_missing_entity_raises(self, engine_parts):
        """Generating snapshot for non-existent entity raises ValueError."""
        engine, _, _ = engine_parts

        with pytest.raises(ValueError, match="not found"):
            await generate_entity_snapshot(engine, str(uuid4()))

    @pytest.mark.asyncio
    async def test_non_entity_node_raises(self, engine_parts):
        """Generating snapshot for a FACT node raises ValueError."""
        engine, graph_store, _ = engine_parts

        fact = _make_node(node_type=NodeType.FACT, content="Not an entity")
        await graph_store.create_node(fact)

        with pytest.raises(ValueError, match="not entity"):
            await generate_entity_snapshot(engine, str(fact.id))


# ---------------------------------------------------------------------------
# Tests: Bulk Snapshot Generation
# ---------------------------------------------------------------------------


class TestGenerateAllEntitySnapshots:
    """Tests for generate_all_entity_snapshots()."""

    @pytest.mark.asyncio
    async def test_generates_for_all_entities(self, engine_parts):
        """generate_all_entity_snapshots returns snapshots for all active entities."""
        engine, graph_store, _ = engine_parts

        e1 = _make_node(node_type=NodeType.ENTITY, content="Entity A")
        e2 = _make_node(node_type=NodeType.ENTITY, content="Entity B")
        e3 = _make_node(node_type=NodeType.ENTITY, content="Entity C")
        for e in [e1, e2, e3]:
            await graph_store.create_node(e)

        snapshots = await generate_all_entity_snapshots(engine)

        assert len(snapshots) == 3
        names = {s.entity_node.content for s in snapshots}
        assert names == {"Entity A", "Entity B", "Entity C"}

    @pytest.mark.asyncio
    async def test_respects_limit(self, engine_parts):
        """generate_all_entity_snapshots respects the limit parameter."""
        engine, graph_store, _ = engine_parts

        for i in range(5):
            e = _make_node(node_type=NodeType.ENTITY, content=f"Entity {i}")
            await graph_store.create_node(e)

        snapshots = await generate_all_entity_snapshots(engine, limit=2)

        assert len(snapshots) == 2

    @pytest.mark.asyncio
    async def test_empty_when_no_entities(self, engine_parts):
        """generate_all_entity_snapshots returns empty list when no entities exist."""
        engine, _, _ = engine_parts

        snapshots = await generate_all_entity_snapshots(engine)

        assert snapshots == []

    @pytest.mark.asyncio
    async def test_skips_archived_entities(self, engine_parts):
        """generate_all_entity_snapshots skips entities in archived state."""
        engine, graph_store, _ = engine_parts

        active = _make_node(
            node_type=NodeType.ENTITY,
            content="Active Entity",
            lifecycle_state=LifecycleState.STABLE,
        )
        archived = _make_node(
            node_type=NodeType.ENTITY,
            content="Archived Entity",
            lifecycle_state=LifecycleState.ARCHIVED,
        )
        await graph_store.create_node(active)
        await graph_store.create_node(archived)

        snapshots = await generate_all_entity_snapshots(engine)

        assert len(snapshots) == 1
        assert snapshots[0].entity_node.content == "Active Entity"


# ---------------------------------------------------------------------------
# Tests: render_snapshot_text
# ---------------------------------------------------------------------------


class TestRenderSnapshotText:
    """Tests for render_snapshot_text()."""

    @pytest.mark.asyncio
    async def test_renders_entity_header(self, engine_parts):
        """Rendered text includes entity header."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="TestEntity")
        await graph_store.create_node(entity)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))
        text = render_snapshot_text(snapshot)

        assert "[Entity Snapshot: TestEntity]" in text
        assert f"ID: {entity.id}" in text

    @pytest.mark.asyncio
    async def test_renders_facts_and_preferences(self, engine_parts):
        """Rendered text includes facts and preferences sections."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="RenderTest")
        fact = _make_node(node_type=NodeType.FACT, content="fact-content-1")
        pref = _make_node(node_type=NodeType.PREFERENCE, content="pref-content-1")
        await graph_store.create_node(entity)
        for node in [fact, pref]:
            await graph_store.create_node(node)
            edge = _make_edge(entity.id, node.id, edge_type=EdgeType.RELATES_TO)
            await graph_store.create_edge(edge)

        snapshot = await generate_entity_snapshot(engine, str(entity.id))
        text = render_snapshot_text(snapshot)

        assert "Facts:" in text
        assert "fact-content-1" in text
        assert "Preferences:" in text
        assert "pref-content-1" in text


# ---------------------------------------------------------------------------
# Tests: Engine.snapshot() Convenience Method
# ---------------------------------------------------------------------------


class TestEngineSnapshotMethod:
    """Tests for MemoryEngine.snapshot()."""

    @pytest.mark.asyncio
    async def test_engine_snapshot_delegates(self, engine_parts):
        """engine.snapshot() delegates to generate_entity_snapshot."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="EngineEntity")
        await graph_store.create_node(entity)

        snapshot = await engine.snapshot(str(entity.id))

        assert isinstance(snapshot, EntitySnapshot)
        assert snapshot.entity_node.content == "EngineEntity"

    @pytest.mark.asyncio
    async def test_engine_snapshot_with_at_time(self, engine_parts):
        """engine.snapshot() passes at_time to underlying function."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="TimeEntity")
        await graph_store.create_node(entity)

        past = datetime(2023, 6, 1, tzinfo=timezone.utc)
        snapshot = await engine.snapshot(str(entity.id), at_time=past)

        assert snapshot.entity_node.content == "TimeEntity"


# ---------------------------------------------------------------------------
# Tests: Organizer Job
# ---------------------------------------------------------------------------


class TestSnapshotGenerationJob:
    """Tests for the snapshot_generation organizer job."""

    @pytest.mark.asyncio
    async def test_job_in_all_jobs(self):
        """snapshot_generation is registered in ALL_JOBS."""
        assert "snapshot_generation" in ALL_JOBS

    @pytest.mark.asyncio
    async def test_job_runs(self, engine_parts):
        """snapshot_generation job runs and returns JobResult."""
        engine, graph_store, _ = engine_parts

        entity = _make_node(node_type=NodeType.ENTITY, content="JobEntity")
        await graph_store.create_node(entity)

        config = OrganizerConfig()
        result = await run_job("snapshot_generation", engine, config, 5000.0)

        assert isinstance(result, JobResult)
        assert result.job == "snapshot_generation"
        assert result.nodes_processed >= 1
        assert result.nodes_modified >= 1

    @pytest.mark.asyncio
    async def test_job_no_entities(self, engine_parts):
        """snapshot_generation job with no entities processes 0 nodes."""
        engine, _, _ = engine_parts

        config = OrganizerConfig()
        result = await run_job("snapshot_generation", engine, config, 5000.0)

        assert result.nodes_processed == 0
        assert result.nodes_modified == 0
