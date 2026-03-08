"""Tests for the hierarchical summarization pipeline (issue #10).

Covers:
- Daily summary generation from events/nodes
- Weekly rollup from daily summaries
- Monthly rollup from weekly summaries
- Correct evidence_refs on summary nodes
- DERIVED_FROM edges between summaries and sources
- Config thresholds (min events, min summaries)
- Time budget enforcement
- Summary node properties (node_type, epistemic_type, lifecycle_state)
- Idempotency (no duplicate summaries)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import duckdb
import pytest
import pytest_asyncio

from prme.config import OrganizerConfig, PRMEConfig
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.jobs import run_job
from prme.organizer.models import JobResult
from prme.organizer.summarization import (
    SummarizationLevel,
    generate_daily_summaries,
    roll_up_monthly,
    roll_up_weekly,
)
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.schema import initialize_database
from prme.types import (
    DecayProfile,
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    SourceType,
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
    decay_profile: DecayProfile = DecayProfile.MEDIUM,
    salience_base: float = 0.5,
    confidence_base: float = 0.5,
    salience: float = 0.5,
    confidence: float = 0.5,
    epistemic_type: EpistemicType = EpistemicType.ASSERTED,
    evidence_refs: list | None = None,
    created_at: datetime | None = None,
    metadata: dict | None = None,
) -> MemoryNode:
    """Create a MemoryNode for testing."""
    now = datetime.now(timezone.utc)
    return MemoryNode(
        id=uuid4(),
        user_id=user_id,
        node_type=node_type,
        content=content,
        lifecycle_state=lifecycle_state,
        decay_profile=decay_profile,
        salience_base=salience_base,
        confidence_base=confidence_base,
        salience=salience,
        confidence=confidence,
        epistemic_type=epistemic_type,
        evidence_refs=evidence_refs or [uuid4()],
        created_at=created_at or now,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Fixture: lightweight MemoryEngine with DuckDB graph store
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


@pytest_asyncio.fixture
async def engine_parts(tmp_path):
    """Create a minimal MemoryEngine with real DuckDB backends.

    Yields (engine, graph_store, conn) for direct graph manipulation.
    """
    from prme.organizer.maintenance import MaintenanceRunner
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
    engine._maintenance_runner = MaintenanceRunner(engine, config.organizer)

    yield engine, graph_store, conn

    await write_queue.stop()
    await vector_index.close()
    await lexical_index.close()
    conn.close()


# ---------------------------------------------------------------------------
# Daily Summary Generation
# ---------------------------------------------------------------------------


class TestDailySummaryGeneration:
    """Tests for generate_daily_summaries()."""

    @pytest.mark.asyncio
    async def test_creates_daily_summary_when_threshold_met(self, engine_parts):
        """Creates a daily summary when day has enough events."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=3,
            summarization_max_items_per_summary=5,
        )

        # Create 5 nodes on the same day
        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            node = _make_node(
                content=f"Fact {i} for daily summary",
                salience_base=0.5 + (i * 0.05),
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)

        result = await generate_daily_summaries(engine, config, 5000.0)
        assert result.nodes_modified == 1
        assert result.nodes_processed == 1

        # Verify summary node was created
        summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.node_type == NodeType.SUMMARY
        assert summary.epistemic_type == EpistemicType.OBSERVED
        assert summary.lifecycle_state == LifecycleState.STABLE
        assert "2026-03-01" in summary.content
        assert summary.metadata["summarization_level"] == "daily"
        assert summary.metadata["period_key"] == "2026-03-01"
        assert summary.metadata["source_count"] == 5

    @pytest.mark.asyncio
    async def test_no_summary_below_threshold(self, engine_parts):
        """No daily summary when event count is below threshold."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=10,  # High threshold
        )

        # Only create 3 nodes
        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            node = _make_node(
                content=f"Fact {i}",
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)

        result = await generate_daily_summaries(engine, config, 5000.0)
        assert result.nodes_modified == 0
        assert result.nodes_processed == 0

    @pytest.mark.asyncio
    async def test_multiple_days_get_separate_summaries(self, engine_parts):
        """Each day with enough events gets its own summary."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_max_items_per_summary=5,
        )

        # Create nodes for 3 different days
        for day_offset in range(3):
            day = datetime(2026, 3, 1 + day_offset, 12, 0, 0, tzinfo=timezone.utc)
            for i in range(3):
                node = _make_node(
                    content=f"Day {day_offset} fact {i}",
                    created_at=day + timedelta(hours=i),
                )
                await graph_store.create_node(node)

        result = await generate_daily_summaries(engine, config, 5000.0)
        assert result.nodes_modified == 3

        summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        assert len(summaries) == 3

    @pytest.mark.asyncio
    async def test_selects_top_salient_items(self, engine_parts):
        """Summary picks top-N most salient items, not all."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_max_items_per_summary=2,  # Only 2 items
        )

        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Create nodes with varying salience
        for i, salience in enumerate([0.3, 0.9, 0.1, 0.8]):
            node = _make_node(
                content=f"Fact with salience {salience}",
                salience_base=salience,
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)

        result = await generate_daily_summaries(engine, config, 5000.0)
        assert result.nodes_modified == 1

        summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        assert len(summaries) == 1
        # Summary should contain the 2 highest salience items (0.9, 0.8)
        assert "salience 0.9" in summaries[0].content
        assert "salience 0.8" in summaries[0].content
        assert summaries[0].metadata["source_count"] == 2

    @pytest.mark.asyncio
    async def test_summary_has_correct_evidence_refs(self, engine_parts):
        """Summary node's evidence_refs include source node evidence + source node IDs."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_max_items_per_summary=10,
        )

        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        source_nodes = []
        for i in range(3):
            node = _make_node(
                content=f"Fact {i}",
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)
            source_nodes.append(node)

        await generate_daily_summaries(engine, config, 5000.0)

        summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        assert len(summaries) == 1
        summary = summaries[0]

        # Evidence refs should contain source node IDs
        evidence_ref_set = set(summary.evidence_refs)
        for src_node in source_nodes:
            assert src_node.id in evidence_ref_set

    @pytest.mark.asyncio
    async def test_derived_from_edges_created(self, engine_parts):
        """DERIVED_FROM edges are created from summary to each source node."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_max_items_per_summary=10,
        )

        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        source_nodes = []
        for i in range(3):
            node = _make_node(
                content=f"Fact {i}",
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)
            source_nodes.append(node)

        await generate_daily_summaries(engine, config, 5000.0)

        summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        assert len(summaries) == 1
        summary = summaries[0]

        # Check DERIVED_FROM edges
        edges = await graph_store.get_edges(
            source_id=str(summary.id),
            edge_type=EdgeType.DERIVED_FROM,
        )
        assert len(edges) == 3
        target_ids = {e.target_id for e in edges}
        for src_node in source_nodes:
            assert src_node.id in target_ids

    @pytest.mark.asyncio
    async def test_idempotent_no_duplicate_summaries(self, engine_parts):
        """Running daily summarization twice doesn't create duplicates."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_max_items_per_summary=10,
        )

        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            node = _make_node(
                content=f"Fact {i}",
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)

        # Run twice
        await generate_daily_summaries(engine, config, 5000.0)
        result2 = await generate_daily_summaries(engine, config, 5000.0)

        # Second run should not create any new summaries
        assert result2.nodes_modified == 0

        summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        assert len(summaries) == 1

    @pytest.mark.asyncio
    async def test_excludes_summary_nodes_from_sources(self, engine_parts):
        """Daily summarization excludes existing summary nodes from source grouping."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_max_items_per_summary=10,
        )

        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Create fact nodes
        for i in range(3):
            node = _make_node(
                content=f"Fact {i}",
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)

        # Create a summary node on the same day (from a previous run)
        summary_node = _make_node(
            content="Existing summary",
            node_type=NodeType.SUMMARY,
            lifecycle_state=LifecycleState.STABLE,
            created_at=day + timedelta(hours=5),
            metadata={"summarization_level": "weekly", "period_key": "2026-W09"},
        )
        await graph_store.create_node(summary_node)

        result = await generate_daily_summaries(engine, config, 5000.0)
        # Should only count the 3 fact nodes, not the summary
        assert result.nodes_modified == 1


# ---------------------------------------------------------------------------
# Weekly Rollup
# ---------------------------------------------------------------------------


class TestWeeklyRollup:
    """Tests for roll_up_weekly()."""

    @pytest.mark.asyncio
    async def test_creates_weekly_from_daily_summaries(self, engine_parts):
        """Weekly rollup creates summary from enough daily summaries."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_weekly_min_summaries=2,
            summarization_max_items_per_summary=10,
        )

        # Create daily summary nodes within same week (Mon 2026-03-02 to Sun 2026-03-08)
        for day_offset in range(4):
            day = datetime(2026, 3, 2 + day_offset, 12, 0, 0, tzinfo=timezone.utc)
            period_key = day.strftime("%Y-%m-%d")
            node = _make_node(
                content=f"[daily summary: {period_key}] content",
                node_type=NodeType.SUMMARY,
                lifecycle_state=LifecycleState.STABLE,
                created_at=day,
                metadata={
                    "summarization_level": "daily",
                    "period_key": period_key,
                    "source_count": 5,
                },
            )
            await graph_store.create_node(node)

        result = await roll_up_weekly(engine, config, 5000.0)
        assert result.nodes_modified == 1

        # Verify weekly summary was created
        all_summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        weekly_summaries = [
            n for n in all_summaries
            if n.metadata and n.metadata.get("summarization_level") == "weekly"
        ]
        assert len(weekly_summaries) == 1
        assert "weekly summary" in weekly_summaries[0].content.lower()

    @pytest.mark.asyncio
    async def test_no_weekly_below_threshold(self, engine_parts):
        """No weekly summary when not enough daily summaries."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_weekly_min_summaries=5,  # High threshold
        )

        # Only 2 daily summaries
        for day_offset in range(2):
            day = datetime(2026, 3, 2 + day_offset, 12, 0, 0, tzinfo=timezone.utc)
            period_key = day.strftime("%Y-%m-%d")
            node = _make_node(
                content=f"[daily summary: {period_key}]",
                node_type=NodeType.SUMMARY,
                lifecycle_state=LifecycleState.STABLE,
                created_at=day,
                metadata={
                    "summarization_level": "daily",
                    "period_key": period_key,
                    "source_count": 5,
                },
            )
            await graph_store.create_node(node)

        result = await roll_up_weekly(engine, config, 5000.0)
        assert result.nodes_modified == 0

    @pytest.mark.asyncio
    async def test_weekly_derived_from_edges(self, engine_parts):
        """Weekly summary has DERIVED_FROM edges to daily summaries."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_weekly_min_summaries=2,
            summarization_max_items_per_summary=10,
        )

        daily_nodes = []
        for day_offset in range(3):
            day = datetime(2026, 3, 2 + day_offset, 12, 0, 0, tzinfo=timezone.utc)
            period_key = day.strftime("%Y-%m-%d")
            node = _make_node(
                content=f"[daily summary: {period_key}]",
                node_type=NodeType.SUMMARY,
                lifecycle_state=LifecycleState.STABLE,
                created_at=day,
                metadata={
                    "summarization_level": "daily",
                    "period_key": period_key,
                    "source_count": 5,
                },
            )
            await graph_store.create_node(node)
            daily_nodes.append(node)

        await roll_up_weekly(engine, config, 5000.0)

        all_summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        weekly_summaries = [
            n for n in all_summaries
            if n.metadata and n.metadata.get("summarization_level") == "weekly"
        ]
        assert len(weekly_summaries) == 1

        edges = await graph_store.get_edges(
            source_id=str(weekly_summaries[0].id),
            edge_type=EdgeType.DERIVED_FROM,
        )
        assert len(edges) == 3


