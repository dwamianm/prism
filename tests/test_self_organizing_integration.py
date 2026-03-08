"""End-to-end integration tests for RFC-0015 Self-Organizing Memory.

Validates that virtual decay, opportunistic maintenance, explicit organize(),
and end_session() all work correctly through the full MemoryEngine stack with
real DuckDB backends. Each test creates a fresh temporary database.

Tests cover:
- Virtual decay through the full retrieval pipeline
- Idle period accuracy (decay reflects elapsed time after engine restart)
- Opportunistic maintenance (auto-promotion, threshold archival, enable/disable)
- Explicit organize() with job selection and result reporting
- end_session() convenience method
- Deterministic rebuild validation (same timestamp = same scores)
- Decay profile assignment from DEFAULT_DECAY_PROFILE_MAPPING
"""

from __future__ import annotations

import asyncio
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from prme import (
    DECAY_LAMBDAS,
    DEFAULT_DECAY_PROFILE_MAPPING,
    DecayProfile,
    LifecycleState,
    MemoryEngine,
    NodeType,
    PRMEConfig,
    Scope,
)
from prme.config import OrganizerConfig
from prme.organizer.decay import apply_virtual_decay
from prme.organizer.models import JobResult, OrganizeResult
from prme.types import EpistemicType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_integ_") as d:
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
    """Create a MemoryEngine from config."""
    return await MemoryEngine.create(config)


def _age_node(engine: MemoryEngine, node_id: str, days: float) -> None:
    """Directly update DuckDB to set a node's timestamps to `days` ago."""
    old_ts = datetime.now(timezone.utc) - timedelta(days=days)
    engine._conn.execute(
        "UPDATE nodes SET last_reinforced_at = ?, created_at = ? WHERE id = ?::UUID",
        [old_ts, old_ts, node_id],
    )


def _pin_node(engine: MemoryEngine, node_id: str) -> None:
    """Directly set pinned=True in DuckDB for a node."""
    engine._conn.execute(
        "UPDATE nodes SET pinned = TRUE WHERE id = ?::UUID",
        [node_id],
    )


def _set_salience_base(engine: MemoryEngine, node_id: str, value: float) -> None:
    """Directly set salience_base in DuckDB for a node."""
    engine._conn.execute(
        "UPDATE nodes SET salience_base = ? WHERE id = ?::UUID",
        [value, node_id],
    )


def _add_evidence_ref(engine: MemoryEngine, node_id: str) -> None:
    """Add a fake evidence ref to a node via DuckDB update."""
    import json
    row = engine._conn.execute(
        "SELECT evidence_refs FROM nodes WHERE id = ?::UUID",
        [node_id],
    ).fetchone()
    if row and row[0]:
        try:
            refs = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except (json.JSONDecodeError, TypeError):
            refs = []
    else:
        refs = []
    refs.append(str(uuid4()))
    engine._conn.execute(
        "UPDATE nodes SET evidence_refs = ?::JSON WHERE id = ?::UUID",
        [json.dumps(refs), node_id],
    )


async def _get_node_ids(engine: MemoryEngine, user_id: str = "test-user") -> list[str]:
    """Get all node IDs for a user."""
    nodes = await engine.query_nodes(user_id=user_id)
    return [str(n.id) for n in nodes]


# ---------------------------------------------------------------------------
# 1. Virtual Decay Through Full Pipeline
# ---------------------------------------------------------------------------


