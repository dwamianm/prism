"""Integration tests for contradiction modeling (Phase 03.3).

Tests cover the full contradiction flow: detection at ingestion,
lifecycle transitions, graph operations (contradict/resolve_contradiction),
SupersedenceDetector temporal_intent branching, retrieval classification
of CONTESTED nodes, and lifecycle transition validation.

Uses real DuckDB + DuckPGQGraphStore (no mocks for graph store).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import duckdb
import pytest
import pytest_asyncio

from prme.ingestion.graph_writer import WriteQueueGraphWriter
from prme.ingestion.supersedence import SupersedenceDetector
from prme.models.nodes import MemoryNode
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import classify_into_sections
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.schema import create_schema
from prme.storage.write_queue import WriteQueue, WriteTracker
from prme.types import (
    ALLOWED_TRANSITIONS,
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    SourceType,
    validate_transition,
)

from prme.models.edges import MemoryEdge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def graph_store_and_conn(tmp_path):
    """Create isolated DuckDB, initialize schema, yield (graph_store, conn).

    Uses file-backed DuckDB in tmp_path for test isolation (issue #19).
    """
    db_path = str(tmp_path / "test.duckdb")
    conn = duckdb.connect(db_path)
    create_schema(conn)
    conn_lock = asyncio.Lock()
    graph_store = DuckPGQGraphStore(conn, conn_lock)
    yield graph_store, conn
    conn.close()


def _make_fact_node(
    *,
    user_id: str = "user1",
    content: str = "test fact",
    predicate: str = "works_at",
    obj: str = "Google",
    lifecycle_state: LifecycleState = LifecycleState.STABLE,
    node_type: NodeType = NodeType.FACT,
) -> MemoryNode:
    """Create a fact MemoryNode with predicate metadata."""
    return MemoryNode(
        node_type=node_type,
        user_id=user_id,
        content=content,
        scope=Scope.PERSONAL,
        lifecycle_state=lifecycle_state,
        epistemic_type=EpistemicType.ASSERTED,
        source_type=SourceType.USER_STATED,
        metadata={"predicate": predicate, "object": obj, "subject": "Alice"},
    )


def _make_entity_node(
    *,
    user_id: str = "user1",
    content: str = "Alice",
) -> MemoryNode:
    """Create an entity MemoryNode."""
    return MemoryNode(
        node_type=NodeType.ENTITY,
        user_id=user_id,
        content=content,
        scope=Scope.PERSONAL,
        lifecycle_state=LifecycleState.STABLE,
        epistemic_type=EpistemicType.OBSERVED,
        source_type=SourceType.USER_STATED,
    )


# ---------------------------------------------------------------------------
# Test 1: contradict() creates edge and transitions both nodes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contradict_creates_edge_and_transitions_both_nodes(
    graph_store_and_conn,
):
    graph_store, conn = graph_store_and_conn

    # Create two STABLE fact nodes with same subject, same predicate, different object
    node_a = _make_fact_node(content="Alice works at Google", obj="Google")
    node_b = _make_fact_node(content="Alice works at Meta", obj="Meta")
    await graph_store.create_node(node_a)
    await graph_store.create_node(node_b)

    event_id = str(uuid4())
    await graph_store.contradict(
        str(node_a.id), str(node_b.id), evidence_id=event_id
    )

    # Both nodes should now be CONTESTED
    refreshed_a = await graph_store.get_node(str(node_a.id), include_superseded=True)
    refreshed_b = await graph_store.get_node(str(node_b.id), include_superseded=True)
    assert refreshed_a is not None
    assert refreshed_b is not None
    assert refreshed_a.lifecycle_state == LifecycleState.CONTESTED
    assert refreshed_b.lifecycle_state == LifecycleState.CONTESTED

    # CONTRADICTS edge should exist from node_b to node_a
    edges = await graph_store.get_edges(
        source_id=str(node_b.id), edge_type=EdgeType.CONTRADICTS
    )
    assert len(edges) == 1
    assert edges[0].target_id == node_a.id
    assert edges[0].source_id == node_b.id

    # CONTRADICTION_NOTED operation should be logged
    ops = conn.execute(
        "SELECT op_type, target_id FROM operations WHERE op_type = 'CONTRADICTION_NOTED'"
    ).fetchall()
    assert len(ops) >= 1
    assert ops[0][0] == "CONTRADICTION_NOTED"


# ---------------------------------------------------------------------------
# Test 2: contradict() rejects non-active nodes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contradict_rejects_non_active_nodes(graph_store_and_conn):
    graph_store, conn = graph_store_and_conn

    # Create a SUPERSEDED node and a STABLE node
    node_a = _make_fact_node(
        content="Alice works at Google",
        obj="Google",
        lifecycle_state=LifecycleState.STABLE,
    )
    node_b = _make_fact_node(
        content="Alice works at Meta",
        obj="Meta",
        lifecycle_state=LifecycleState.STABLE,
    )
    await graph_store.create_node(node_a)
    await graph_store.create_node(node_b)

    # Manually transition node_a to SUPERSEDED
    conn.execute(
        "UPDATE nodes SET lifecycle_state = 'superseded' WHERE id = ?",
        [str(node_a.id)],
    )

    # contradict() should raise ValueError
    with pytest.raises(ValueError, match="does not allow transition to CONTESTED"):
        await graph_store.contradict(str(node_a.id), str(node_b.id))


# ---------------------------------------------------------------------------
# Test 3: resolve_contradiction() transitions winner and loser
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_contradiction_transitions_winner_and_loser(
    graph_store_and_conn,
):
    graph_store, conn = graph_store_and_conn

    # Create two nodes and contradict them
    node_a = _make_fact_node(content="Alice works at Google", obj="Google")
    node_b = _make_fact_node(content="Alice works at Meta", obj="Meta")
    await graph_store.create_node(node_a)
    await graph_store.create_node(node_b)
    await graph_store.contradict(str(node_a.id), str(node_b.id))

    # Resolve: node_b wins, node_a loses
    await graph_store.resolve_contradiction(
        str(node_b.id), str(node_a.id), resolver_actor_id="user1"
    )

    # Winner should be STABLE, loser should be DEPRECATED
    winner = await graph_store.get_node(str(node_b.id), include_superseded=True)
    loser = await graph_store.get_node(str(node_a.id), include_superseded=True)
    assert winner is not None
    assert loser is not None
    assert winner.lifecycle_state == LifecycleState.STABLE
    assert loser.lifecycle_state == LifecycleState.DEPRECATED

    # CONTRADICTION_RESOLVED operation should be logged
    resolved_ops = conn.execute(
        "SELECT op_type FROM operations WHERE op_type = 'CONTRADICTION_RESOLVED'"
    ).fetchall()
    assert len(resolved_ops) >= 1

    # EPISTEMIC_TRANSITION operations should be logged for both
    transition_ops = conn.execute(
        "SELECT op_type, target_id FROM operations WHERE op_type = 'EPISTEMIC_TRANSITION'"
    ).fetchall()
    assert len(transition_ops) >= 2
    target_ids = {row[1] for row in transition_ops}
    assert str(node_a.id) in target_ids
    assert str(node_b.id) in target_ids


# ---------------------------------------------------------------------------
# Test 4: resolve_contradiction() rejects non-contested nodes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_contradiction_rejects_non_contested_nodes(
    graph_store_and_conn,
):
    graph_store, conn = graph_store_and_conn

    # Create two STABLE nodes (not contested)
    node_a = _make_fact_node(content="Alice works at Google", obj="Google")
    node_b = _make_fact_node(content="Alice works at Meta", obj="Meta")
    await graph_store.create_node(node_a)
    await graph_store.create_node(node_b)

    # resolve_contradiction() should raise ValueError
    with pytest.raises(ValueError, match="not CONTESTED"):
        await graph_store.resolve_contradiction(
            str(node_a.id), str(node_b.id)
        )


# ---------------------------------------------------------------------------
# Test 5: SupersedenceDetector branches on temporal_intent="assertion"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supersedence_detector_branches_on_temporal_intent(
    graph_store_and_conn,
):
    graph_store, conn = graph_store_and_conn

    # Set up WriteQueue + WriteQueueGraphWriter
    write_queue = WriteQueue()
    await write_queue.start()
    try:
        tracker = WriteTracker()
        graph_writer = WriteQueueGraphWriter(graph_store, write_queue, tracker)

        # Create entity node
        entity = _make_entity_node()
        await graph_store.create_node(entity)

        # Create existing fact node (works_at=Google)
        existing_fact = _make_fact_node(
            content="Alice works at Google", predicate="works_at", obj="Google"
        )
        await graph_store.create_node(existing_fact)

        # Create HAS_FACT edge from entity to existing fact
        has_fact_edge = MemoryEdge(
            source_id=entity.id,
            target_id=existing_fact.id,
            edge_type=EdgeType.HAS_FACT,
            user_id="user1",
        )
        await graph_store.create_edge(has_fact_edge)

        # Create new fact node (works_at=Meta)
        new_fact = _make_fact_node(
            content="Alice works at Meta", predicate="works_at", obj="Meta"
        )
        await graph_store.create_node(new_fact)

        # Detect with temporal_intent="assertion"
        detector = SupersedenceDetector(graph_store, graph_writer)
        result = await detector.detect_and_supersede(
            new_fact_node_id=str(new_fact.id),
            subject_entity_id=str(entity.id),
            predicate="works_at",
            object_value="Meta",
            user_id="user1",
            temporal_intent="assertion",
        )

        # Result should contain the existing fact ID
        assert str(existing_fact.id) in result

        # Existing fact should now be CONTESTED (not SUPERSEDED)
        refreshed_existing = await graph_store.get_node(
            str(existing_fact.id), include_superseded=True
        )
        assert refreshed_existing is not None
        assert refreshed_existing.lifecycle_state == LifecycleState.CONTESTED

        # New fact should also be CONTESTED
        refreshed_new = await graph_store.get_node(
            str(new_fact.id), include_superseded=True
        )
        assert refreshed_new is not None
        assert refreshed_new.lifecycle_state == LifecycleState.CONTESTED

        # CONTRADICTS edge should exist (not SUPERSEDES)
        contradicts_edges = await graph_store.get_edges(
            edge_type=EdgeType.CONTRADICTS
        )
        assert len(contradicts_edges) >= 1

        supersedes_edges = await graph_store.get_edges(
            edge_type=EdgeType.SUPERSEDES
        )
        assert len(supersedes_edges) == 0
    finally:
        await write_queue.stop()


# ---------------------------------------------------------------------------
# Test 6: SupersedenceDetector defaults to supersedence (None temporal_intent)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supersedence_detector_defaults_to_supersedence(
    graph_store_and_conn,
):
    graph_store, conn = graph_store_and_conn

    # Set up WriteQueue + WriteQueueGraphWriter
    write_queue = WriteQueue()
    await write_queue.start()
    try:
        tracker = WriteTracker()
        graph_writer = WriteQueueGraphWriter(graph_store, write_queue, tracker)

        # Create entity node
        entity = _make_entity_node()
        await graph_store.create_node(entity)

        # Create existing fact node (works_at=Google)
        existing_fact = _make_fact_node(
            content="Alice works at Google", predicate="works_at", obj="Google"
        )
        await graph_store.create_node(existing_fact)

        # Create HAS_FACT edge from entity to existing fact
        has_fact_edge = MemoryEdge(
            source_id=entity.id,
            target_id=existing_fact.id,
            edge_type=EdgeType.HAS_FACT,
            user_id="user1",
        )
        await graph_store.create_edge(has_fact_edge)

        # Create new fact node (works_at=Meta)
        new_fact = _make_fact_node(
            content="Alice works at Meta", predicate="works_at", obj="Meta"
        )
        await graph_store.create_node(new_fact)

        # Detect with temporal_intent=None (default)
        detector = SupersedenceDetector(graph_store, graph_writer)
        result = await detector.detect_and_supersede(
            new_fact_node_id=str(new_fact.id),
            subject_entity_id=str(entity.id),
            predicate="works_at",
            object_value="Meta",
            user_id="user1",
            temporal_intent=None,
        )

        # Result should contain the existing fact ID
        assert str(existing_fact.id) in result

        # Existing fact should be SUPERSEDED (original behavior preserved)
        refreshed_existing = await graph_store.get_node(
            str(existing_fact.id), include_superseded=True
        )
        assert refreshed_existing is not None
        assert refreshed_existing.lifecycle_state == LifecycleState.SUPERSEDED

        # SUPERSEDES edge should exist (not CONTRADICTS)
        supersedes_edges = await graph_store.get_edges(
            edge_type=EdgeType.SUPERSEDES
        )
        assert len(supersedes_edges) >= 1

        contradicts_edges = await graph_store.get_edges(
            edge_type=EdgeType.CONTRADICTS
        )
        assert len(contradicts_edges) == 0
    finally:
        await write_queue.stop()


# ---------------------------------------------------------------------------
# Test 7: CONTESTED node classified as contested_claims
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contested_node_classified_as_contested_claims(
    graph_store_and_conn,
):
    graph_store, conn = graph_store_and_conn

    # Create a CONTESTED MemoryNode
    contested_node = _make_fact_node(
        content="Alice works at Google",
        obj="Google",
        lifecycle_state=LifecycleState.CONTESTED,
    )
    candidate = RetrievalCandidate(node=contested_node)
    assert classify_into_sections(candidate) == "contested_claims"

    # Create a STABLE fact node -- should go to stable_facts (unchanged)
    stable_node = _make_fact_node(
        content="Alice works at Meta",
        obj="Meta",
        lifecycle_state=LifecycleState.STABLE,
    )
    stable_candidate = RetrievalCandidate(node=stable_node)
    assert classify_into_sections(stable_candidate) == "stable_facts"


# ---------------------------------------------------------------------------
# Test 8: CONTESTED node included in default query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_contested_node_included_in_default_query(graph_store_and_conn):
    graph_store, conn = graph_store_and_conn

    # Create a CONTESTED node in the graph store
    contested_node = _make_fact_node(
        content="Alice works at Google",
        obj="Google",
        lifecycle_state=LifecycleState.CONTESTED,
    )
    await graph_store.create_node(contested_node)

    # query_nodes with default lifecycle filter should include CONTESTED
    results = await graph_store.query_nodes(user_id="user1")
    result_ids = [str(n.id) for n in results]
    assert str(contested_node.id) in result_ids


# ---------------------------------------------------------------------------
# Test 9: Lifecycle transition validation for CONTESTED/DEPRECATED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifecycle_transitions_contested_deprecated(graph_store_and_conn):
    """Verify CONTESTED and DEPRECATED transition rules per types.py."""
    # TENTATIVE -> CONTESTED: allowed
    assert validate_transition(LifecycleState.TENTATIVE, LifecycleState.CONTESTED) is True
    # STABLE -> CONTESTED: allowed
    assert validate_transition(LifecycleState.STABLE, LifecycleState.CONTESTED) is True
    # CONTESTED -> STABLE: allowed (winner)
    assert validate_transition(LifecycleState.CONTESTED, LifecycleState.STABLE) is True
    # CONTESTED -> DEPRECATED: allowed (loser)
    assert validate_transition(LifecycleState.CONTESTED, LifecycleState.DEPRECATED) is True
    # CONTESTED -> ARCHIVED: allowed
    assert validate_transition(LifecycleState.CONTESTED, LifecycleState.ARCHIVED) is True
    # DEPRECATED -> ARCHIVED: allowed
    assert validate_transition(LifecycleState.DEPRECATED, LifecycleState.ARCHIVED) is True
    # DEPRECATED -> STABLE: NOT allowed (DEPRECATED is terminal-ish)
    assert validate_transition(LifecycleState.DEPRECATED, LifecycleState.STABLE) is False
    # CONTESTED -> SUPERSEDED: NOT allowed
    assert validate_transition(LifecycleState.CONTESTED, LifecycleState.SUPERSEDED) is False
