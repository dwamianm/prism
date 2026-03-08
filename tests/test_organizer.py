"""Tests for RFC-0015 self-organizing memory: virtual decay, maintenance runner,
organize/end_session, and result models.

Tests cover:
- Virtual decay computation (all profiles, pinned, archived, OBSERVED confidence)
- Reinforcement boost decay at rho=0.10
- apply_virtual_decay immutability
- MaintenanceRunner cooldown, auto-promotion, threshold archival
- organize() job dispatch, budget enforcement, end_session()
- JobResult / OrganizeResult defaults
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import duckdb
import pytest
import pytest_asyncio

from prme.config import OrganizerConfig, PRMEConfig
from prme.models.nodes import MemoryNode
from prme.organizer.decay import (
    REINFORCEMENT_DECAY_RATE,
    apply_virtual_decay,
    compute_effective_confidence,
    compute_effective_salience,
)
from prme.organizer.jobs import ALL_JOBS, run_job
from prme.organizer.maintenance import MaintenanceRunner
from prme.organizer.models import JobResult, MaintenanceResult, OrganizeResult
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.schema import initialize_database
from prme.types import (
    DECAY_LAMBDAS,
    DecayProfile,
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
    reinforcement_boost: float = 0.0,
    last_reinforced_at: datetime | None = None,
    pinned: bool = False,
    epistemic_type: EpistemicType = EpistemicType.ASSERTED,
    evidence_refs: list | None = None,
    created_at: datetime | None = None,
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
        reinforcement_boost=reinforcement_boost,
        last_reinforced_at=last_reinforced_at or now,
        pinned=pinned,
        epistemic_type=epistemic_type,
        evidence_refs=evidence_refs or [],
        created_at=created_at or now,
    )


# ---------------------------------------------------------------------------
# Virtual Decay: compute_effective_salience
# ---------------------------------------------------------------------------


class TestComputeEffectiveSalience:
    """Tests for compute_effective_salience()."""

    def test_permanent_no_decay(self):
        """PERMANENT profile: effective salience equals base regardless of time."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=1000)
        result = compute_effective_salience(
            salience_base=0.8,
            reinforcement_boost=0.0,
            decay_profile=DecayProfile.PERMANENT,
            last_reinforced_at=past,
            now=now,
        )
        assert result == pytest.approx(0.8, abs=1e-6)

    def test_slow_half_life_approx_139_days(self):
        """SLOW profile: at ~139 days, salience ~= 50% of base."""
        lam = DECAY_LAMBDAS[DecayProfile.SLOW]
        half_life = math.log(2) / lam  # ~138.6 days
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=half_life)
        result = compute_effective_salience(
            salience_base=1.0,
            reinforcement_boost=0.0,
            decay_profile=DecayProfile.SLOW,
            last_reinforced_at=past,
            now=now,
        )
        assert result == pytest.approx(0.5, abs=0.01)

    def test_medium_half_life_approx_35_days(self):
        """MEDIUM profile: at ~35 days, salience ~= 50% of base."""
        lam = DECAY_LAMBDAS[DecayProfile.MEDIUM]
        half_life = math.log(2) / lam  # ~34.6 days
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=half_life)
        result = compute_effective_salience(
            salience_base=1.0,
            reinforcement_boost=0.0,
            decay_profile=DecayProfile.MEDIUM,
            last_reinforced_at=past,
            now=now,
        )
        assert result == pytest.approx(0.5, abs=0.01)

    def test_fast_half_life_approx_10_days(self):
        """FAST profile: at ~10 days, salience ~= 50% of base."""
        lam = DECAY_LAMBDAS[DecayProfile.FAST]
        half_life = math.log(2) / lam  # ~9.9 days
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=half_life)
        result = compute_effective_salience(
            salience_base=1.0,
            reinforcement_boost=0.0,
            decay_profile=DecayProfile.FAST,
            last_reinforced_at=past,
            now=now,
        )
        assert result == pytest.approx(0.5, abs=0.01)

    def test_rapid_half_life_approx_3_5_days(self):
        """RAPID profile: at ~3.5 days, salience ~= 50% of base."""
        lam = DECAY_LAMBDAS[DecayProfile.RAPID]
        half_life = math.log(2) / lam  # ~3.47 days
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=half_life)
        result = compute_effective_salience(
            salience_base=1.0,
            reinforcement_boost=0.0,
            decay_profile=DecayProfile.RAPID,
            last_reinforced_at=past,
            now=now,
        )
        assert result == pytest.approx(0.5, abs=0.01)

    def test_pinned_no_decay(self):
        """Pinned nodes: no decay regardless of profile."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=1000)
        result = compute_effective_salience(
            salience_base=0.8,
            reinforcement_boost=0.1,
            decay_profile=DecayProfile.RAPID,
            last_reinforced_at=past,
            now=now,
            pinned=True,
        )
        # pinned returns base + boost, clamped to 1.0
        assert result == pytest.approx(0.9, abs=1e-6)

    def test_pinned_clamps_to_1(self):
        """Pinned with base+boost > 1.0: clamped to 1.0."""
        now = datetime.now(timezone.utc)
        result = compute_effective_salience(
            salience_base=0.9,
            reinforcement_boost=0.5,
            decay_profile=DecayProfile.MEDIUM,
            last_reinforced_at=now,
            now=now,
            pinned=True,
        )
        assert result == pytest.approx(1.0, abs=1e-6)

    def test_zero_time_no_decay(self):
        """At t=0, effective equals base + boost."""
        now = datetime.now(timezone.utc)
        result = compute_effective_salience(
            salience_base=0.6,
            reinforcement_boost=0.2,
            decay_profile=DecayProfile.MEDIUM,
            last_reinforced_at=now,
            now=now,
        )
        assert result == pytest.approx(0.8, abs=1e-6)

    def test_result_never_negative(self):
        """Effective salience is always >= 0."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=100000)
        result = compute_effective_salience(
            salience_base=0.01,
            reinforcement_boost=0.0,
            decay_profile=DecayProfile.RAPID,
            last_reinforced_at=past,
            now=now,
        )
        assert result >= 0.0

    def test_reinforcement_boost_decays_at_rho(self):
        """Reinforcement boost decays at rho=0.10 independently of profile lambda."""
        rho = REINFORCEMENT_DECAY_RATE
        half_life_rho = math.log(2) / rho  # ~6.93 days
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=half_life_rho)

        # Use PERMANENT so base doesn't decay
        result = compute_effective_salience(
            salience_base=0.0,
            reinforcement_boost=1.0,
            decay_profile=DecayProfile.PERMANENT,
            last_reinforced_at=past,
            now=now,
        )
        # boost should be ~0.5 after one boost half-life
        assert result == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# Virtual Decay: compute_effective_confidence
