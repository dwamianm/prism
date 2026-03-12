"""Tests for session context window expansion (Stage 5.5b).

Verifies that adjacent turns from the same session_id are included
when a node from that session is retrieved, addressing the "orphaned
question" problem in conversational data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.session_context import expand_session_context
from prme.types import LifecycleState, NodeType, Scope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(
    *,
    session_id: str | None = None,
    content: str = "test content",
    user_id: str = "user-1",
    created_at: datetime | None = None,
    node_id=None,
) -> MemoryNode:
    """Create a MemoryNode stub for testing."""
    ts = created_at or datetime.now(timezone.utc)
    return MemoryNode(
        id=node_id or uuid4(),
        user_id=user_id,
        session_id=session_id,
        node_type=NodeType.EVENT,
        content=content,
        confidence=0.8,
        salience=0.5,
        confidence_base=0.8,
        salience_base=0.5,
        lifecycle_state=LifecycleState.STABLE,
        scope=Scope.PERSONAL,
        created_at=ts,
        updated_at=ts,
        last_reinforced_at=ts,
    )


def _make_candidate(
    node: MemoryNode,
    composite_score: float = 0.5,
    paths: list[str] | None = None,
) -> RetrievalCandidate:
    """Create a RetrievalCandidate with a pre-set composite score."""
    return RetrievalCandidate(
        node=node,
        paths=paths or ["VECTOR"],
        path_count=len(paths) if paths else 1,
        semantic_score=0.5,
        lexical_score=0.0,
        graph_proximity=0.0,
        composite_score=composite_score,
    )


class FakeGraphStore:
    """Minimal fake GraphStore that returns pre-configured nodes for query_nodes."""

    def __init__(self, nodes: list[MemoryNode]):
        self._nodes = nodes

    async def query_nodes(self, **kwargs) -> list[MemoryNode]:
        user_id = kwargs.get("user_id")
        result = self._nodes
        if user_id is not None:
            result = [n for n in result if n.user_id == user_id]
        limit = kwargs.get("limit", 100)
        return result[:limit]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSessionContextExpansion:
    """Tests for expand_session_context."""

    @pytest.mark.asyncio
    async def test_adjacent_turns_included(self):
        """Retrieved node should pull in adjacent turns from the same session."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        # Create 7 session nodes (turns 0-6). We retrieve turn 3.
        session_nodes = []
        for i in range(7):
            session_nodes.append(
                _make_node(
                    session_id="s1",
                    content=f"turn {i}",
                    created_at=base_time + timedelta(minutes=i),
                )
            )

        # The retrieved candidate is turn 3.
        scored = [_make_candidate(session_nodes[3], composite_score=0.8)]

        graph_store = FakeGraphStore(session_nodes)
        config = PackingConfig(
            session_context_window=3,
            session_context_top_k=20,
            session_context_score_decay=0.85,
        )

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        # Should include turn 3 (original) + turns 0,1,2,4,5,6 (window=3).
        expanded_ids = {str(c.node.id) for c in expanded}
        for node in session_nodes:
            assert str(node.id) in expanded_ids, (
                f"Node {node.content} should be in expanded results"
            )

        # Original should have highest score.
        assert expanded[0].composite_score == 0.8
        assert str(expanded[0].node.id) == str(session_nodes[3].id)

    @pytest.mark.asyncio
    async def test_no_expansion_without_session_id(self):
        """Nodes without session_id should not trigger expansion."""
        node = _make_node(session_id=None, content="no session")
        scored = [_make_candidate(node, composite_score=0.8)]

        # Add some session nodes that should NOT be pulled in.
        other_nodes = [
            _make_node(session_id="s1", content="other turn"),
        ]
        graph_store = FakeGraphStore([node] + other_nodes)
        config = PackingConfig(session_context_window=3)

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        # Should remain just the original.
        assert len(expanded) == 1
        assert str(expanded[0].node.id) == str(node.id)

    @pytest.mark.asyncio
    async def test_deduplication(self):
        """Nodes already in scored results should not be duplicated."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        session_nodes = []
        for i in range(5):
            session_nodes.append(
                _make_node(
                    session_id="s1",
                    content=f"turn {i}",
                    created_at=base_time + timedelta(minutes=i),
                )
            )

        # Both turn 1 and turn 3 are already retrieved.
        scored = [
            _make_candidate(session_nodes[3], composite_score=0.8),
            _make_candidate(session_nodes[1], composite_score=0.6),
        ]

        graph_store = FakeGraphStore(session_nodes)
        config = PackingConfig(
            session_context_window=3,
            session_context_top_k=20,
            session_context_score_decay=0.85,
        )

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        # Count each node -- each should appear exactly once.
        id_counts: dict[str, int] = {}
        for c in expanded:
            nid = str(c.node.id)
            id_counts[nid] = id_counts.get(nid, 0) + 1

        for nid, count in id_counts.items():
            assert count == 1, f"Node {nid} appeared {count} times (expected 1)"

    @pytest.mark.asyncio
    async def test_window_size_respected(self):
        """Only nodes within the configured window should be included."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        # Create 10 session nodes (turns 0-9). Retrieve turn 5.
        session_nodes = []
        for i in range(10):
            session_nodes.append(
                _make_node(
                    session_id="s1",
                    content=f"turn {i}",
                    created_at=base_time + timedelta(minutes=i),
                )
            )

        scored = [_make_candidate(session_nodes[5], composite_score=0.8)]

        graph_store = FakeGraphStore(session_nodes)
        # Window of 2 means turns 3,4,5,6,7 (5 total).
        config = PackingConfig(
            session_context_window=2,
            session_context_top_k=20,
            session_context_score_decay=0.85,
        )

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        expanded_ids = {str(c.node.id) for c in expanded}

        # Turns 3,4,5,6,7 should be included (window=2 around position 5).
        for i in [3, 4, 5, 6, 7]:
            assert str(session_nodes[i].id) in expanded_ids, (
                f"Turn {i} should be in expanded results (within window)"
            )

        # Turns 0,1,2,8,9 should NOT be included.
        for i in [0, 1, 2, 8, 9]:
            assert str(session_nodes[i].id) not in expanded_ids, (
                f"Turn {i} should NOT be in expanded results (outside window)"
            )

    @pytest.mark.asyncio
    async def test_context_nodes_have_lower_score(self):
        """Expanded context nodes should score below their trigger."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        session_nodes = []
        for i in range(5):
            session_nodes.append(
                _make_node(
                    session_id="s1",
                    content=f"turn {i}",
                    created_at=base_time + timedelta(minutes=i),
                )
            )

        trigger_score = 0.8
        decay = 0.85
        scored = [_make_candidate(session_nodes[2], composite_score=trigger_score)]

        graph_store = FakeGraphStore(session_nodes)
        config = PackingConfig(
            session_context_window=3,
            session_context_top_k=20,
            session_context_score_decay=decay,
        )

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        trigger_id = str(session_nodes[2].id)
        expected_context_score = trigger_score * decay

        for c in expanded:
            if str(c.node.id) == trigger_id:
                assert c.composite_score == trigger_score
            else:
                assert c.composite_score == pytest.approx(expected_context_score), (
                    f"Context node {c.node.content} score {c.composite_score} "
                    f"!= expected {expected_context_score}"
                )

    @pytest.mark.asyncio
    async def test_context_nodes_marked_with_session_context_path(self):
        """Expanded nodes should have SESSION_CONTEXT in their paths."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        session_nodes = []
        for i in range(3):
            session_nodes.append(
                _make_node(
                    session_id="s1",
                    content=f"turn {i}",
                    created_at=base_time + timedelta(minutes=i),
                )
            )

        scored = [_make_candidate(session_nodes[1], composite_score=0.8)]

        graph_store = FakeGraphStore(session_nodes)
        config = PackingConfig(
            session_context_window=3,
            session_context_top_k=20,
        )

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        trigger_id = str(session_nodes[1].id)
        for c in expanded:
            if str(c.node.id) != trigger_id:
                assert "SESSION_CONTEXT" in c.paths, (
                    f"Context node {c.node.content} should have SESSION_CONTEXT path"
                )

    @pytest.mark.asyncio
    async def test_window_zero_disables_expansion(self):
        """Setting session_context_window=0 should disable expansion entirely."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        session_nodes = []
        for i in range(5):
            session_nodes.append(
                _make_node(
                    session_id="s1",
                    content=f"turn {i}",
                    created_at=base_time + timedelta(minutes=i),
                )
            )

        scored = [_make_candidate(session_nodes[2], composite_score=0.8)]

        graph_store = FakeGraphStore(session_nodes)
        config = PackingConfig(session_context_window=0)

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        assert len(expanded) == 1
        assert str(expanded[0].node.id) == str(session_nodes[2].id)

    @pytest.mark.asyncio
    async def test_top_k_limits_expansion(self):
        """Only the top session_context_top_k candidates should be expanded."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        # Session A: 3 nodes. Session B: 3 nodes.
        session_a_nodes = [
            _make_node(
                session_id="sa",
                content=f"sa-turn-{i}",
                created_at=base_time + timedelta(minutes=i),
            )
            for i in range(3)
        ]
        session_b_nodes = [
            _make_node(
                session_id="sb",
                content=f"sb-turn-{i}",
                created_at=base_time + timedelta(minutes=10 + i),
            )
            for i in range(3)
        ]

        all_nodes = session_a_nodes + session_b_nodes

        # Retrieve sa-turn-1 (high score) and sb-turn-1 (low score).
        # With top_k=1, only the highest-scored candidate gets expanded.
        scored = [
            _make_candidate(session_a_nodes[1], composite_score=0.9),
            _make_candidate(session_b_nodes[1], composite_score=0.3),
        ]

        graph_store = FakeGraphStore(all_nodes)
        config = PackingConfig(
            session_context_window=3,
            session_context_top_k=1,  # Only expand top-1
        )

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        expanded_ids = {str(c.node.id) for c in expanded}

        # Session A nodes (sa-turn-0, sa-turn-2) should be expanded.
        assert str(session_a_nodes[0].id) in expanded_ids
        assert str(session_a_nodes[2].id) in expanded_ids

        # Session B adjacent nodes (sb-turn-0, sb-turn-2) should NOT be
        # expanded because sb-turn-1 is outside top_k=1.
        assert str(session_b_nodes[0].id) not in expanded_ids
        assert str(session_b_nodes[2].id) not in expanded_ids

    @pytest.mark.asyncio
    async def test_multiple_sessions(self):
        """Expansion should work across multiple sessions independently."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        session_a = [
            _make_node(
                session_id="sa",
                content=f"sa-{i}",
                created_at=base_time + timedelta(minutes=i),
            )
            for i in range(3)
        ]
        session_b = [
            _make_node(
                session_id="sb",
                content=f"sb-{i}",
                created_at=base_time + timedelta(minutes=10 + i),
            )
            for i in range(3)
        ]

        scored = [
            _make_candidate(session_a[1], composite_score=0.8),
            _make_candidate(session_b[1], composite_score=0.7),
        ]

        graph_store = FakeGraphStore(session_a + session_b)
        config = PackingConfig(
            session_context_window=1,
            session_context_top_k=20,
        )

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        expanded_ids = {str(c.node.id) for c in expanded}

        # sa-0, sa-1, sa-2 should all be present (window=1 around sa-1).
        for n in session_a:
            assert str(n.id) in expanded_ids

        # sb-0, sb-1, sb-2 should all be present (window=1 around sb-1).
        for n in session_b:
            assert str(n.id) in expanded_ids

    @pytest.mark.asyncio
    async def test_edge_of_session_no_overflow(self):
        """Window at session edges should not cause index errors."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        session_nodes = [
            _make_node(
                session_id="s1",
                content=f"turn {i}",
                created_at=base_time + timedelta(minutes=i),
            )
            for i in range(5)
        ]

        # Retrieve the first node (index 0) -- window should not go negative.
        scored = [_make_candidate(session_nodes[0], composite_score=0.8)]

        graph_store = FakeGraphStore(session_nodes)
        config = PackingConfig(
            session_context_window=3,
            session_context_top_k=20,
        )

        expanded = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        expanded_ids = {str(c.node.id) for c in expanded}

        # Turns 0,1,2,3 should be included (0 + 3 forward).
        for i in [0, 1, 2, 3]:
            assert str(session_nodes[i].id) in expanded_ids

        # Turn 4 should NOT be included (beyond window).
        assert str(session_nodes[4].id) not in expanded_ids

    @pytest.mark.asyncio
    async def test_deterministic_ordering(self):
        """Expanded results should be in deterministic order."""
        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        session_nodes = [
            _make_node(
                session_id="s1",
                content=f"turn {i}",
                created_at=base_time + timedelta(minutes=i),
            )
            for i in range(5)
        ]

        scored = [_make_candidate(session_nodes[2], composite_score=0.8)]

        graph_store = FakeGraphStore(session_nodes)
        config = PackingConfig(
            session_context_window=3,
            session_context_top_k=20,
        )

        # Run twice to verify determinism.
        expanded1 = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )
        expanded2 = await expand_session_context(
            scored, graph_store, user_id="user-1", config=config,
        )

        ids1 = [str(c.node.id) for c in expanded1]
        ids2 = [str(c.node.id) for c in expanded2]
        assert ids1 == ids2

        # Trigger should be first (highest score).
        assert str(expanded1[0].node.id) == str(session_nodes[2].id)

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """Empty scored list should return empty."""
        graph_store = FakeGraphStore([])
        config = PackingConfig(session_context_window=3)

        expanded = await expand_session_context(
            [], graph_store, user_id="user-1", config=config,
        )

        assert expanded == []
