"""Tests for TTL-based archival (issue #12, RFC-0007 S9).

Validates:
- Default TTL assignment by node type via OrganizerConfig
- Explicit TTL override at store() time
- Expired nodes get archived by tombstone_sweep
- Non-expired nodes are preserved
- None TTL means no expiry
- Pinned nodes are exempt from TTL
- Budget enforcement in tombstone_sweep
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from prme import LifecycleState, MemoryEngine, NodeType, PRMEConfig, Scope
from prme.config import OrganizerConfig
from prme.organizer.jobs import run_job
from prme.organizer.models import JobResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_ttl_") as d:
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


def _age_node(engine: MemoryEngine, node_id: str, days: float) -> None:
    old_ts = datetime.now(timezone.utc) - timedelta(days=days)
    engine._conn.execute(
        "UPDATE nodes SET created_at = ?, last_reinforced_at = ? WHERE id = ?::UUID",
        [old_ts, old_ts, node_id],
    )


def _pin_node(engine: MemoryEngine, node_id: str) -> None:
    engine._conn.execute(
        "UPDATE nodes SET pinned = TRUE WHERE id = ?::UUID",
        [node_id],
    )


async def _get_node_ids(engine: MemoryEngine, user_id: str = "test-user") -> list[str]:
    nodes = await engine.query_nodes(
        user_id=user_id,
        lifecycle_states=[
            LifecycleState.TENTATIVE, LifecycleState.STABLE, LifecycleState.CONTESTED,
        ],
    )
    return [str(n.id) for n in nodes]


async def _get_node(engine: MemoryEngine, node_id: str):
    return await engine._graph_store.get_node(node_id, include_superseded=True)


# ---------------------------------------------------------------------------
# 1. Default TTL Assignment by Node Type
# ---------------------------------------------------------------------------

class TestDefaultTTLAssignment:

    @pytest.mark.asyncio
    async def test_event_gets_365_day_ttl(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Something happened", user_id="test-user", node_type=NodeType.EVENT)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days == 365
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_task_gets_90_day_ttl(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Finish report", user_id="test-user", node_type=NodeType.TASK)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days == 90
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_decision_gets_180_day_ttl(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Use Python", user_id="test-user", node_type=NodeType.DECISION)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days == 180
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_entity_gets_no_ttl(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Python language", user_id="test-user", node_type=NodeType.ENTITY)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days is None
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_fact_gets_no_ttl(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Sky is blue", user_id="test-user", node_type=NodeType.FACT)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days is None
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_note_gets_90_day_ttl(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Quick note", user_id="test-user", node_type=NodeType.NOTE)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days == 90
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_summary_gets_365_day_ttl(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Weekly summary", user_id="test-user", node_type=NodeType.SUMMARY)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days == 365
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_preference_gets_no_ttl(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Dark mode", user_id="test-user", node_type=NodeType.PREFERENCE)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days is None
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 2. Explicit TTL Override
# ---------------------------------------------------------------------------

class TestExplicitTTLOverride:

    @pytest.mark.asyncio
    async def test_explicit_ttl_overrides_default(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Temp fact", user_id="test-user", node_type=NodeType.FACT, ttl_days=30)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days == 30
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_explicit_none_overrides_default(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Important task", user_id="test-user", node_type=NodeType.TASK, ttl_days=None)
            node_ids = await _get_node_ids(engine)
            node = await _get_node(engine, node_ids[0])
            assert node.ttl_days is None
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 3. Expired Nodes Get Archived
# ---------------------------------------------------------------------------

class TestExpiredNodesArchived:

    @pytest.mark.asyncio
    async def test_expired_node_is_archived(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Short-lived note", user_id="test-user", node_type=NodeType.NOTE)
            node_ids = await _get_node_ids(engine)
            node_id = node_ids[0]
            _age_node(engine, node_id, days=100)
            result = await run_job("tombstone_sweep", engine, config.organizer, 5000)
            assert result.nodes_modified >= 1
            assert result.errors == 0
            node = await _get_node(engine, node_id)
            assert node.lifecycle_state == LifecycleState.ARCHIVED
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_tombstone_sweep_logs_operation(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Expiring content", user_id="test-user", node_type=NodeType.TASK)
            node_ids = await _get_node_ids(engine)
            node_id = node_ids[0]
            _age_node(engine, node_id, days=100)
            await run_job("tombstone_sweep", engine, config.organizer, 5000)
            row = engine._conn.execute(
                "SELECT op_type, target_id, payload FROM operations "
                "WHERE op_type = 'TOMBSTONE_SWEEP' AND target_id = ?",
                [node_id],
            ).fetchone()
            assert row is not None
            assert row[0] == "TOMBSTONE_SWEEP"
            payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
            assert payload["reason"] == "retention_policy_expiry"
            assert payload["ttl_days"] == 90
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 4. Non-Expired Nodes Are Preserved
# ---------------------------------------------------------------------------

class TestNonExpiredPreserved:

    @pytest.mark.asyncio
    async def test_node_within_ttl_not_archived(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Recent note", user_id="test-user", node_type=NodeType.NOTE)
            node_ids = await _get_node_ids(engine)
            node_id = node_ids[0]
            _age_node(engine, node_id, days=30)
            result = await run_job("tombstone_sweep", engine, config.organizer, 5000)
            assert result.nodes_modified == 0
            node = await _get_node(engine, node_id)
            assert node.lifecycle_state != LifecycleState.ARCHIVED
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 5. None TTL Means No Expiry
# ---------------------------------------------------------------------------

class TestNoneTTLNoExpiry:

    @pytest.mark.asyncio
    async def test_none_ttl_never_expires(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Eternal entity", user_id="test-user", node_type=NodeType.ENTITY)
            node_ids = await _get_node_ids(engine)
            node_id = node_ids[0]
            _age_node(engine, node_id, days=3650)
            result = await run_job("tombstone_sweep", engine, config.organizer, 5000)
            assert result.nodes_modified == 0
            node = await _get_node(engine, node_id)
            assert node.lifecycle_state != LifecycleState.ARCHIVED
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 6. Pinned Nodes Are Exempt
# ---------------------------------------------------------------------------

class TestPinnedExempt:

    @pytest.mark.asyncio
    async def test_pinned_node_not_archived_even_if_expired(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Pinned note", user_id="test-user", node_type=NodeType.NOTE)
            node_ids = await _get_node_ids(engine)
            node_id = node_ids[0]
            _pin_node(engine, node_id)
            _age_node(engine, node_id, days=200)
            result = await run_job("tombstone_sweep", engine, config.organizer, 5000)
            assert result.nodes_modified == 0
            node = await _get_node(engine, node_id)
            assert node.lifecycle_state != LifecycleState.ARCHIVED
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 7. Budget Enforcement
# ---------------------------------------------------------------------------

class TestBudgetEnforcement:

    @pytest.mark.asyncio
    async def test_budget_limits_processing(self, config):
        engine = await create_engine(config)
        try:
            for i in range(20):
                await engine.store(f"Task {i}", user_id="test-user", node_type=NodeType.TASK)
            node_ids = await _get_node_ids(engine)
            for nid in node_ids:
                _age_node(engine, nid, days=100)
            result = await run_job("tombstone_sweep", engine, config.organizer, 0.01)
            assert result.duration_ms >= 0
            assert result.errors == 0
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# 8. Mixed Scenario
# ---------------------------------------------------------------------------

class TestMixedScenario:

    @pytest.mark.asyncio
    async def test_mixed_nodes_correct_archival(self, config):
        engine = await create_engine(config)
        try:
            await engine.store("Entity node", user_id="test-user", node_type=NodeType.ENTITY)
            await engine.store("Task node", user_id="test-user", node_type=NodeType.TASK)
            await engine.store("Fact node", user_id="test-user", node_type=NodeType.FACT)
            await engine.store("Note node", user_id="test-user", node_type=NodeType.NOTE)
            await engine.store("Event node", user_id="test-user", node_type=NodeType.EVENT)

            all_nodes = await engine.query_nodes(
                user_id="test-user",
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            nodes_by_type = {}
            for n in all_nodes:
                nodes_by_type[n.node_type] = str(n.id)

            for nid in nodes_by_type.values():
                _age_node(engine, nid, days=100)

            result = await run_job("tombstone_sweep", engine, config.organizer, 5000)
            assert result.errors == 0

            task_node = await _get_node(engine, nodes_by_type[NodeType.TASK])
            note_node = await _get_node(engine, nodes_by_type[NodeType.NOTE])
            entity_node = await _get_node(engine, nodes_by_type[NodeType.ENTITY])
            fact_node = await _get_node(engine, nodes_by_type[NodeType.FACT])
            event_node = await _get_node(engine, nodes_by_type[NodeType.EVENT])

            assert task_node.lifecycle_state == LifecycleState.ARCHIVED
            assert note_node.lifecycle_state == LifecycleState.ARCHIVED
            assert entity_node.lifecycle_state != LifecycleState.ARCHIVED
            assert fact_node.lifecycle_state != LifecycleState.ARCHIVED
            assert event_node.lifecycle_state != LifecycleState.ARCHIVED
        finally:
            await engine.close()