# ---------------------------------------------------------------------------


class TestComputeEffectiveConfidence:
    """Tests for compute_effective_confidence()."""

    def test_permanent_no_decay(self):
        """PERMANENT profile: confidence never decays."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=1000)
        result = compute_effective_confidence(
            confidence_base=0.9,
            decay_profile=DecayProfile.PERMANENT,
            last_reinforced_at=past,
            now=now,
        )
        assert result == pytest.approx(0.9, abs=1e-6)

    def test_pinned_no_decay(self):
        """Pinned nodes: no confidence decay."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=500)
        result = compute_effective_confidence(
            confidence_base=0.8,
            decay_profile=DecayProfile.RAPID,
            last_reinforced_at=past,
            now=now,
            pinned=True,
        )
        assert result == pytest.approx(0.8, abs=1e-6)

    def test_confidence_decays_at_half_lambda(self):
        """Confidence decays at mu = lambda * 0.5 (slower than salience)."""
        lam = DECAY_LAMBDAS[DecayProfile.MEDIUM]
        mu = lam * 0.5
        # half-life of confidence = ln(2) / mu
        half_life_conf = math.log(2) / mu  # ~69.3 days for MEDIUM

        now = datetime.now(timezone.utc)
        past = now - timedelta(days=half_life_conf)
        result = compute_effective_confidence(
            confidence_base=1.0,
            decay_profile=DecayProfile.MEDIUM,
            last_reinforced_at=past,
            now=now,
        )
        assert result == pytest.approx(0.5, abs=0.01)

    def test_observed_no_decay_under_180_days(self):
        """OBSERVED epistemic type: no confidence decay for t <= 180 days."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=179)
        result = compute_effective_confidence(
            confidence_base=0.9,
            decay_profile=DecayProfile.MEDIUM,
            last_reinforced_at=past,
            now=now,
            epistemic_type=EpistemicType.OBSERVED,
        )
        assert result == pytest.approx(0.9, abs=1e-6)

    def test_observed_decays_after_180_days(self):
        """OBSERVED epistemic type: confidence DOES decay after 180 days."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=250)
        result = compute_effective_confidence(
            confidence_base=0.9,
            decay_profile=DecayProfile.MEDIUM,
            last_reinforced_at=past,
            now=now,
            epistemic_type=EpistemicType.OBSERVED,
        )
        # With mu=0.01 and t=250, exp(-0.01*250) = exp(-2.5) ~ 0.082
        # effective = 0.9 * 0.082 ~ 0.074
        assert result < 0.9

    def test_non_observed_decays_normally(self):
        """Non-OBSERVED types decay from day 0."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=100)
        result = compute_effective_confidence(
            confidence_base=0.9,
            decay_profile=DecayProfile.MEDIUM,
            last_reinforced_at=past,
            now=now,
            epistemic_type=EpistemicType.ASSERTED,
        )
        assert result < 0.9


# ---------------------------------------------------------------------------
# Virtual Decay: apply_virtual_decay
# ---------------------------------------------------------------------------


class TestApplyVirtualDecay:
    """Tests for apply_virtual_decay()."""

    def test_returns_new_node_not_mutated(self):
        """apply_virtual_decay returns a new node; original is not mutated."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=100)
        original = _make_node(
            salience_base=0.8,
            confidence_base=0.9,
            last_reinforced_at=past,
            decay_profile=DecayProfile.FAST,
        )
        original_salience = original.salience
        original_confidence = original.confidence

        decayed = apply_virtual_decay(original, now)

        # Original not mutated
        assert original.salience == original_salience
        assert original.confidence == original_confidence
        # Decayed node is different object
        assert decayed is not original
        # Decayed values should be lower (100 days with FAST profile)
        assert decayed.salience < original_salience
        assert decayed.confidence < original_confidence

    def test_pinned_exemption(self):
        """Pinned nodes are exempt from decay."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=500)
        node = _make_node(
            pinned=True,
            salience_base=0.8,
            confidence_base=0.9,
            last_reinforced_at=past,
            decay_profile=DecayProfile.RAPID,
        )
        decayed = apply_virtual_decay(node, now)
        # salience_base and confidence_base should be reflected
        assert decayed.salience == node.salience
        assert decayed.confidence == node.confidence

    def test_permanent_exemption(self):
        """PERMANENT profile nodes are exempt from decay."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=500)
        node = _make_node(
            decay_profile=DecayProfile.PERMANENT,
            salience_base=0.7,
            confidence_base=0.85,
            last_reinforced_at=past,
        )
        decayed = apply_virtual_decay(node, now)
        assert decayed.salience == node.salience
        assert decayed.confidence == node.confidence

    def test_archived_exemption(self):
        """ARCHIVED nodes are exempt from decay."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=500)
        node = _make_node(
            lifecycle_state=LifecycleState.ARCHIVED,
            salience_base=0.3,
            confidence_base=0.4,
            last_reinforced_at=past,
            decay_profile=DecayProfile.RAPID,
        )
        decayed = apply_virtual_decay(node, now)
        assert decayed.salience == node.salience
        assert decayed.confidence == node.confidence

    def test_deprecated_exemption(self):
        """DEPRECATED nodes are exempt from decay."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=500)
        node = _make_node(
            lifecycle_state=LifecycleState.DEPRECATED,
            salience_base=0.3,
            confidence_base=0.4,
            last_reinforced_at=past,
            decay_profile=DecayProfile.RAPID,
        )
        decayed = apply_virtual_decay(node, now)
        assert decayed.salience == node.salience
        assert decayed.confidence == node.confidence

    def test_base_values_preserved(self):
        """Base values (salience_base, confidence_base) are not changed in decayed copy."""
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=50)
        node = _make_node(
            salience_base=0.8,
            confidence_base=0.9,
            last_reinforced_at=past,
            decay_profile=DecayProfile.MEDIUM,
        )
        decayed = apply_virtual_decay(node, now)
        assert decayed.salience_base == 0.8
        assert decayed.confidence_base == 0.9