class TestVirtualDecayPipeline:
    """Verify that virtual decay is applied during real retrieve() calls."""

    @pytest.mark.asyncio
    async def test_aged_node_has_lower_score_than_base(self, config):
        """Store a node, age it 60 days, retrieve, verify decayed scores."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Python is a great programming language",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            node_ids = await _get_node_ids(engine)
            assert len(node_ids) >= 1
            node_id = node_ids[0]

            # Age the node 60 days
            _age_node(engine, node_id, days=60)

            # Also update updated_at so recency factor is consistent
            old_ts = datetime.now(timezone.utc) - timedelta(days=60)
            engine._conn.execute(
                "UPDATE nodes SET updated_at = ? WHERE id = ?::UUID",
                [old_ts, node_id],
            )

            # Retrieve and check that the scored results use decayed values
            response = await engine.retrieve(
                "programming language",
                user_id="test-user",
            )

            # The retrieval should return results
            assert len(response.results) >= 1

            # Score traces should show decayed salience below the base of 0.5
            for trace in response.score_traces:
                # MEDIUM decay at 60 days: exp(-0.02*60) = exp(-1.2) ~ 0.30
                # effective = 0.5 * 0.30 = 0.15
                assert trace.salience < 0.5, (
                    f"Expected decayed salience < 0.5, got {trace.salience}"
                )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_fresh_node_ranks_higher_than_aged(self, config):
        """Two identical nodes: fresh one should rank higher than 90-day-old one."""
        engine = await create_engine(config)
        try:
            # Store two nodes with identical content
            await engine.store(
                "Machine learning requires training data",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            await engine.store(
                "Machine learning requires training data",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            assert len(nodes) >= 2

            # Age the first node 90 days; leave the second fresh
            old_node_id = str(nodes[0].id)
            old_ts = datetime.now(timezone.utc) - timedelta(days=90)
            engine._conn.execute(
                "UPDATE nodes SET last_reinforced_at = ?, created_at = ?, updated_at = ? WHERE id = ?::UUID",
                [old_ts, old_ts, old_ts, old_node_id],
            )

            response = await engine.retrieve(
                "machine learning training",
                user_id="test-user",
            )

            # Should have at least 2 results
            assert len(response.results) >= 2

            # The first result (highest score) should be the fresh node
            top_result = response.results[0]
            assert str(top_result.node.id) != old_node_id, (
                "Aged node should not rank first"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_pinned_node_does_not_decay(self, config):
        """A pinned node aged 90 days should not show decayed scores."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Critical fact that must persist",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)
            base_salience = nodes[0].salience_base

            # Pin and age the node
            _pin_node(engine, node_id)
            _age_node(engine, node_id, days=90)
            old_ts = datetime.now(timezone.utc) - timedelta(days=90)
            engine._conn.execute(
                "UPDATE nodes SET updated_at = ? WHERE id = ?::UUID",
                [old_ts, node_id],
            )

            response = await engine.retrieve(
                "critical persist fact",
                user_id="test-user",
            )

            assert len(response.results) >= 1

            # The score trace salience should equal the base (no decay)
            for trace in response.score_traces:
                assert trace.salience == pytest.approx(base_salience, abs=0.01), (
                    f"Pinned node should not decay: expected {base_salience}, got {trace.salience}"
                )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_permanent_profile_does_not_decay(self, config):
        """A node with PERMANENT decay profile should not decay even after 500 days."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Immutable core identity fact",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)
            base_salience = nodes[0].salience_base

            # Set to PERMANENT profile and age 500 days
            engine._conn.execute(
                "UPDATE nodes SET decay_profile = 'permanent' WHERE id = ?::UUID",
                [node_id],
            )
            _age_node(engine, node_id, days=500)
            old_ts = datetime.now(timezone.utc) - timedelta(days=500)
            engine._conn.execute(
                "UPDATE nodes SET updated_at = ? WHERE id = ?::UUID",
                [old_ts, node_id],
            )

            response = await engine.retrieve(
                "immutable core identity",
                user_id="test-user",
            )

            assert len(response.results) >= 1
            for trace in response.score_traces:
                assert trace.salience == pytest.approx(base_salience, abs=0.01), (
                    f"PERMANENT profile should not decay"
                )
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 2. Idle Period Accuracy
# ---------------------------------------------------------------------------


class TestIdlePeriodAccuracy:
    """Verify that decay correctly reflects time elapsed after engine restart."""

    @pytest.mark.asyncio
    async def test_reopened_engine_reflects_elapsed_decay(self, config):
        """Store, close, reopen, and verify scores reflect decay."""
        # Phase 1: create engine and store a node
        engine = await create_engine(config)
        try:
            await engine.store(
                "Decay should apply after idle",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # Age the node before closing (simulate creation 60 days ago)
            _age_node(engine, node_id, days=60)
            old_ts = datetime.now(timezone.utc) - timedelta(days=60)
            engine._conn.execute(
                "UPDATE nodes SET updated_at = ? WHERE id = ?::UUID",
                [old_ts, node_id],
            )
        finally:
            await engine.close()

        # Phase 2: reopen engine and retrieve without any organize()
        engine2 = await create_engine(config)
        try:
            response = await engine2.retrieve(
                "decay idle apply",
                user_id="test-user",
            )

            assert len(response.results) >= 1

            # Verify decay is applied (MEDIUM profile, 60 days)
            lam = DECAY_LAMBDAS[DecayProfile.MEDIUM]
            expected_salience = 0.5 * math.exp(-lam * 60)

            for trace in response.score_traces:
                assert trace.salience == pytest.approx(expected_salience, abs=0.05), (
                    f"Expected ~{expected_salience:.3f} after 60-day idle, got {trace.salience:.3f}"
                )
        finally:
            await engine2.close()

    @pytest.mark.asyncio
    async def test_fresh_node_no_decay_after_reopen(self, config):
        """A node created just before close should have no significant decay after reopen."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Fresh node no decay expected",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
        finally:
            await engine.close()

        engine2 = await create_engine(config)
        try:
            response = await engine2.retrieve(
                "fresh node decay",
                user_id="test-user",
            )

            assert len(response.results) >= 1

            # Salience should be very close to base (only seconds elapsed)
            for trace in response.score_traces:
                assert trace.salience >= 0.45, (
                    f"Fresh node should have near-base salience, got {trace.salience}"
                )
        finally:
            await engine2.close()


