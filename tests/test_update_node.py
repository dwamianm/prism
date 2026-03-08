"""Tests for GraphStore.update_node() method.

Tests the DuckDB-backed GraphStore implementation (DuckPGQGraphStore)
to verify that update_node correctly updates arbitrary fields on
existing nodes with proper serialization and validation.

Each test gets an isolated DuckDB connection via tmp_path to prevent
cross-test connection sharing (issue #19).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import duckdb
import pytest
import pytest_asyncio

from prme.models.nodes import MemoryNode
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.schema import initialize_database
from prme.types import DecayProfile, LifecycleState, NodeType, Scope


@pytest_asyncio.fixture
async def graph_store(tmp_path):
    """Create an isolated DuckDB-backed graph store for testing.

    Uses tmp_path for file-backed DuckDB to ensure complete isolation
    between tests. Connection is closed on teardown.
    """
    db_path = str(tmp_path / "test.duckdb")
    conn = duckdb.connect(db_path)
    initialize_database(conn)
    conn_lock = asyncio.Lock()
    store = DuckPGQGraphStore(conn, conn_lock)
    yield store
    conn.close()


def _make_node(**kwargs) -> MemoryNode:
    """Helper to create a MemoryNode with sensible defaults."""
    defaults = dict(
        node_type=NodeType.FACT,
        user_id="test-user",
        content="test content",
    )
    defaults.update(kwargs)
    return MemoryNode(**defaults)


class TestUpdateNode:
    """Tests for GraphStore.update_node() method."""

    @pytest.mark.asyncio
    async def test_update_single_field(self, graph_store):
        """Update reinforcement_boost and verify."""
        node = _make_node(reinforcement_boost=0.0)
        node_id = await graph_store.create_node(node)

        await graph_store.update_node(node_id, reinforcement_boost=0.3)

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.reinforcement_boost == pytest.approx(0.3, abs=1e-6)

    @pytest.mark.asyncio
    async def test_update_multiple_fields(self, graph_store):
        """Update reinforcement_boost and last_reinforced_at together."""
        now = datetime.now(timezone.utc)
        node = _make_node()
        node_id = await graph_store.create_node(node)

        new_time = now + timedelta(hours=1)
        await graph_store.update_node(
            node_id,
            reinforcement_boost=0.5,
            last_reinforced_at=new_time,
        )

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.reinforcement_boost == pytest.approx(0.5, abs=1e-6)
        # Allow for microsecond truncation
        delta = abs((retrieved.last_reinforced_at - new_time).total_seconds())
        assert delta < 1.0

    @pytest.mark.asyncio
    async def test_update_evidence_refs(self, graph_store):
        """Update evidence_refs with a list of UUIDs."""
        node = _make_node()
        node_id = await graph_store.create_node(node)

        uuid1 = uuid4()
        uuid2 = uuid4()
        await graph_store.update_node(node_id, evidence_refs=[uuid1, uuid2])

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert len(retrieved.evidence_refs) == 2
        assert uuid1 in retrieved.evidence_refs
        assert uuid2 in retrieved.evidence_refs

    @pytest.mark.asyncio
    async def test_update_decay_profile(self, graph_store):
        """Change decay_profile enum value."""
        node = _make_node(decay_profile=DecayProfile.MEDIUM)
        node_id = await graph_store.create_node(node)

        await graph_store.update_node(node_id, decay_profile=DecayProfile.SLOW)

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.decay_profile == DecayProfile.SLOW

    @pytest.mark.asyncio
    async def test_update_nonexistent_node_raises(self, graph_store):
        """ValueError for missing node."""
        fake_id = str(uuid4())
        with pytest.raises(ValueError, match="not found"):
            await graph_store.update_node(fake_id, reinforcement_boost=0.1)

    @pytest.mark.asyncio
    async def test_update_no_valid_fields_raises(self, graph_store):
        """ValueError when all kwargs are invalid field names."""
        node = _make_node()
        node_id = await graph_store.create_node(node)

        with pytest.raises(ValueError, match="No valid fields"):
            await graph_store.update_node(node_id, nonexistent_field="foo")

    @pytest.mark.asyncio
    async def test_update_sets_updated_at(self, graph_store):
        """updated_at is automatically set to now."""
        node = _make_node()
        node_id = await graph_store.create_node(node)

        before = datetime.now(timezone.utc)
        await graph_store.update_node(node_id, reinforcement_boost=0.1)
        after = datetime.now(timezone.utc)

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        # updated_at should be between before and after
        assert retrieved.updated_at >= before - timedelta(seconds=1)
        assert retrieved.updated_at <= after + timedelta(seconds=1)

    @pytest.mark.asyncio
    async def test_round_trip_all_decay_fields(self, graph_store):
        """Update all decay-related fields and verify round-trip."""
        node = _make_node()
        node_id = await graph_store.create_node(node)

        now = datetime.now(timezone.utc)
        uuid1 = uuid4()
        await graph_store.update_node(
            node_id,
            reinforcement_boost=0.42,
            last_reinforced_at=now,
            confidence_base=0.88,
            salience_base=0.95,
            decay_profile=DecayProfile.PERMANENT,
            pinned=True,
            evidence_refs=[uuid1],
            metadata={"source": "test"},
        )

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.reinforcement_boost == pytest.approx(0.42, abs=1e-6)
        assert retrieved.confidence_base == pytest.approx(0.88, abs=1e-6)
        assert retrieved.salience_base == pytest.approx(0.95, abs=1e-6)
        assert retrieved.decay_profile == DecayProfile.PERMANENT
        assert retrieved.pinned is True
        assert len(retrieved.evidence_refs) == 1
        assert uuid1 in retrieved.evidence_refs
        assert retrieved.metadata == {"source": "test"}

    @pytest.mark.asyncio
    async def test_update_confidence_and_salience(self, graph_store):
        """Update confidence and salience directly."""
        node = _make_node(confidence=0.5, salience=0.5)
        node_id = await graph_store.create_node(node)

        await graph_store.update_node(node_id, confidence=0.9, salience=0.8)

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.confidence == pytest.approx(0.9, abs=1e-6)
        assert retrieved.salience == pytest.approx(0.8, abs=1e-6)

    @pytest.mark.asyncio
    async def test_update_superseded_by(self, graph_store):
        """Update superseded_by with a UUID."""
        node = _make_node()
        node_id = await graph_store.create_node(node)

        # Also update lifecycle_state so we can retrieve it with include_superseded
        replacement_id = uuid4()
        await graph_store.update_node(
            node_id,
            superseded_by=replacement_id,
            lifecycle_state=LifecycleState.SUPERSEDED,
        )

        # Must use include_superseded=True since lifecycle is now SUPERSEDED
        retrieved = await graph_store.get_node(node_id, include_superseded=True)
        assert retrieved is not None
        assert retrieved.superseded_by == replacement_id
        assert retrieved.lifecycle_state == LifecycleState.SUPERSEDED

    @pytest.mark.asyncio
    async def test_update_preserves_unmodified_fields(self, graph_store):
        """Updating one field should not change other fields."""
        node = _make_node(
            content="Important fact",
            confidence=0.9,
            salience=0.8,
            decay_profile=DecayProfile.SLOW,
        )
        node_id = await graph_store.create_node(node)

        # Only update reinforcement_boost
        await graph_store.update_node(node_id, reinforcement_boost=0.3)

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.content == "Important fact"
        assert retrieved.confidence == pytest.approx(0.9, abs=1e-6)
        assert retrieved.salience == pytest.approx(0.8, abs=1e-6)
        assert retrieved.decay_profile == DecayProfile.SLOW

    @pytest.mark.asyncio
    async def test_update_lifecycle_state(self, graph_store):
        """Update lifecycle_state via update_node."""
        node = _make_node()
        node_id = await graph_store.create_node(node)

        await graph_store.update_node(
            node_id,
            lifecycle_state=LifecycleState.STABLE,
        )

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.lifecycle_state == LifecycleState.STABLE

    @pytest.mark.asyncio
    async def test_update_with_mixed_valid_and_invalid_fields(self, graph_store):
        """Valid fields are applied even when mixed with invalid ones."""
        node = _make_node(reinforcement_boost=0.0)
        node_id = await graph_store.create_node(node)

        # reinforcement_boost is valid, bogus_field is not
        await graph_store.update_node(
            node_id,
            reinforcement_boost=0.7,
            bogus_field="ignored",
        )

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.reinforcement_boost == pytest.approx(0.7, abs=1e-6)

    @pytest.mark.asyncio
    async def test_update_metadata_dict(self, graph_store):
        """Update metadata with a dict value."""
        node = _make_node(metadata={"key": "old"})
        node_id = await graph_store.create_node(node)

        await graph_store.update_node(node_id, metadata={"key": "new", "extra": 42})

        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.metadata == {"key": "new", "extra": 42}

    @pytest.mark.asyncio
    async def test_update_pinned_toggle(self, graph_store):
        """Toggle pinned field from False to True and back."""
        node = _make_node(pinned=False)
        node_id = await graph_store.create_node(node)

        await graph_store.update_node(node_id, pinned=True)
        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.pinned is True

        await graph_store.update_node(node_id, pinned=False)
        retrieved = await graph_store.get_node(node_id)
        assert retrieved is not None
        assert retrieved.pinned is False