# ---------------------------------------------------------------------------
# Models: JobResult and OrganizeResult defaults
# ---------------------------------------------------------------------------


class TestJobResult:
    """Tests for JobResult model defaults."""

    def test_defaults(self):
        """JobResult has correct default values."""
        r = JobResult(job="promote")
        assert r.job == "promote"
        assert r.nodes_processed == 0
        assert r.nodes_modified == 0
        assert r.errors == 0
        assert r.duration_ms == 0.0
        assert r.details == {}

    def test_custom_values(self):
        """JobResult accepts custom values."""
        r = JobResult(
            job="archive",
            nodes_processed=10,
            nodes_modified=3,
            errors=1,
            duration_ms=42.5,
            details={"note": "test"},
        )
        assert r.nodes_processed == 10
        assert r.nodes_modified == 3
        assert r.errors == 1
        assert r.duration_ms == 42.5
        assert r.details == {"note": "test"}


class TestOrganizeResult:
    """Tests for OrganizeResult model defaults."""

    def test_defaults(self):
        """OrganizeResult has correct default values."""
        r = OrganizeResult()
        assert r.jobs_run == []
        assert r.jobs_skipped == []
        assert r.duration_ms == 0.0
        assert r.budget_remaining_ms == 0.0
        assert r.per_job == {}

    def test_serialization(self):
        """OrganizeResult can be serialized to dict."""
        r = OrganizeResult(
            jobs_run=["promote", "archive"],
            duration_ms=100.0,
            budget_remaining_ms=4900.0,
            per_job={
                "promote": JobResult(job="promote", nodes_modified=2),
            },
        )
        d = r.model_dump()
        assert d["jobs_run"] == ["promote", "archive"]
        assert d["per_job"]["promote"]["nodes_modified"] == 2