# ---------------------------------------------------------------------------
# 3. Opportunistic Maintenance
# ---------------------------------------------------------------------------


class TestOpportunisticMaintenance:
    """Verify that opportunistic maintenance runs correctly during retrieve/ingest."""

    @pytest.mark.asyncio
    async def test_auto_promotion_on_retrieve(self, tmp_dir):
        """A tentative node old enough with evidence should be promoted on retrieve()."""
        Path(tmp_dir, "lexical_index").mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(Path(tmp_dir) / "lexical_index"),
            organizer=OrganizerConfig(
                opportunistic_cooldown=0,
                promotion_age_days=1.0,
                promotion_evidence_count=1,
            ),
        )
        engine = await create_engine(config)
        try:
            # Store a node (store() creates one evidence ref automatically)
            await engine.store(
                "Fact eligible for promotion",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # Age the node beyond promotion_age_days (10 days)
            _age_node(engine, node_id, days=10)

            # Verify it's still TENTATIVE before retrieve
            node_before = await engine.get_node(node_id)
            assert node_before.lifecycle_state == LifecycleState.TENTATIVE

            # Trigger retrieve to fire opportunistic maintenance
            await engine.retrieve("promotion test", user_id="test-user")

            # Check the node was promoted
            node_after = await engine.get_node(node_id, include_superseded=True)
            assert node_after.lifecycle_state == LifecycleState.STABLE, (
                f"Expected STABLE after auto-promotion, got {node_after.lifecycle_state}"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_threshold_archival_on_retrieve(self, tmp_dir):
        """A node with very low salience should be archived by opportunistic maintenance."""
        Path(tmp_dir, "lexical_index").mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(Path(tmp_dir) / "lexical_index"),
            organizer=OrganizerConfig(
                opportunistic_cooldown=0,
                force_archive_salience_threshold=0.05,
            ),
        )
        engine = await create_engine(config)
        try:
            await engine.store(
                "Low salience node to be archived",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # Set very low salience_base and age far in the past
            _set_salience_base(engine, node_id, 0.01)
            _age_node(engine, node_id, days=500)

            # Verify it's active before retrieve
            node_before = await engine.get_node(node_id)
            assert node_before.lifecycle_state == LifecycleState.TENTATIVE

            # Trigger retrieve to fire opportunistic maintenance
            await engine.retrieve("archival test", user_id="test-user")

            # Check the node was archived
            node_after = await engine.get_node(node_id, include_superseded=True)
            assert node_after.lifecycle_state == LifecycleState.ARCHIVED, (
                f"Expected ARCHIVED, got {node_after.lifecycle_state}"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_disabled_maintenance_no_promotion(self, tmp_dir):
        """When opportunistic_enabled=False, no auto-promotion occurs."""
        Path(tmp_dir, "lexical_index").mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(Path(tmp_dir) / "lexical_index"),
            organizer=OrganizerConfig(
                opportunistic_enabled=False,
                promotion_age_days=1.0,
                promotion_evidence_count=1,
            ),
        )
        engine = await create_engine(config)
        try:
            await engine.store(
                "Node that should stay tentative",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # Age well beyond promotion threshold
            _age_node(engine, node_id, days=30)

            # Trigger retrieve multiple times
            await engine.retrieve("should not promote", user_id="test-user")
            await engine.retrieve("still not promoted", user_id="test-user")

            # Node should remain TENTATIVE
            node_after = await engine.get_node(node_id)
            assert node_after.lifecycle_state == LifecycleState.TENTATIVE, (
                f"Expected TENTATIVE with disabled maintenance, got {node_after.lifecycle_state}"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_auto_promotion_on_ingest(self, tmp_dir):
        """Opportunistic maintenance should also run on ingest()."""
        Path(tmp_dir, "lexical_index").mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(Path(tmp_dir) / "lexical_index"),
            organizer=OrganizerConfig(
                opportunistic_cooldown=0,
                promotion_age_days=1.0,
                promotion_evidence_count=1,
            ),
        )
        engine = await create_engine(config)
        try:
            # Store initial node
            await engine.store(
                "Node for ingest-triggered promotion",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)
            _age_node(engine, node_id, days=10)

            # Reset the maintenance runner cooldown so it fires again
            engine._maintenance_runner._last_maintained_at = 0.0

            # Ingest triggers maintenance
            await engine.ingest(
                "Another message to trigger maintenance",
                user_id="test-user",
            )

            # Check promotion happened
            node_after = await engine.get_node(node_id, include_superseded=True)
            assert node_after.lifecycle_state == LifecycleState.STABLE, (
                f"Expected STABLE after ingest-triggered maintenance, got {node_after.lifecycle_state}"
            )
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 4. Explicit organize()
# ---------------------------------------------------------------------------


class TestExplicitOrganize:
    """Verify the MemoryEngine.organize() method."""

    @pytest.mark.asyncio
    async def test_organize_promotes_eligible_nodes(self, tmp_dir):
        """organize(jobs=['promote']) promotes nodes meeting criteria."""
        Path(tmp_dir, "lexical_index").mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(Path(tmp_dir) / "lexical_index"),
            organizer=OrganizerConfig(promotion_evidence_count=1),
        )
        engine = await create_engine(config)
        try:
            # Store 5 tentative nodes (store() creates 1 evidence ref each)
            for i in range(5):
                await engine.store(
                    f"Promotable fact number {i}",
                    user_id="test-user",
                    node_type=NodeType.FACT,
                    scope=Scope.PERSONAL,
                )

            nodes = await engine.query_nodes(user_id="test-user")
            assert len(nodes) >= 5

            # Age all nodes beyond promotion threshold
            for node in nodes:
                _age_node(engine, str(node.id), days=30)

            # Run organize
            result = await engine.organize(
                user_id="test-user",
                jobs=["promote"],
            )

            assert isinstance(result, OrganizeResult)
            assert "promote" in result.jobs_run
            assert "promote" in result.per_job
            assert result.per_job["promote"].nodes_modified >= 1

            # Verify nodes are now STABLE
            # query_nodes defaults to active states which includes STABLE
            stable_nodes = await engine.query_nodes(
                user_id="test-user",
                lifecycle_states=[LifecycleState.STABLE],
            )
            assert len(stable_nodes) >= 1
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_organize_archives_low_salience_nodes(self, config):
        """organize(jobs=['archive']) archives nodes below threshold."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Node with very low salience to archive",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # Set very low salience and age
            _set_salience_base(engine, node_id, 0.01)
            _age_node(engine, node_id, days=500)

            result = await engine.organize(
                user_id="test-user",
                jobs=["archive"],
            )

            assert "archive" in result.jobs_run
            assert result.per_job["archive"].nodes_modified >= 1

            # Verify node is archived
            node_after = await engine.get_node(node_id, include_superseded=True)
            assert node_after.lifecycle_state == LifecycleState.ARCHIVED
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_organize_decay_sweep_with_archival(self, config):
        """organize(jobs=['decay_sweep']) handles threshold transitions."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Node for decay sweep archival",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # Set salience below force_archive threshold (default 0.05)
            _set_salience_base(engine, node_id, 0.01)
            _age_node(engine, node_id, days=500)

            result = await engine.organize(
                user_id="test-user",
                jobs=["decay_sweep"],
            )

            assert "decay_sweep" in result.jobs_run
            assert result.per_job["decay_sweep"].nodes_modified >= 1

            node_after = await engine.get_node(node_id, include_superseded=True)
            assert node_after.lifecycle_state == LifecycleState.ARCHIVED
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_organize_deduplicate_runs_without_error(self, config):
        """Deduplicate job should run and return real results."""
        engine = await create_engine(config)
        try:
            result = await engine.organize(
                user_id="test-user",
                jobs=["deduplicate"],
            )

            assert "deduplicate" in result.jobs_run
            assert "deduplicate" in result.per_job
            # Now a real implementation, not a stub
            assert "duplicates_found" in result.per_job["deduplicate"].details
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_organize_all_jobs_run(self, config):
        """organize() with no job filter runs all 9 jobs."""
        from prme.organizer.jobs import ALL_JOBS

        engine = await create_engine(config)
        try:
            result = await engine.organize(user_id="test-user")

            assert set(result.jobs_run) == set(ALL_JOBS)
            assert result.jobs_skipped == []
            assert result.duration_ms >= 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_organize_result_structure(self, config):
        """OrganizeResult has correct type and structure."""
        engine = await create_engine(config)
        try:
            result = await engine.organize(
                user_id="test-user",
                jobs=["promote", "archive"],
            )

            assert isinstance(result, OrganizeResult)
            assert isinstance(result.jobs_run, list)
            assert isinstance(result.per_job, dict)
            assert result.duration_ms >= 0
            assert result.budget_remaining_ms >= 0

            for job_name in result.jobs_run:
                jr = result.per_job[job_name]
                assert isinstance(jr, JobResult)
                assert jr.job == job_name
                assert jr.duration_ms >= 0
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 5. end_session()
# ---------------------------------------------------------------------------


class TestEndSession:
    """Verify the MemoryEngine.end_session() method."""

    @pytest.mark.asyncio
    async def test_end_session_runs_promote_and_feedback_apply(self, config):
        """end_session() runs exactly promote and feedback_apply."""
        engine = await create_engine(config)
        try:
            result = await engine.end_session(user_id="test-user")

            assert isinstance(result, OrganizeResult)
            assert set(result.jobs_run) == {"promote", "feedback_apply"}
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_end_session_promotes_eligible(self, tmp_dir):
        """end_session() actually promotes eligible tentative nodes."""
        Path(tmp_dir, "lexical_index").mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(Path(tmp_dir) / "lexical_index"),
            organizer=OrganizerConfig(promotion_evidence_count=1),
        )
        engine = await create_engine(config)
        try:
            # Store nodes and age them
            for i in range(3):
                await engine.store(
                    f"Session fact {i}",
                    user_id="test-user",
                    node_type=NodeType.FACT,
                    scope=Scope.PERSONAL,
                )

            nodes = await engine.query_nodes(user_id="test-user")
            for node in nodes:
                _age_node(engine, str(node.id), days=30)

            result = await engine.end_session(user_id="test-user")

            assert "promote" in result.jobs_run
            assert result.per_job["promote"].nodes_modified >= 1

            # Verify at least one node was promoted
            stable_nodes = await engine.query_nodes(
                user_id="test-user",
                lifecycle_states=[LifecycleState.STABLE],
            )
            assert len(stable_nodes) >= 1
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 6. Deterministic Rebuild Validation
# ---------------------------------------------------------------------------


class TestDeterministicRebuild:
    """Verify that the same timestamp produces the same scores across engine restarts."""

    @pytest.mark.asyncio
    async def test_same_timestamp_same_scores_after_restart(self, config):
        """Store, close, reopen, retrieve -- same node order and near-identical scores."""
        # Phase 1: create engine, store nodes, and age them
        engine = await create_engine(config)
        try:
            await engine.store(
                "Deterministic rebuild test content alpha",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            await engine.store(
                "Deterministic rebuild test content beta",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            # Age them to make decay noticeable
            for node in nodes:
                _age_node(engine, str(node.id), days=30)
                old_ts = datetime.now(timezone.utc) - timedelta(days=30)
                engine._conn.execute(
                    "UPDATE nodes SET updated_at = ? WHERE id = ?::UUID",
                    [old_ts, str(node.id)],
                )
        finally:
            await engine.close()

        # Phase 2: reopen and retrieve twice in quick succession
        # The two calls should produce near-identical scores since they
        # happen within milliseconds of each other.
        engine2 = await create_engine(config)
        try:
            response1 = await engine2.retrieve(
                "deterministic rebuild",
                user_id="test-user",
            )
            response2 = await engine2.retrieve(
                "deterministic rebuild",
                user_id="test-user",
            )

            scores1 = sorted(
                [(str(r.node.id), r.composite_score) for r in response1.results],
                key=lambda x: x[0],
            )
            scores2 = sorted(
                [(str(r.node.id), r.composite_score) for r in response2.results],
                key=lambda x: x[0],
            )

            # Both retrievals should return the same node IDs
            ids1 = [s[0] for s in scores1]
            ids2 = [s[0] for s in scores2]
            assert ids1 == ids2, "Same nodes should be returned"

            # Scores should be nearly identical (sub-millisecond time difference)
            for (id1, score1), (id2, score2) in zip(scores1, scores2):
                assert score1 == pytest.approx(score2, abs=0.01), (
                    f"Scores should be nearly identical for node {id1}: "
                    f"{score1} vs {score2}"
                )
        finally:
            await engine2.close()

    @pytest.mark.asyncio
    async def test_different_profiles_different_decay_rates(self, config):
        """Nodes with different decay profiles should have different effective salience after aging."""
        engine = await create_engine(config)
        try:
            # Store two nodes
            await engine.store(
                "Slow decay profile node",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            await engine.store(
                "Rapid decay profile node",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            assert len(nodes) >= 2

            slow_id = str(nodes[0].id)
            rapid_id = str(nodes[1].id)

            # Set different profiles
            engine._conn.execute(
                "UPDATE nodes SET decay_profile = 'slow' WHERE id = ?::UUID",
                [slow_id],
            )
            engine._conn.execute(
                "UPDATE nodes SET decay_profile = 'rapid' WHERE id = ?::UUID",
                [rapid_id],
            )

            # Age both 30 days
            for nid in [slow_id, rapid_id]:
                _age_node(engine, nid, days=30)
                old_ts = datetime.now(timezone.utc) - timedelta(days=30)
                engine._conn.execute(
                    "UPDATE nodes SET updated_at = ? WHERE id = ?::UUID",
                    [old_ts, nid],
                )

            response = await engine.retrieve(
                "decay profile node",
                user_id="test-user",
            )

            assert len(response.results) >= 2

            # Map results by node ID
            results_by_id = {str(r.node.id): r for r in response.results}
            traces_by_id = {}
            for r, t in zip(response.results, response.score_traces):
                traces_by_id[str(r.node.id)] = t

            slow_salience = traces_by_id[slow_id].salience
            rapid_salience = traces_by_id[rapid_id].salience

            # SLOW (lambda=0.005, 30 days): exp(-0.15) ~ 0.86 * 0.5 = 0.43
            # RAPID (lambda=0.200, 30 days): exp(-6.0) ~ 0.0025 * 0.5 = 0.0012
            assert slow_salience > rapid_salience, (
                f"SLOW ({slow_salience}) should have higher salience than RAPID ({rapid_salience})"
            )
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 7. Decay Profile Assignment
# ---------------------------------------------------------------------------


class TestDecayProfileAssignment:
    """Verify default decay profile assignment based on epistemic type."""

    @pytest.mark.asyncio
    async def test_default_decay_profile_is_medium(self, config):
        """Nodes stored without explicit epistemic_type get MEDIUM decay profile."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Default profile node",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            assert len(nodes) >= 1
            # Default epistemic_type for FACT is ASSERTED, default decay_profile is MEDIUM
            assert nodes[0].decay_profile == DecayProfile.MEDIUM
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_decay_profile_mapping_completeness(self):
        """Verify DEFAULT_DECAY_PROFILE_MAPPING covers all epistemic types."""
        for et in EpistemicType:
            assert et in DEFAULT_DECAY_PROFILE_MAPPING, (
                f"Missing mapping for EpistemicType.{et.name}"
            )

    @pytest.mark.asyncio
    async def test_stored_node_decay_profile_round_trip(self, config):
        """Decay profile persists correctly through store/retrieve cycle."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Round trip decay profile test",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # Manually set a specific profile
            engine._conn.execute(
                "UPDATE nodes SET decay_profile = 'slow' WHERE id = ?::UUID",
                [node_id],
            )

            # Read back
            node_after = await engine.get_node(node_id)
            assert node_after.decay_profile == DecayProfile.SLOW
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_all_profiles_round_trip(self, config):
        """All DecayProfile values persist correctly."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Profile round trip test",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            for profile in DecayProfile:
                engine._conn.execute(
                    "UPDATE nodes SET decay_profile = ? WHERE id = ?::UUID",
                    [profile.value, node_id],
                )
                node = await engine.get_node(node_id)
                assert node.decay_profile == profile, (
                    f"Expected {profile}, got {node.decay_profile}"
                )
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 8. Combined Scenarios
# ---------------------------------------------------------------------------


class TestCombinedScenarios:
    """Multi-step scenarios testing the interaction of multiple RFC-0015 features."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_store_decay_promote_archive(self, tmp_dir):
        """Full lifecycle: store -> age -> promote -> further age -> archive."""
        Path(tmp_dir, "lexical_index").mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(Path(tmp_dir) / "lexical_index"),
            organizer=OrganizerConfig(promotion_evidence_count=1),
        )
        engine = await create_engine(config)
        try:
            # 1. Store a node
            await engine.store(
                "Full lifecycle test node",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # 2. Verify initial state
            node = await engine.get_node(node_id)
            assert node.lifecycle_state == LifecycleState.TENTATIVE

            # 3. Age and promote
            _age_node(engine, node_id, days=30)
            result = await engine.organize(
                user_id="test-user",
                jobs=["promote"],
            )
            assert result.per_job["promote"].nodes_modified >= 1

            node = await engine.get_node(node_id, include_superseded=True)
            assert node.lifecycle_state == LifecycleState.STABLE

            # 4. Set very low salience and age further
            _set_salience_base(engine, node_id, 0.01)
            _age_node(engine, node_id, days=500)

            # 5. Run archive
            result = await engine.organize(
                user_id="test-user",
                jobs=["archive"],
            )
            assert result.per_job["archive"].nodes_modified >= 1

            node = await engine.get_node(node_id, include_superseded=True)
            assert node.lifecycle_state == LifecycleState.ARCHIVED
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_pinned_node_survives_organize_archive(self, config):
        """A pinned node should not be archived by organize()."""
        engine = await create_engine(config)
        try:
            await engine.store(
                "Pinned node should survive",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)

            # Set very low salience, age, and pin
            _set_salience_base(engine, node_id, 0.01)
            _age_node(engine, node_id, days=500)
            _pin_node(engine, node_id)

            # Run both archive and decay_sweep
            result = await engine.organize(
                user_id="test-user",
                jobs=["archive", "decay_sweep"],
            )

            # Node should still be active (TENTATIVE), not archived
            node = await engine.get_node(node_id)
            assert node.lifecycle_state == LifecycleState.TENTATIVE, (
                f"Pinned node should survive archival, got {node.lifecycle_state}"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_multiple_organize_passes_idempotent(self, tmp_dir):
        """Running organize() twice should be idempotent for already-transitioned nodes."""
        Path(tmp_dir, "lexical_index").mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(Path(tmp_dir) / "lexical_index"),
            organizer=OrganizerConfig(promotion_evidence_count=1),
        )
        engine = await create_engine(config)
        try:
            await engine.store(
                "Node for idempotent organize test",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            nodes = await engine.query_nodes(user_id="test-user")
            node_id = str(nodes[0].id)
            _age_node(engine, node_id, days=30)

            # First organize: should promote
            result1 = await engine.organize(
                user_id="test-user",
                jobs=["promote"],
            )
            assert result1.per_job["promote"].nodes_modified >= 1

            # Second organize: nothing to promote
            result2 = await engine.organize(
                user_id="test-user",
                jobs=["promote"],
            )
            # No new promotions (already stable)
            assert result2.per_job["promote"].nodes_modified == 0

            # Node is still STABLE
            node = await engine.get_node(node_id, include_superseded=True)
            assert node.lifecycle_state == LifecycleState.STABLE
        finally:
            await engine.close()