# ---------------------------------------------------------------------------
# Monthly Rollup
# ---------------------------------------------------------------------------


class TestMonthlyRollup:
    """Tests for roll_up_monthly()."""

    @pytest.mark.asyncio
    async def test_creates_monthly_from_weekly_summaries(self, engine_parts):
        """Monthly rollup creates summary from enough weekly summaries."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_monthly_min_summaries=2,
            summarization_max_items_per_summary=10,
        )

        # Create weekly summary nodes within same month
        for week in range(3):
            day = datetime(2026, 3, 2 + (week * 7), 12, 0, 0, tzinfo=timezone.utc)
            iso = day.isocalendar()
            week_key = f"{iso[0]}-W{iso[1]:02d}"
            node = _make_node(
                content=f"[weekly summary: {week_key}]",
                node_type=NodeType.SUMMARY,
                lifecycle_state=LifecycleState.STABLE,
                created_at=day,
                metadata={
                    "summarization_level": "weekly",
                    "period_key": week_key,
                    "source_count": 5,
                },
            )
            await graph_store.create_node(node)

        result = await roll_up_monthly(engine, config, 5000.0)
        assert result.nodes_modified == 1

        all_summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        monthly_summaries = [
            n for n in all_summaries
            if n.metadata and n.metadata.get("summarization_level") == "monthly"
        ]
        assert len(monthly_summaries) == 1
        assert "monthly summary" in monthly_summaries[0].content.lower()
        assert monthly_summaries[0].metadata["period_key"] == "2026-03"

    @pytest.mark.asyncio
    async def test_no_monthly_below_threshold(self, engine_parts):
        """No monthly summary when not enough weekly summaries."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_monthly_min_summaries=5,  # High threshold
        )

        day = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
        iso = day.isocalendar()
        week_key = f"{iso[0]}-W{iso[1]:02d}"
        node = _make_node(
            content=f"[weekly summary: {week_key}]",
            node_type=NodeType.SUMMARY,
            lifecycle_state=LifecycleState.STABLE,
            created_at=day,
            metadata={
                "summarization_level": "weekly",
                "period_key": week_key,
                "source_count": 5,
            },
        )
        await graph_store.create_node(node)

        result = await roll_up_monthly(engine, config, 5000.0)
        assert result.nodes_modified == 0

    @pytest.mark.asyncio
    async def test_monthly_derived_from_edges(self, engine_parts):
        """Monthly summary has DERIVED_FROM edges to weekly summaries."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_monthly_min_summaries=2,
            summarization_max_items_per_summary=10,
        )

        weekly_nodes = []
        for week in range(3):
            day = datetime(2026, 3, 2 + (week * 7), 12, 0, 0, tzinfo=timezone.utc)
            iso = day.isocalendar()
            week_key = f"{iso[0]}-W{iso[1]:02d}"
            node = _make_node(
                content=f"[weekly summary: {week_key}]",
                node_type=NodeType.SUMMARY,
                lifecycle_state=LifecycleState.STABLE,
                created_at=day,
                metadata={
                    "summarization_level": "weekly",
                    "period_key": week_key,
                    "source_count": 5,
                },
            )
            await graph_store.create_node(node)
            weekly_nodes.append(node)

        await roll_up_monthly(engine, config, 5000.0)

        all_summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        monthly_summaries = [
            n for n in all_summaries
            if n.metadata and n.metadata.get("summarization_level") == "monthly"
        ]
        assert len(monthly_summaries) == 1

        edges = await graph_store.get_edges(
            source_id=str(monthly_summaries[0].id),
            edge_type=EdgeType.DERIVED_FROM,
        )
        assert len(edges) == 3


# ---------------------------------------------------------------------------
# Time Budget Enforcement
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    """Tests for time budget enforcement."""

    @pytest.mark.asyncio
    async def test_daily_respects_zero_budget(self, engine_parts):
        """Daily summarization with 0 budget creates nothing."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(summarization_daily_min_events=2)

        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            node = _make_node(
                content=f"Fact {i}",
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)

        # Zero budget -- should exit immediately
        result = await generate_daily_summaries(engine, config, 0.0)
        assert result.nodes_modified == 0