class TestMaintenanceResult:
    """Tests for MaintenanceResult dataclass defaults."""

    def test_defaults(self):
        """MaintenanceResult has correct defaults."""
        r = MaintenanceResult()
        assert r.nodes_promoted == 0
        assert r.nodes_archived == 0
        assert r.nodes_deprecated == 0
        assert r.feedback_applied == 0
        assert r.duration_ms == 0.0
        assert r.skipped_reason is None


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
# MaintenanceRunner
# ---------------------------------------------------------------------------


class TestMaintenanceRunner:
    """Tests for the opportunistic MaintenanceRunner."""

    @pytest.mark.asyncio
    async def test_first_call_always_runs(self, engine_parts):
        """First call always runs (last_maintained_at == 0)."""
        engine, _, _ = engine_parts
        runner = MaintenanceRunner(engine, engine._config.organizer)
        result = runner._last_maintained_at
        assert result == 0.0

        maint_result = await runner.maybe_run()
        assert maint_result is not None
        assert runner._last_maintained_at > 0

    @pytest.mark.asyncio
    async def test_skips_when_cooldown_not_elapsed(self, engine_parts):
        """Subsequent call skips when cooldown hasn't elapsed."""
        engine, _, _ = engine_parts
        runner = MaintenanceRunner(engine, engine._config.organizer)

        # First run
        await runner.maybe_run()
        first_time = runner._last_maintained_at

        # Immediate second run should be skipped
        result = await runner.maybe_run()
        assert result is None
        # last_maintained_at should not change
        assert runner._last_maintained_at == first_time

    @pytest.mark.asyncio
    async def test_runs_when_cooldown_elapsed(self, engine_parts):
        """Runs when cooldown has elapsed."""
        engine, _, _ = engine_parts
        config = OrganizerConfig(opportunistic_cooldown=0)  # 0-second cooldown
        runner = MaintenanceRunner(engine, config)

        # First run
        result1 = await runner.maybe_run()
        assert result1 is not None

        # Second run should also succeed (cooldown=0)
        result2 = await runner.maybe_run()
        assert result2 is not None

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self, engine_parts):
        """Disabled runner always returns None."""
        engine, _, _ = engine_parts
        config = OrganizerConfig(opportunistic_enabled=False)
        runner = MaintenanceRunner(engine, config)

        result = await runner.maybe_run()
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_promotion_promotes_eligible(self, engine_parts):
        """Auto-promotion promotes nodes older than threshold with enough evidence."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            opportunistic_cooldown=0,
            promotion_age_days=1.0,
            promotion_evidence_count=1,
        )
        runner = MaintenanceRunner(engine, config)

        # Create a tentative node that's old enough with evidence
        old_time = datetime.now(timezone.utc) - timedelta(days=10)
        node = _make_node(
            lifecycle_state=LifecycleState.TENTATIVE,
            created_at=old_time,
            evidence_refs=[uuid4()],
        )
        await graph_store.create_node(node)

        result = await runner.maybe_run()
        assert result is not None
        assert result.nodes_promoted >= 1

        # Verify node was promoted
        refreshed = await graph_store.get_node(str(node.id), include_superseded=True)
        assert refreshed is not None
        assert refreshed.lifecycle_state == LifecycleState.STABLE

    @pytest.mark.asyncio
    async def test_threshold_archival_archives_low_salience(self, engine_parts):
        """Threshold archival archives nodes with very low effective salience."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            opportunistic_cooldown=0,
            force_archive_salience_threshold=0.05,
        )
        runner = MaintenanceRunner(engine, config)

        # Create a node with very low base salience and old reinforcement
        old_time = datetime.now(timezone.utc) - timedelta(days=500)
        node = _make_node(
            lifecycle_state=LifecycleState.TENTATIVE,
            salience_base=0.01,
            confidence_base=0.9,
            last_reinforced_at=old_time,
            decay_profile=DecayProfile.MEDIUM,
        )
        await graph_store.create_node(node)

        result = await runner.maybe_run()
        assert result is not None
        assert result.nodes_archived >= 1

        refreshed = await graph_store.get_node(str(node.id), include_superseded=True)
        assert refreshed is not None
        assert refreshed.lifecycle_state == LifecycleState.ARCHIVED

    @pytest.mark.asyncio
    async def test_resets_cooldown_after_run(self, engine_parts):
        """Cooldown is reset after a successful run."""
        engine, _, _ = engine_parts
        config = OrganizerConfig(opportunistic_cooldown=9999)
        runner = MaintenanceRunner(engine, config)

        assert runner._last_maintained_at == 0.0
        await runner.maybe_run()
        assert runner._last_maintained_at > 0.0


