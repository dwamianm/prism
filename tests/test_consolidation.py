"""Tests for predictive forgetting / consolidation pipeline (issue #22).

Tests cover:
- Cluster detection with similar memories
- Small clusters (below min_size) are skipped
- Consolidation creates proper SUMMARY nodes
- DERIVED_FROM edges are created
- Forgetting archives old members but preserves recent/high-confidence
- Full pipeline end-to-end
- Budget enforcement
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prme.config import OrganizerConfig, PRMEConfig
from prme.organizer.consolidation import (
    cluster_similar_memories,
    consolidate_cluster,
    forget_consolidated,
    run_consolidation_pipeline,
)
from prme.organizer.models import ConsolidationResult
from prme.storage.engine import MemoryEngine
from prme.types import (
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_consolidation_") as d:
        yield d


@pytest.fixture
def base_config(tmp_dir):
    """Config for consolidation tests."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
    )


async def create_engine(config: PRMEConfig) -> MemoryEngine:
    """Create a MemoryEngine from config."""
    return await MemoryEngine.create(config)


async def store_fact(
    engine: MemoryEngine,
    content: str,
    user_id: str = "test-user",
    confidence: float = 0.5,
) -> str:
    """Store a FACT and return its event ID."""
    return await engine.store(
        content,
        user_id=user_id,
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Test cluster detection
# ---------------------------------------------------------------------------


class TestClusterDetection:
    """Tests for cluster_similar_memories()."""

    @pytest.mark.asyncio
    async def test_detects_clusters_of_similar_memories(self, base_config):
        """Semantically similar memories should be clustered together."""
        engine = await create_engine(base_config)
        try:
            for content in [
                "Python is widely used for machine learning",
                "Python is a popular language for ML projects",
                "Python is commonly used in machine learning applications",
                "Python is the top choice for ML development",
            ]:
                await store_fact(engine, content)

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )

            assert len(clusters) >= 1
            biggest = max(clusters, key=lambda c: len(c.member_ids))
            assert len(biggest.member_ids) >= 3
            assert biggest.avg_similarity > 0.0
            assert biggest.topic_summary != ""
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_small_clusters_below_min_size_are_skipped(self, base_config):
        """Clusters with fewer than min_cluster_size members are not returned."""
        engine = await create_engine(base_config)
        try:
            await store_fact(engine, "Rust is great for systems programming")
            await store_fact(engine, "Rust excels at systems-level programming")

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )
            assert len(clusters) == 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_dissimilar_memories_not_clustered(self, base_config):
        """Memories about unrelated topics should not form clusters."""
        engine = await create_engine(base_config)
        try:
            await store_fact(engine, "The Eiffel Tower is 330 meters tall")
            await store_fact(engine, "Photosynthesis converts sunlight to energy")
            await store_fact(engine, "TCP uses three-way handshake")

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.85,
            )
            assert len(clusters) == 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_empty_store_returns_no_clusters(self, base_config):
        """An empty store should return no clusters."""
        engine = await create_engine(base_config)
        try:
            clusters = await cluster_similar_memories(engine)
            assert clusters == []
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Test consolidation
# ---------------------------------------------------------------------------


class TestConsolidation:
    """Tests for consolidate_cluster()."""

    @pytest.mark.asyncio
    async def test_creates_summary_node(self, base_config):
        """Consolidation should create a SUMMARY node."""
        engine = await create_engine(base_config)
        try:
            for c in [
                "Python is widely used for machine learning",
                "Python is a popular language for ML projects",
                "Python is commonly used in machine learning applications",
            ]:
                await store_fact(engine, c)

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )
            assert len(clusters) >= 1

            summary_node = await consolidate_cluster(engine, clusters[0])
            assert summary_node.node_type == NodeType.SUMMARY
            assert "Consolidated" in summary_node.content
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_summary_has_inferred_epistemic_type(self, base_config):
        """Summary nodes should have INFERRED epistemic type."""
        engine = await create_engine(base_config)
        try:
            for c in [
                "JavaScript runs in web browsers",
                "JavaScript is the language of web browsers",
                "JavaScript executes in browser environments",
            ]:
                await store_fact(engine, c)

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )
            assert len(clusters) >= 1

            summary = await consolidate_cluster(engine, clusters[0])
            assert summary.epistemic_type == EpistemicType.INFERRED
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_derives_from_edges_created(self, base_config):
        """DERIVED_FROM edges should connect summary to each cluster member."""
        engine = await create_engine(base_config)
        try:
            for c in [
                "Docker containers provide isolation",
                "Docker uses containers for process isolation",
                "Docker containerization isolates applications",
            ]:
                await store_fact(engine, c)

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )
            assert len(clusters) >= 1

            cluster = clusters[0]
            summary = await consolidate_cluster(engine, cluster)

            edges = await engine._graph_store.get_edges(
                source_id=str(summary.id),
                edge_type=EdgeType.DERIVED_FROM,
            )
            assert len(edges) >= len(cluster.member_ids)
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_summary_has_evidence_refs(self, base_config):
        """Summary should have evidence_refs pointing to cluster members."""
        engine = await create_engine(base_config)
        try:
            await store_fact(engine, "Kubernetes orchestrates containers automatically", confidence=0.9)
            await store_fact(engine, "Kubernetes manages container orchestration", confidence=0.7)
            await store_fact(engine, "Kubernetes provides automated container management", confidence=0.5)

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )
            assert len(clusters) >= 1

            summary = await consolidate_cluster(engine, clusters[0])
            updated = await engine.get_node(str(summary.id))
            assert updated is not None
            assert len(updated.evidence_refs) >= 3
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Test forgetting
# ---------------------------------------------------------------------------


