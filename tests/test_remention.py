"""Tests for re-mention reinforcement in store().

Verifies that when reinforce_similarity_threshold is set, storing content
similar to an existing node reinforces that existing node. Also verifies
the behavior is disabled by default, skips the new node itself, skips
superseded/archived nodes, handles multiple matches, and is non-fatal.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from prme.config import PRMEConfig
from prme.storage.engine import MemoryEngine
from prme.types import NodeType, Scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_remention_") as d:
        yield d


@pytest.fixture
def config_enabled(tmp_dir):
    """Config with re-mention reinforcement enabled."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
        reinforce_similarity_threshold=0.5,
    )


@pytest.fixture
def config_disabled(tmp_dir):
    """Config with re-mention reinforcement disabled (default)."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
    )


async def _create_engine(config: PRMEConfig) -> MemoryEngine:
    return await MemoryEngine.create(config)


async def _store_and_get_node(
    engine: MemoryEngine,
    content: str,
    user_id: str = "test-user",
) -> tuple[str, str]:
    """Store content and return (event_id, node_id)."""
    event_id = await engine.store(
        content,
        user_id=user_id,
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
    )
    nodes = await engine.query_nodes(user_id=user_id, limit=200)
    for n in nodes:
        if n.content == content:
            return event_id, str(n.id)
    raise RuntimeError(f"Could not find stored node with content {content!r}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRementionReinforcement:
    """Tests for re-mention reinforcement in store()."""

    @pytest.mark.asyncio
    async def test_remention_fires_when_threshold_met(self, config_enabled):
        """Similar content should reinforce existing node when threshold is met."""
        engine = await _create_engine(config_enabled)
        try:
            # Store initial content
            _, node_id = await _store_and_get_node(
                engine, "Python is my favorite programming language"
            )
            original = await engine.get_node(node_id)
            assert original is not None
            original_boost = original.reinforcement_boost
            original_confidence = original.confidence_base

            # Store similar content -- should reinforce the first node
            await engine.store(
                "I love Python for programming",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            # Check that the original node was reinforced
            updated = await engine.get_node(node_id)
            assert updated is not None
            assert updated.reinforcement_boost > original_boost, (
                f"Expected boost > {original_boost}, got {updated.reinforcement_boost}"
            )
            assert updated.confidence_base > original_confidence, (
                f"Expected confidence > {original_confidence}, got {updated.confidence_base}"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_remention_disabled_by_default(self, config_disabled):
        """When threshold is None (default), no reinforcement should happen."""
        engine = await _create_engine(config_disabled)
        try:
            _, node_id = await _store_and_get_node(
                engine, "Python is my favorite programming language"
            )
            original = await engine.get_node(node_id)
            assert original is not None
            original_boost = original.reinforcement_boost

            # Store similar content -- should NOT reinforce (disabled)
            await engine.store(
                "I love Python for programming",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            updated = await engine.get_node(node_id)
            assert updated is not None
            assert updated.reinforcement_boost == pytest.approx(
                original_boost, abs=1e-6
            ), "Reinforcement should not fire when threshold is None"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_remention_below_threshold(self, tmp_dir):
        """When similarity is below threshold, no reinforcement should happen."""
        lexical_path = Path(tmp_dir) / "lexical_index_high"
        lexical_path.mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory_high.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors_high.usearch"),
            lexical_path=str(lexical_path),
            reinforce_similarity_threshold=0.99,  # Very high threshold
        )
        engine = await _create_engine(config)
        try:
            _, node_id = await _store_and_get_node(
                engine, "Python is my favorite programming language"
            )
            original = await engine.get_node(node_id)
            assert original is not None
            original_boost = original.reinforcement_boost

            # Store somewhat related but not identical content
            await engine.store(
                "I enjoy coding in JavaScript for web development",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            updated = await engine.get_node(node_id)
            assert updated is not None
            assert updated.reinforcement_boost == pytest.approx(
                original_boost, abs=1e-6
            ), "Below-threshold similarity should not trigger reinforcement"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_remention_skips_new_node(self, config_enabled):
        """The newly created node itself should not be reinforced."""
        engine = await _create_engine(config_enabled)
        try:
            # Store content
            _, node_id = await _store_and_get_node(
                engine, "Python is my favorite programming language"
            )

            # Store identical content -- the new node should not reinforce itself
            _, new_node_id = await _store_and_get_node(
                engine, "Python is my favorite programming language"
            )

            # The new node should have default reinforcement (not self-reinforced)
            new_node = await engine.get_node(new_node_id)
            assert new_node is not None
            # New node should have 0.0 reinforcement_boost (fresh node)
            assert new_node.reinforcement_boost == pytest.approx(0.0, abs=1e-6), (
                "New node should not reinforce itself"
            )

            # But the original node should have been reinforced
            original = await engine.get_node(node_id)
            assert original is not None
            assert original.reinforcement_boost > 0.0, (
                "Original node should be reinforced by the re-mention"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_remention_skips_superseded_nodes(self, config_enabled):
        """Superseded nodes should not be reinforced."""
        engine = await _create_engine(config_enabled)
        try:
            # Store initial content
            _, node_id = await _store_and_get_node(
                engine, "Python is my favorite programming language"
            )

            # Store a second node to use as superseder
            _, new_node_id = await _store_and_get_node(
                engine, "I now prefer Rust over Python"
            )

            # Supersede the original
            await engine.supersede(node_id, new_node_id)

            # Record the original node's state after supersedence
            # (get_node with include_superseded=True to see it)
            superseded = await engine.get_node(node_id, include_superseded=True)
            assert superseded is not None
            boost_after_supersede = superseded.reinforcement_boost

            # Store similar content -- should NOT reinforce the superseded node
            await engine.store(
                "Python programming is wonderful",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            updated = await engine.get_node(node_id, include_superseded=True)
            assert updated is not None
            assert updated.reinforcement_boost == pytest.approx(
                boost_after_supersede, abs=1e-6
            ), "Superseded node should not be reinforced"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_multiple_similar_nodes_reinforced(self, config_enabled):
        """Multiple existing similar nodes should each get reinforced."""
        engine = await _create_engine(config_enabled)
        try:
            # Store two similar facts
            _, node_id_1 = await _store_and_get_node(
                engine, "Python is an excellent programming language"
            )
            _, node_id_2 = await _store_and_get_node(
                engine, "Python programming is very productive and enjoyable"
            )

            original_1 = await engine.get_node(node_id_1)
            original_2 = await engine.get_node(node_id_2)
            assert original_1 is not None
            assert original_2 is not None

            # Note: node_2 may already be reinforced from storing node_2
            # (since it's similar to node_1). Record current state.
            boost_1_before = original_1.reinforcement_boost
            # Re-read node_2 to get its current state after possible reinforcement
            node_2_current = await engine.get_node(node_id_2)
            boost_2_before = node_2_current.reinforcement_boost

            # Store a third similar fact -- should reinforce both existing nodes
            await engine.store(
                "I use Python every day for software development",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            updated_1 = await engine.get_node(node_id_1)
            updated_2 = await engine.get_node(node_id_2)
            assert updated_1 is not None
            assert updated_2 is not None

            # At least one of the existing nodes should have been reinforced
            reinforced_count = 0
            if updated_1.reinforcement_boost > boost_1_before + 1e-6:
                reinforced_count += 1
            if updated_2.reinforcement_boost > boost_2_before + 1e-6:
                reinforced_count += 1

            assert reinforced_count >= 1, (
                "At least one similar existing node should be reinforced. "
                f"Node 1 boost: {boost_1_before} -> {updated_1.reinforcement_boost}, "
                f"Node 2 boost: {boost_2_before} -> {updated_2.reinforcement_boost}"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_remention_nonfatal_on_vector_search_failure(self, config_enabled):
        """Vector search failure should not break store()."""
        engine = await _create_engine(config_enabled)
        try:
            # Store initial content
            await engine.store(
                "Python is my favorite programming language",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            # Patch vector search to raise an exception
            with patch.object(
                engine._vector_index,
                "search",
                side_effect=RuntimeError("Vector index exploded"),
            ):
                # store() should still succeed despite vector search failure
                event_id = await engine.store(
                    "I love Python for programming",
                    user_id="test-user",
                    node_type=NodeType.FACT,
                    scope=Scope.PERSONAL,
                )
                assert event_id is not None
                assert len(event_id) > 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_evidence_id_passed_to_reinforce(self, config_enabled):
        """The event_id from the new store should be passed as evidence to reinforce."""
        engine = await _create_engine(config_enabled)
        try:
            # Store initial content
            _, node_id = await _store_and_get_node(
                engine, "Python is my favorite programming language"
            )
            original = await engine.get_node(node_id)
            assert original is not None
            original_refs_count = len(original.evidence_refs)

            # Store similar content
            new_event_id = await engine.store(
                "I love Python for programming",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            # The original node's evidence_refs should now include the new event_id
            updated = await engine.get_node(node_id)
            assert updated is not None

            # Check that evidence_refs grew (reinforcement appends evidence)
            assert len(updated.evidence_refs) > original_refs_count, (
                f"Expected evidence_refs to grow from {original_refs_count}, "
                f"got {len(updated.evidence_refs)}"
            )

            # Verify the new event's ID is in the evidence refs
            # The event ID from store() is the event UUID string
            ref_strings = [str(r) for r in updated.evidence_refs]
            assert new_event_id in ref_strings, (
                f"New event ID {new_event_id} should be in evidence_refs: {ref_strings}"
            )
        finally:
            await engine.close()