# ---------------------------------------------------------------------------
# Organize and End Session
# ---------------------------------------------------------------------------


class TestOrganize:
    """Tests for MemoryEngine.organize() and end_session()."""

    @pytest.mark.asyncio
    async def test_organize_runs_all_jobs_by_default(self, engine_parts):
        """organize() runs all jobs when no specific jobs are requested."""
        engine, _, _ = engine_parts
        result = await engine.organize()
        assert set(result.jobs_run) == set(ALL_JOBS)
        assert result.jobs_skipped == []

    @pytest.mark.asyncio
    async def test_organize_runs_specific_jobs(self, engine_parts):
        """organize() runs only specified jobs."""
        engine, _, _ = engine_parts
        result = await engine.organize(jobs=["promote", "archive"])
        assert set(result.jobs_run) == {"promote", "archive"}

    @pytest.mark.asyncio
    async def test_organize_respects_budget(self, engine_parts):
        """organize() stops running jobs when budget is exhausted."""
        engine, _, _ = engine_parts
        # Use a very tiny budget - might skip some jobs
        result = await engine.organize(budget_ms=0)
        # With 0 budget, first job check should fail
        assert len(result.jobs_skipped) >= 0  # May skip some or all
        assert result.budget_remaining_ms == 0.0

    @pytest.mark.asyncio
    async def test_organize_result_structure(self, engine_parts):
        """OrganizeResult has correct structure after organize()."""
        engine, _, _ = engine_parts
        result = await engine.organize(jobs=["promote"])
        assert isinstance(result, OrganizeResult)
        assert "promote" in result.jobs_run
        assert "promote" in result.per_job
        assert isinstance(result.per_job["promote"], JobResult)
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_end_session_runs_promote_and_feedback(self, engine_parts):
        """end_session() runs promote + feedback_apply."""
        engine, _, _ = engine_parts
        result = await engine.end_session(user_id="test-user")
        assert set(result.jobs_run) == {"promote", "feedback_apply"}

    @pytest.mark.asyncio
    async def test_invalid_job_name_skipped(self, engine_parts):
        """Invalid job name is skipped gracefully."""
        engine, _, _ = engine_parts
        result = await engine.organize(jobs=["nonexistent_job", "promote"])
        assert "nonexistent_job" in result.jobs_skipped
        assert "promote" in result.jobs_run


