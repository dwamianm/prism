"""Request-triggered maintenance cannot mutate or process another tenant."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.models import MemoryNode
from prme.types import LifecycleState, NodeType
from tests.test_durable_ingestion import config, user  # noqa: F401


@pytest.mark.parametrize("trigger", ["retrieve", "ingest"])
async def test_request_maintenance_stays_scoped_and_cooldowns_are_per_user(config, user, trigger, monkeypatch):  # noqa: F811
    config.organizer.promotion_age_days = 1
    config.organizer.promotion_evidence_count = 1
    config.organizer.opportunistic_cooldown = 3600
    old_time = datetime.now(timezone.utc) - timedelta(days=10)
    other = user + "-other"
    async with MemoryEngine.open(config) as engine:
        nodes = {}
        for owner in (user, other):
            promote = MemoryNode(user_id=owner, node_type=NodeType.FACT, content="eligible fact",
                                 created_at=old_time, evidence_refs=[uuid4()])
            archive = MemoryNode(user_id=owner, node_type=NodeType.NOTE, content="weak note",
                                 salience=0.0, salience_base=0.0)
            for node in (promote, archive):
                await engine._graph_store.create_node(node)
            nodes[owner] = (promote, archive)
        # Queue work while maintenance is disabled, then enable it.
        pending = await engine.ingest_fast("foreign durable work", user_id=other)
        foreign_before = [await engine.get_node(str(n.id), include_superseded=True) for n in nodes[other]]
        config.organizer.opportunistic_enabled = True
        if trigger == "ingest":
            monkeypatch.setattr(engine._pipeline, "ingest", AsyncMock(return_value=str(uuid4())))

        await getattr(engine, trigger)("memory", user_id=user)
        await engine._maintenance_runner.drain()
        assert (await engine.get_node(str(nodes[user][0].id))).lifecycle_state == LifecycleState.STABLE
        assert (await engine.get_node(str(nodes[user][1].id), include_superseded=True)).lifecycle_state == LifecycleState.ARCHIVED
        assert [await engine.get_node(str(n.id), include_superseded=True) for n in nodes[other]] == foreign_before
        assert (await engine.processing_status(pending, user_id=other)).status == "pending"
        assert not engine._maintenance_runner._is_due(user)
        assert engine._maintenance_runner._is_due(other)
        await getattr(engine, trigger)("memory", user_id=other)
        await engine._maintenance_runner.drain()
        assert (await engine.get_node(str(nodes[other][0].id))).lifecycle_state == LifecycleState.STABLE
        assert (await engine.get_node(str(nodes[other][1].id), include_superseded=True)).lifecycle_state == LifecycleState.ARCHIVED
        assert (await engine.processing_status(pending, user_id=other)).status == "complete"
        assert not engine._maintenance_runner._is_due(other)


async def test_maintenance_defers_remaining_work_when_budget_is_spent(config, user, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    config.organizer.opportunistic_enabled = True
    config.organizer.opportunistic_budget_ms = 200
    config.organizer.promotion_age_days = 1
    config.organizer.promotion_evidence_count = 1
    old = datetime.now(timezone.utc) - timedelta(days=10)
    async with MemoryEngine.open(config) as engine:
        nodes = [MemoryNode(user_id=user, node_type=NodeType.FACT, content=str(i),
                            created_at=old, evidence_refs=[uuid4()]) for i in range(5)]
        for node in nodes:
            await engine._graph_store.create_node(node)
        clock = [10.0]
        monkeypatch.setattr("prme.organizer.maintenance.time", SimpleNamespace(monotonic=lambda: clock[0]))
        original = engine.promote

        async def timed_promote(node_id, *, user_id=None):
            await original(node_id, user_id=user_id)
            clock[0] += 0.15

        monkeypatch.setattr(engine, "promote", timed_promote)
        result = await engine._maintenance_runner.maybe_run(user_id=user)
        # Finish a started durable write, then stop before the third one.
        assert result.nodes_promoted == 2
        assert result.duration_ms == 300
        remaining = await engine.scan_nodes(user_id=user, lifecycle_states=[LifecycleState.TENTATIVE])
        assert len(remaining) == 3
        config.organizer.opportunistic_budget_ms = 0
        config.organizer.opportunistic_cooldown = 0
        result = await engine._maintenance_runner.maybe_run(user_id=user)
        assert result.nodes_promoted == 0 and result.nodes_archived == 0
