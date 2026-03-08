"""Tests for MemoryEngine.reinforce() method.

Verifies that reinforce() correctly bumps reinforcement_boost and
confidence_base, respects caps, handles evidence_refs, updates
last_reinforced_at, and raises ValueError for nonexistent nodes.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine
from prme.types import NodeType, Scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_reinf_") as d:
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


async def _store_and_get_node_id(
    engine: MemoryEngine, content: str = "test fact"
) -> str:
    """Store a fact node and return the node ID (not event ID)."""
    await engine.store(
        content,
        user_id="test-user",
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
    )
    nodes = await engine.query_nodes(user_id="test-user", limit=100)
    for n in nodes:
        if n.content == content:
            return str(n.id)
    raise RuntimeError(f"Could not find stored node with content {content!r}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReinforce:
    """Tests for MemoryEngine.reinforce()."""

    @pytest.mark.asyncio
    async def test_basic_reinforce_bumps_boost_and_confidence(self, config):
        """reinforce() should bump reinforcement_boost by 0.15 and confidence_base by 0.05."""
        engine = await create_engine(config)
        try:
            node_id = await _store_and_get_node_id(engine)
            original = await engine.get_node(node_id)
            assert original is not None

            await engine.reinforce(node_id)

            updated = await engine.get_node(node_id)
            assert updated is not None
            assert updated.reinforcement_boost == pytest.approx(
                original.reinforcement_boost + 0.15, abs=1e-6
            )
            assert updated.confidence_base == pytest.approx(
                original.confidence_base + 0.05, abs=1e-6
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_reinforcement_boost_caps_at_0_5(self, config):
        """reinforcement_boost should never exceed 0.5."""
        engine = await create_engine(config)
        try:
            node_id = await _store_and_get_node_id(engine)

            # Reinforce many times (0.15 * 10 = 1.5, but should cap at 0.5)
            for _ in range(10):
                await engine.reinforce(node_id)

            node = await engine.get_node(node_id)
            assert node is not None
            assert node.reinforcement_boost == pytest.approx(0.5, abs=1e-6)
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_confidence_base_caps_at_0_95(self, config):
        """confidence_base should never exceed 0.95."""
        engine = await create_engine(config)
        try:
            node_id = await _store_and_get_node_id(engine)

            # Reinforce many times (0.05 * 20 = 1.0, but should cap at 0.95)
            for _ in range(20):
                await engine.reinforce(node_id)

            node = await engine.get_node(node_id)
            assert node is not None
            assert node.confidence_base <= 0.95 + 1e-6
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_evidence_id_appended(self, config):
        """Providing evidence_id should append it to evidence_refs."""
        engine = await create_engine(config)
        try:
            node_id = await _store_and_get_node_id(engine)
            original = await engine.get_node(node_id)
            assert original is not None
            original_refs_count = len(original.evidence_refs)

            evidence_uuid = str(uuid4())
            await engine.reinforce(node_id, evidence_id=evidence_uuid)

            updated = await engine.get_node(node_id)
            assert updated is not None
            assert len(updated.evidence_refs) == original_refs_count + 1
            assert UUID(evidence_uuid) in updated.evidence_refs
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_no_evidence_id_preserves_refs(self, config):
        """Reinforcing without evidence_id should not modify evidence_refs."""
        engine = await create_engine(config)
        try:
            node_id = await _store_and_get_node_id(engine)
            original = await engine.get_node(node_id)
            assert original is not None
            original_refs = list(original.evidence_refs)

            await engine.reinforce(node_id)

            updated = await engine.get_node(node_id)
            assert updated is not None
            assert updated.evidence_refs == original_refs
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_nonexistent_node_raises_valueerror(self, config):
        """reinforce() should raise ValueError for a nonexistent node_id."""
        engine = await create_engine(config)
        try:
            fake_id = str(uuid4())
            with pytest.raises(ValueError, match="not found"):
                await engine.reinforce(fake_id)
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_last_reinforced_at_updated(self, config):
        """reinforce() should update last_reinforced_at to approximately now."""
        engine = await create_engine(config)
        try:
            node_id = await _store_and_get_node_id(engine)

            before = datetime.now(timezone.utc)
            await engine.reinforce(node_id)
            after = datetime.now(timezone.utc)

            updated = await engine.get_node(node_id)
            assert updated is not None
            # last_reinforced_at should be between before and after (with tolerance)
            assert updated.last_reinforced_at >= before - timedelta(seconds=2)
            assert updated.last_reinforced_at <= after + timedelta(seconds=2)
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_multiple_reinforces_accumulate(self, config):
        """Multiple reinforcements should accumulate boost and confidence correctly."""
        engine = await create_engine(config)
        try:
            node_id = await _store_and_get_node_id(engine)
            original = await engine.get_node(node_id)
            assert original is not None

            # Reinforce 3 times
            for _ in range(3):
                await engine.reinforce(node_id)

            updated = await engine.get_node(node_id)
            assert updated is not None

            expected_boost = min(original.reinforcement_boost + 0.15 * 3, 0.5)
            expected_confidence = min(original.confidence_base + 0.05 * 3, 0.95)

            assert updated.reinforcement_boost == pytest.approx(
                expected_boost, abs=1e-6
            )
            assert updated.confidence_base == pytest.approx(
                expected_confidence, abs=1e-6
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_multiple_evidence_ids_accumulate(self, config):
        """Multiple reinforcements with evidence_ids should append all of them."""
        engine = await create_engine(config)
        try:
            node_id = await _store_and_get_node_id(engine)
            original = await engine.get_node(node_id)
            assert original is not None
            original_count = len(original.evidence_refs)

            ids = [str(uuid4()) for _ in range(3)]
            for eid in ids:
                await engine.reinforce(node_id, evidence_id=eid)

            updated = await engine.get_node(node_id)
            assert updated is not None
            assert len(updated.evidence_refs) == original_count + 3
            for eid in ids:
                assert UUID(eid) in updated.evidence_refs
        finally:
            await engine.close()