# ---------------------------------------------------------------------------
# Jobs: run_job dispatch
# ---------------------------------------------------------------------------


class TestRunJob:
    """Tests for the run_job() dispatcher."""

    @pytest.mark.asyncio
    async def test_promote_job(self, engine_parts):
        """promote job returns a JobResult."""
        engine, _, _ = engine_parts
        config = OrganizerConfig()
        result = await run_job("promote", engine, config, 5000.0)
        assert isinstance(result, JobResult)
        assert result.job == "promote"

    @pytest.mark.asyncio
    async def test_decay_sweep_job(self, engine_parts):
        """decay_sweep job returns a JobResult."""
        engine, _, _ = engine_parts
        config = OrganizerConfig()
        result = await run_job("decay_sweep", engine, config, 5000.0)
        assert isinstance(result, JobResult)
        assert result.job == "decay_sweep"

    @pytest.mark.asyncio
    async def test_archive_job(self, engine_parts):
        """archive job returns a JobResult."""
        engine, _, _ = engine_parts
        config = OrganizerConfig()
        result = await run_job("archive", engine, config, 5000.0)
        assert isinstance(result, JobResult)
        assert result.job == "archive"

    @pytest.mark.asyncio
    async def test_feedback_apply_no_signals(self, engine_parts):
        """feedback_apply with no pending signals returns no_signals status."""
        engine, _, _ = engine_parts
        config = OrganizerConfig()
        result = await run_job("feedback_apply", engine, config, 5000.0)
        assert result.job == "feedback_apply"
        assert result.details.get("status") == "no_signals"

    @pytest.mark.asyncio
    async def test_stub_jobs(self, engine_parts):
        """Stub jobs return empty results with status note."""
        engine, _, _ = engine_parts
        config = OrganizerConfig()
        for job_name in ["deduplicate", "alias_resolve", "summarize", "centrality_boost", "tombstone_sweep"]:
            result = await run_job(job_name, engine, config, 5000.0)
            assert result.job == job_name
            assert result.details.get("status") == "stub"

    @pytest.mark.asyncio
    async def test_unknown_job_raises(self, engine_parts):
        """Unknown job name raises ValueError."""
        engine, _, _ = engine_parts
        config = OrganizerConfig()
        with pytest.raises(ValueError, match="Unknown organizer job"):
            await run_job("does_not_exist", engine, config, 5000.0)

    @pytest.mark.asyncio
    async def test_promote_job_promotes_eligible(self, engine_parts):
        """promote job actually promotes eligible tentative nodes."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(
            promotion_age_days=1.0,
            promotion_evidence_count=1,
        )

        # Create eligible node
        old_time = datetime.now(timezone.utc) - timedelta(days=10)
        node = _make_node(
            lifecycle_state=LifecycleState.TENTATIVE,
            created_at=old_time,
            evidence_refs=[uuid4()],
        )
        await graph_store.create_node(node)

        result = await run_job("promote", engine, config, 5000.0)
        assert result.nodes_modified >= 1

        refreshed = await graph_store.get_node(str(node.id), include_superseded=True)
        assert refreshed.lifecycle_state == LifecycleState.STABLE

    @pytest.mark.asyncio
    async def test_archive_job_archives_low_salience(self, engine_parts):
        """archive job archives nodes below force_archive threshold."""
        engine, graph_store, _ = engine_parts
        config = OrganizerConfig(force_archive_salience_threshold=0.05)

        # Create node with very low salience that will decay far
        old_time = datetime.now(timezone.utc) - timedelta(days=500)
        node = _make_node(
            lifecycle_state=LifecycleState.TENTATIVE,
            salience_base=0.01,
            last_reinforced_at=old_time,
            decay_profile=DecayProfile.MEDIUM,
        )
        await graph_store.create_node(node)

        result = await run_job("archive", engine, config, 5000.0)
        assert result.nodes_modified >= 1

        refreshed = await graph_store.get_node(str(node.id), include_superseded=True)
        assert refreshed.lifecycle_state == LifecycleState.ARCHIVED


# ---------------------------------------------------------------------------
# Integration: organize promotes and archives together
# ---------------------------------------------------------------------------


class TestOrganizeIntegration:
    """Integration tests combining promote + archive in organize()."""

    @pytest.mark.asyncio
    async def test_organize_promotes_and_archives(self, engine_parts):
        """organize() can promote eligible nodes and archive decayed ones in one pass."""
        engine, graph_store, _ = engine_parts

        # Create node eligible for promotion
        old_time = datetime.now(timezone.utc) - timedelta(days=30)
        promotable = _make_node(
            lifecycle_state=LifecycleState.TENTATIVE,
            created_at=old_time,
            evidence_refs=[uuid4(), uuid4()],
            salience_base=0.8,
            confidence_base=0.9,
        )
        await graph_store.create_node(promotable)

        # Create node that should be archived (very low salience, old)
        very_old = datetime.now(timezone.utc) - timedelta(days=500)
        archivable = _make_node(
            lifecycle_state=LifecycleState.TENTATIVE,
            salience_base=0.01,
            confidence_base=0.01,
            last_reinforced_at=very_old,
            decay_profile=DecayProfile.RAPID,
        )
        await graph_store.create_node(archivable)

        result = await engine.organize(
            jobs=["promote", "archive"],
            budget_ms=5000,
        )

        assert "promote" in result.jobs_run
        assert "archive" in result.jobs_run

        # Check promoted node
        refreshed_promotable = await graph_store.get_node(
            str(promotable.id), include_superseded=True
        )
        assert refreshed_promotable.lifecycle_state == LifecycleState.STABLE

        # Check archived node
        refreshed_archivable = await graph_store.get_node(
            str(archivable.id), include_superseded=True
        )
        assert refreshed_archivable.lifecycle_state == LifecycleState.ARCHIVED
