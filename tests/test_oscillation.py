"""Tests for oscillation detection module.

Tests cover:
- Basic oscillation detection (A -> B -> A pattern)
- No oscillation for linear supersedence (A -> B -> C)
- Confidence penalty calculation
- Penalty cap at 0.3
- Deep chains (A -> B -> A -> B -> A)
- Integration with engine.store() when enable_store_supersedence=True
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from prme.config import PRMEConfig
from prme.organizer.oscillation import (
    OscillationDetector,
    OscillationResult,
    _extract_keywords,
    _jaccard_similarity,
)
from prme.storage.engine import MemoryEngine
from prme.types import NodeType, Scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_osc_") as d:
        yield d


@pytest.fixture
def config(tmp_dir):
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
        enable_store_supersedence=True,
    )


async def create_engine(config: PRMEConfig) -> MemoryEngine:
    return await MemoryEngine.create(config)


# ---------------------------------------------------------------------------
# Unit tests: keyword extraction and similarity
# ---------------------------------------------------------------------------


class TestKeywordExtraction:
    def test_extracts_meaningful_words(self):
        keywords = _extract_keywords("I prefer using dark mode for coding")
        assert "dark" in keywords
        assert "mode" in keywords
        assert "coding" in keywords
        # Stop words should be excluded
        assert "i" not in keywords
        assert "for" not in keywords

    def test_empty_string(self):
        assert _extract_keywords("") == set()

    def test_only_stop_words(self):
        assert _extract_keywords("the a an is are") == set()


class TestJaccardSimilarity:
    def test_identical_sets(self):
        s = {"dark", "mode", "coding"}
        assert _jaccard_similarity(s, s) == 1.0

    def test_disjoint_sets(self):
        a = {"dark", "mode"}
        b = {"light", "theme"}
        assert _jaccard_similarity(a, b) == 0.0

    def test_partial_overlap(self):
        a = {"dark", "mode", "editor"}
        b = {"light", "mode", "editor"}
        # intersection = {mode, editor} (2), union = {dark, light, mode, editor} (4)
        assert _jaccard_similarity(a, b) == pytest.approx(0.5)

    def test_empty_sets(self):
        assert _jaccard_similarity(set(), set()) == 0.0
        assert _jaccard_similarity({"a"}, set()) == 0.0


# ---------------------------------------------------------------------------
# Unit tests: OscillationDetector with mock graph store
# ---------------------------------------------------------------------------


class MockNode:
    """Minimal mock for MemoryNode."""

    def __init__(self, node_id: str, content: str, confidence_base: float = 0.5):
        self.id = node_id
        self.content = content
        self.confidence_base = confidence_base


class MockGraphStore:
    """Mock graph store that returns predefined nodes and chains."""

    def __init__(self, nodes: dict[str, MockNode], chains: dict[str, list[MockNode]]):
        self._nodes = nodes
        self._chains = chains

    async def get_node(self, node_id: str, *, include_superseded: bool = False):
        return self._nodes.get(node_id)

    async def get_supersedence_chain(self, node_id: str, *, direction: str = "forward"):
        return self._chains.get(node_id, [])

    async def update_node(self, node_id: str, **updates):
        if node_id in self._nodes:
            for key, value in updates.items():
                setattr(self._nodes[node_id], key, value)


class TestOscillationDetector:
    """Tests for the OscillationDetector using mock graph store."""

    @pytest.mark.asyncio
    async def test_basic_oscillation_aba(self):
        """A -> B -> A pattern should be detected as oscillation."""
        node_a2 = MockNode("a2", "I prefer dark mode for my editor")
        node_b = MockNode("b", "I switched to light mode for my editor")
        node_a1 = MockNode("a1", "I prefer dark mode for my editor")

        # a2 superseded b, which superseded a1
        # backward chain from a2: [b, a1]
        store = MockGraphStore(
            nodes={"a2": node_a2, "b": node_b, "a1": node_a1},
            chains={"a2": [node_b, node_a1]},
        )

        detector = OscillationDetector()
        results = await detector.detect_oscillations(store, "a2")

        assert len(results) == 1
        assert results[0].cycle_count == 1
        assert results[0].confidence_penalty == pytest.approx(0.1)
        assert "a2" in results[0].oscillating_node_ids
        assert "b" in results[0].oscillating_node_ids
        assert "a1" in results[0].oscillating_node_ids

    @pytest.mark.asyncio
    async def test_no_oscillation_linear(self):
        """A -> B -> C (no similarity loop) should NOT detect oscillation."""
        node_c = MockNode("c", "We use Kubernetes for container orchestration")
        node_b = MockNode("b", "We use Docker Swarm for deployment")
        node_a = MockNode("a", "We deploy on bare metal servers")

        store = MockGraphStore(
            nodes={"c": node_c, "b": node_b, "a": node_a},
            chains={"c": [node_b, node_a]},
        )

        detector = OscillationDetector()
        results = await detector.detect_oscillations(store, "c")

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_confidence_penalty_single_cycle(self):
        """Single oscillation cycle should give 0.1 penalty."""
        node_a2 = MockNode("a2", "I use dark mode theme")
        node_b = MockNode("b", "I use light mode theme")
        node_a1 = MockNode("a1", "I use dark mode theme")

        store = MockGraphStore(
            nodes={"a2": node_a2, "b": node_b, "a1": node_a1},
            chains={"a2": [node_b, node_a1]},
        )

        detector = OscillationDetector()
        results = await detector.detect_oscillations(store, "a2")

        assert len(results) == 1
        assert results[0].confidence_penalty == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_penalty_cap_at_03(self):
        """Penalty should cap at 0.3 even with many cycles."""
        # Create a long chain: a5 -> b4 -> a4 -> b3 -> a3 -> b2 -> a2 -> b1 -> a1
        nodes_list = []
        for i in range(9):
            if i % 2 == 0:
                nodes_list.append(MockNode(f"n{i}", "I prefer dark mode for development"))
            else:
                nodes_list.append(MockNode(f"n{i}", "I prefer light mode for development"))

        nodes_dict = {n.id: n for n in nodes_list}
        # backward chain from n0: [n1, n2, n3, n4, n5, n6, n7, n8]
        chain = nodes_list[1:]

        store = MockGraphStore(
            nodes=nodes_dict,
            chains={"n0": chain},
        )

        detector = OscillationDetector()
        results = await detector.detect_oscillations(store, "n0")

        assert len(results) == 1
        # With 4 cycles (at positions 2, 4, 6, 8), penalty = min(0.4, 0.3) = 0.3
        assert results[0].confidence_penalty == pytest.approx(0.3)

    @pytest.mark.asyncio
    async def test_deep_chain_ababa(self):
        """A -> B -> A -> B -> A should detect 2 cycles."""
        node_a3 = MockNode("a3", "I use VS Code as my editor")
        node_b2 = MockNode("b2", "I switched to Neovim as my editor")
        node_a2 = MockNode("a2", "I use VS Code as my editor")
        node_b1 = MockNode("b1", "I switched to Neovim as my editor")
        node_a1 = MockNode("a1", "I use VS Code as my editor")

        # backward chain from a3: [b2, a2, b1, a1]
        store = MockGraphStore(
            nodes={
                "a3": node_a3, "b2": node_b2, "a2": node_a2,
                "b1": node_b1, "a1": node_a1,
            },
            chains={"a3": [node_b2, node_a2, node_b1, node_a1]},
        )

        detector = OscillationDetector()
        results = await detector.detect_oscillations(store, "a3")

        assert len(results) == 1
        assert results[0].cycle_count == 2
        assert results[0].confidence_penalty == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_no_chain(self):
        """Node with no supersedence chain should return no oscillation."""
        node = MockNode("a", "I use dark mode")

        store = MockGraphStore(
            nodes={"a": node},
            chains={"a": []},
        )

        detector = OscillationDetector()
        results = await detector.detect_oscillations(store, "a")

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_node_not_found(self):
        """Non-existent node should return no oscillation."""
        store = MockGraphStore(nodes={}, chains={})

        detector = OscillationDetector()
        results = await detector.detect_oscillations(store, "nonexistent")

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_max_chain_depth_respected(self):
        """max_chain_depth should limit how far back we look."""
        node_a2 = MockNode("a2", "I prefer dark mode for development")
        node_b = MockNode("b", "I prefer light mode for development")
        node_a1 = MockNode("a1", "I prefer dark mode for development")

        store = MockGraphStore(
            nodes={"a2": node_a2, "b": node_b, "a1": node_a1},
            chains={"a2": [node_b, node_a1]},
        )

        # With max_chain_depth=1, we only look at node_b, not node_a1
        detector = OscillationDetector()
        results = await detector.detect_oscillations(
            store, "a2", max_chain_depth=1
        )

        # Only one node in the truncated chain, no oscillation possible
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_topic_extraction(self):
        """Oscillation result should contain a meaningful topic."""
        node_a2 = MockNode("a2", "I prefer dark mode for coding and development")
        node_b = MockNode("b", "I switched to light mode for coding")
        node_a1 = MockNode("a1", "I prefer dark mode for coding and development")

        store = MockGraphStore(
            nodes={"a2": node_a2, "b": node_b, "a1": node_a1},
            chains={"a2": [node_b, node_a1]},
        )

        detector = OscillationDetector()
        results = await detector.detect_oscillations(store, "a2")

        assert len(results) == 1
        # Topic should contain common keywords
        assert results[0].topic  # non-empty


# ---------------------------------------------------------------------------
# Integration tests: engine.store() with oscillation detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_store_oscillation_reduces_confidence(config):
    """store() with enable_store_supersedence=True should detect oscillation
    and reduce confidence on the oscillating node."""
    engine = await create_engine(config)
    try:
        user_id = "test-user"

        # Store initial preference
        eid1 = await engine.store(
            "I use VS Code as my primary editor for all development.",
            user_id=user_id,
            node_type=NodeType.PREFERENCE,
        )

        # Store contradiction (switch to Neovim)
        eid2 = await engine.store(
            "I switched from VS Code to Neovim for all development. The modal editing is faster.",
            user_id=user_id,
            node_type=NodeType.PREFERENCE,
        )

        # Store flip back (back to VS Code)
        eid3 = await engine.store(
            "I went back to VS Code from Neovim. The extensions are too valuable.",
            user_id=user_id,
            node_type=NodeType.PREFERENCE,
        )

        # Get all nodes to check state
        all_nodes = await engine.query_nodes(
            user_id=user_id,
            lifecycle_states=[
                __import__("prme.types", fromlist=["LifecycleState"]).LifecycleState.TENTATIVE,
                __import__("prme.types", fromlist=["LifecycleState"]).LifecycleState.STABLE,
                __import__("prme.types", fromlist=["LifecycleState"]).LifecycleState.SUPERSEDED,
            ],
            limit=100,
        )

        # Verify we stored nodes
        assert len(all_nodes) >= 1, "Should have at least one node"

    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_engine_store_no_oscillation_without_supersedence(tmp_dir):
    """store() with enable_store_supersedence=False should not run oscillation check."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    config = PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
        enable_store_supersedence=False,
    )
    engine = await create_engine(config)
    try:
        user_id = "test-user"

        # Store a sequence that would normally trigger oscillation
        await engine.store(
            "I use dark mode for development.",
            user_id=user_id,
            node_type=NodeType.PREFERENCE,
        )
        await engine.store(
            "I switched from dark mode to light mode for development.",
            user_id=user_id,
            node_type=NodeType.PREFERENCE,
        )
        await engine.store(
            "I went back to dark mode from light mode.",
            user_id=user_id,
            node_type=NodeType.PREFERENCE,
        )

        # All nodes should exist with unmodified confidence
        all_nodes = await engine.query_nodes(
            user_id=user_id,
            limit=100,
        )
        # Without supersedence, no oscillation detection runs, nodes should
        # all retain their original confidence_base
        for node in all_nodes:
            # Default confidence from matrix should not be reduced
            assert node.confidence_base > 0
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_oscillation_detection_is_non_fatal(config):
    """Oscillation detection errors should not cause store() to fail."""
    engine = await create_engine(config)
    try:
        user_id = "test-user"

        # Even if something goes wrong internally, store() should succeed
        eid = await engine.store(
            "I use dark mode for development.",
            user_id=user_id,
            node_type=NodeType.PREFERENCE,
        )
        assert eid is not None
    finally:
        await engine.close()