class TestForgetting:
    """Tests for forget_consolidated()."""

    @pytest.mark.asyncio
    async def test_archives_old_low_confidence_members(self, base_config):
        """Old, low-confidence members should be archived."""
        engine = await create_engine(base_config)
        try:
            for c in [
                "Redis is used as a cache layer",
                "Redis serves as an in-memory cache",
                "Redis is our primary caching solution",
            ]:
                await store_fact(engine, c, confidence=0.4)

            now = datetime.now(timezone.utc)
            old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S.%f+00")
            engine._conn.execute(
                "UPDATE nodes SET created_at = ?::TIMESTAMPTZ, "
                "last_reinforced_at = ?::TIMESTAMPTZ "
                "WHERE user_id = ?",
                [old_ts, old_ts, "test-user"],
            )

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )
            assert len(clusters) >= 1

            cluster = clusters[0]
            summary = await consolidate_cluster(engine, cluster)

            archived = await forget_consolidated(
                engine, cluster, str(summary.id),
                preserve_recent_days=7, min_confidence_preserve=0.8,
            )
            assert archived > 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_preserves_recent_members(self, base_config):
        """Recently created members should NOT be archived."""
        engine = await create_engine(base_config)
        try:
            for c in [
                "MongoDB stores documents in collections",
                "MongoDB is a document database with collections",
                "MongoDB uses collections to organize documents",
            ]:
                await store_fact(engine, c, confidence=0.4)

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )
            assert len(clusters) >= 1

            cluster = clusters[0]
            summary = await consolidate_cluster(engine, cluster)

            archived = await forget_consolidated(
                engine, cluster, str(summary.id),
                preserve_recent_days=7, min_confidence_preserve=0.8,
            )
            assert archived == 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_preserves_high_confidence_members(self, base_config):
        """High-confidence members should NOT be archived even if old."""
        engine = await create_engine(base_config)
        try:
            for c in [
                "GraphQL provides typed API queries",
                "GraphQL uses typed queries for APIs",
                "GraphQL enables typed API query operations",
            ]:
                await store_fact(engine, c, confidence=0.9)

            now = datetime.now(timezone.utc)
            old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S.%f+00")
            engine._conn.execute(
                "UPDATE nodes SET created_at = ?::TIMESTAMPTZ, "
                "last_reinforced_at = ?::TIMESTAMPTZ "
                "WHERE user_id = ?",
                [old_ts, old_ts, "test-user"],
            )

            clusters = await cluster_similar_memories(
                engine, min_cluster_size=3, similarity_threshold=0.70,
            )
            assert len(clusters) >= 1

            cluster = clusters[0]
            summary = await consolidate_cluster(engine, cluster)

            archived = await forget_consolidated(
                engine, cluster, str(summary.id),
                preserve_recent_days=7, min_confidence_preserve=0.8,
            )
            assert archived == 0
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Test full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Tests for run_consolidation_pipeline()."""

    @pytest.mark.asyncio
    async def test_end_to_end_pipeline(self, base_config):
        """Full pipeline should cluster, consolidate, and forget."""
        engine = await create_engine(base_config)
        try:
            for content in [
                "Python is widely used for machine learning",
                "Python is a popular language for ML projects",
                "Python is commonly used in machine learning applications",
                "Python is the top choice for ML development",
                "Python dominates the machine learning landscape",
            ]:
                await store_fact(engine, content, confidence=0.4)

            now = datetime.now(timezone.utc)
            old_ts = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S.%f+00")
            engine._conn.execute(
                "UPDATE nodes SET created_at = ?::TIMESTAMPTZ, "
                "last_reinforced_at = ?::TIMESTAMPTZ "
                "WHERE user_id = ?",
                [old_ts, old_ts, "test-user"],
            )

            config = OrganizerConfig(
                consolidation_min_cluster_size=3,
                consolidation_similarity_threshold=0.70,
                consolidation_preserve_recent_days=7,
                consolidation_min_confidence_preserve=0.8,
            )

            result = await run_consolidation_pipeline(engine, config, budget_ms=10000)

            assert result.clusters_found >= 1
            assert result.summaries_created >= 1
            assert result.nodes_consolidated >= 3
            assert result.nodes_archived >= 1
            assert result.duration_ms > 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_budget_enforcement(self, base_config):
        """Pipeline should respect time budget."""
        engine = await create_engine(base_config)
        try:
            for content in [
                "Terraform manages infrastructure as code",
                "Terraform provides infrastructure-as-code management",
                "Terraform is an IaC tool for infrastructure management",
            ]:
                await store_fact(engine, content)

            config = OrganizerConfig(
                consolidation_min_cluster_size=3,
                consolidation_similarity_threshold=0.70,
            )

            result = await run_consolidation_pipeline(engine, config, budget_ms=0)
            assert isinstance(result, ConsolidationResult)
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_pipeline_with_no_similar_memories(self, base_config):
        """Pipeline should handle no clusters gracefully."""
        engine = await create_engine(base_config)
        try:
            await store_fact(engine, "The sun is a star")
            await store_fact(engine, "TCP is a transport protocol")

            config = OrganizerConfig(
                consolidation_min_cluster_size=3,
                consolidation_similarity_threshold=0.90,
            )

            result = await run_consolidation_pipeline(engine, config, budget_ms=5000)

            assert result.clusters_found == 0
            assert result.summaries_created == 0
            assert result.nodes_archived == 0
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Test job integration
# ---------------------------------------------------------------------------


class TestJobIntegration:
    """Test that consolidate job is wired into the organizer."""

    @pytest.mark.asyncio
    async def test_consolidate_in_all_jobs(self):
        """'consolidate' should be listed in ALL_JOBS."""
        from prme.organizer.jobs import ALL_JOBS
        assert "consolidate" in ALL_JOBS

    @pytest.mark.asyncio
    async def test_consolidate_job_dispatches(self, base_config):
        """The consolidate job should run via run_job() dispatch."""
        from prme.organizer.jobs import run_job
        engine = await create_engine(base_config)
        try:
            config = OrganizerConfig()
            result = await run_job("consolidate", engine, config, budget_ms=5000)
            assert result.job == "consolidate"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_organize_runs_consolidate(self, base_config):
        """engine.organize(jobs=['consolidate']) should work."""
        engine = await create_engine(base_config)
        try:
            result = await engine.organize(jobs=["consolidate"], budget_ms=5000)
            assert "consolidate" in result.jobs_run
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Test config and result model
# ---------------------------------------------------------------------------


class TestConsolidationConfig:
    """Test consolidation config parameters."""

    def test_default_config_values(self):
        config = OrganizerConfig()
        assert config.consolidation_min_cluster_size == 3
        assert config.consolidation_similarity_threshold == pytest.approx(0.80)
        assert config.consolidation_preserve_recent_days == 7
        assert config.consolidation_min_confidence_preserve == pytest.approx(0.8)

    def test_config_on_prme_config(self):
        config = PRMEConfig()
        assert config.organizer.consolidation_min_cluster_size == 3


class TestConsolidationResultModel:
    """Test the ConsolidationResult Pydantic model."""

    def test_default_values(self):
        result = ConsolidationResult()
        assert result.clusters_found == 0
        assert result.nodes_consolidated == 0
        assert result.nodes_archived == 0
        assert result.summaries_created == 0

    def test_serialization(self):
        result = ConsolidationResult(
            clusters_found=2, nodes_consolidated=8,
            nodes_archived=5, summaries_created=2, duration_ms=123.45,
        )
        d = result.model_dump()
        assert d["clusters_found"] == 2
        assert d["summaries_created"] == 2