# ---------------------------------------------------------------------------
# Full Pipeline via run_job("summarize")
# ---------------------------------------------------------------------------


class TestSummarizeJob:
    """Tests for the full summarize job through run_job()."""

    @pytest.mark.asyncio
    async def test_summarize_job_runs_all_levels(self, engine_parts):
        """The summarize job runs daily, weekly, and monthly in sequence."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_weekly_min_summaries=2,
            summarization_monthly_min_summaries=2,
            summarization_max_items_per_summary=10,
        )

        # Create enough nodes across enough days within one week and one month
        # Week 1: 3 days with 3 nodes each = 3 daily summaries
        for day_offset in range(3):
            day = datetime(2026, 3, 2 + day_offset, 12, 0, 0, tzinfo=timezone.utc)
            for i in range(3):
                node = _make_node(
                    content=f"Week1 Day{day_offset} Fact {i}",
                    created_at=day + timedelta(hours=i),
                )
                await graph_store.create_node(node)

        # Week 2: 3 days with 3 nodes each = 3 daily summaries
        for day_offset in range(3):
            day = datetime(2026, 3, 9 + day_offset, 12, 0, 0, tzinfo=timezone.utc)
            for i in range(3):
                node = _make_node(
                    content=f"Week2 Day{day_offset} Fact {i}",
                    created_at=day + timedelta(hours=i),
                )
                await graph_store.create_node(node)

        result = await run_job("summarize", engine, config, 10000.0)
        assert result.job == "summarize"
        assert isinstance(result, JobResult)

        # Should have created daily summaries, weekly rollups, and potentially monthly
        assert result.nodes_modified > 0
        assert "daily" in result.details

        # Verify some summaries exist
        all_summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        assert len(all_summaries) > 0

        # Check for daily summaries
        daily = [
            n for n in all_summaries
            if n.metadata and n.metadata.get("summarization_level") == "daily"
        ]
        assert len(daily) >= 1  # At least some daily summaries

    @pytest.mark.asyncio
    async def test_summarize_job_no_longer_stub(self, engine_parts):
        """Summarize job no longer returns stub status."""
        engine, _, _ = engine_parts
        config = OrganizerConfig()
        result = await run_job("summarize", engine, config, 5000.0)
        assert result.details.get("status") != "stub"


# ---------------------------------------------------------------------------
# Summary Node Properties
# ---------------------------------------------------------------------------


class TestSummaryNodeProperties:
    """Tests for summary node property correctness."""

    @pytest.mark.asyncio
    async def test_summary_node_type_is_summary(self, engine_parts):
        """Summary nodes have node_type=SUMMARY."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_max_items_per_summary=10,
        )

        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            node = _make_node(
                content=f"Fact {i}",
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)

        await generate_daily_summaries(engine, config, 5000.0)

        summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        assert len(summaries) == 1
        s = summaries[0]
        assert s.node_type == NodeType.SUMMARY
        assert s.epistemic_type == EpistemicType.OBSERVED
        assert s.lifecycle_state == LifecycleState.STABLE
        assert s.source_type == SourceType.SYSTEM_INFERRED
        assert s.decay_profile == DecayProfile.SLOW
        assert s.scope == Scope.SYSTEM

    @pytest.mark.asyncio
    async def test_summary_salience_is_average_of_sources(self, engine_parts):
        """Summary salience is the average of source nodes' salience_base."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            summarization_daily_min_events=2,
            summarization_max_items_per_summary=10,
        )

        day = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        saliences = [0.3, 0.5, 0.7]
        for i, sal in enumerate(saliences):
            node = _make_node(
                content=f"Fact {i}",
                salience_base=sal,
                created_at=day + timedelta(hours=i),
            )
            await graph_store.create_node(node)

        await generate_daily_summaries(engine, config, 5000.0)

        summaries = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY,
            lifecycle_states=[LifecycleState.STABLE],
            limit=100,
        )
        expected_avg = sum(saliences) / len(saliences)
        assert abs(summaries[0].salience_base - expected_avg) < 0.01
