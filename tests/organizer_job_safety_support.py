"""Deterministic stores and no-loss checks for the organizer job audit (issue #518).

The stores are built from the member, chat and defect probe conversations. No
model is called: each turn is stored as its own note, exactly as the graph
oracle's corpus fixtures do, and every job candidate is created directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from benchmarks.diagnostics.chat_probe import chat_turns, load_conversation
from prme.models.nodes import MemoryNode
from prme.types import (
    ACTIVE_LIFECYCLE_STATES,
    EdgeType,
    LifecycleState,
    NodeType,
    Scope,
)

PROBE_CORPORA = ("member-v1", "chat-v1", "defects-v1")
CONVERSATIONS = Path(__file__).resolve().parents[1] / "benchmarks" / "conversations"
RETIRING_JOBS = frozenset({
    "decay_sweep", "archive", "deduplicate", "alias_resolve",
    "tombstone_sweep", "consolidate",
})
AGING_JOBS = frozenset({
    "promote", "decay_sweep", "archive", "tombstone_sweep", "consolidate",
})
AGED_DAYS = 400


async def build_probe_store(engine, owner: str, corpus: str = PROBE_CORPORA[0]) -> None:
    """Store one probe conversation's turns for ``owner`` as separate notes."""
    conversation = load_conversation(CONVERSATIONS / f"{corpus}.json")
    for turn in chat_turns(conversation):
        await engine.store_with_receipt(
            turn["content"],
            user_id=owner,
            role=turn["role"],
            session_id=turn["session_id"],
            event_time=turn["event_time"],
            metadata=turn["metadata"],
            node_type=NodeType.NOTE,
            scope=Scope.PERSONAL,
            extract=False,
        )


async def add_job_candidates(engine, owner: str) -> None:
    """Add one candidate for each job that mutates or merges nodes."""
    from prme.models import Event

    await engine.store("PostgreSQL", user_id=owner, node_type=NodeType.ENTITY)
    await engine.store("postgres", user_id=owner, node_type=NodeType.ENTITY)

    # Exact copies share validity and provenance, so deduplicate can merge them.
    stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    provenance_event = Event(
        content="Authored evidence for the organizer job candidates",
        role="user",
        user_id=owner,
    )
    await engine._event_store.append(provenance_event)
    provenance = provenance_event.id
    for _ in range(2):
        await engine._graph_store.create_node(MemoryNode(
            content="The telescope is blue",
            user_id=owner,
            node_type=NodeType.FACT,
            valid_from=stamp,
            evidence_refs=[provenance],
        ))

    await engine.store("expired note", user_id=owner, node_type=NodeType.NOTE, ttl_days=0)

    # An eligible tentative node for both promotion paths.
    await engine._graph_store.create_node(MemoryNode(
        content="Aged evidence-backed fact",
        user_id=owner,
        node_type=NodeType.FACT,
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
        evidence_refs=[provenance],
    ))

    # Identical content forms a consolidation cluster under any embedder.
    for _ in range(3):
        await engine.store(
            "the telescope is blue", user_id=owner, node_type=NodeType.NOTE,
        )


async def age_owner_nodes(engine, owner: str, days: int = AGED_DAYS) -> None:
    """Move every node of one owner into the past on both backends.

    Promotion, decay, TTL expiry and consolidation retirement all key off
    stored timestamps. Aging the rows keeps the tests deterministic without
    waiting or faking the engine clock.
    """
    at = datetime.now(timezone.utc) - timedelta(days=days)
    if getattr(engine, "_conn", None) is not None:
        engine._conn.execute(
            "UPDATE nodes SET created_at=?, last_reinforced_at=? WHERE user_id=?",
            [at, at, owner],
        )
        return
    async with engine._graph_store._pool.acquire() as conn:
        await conn.execute(
            "UPDATE nodes SET created_at=$1, last_reinforced_at=$1 WHERE user_id=$2",
            at, owner,
        )


async def add_stale_retired_node(engine, owner: str) -> None:
    """Archive one node with an injected eviction failure.

    The retired node keeps its vector and, on PostgreSQL, a lexical document
    row, so ``index_compaction`` has a real stale entry to clear rather than an
    empty query.
    """
    await engine.store("stale retired note", user_id=owner, node_type=NodeType.NOTE)
    node = (await engine.query_nodes(
        user_id=owner, content_contains_any=["stale retired note"], limit=10,
    ))[0]
    original = engine._delete_from_indexes

    async def fail(node_id):
        raise RuntimeError("authored eviction failure")

    engine._delete_from_indexes = fail
    try:
        await engine.archive(str(node.id), user_id=owner)
    finally:
        engine._delete_from_indexes = original
    if getattr(engine, "_conn", None) is None:
        async with engine._graph_store._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO lexical_documents (node_id, content, user_id, node_type) "
                "VALUES ($1, $2, $3, $4)",
                str(node.id), "stale retired note", owner, "note",
            )


async def prepare_for_job(
    engine, owner: str, job: str, corpora: tuple[str, ...] = PROBE_CORPORA,
) -> None:
    for corpus in corpora:
        await build_probe_store(engine, owner, corpus)
    await add_job_candidates(engine, owner)
    if job in AGING_JOBS:
        await age_owner_nodes(engine, owner)
    if job == "index_compaction":
        await add_stale_retired_node(engine, owner)


def _event_id(event) -> str:
    return str(getattr(event, "id", None) or getattr(event, "event_id"))


async def _index_presence(engine, node_ids: list[str]) -> dict[str, bool]:
    """Whether each node still has a vector or lexical entry."""
    if not node_ids:
        return {}
    if getattr(engine, "_conn", None) is not None:
        placeholders = ", ".join("?" for _ in node_ids)
        rows = engine._conn.execute(
            f"SELECT DISTINCT node_id FROM vector_metadata WHERE node_id IN ({placeholders})",
            node_ids,
        ).fetchall()
        present = {str(row[0]) for row in rows}
        return {node_id: node_id in present for node_id in node_ids}
    async with engine._graph_store._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text AS node_id, embedding IS NOT NULL AS present "
            "FROM nodes WHERE id::text = ANY($1::text[])",
            node_ids,
        )
        embeddings = {row["node_id"]: bool(row["present"]) for row in rows}
        lexical = await conn.fetch(
            "SELECT DISTINCT node_id FROM lexical_documents WHERE node_id = ANY($1::text[])",
            node_ids,
        )
        lexicals = {row["node_id"] for row in lexical}
    return {
        node_id: embeddings.get(node_id, False) or node_id in lexicals
        for node_id in node_ids
    }


async def graph_snapshot(engine, owner: str) -> dict:
    """Every graph value the no-loss checks compare, before or after a job."""
    nodes = await engine.query_nodes(
        user_id=owner, lifecycle_states=list(LifecycleState), limit=100000,
    )
    node_ids = [str(node.id) for node in nodes]
    edges = await engine._graph_store.get_edges(node_ids=node_ids) if node_ids else []
    events = await engine.get_events(owner, limit=100000)
    return {
        "nodes": {str(node.id): node for node in nodes},
        "edges": {str(edge.id): edge for edge in edges},
        "events": {_event_id(event) for event in events},
        "index": await _index_presence(engine, node_ids),
    }


def _supersedes_targets(edges) -> set[str]:
    return {
        str(edge.target_id)
        for edge in edges.values()
        if edge.edge_type == EdgeType.SUPERSEDES
    }


def assert_no_loss(before: dict, after: dict, job: str) -> None:
    """The event log and graph still hold everything the job started with."""
    assert set(after["events"]) >= set(before["events"]), f"{job} lost events"
    for node_id, original in before["nodes"].items():
        assert node_id in after["nodes"], f"{job} removed node {node_id}"
        current = after["nodes"][node_id]
        assert current.content == original.content, f"{job} rewrote node content"
        assert current.node_type == original.node_type, f"{job} rewrote node type"
        assert set(current.evidence_refs) >= set(original.evidence_refs), (
            f"{job} dropped evidence from node {node_id}"
        )
        if (
            current.lifecycle_state in ACTIVE_LIFECYCLE_STATES
            and before["index"].get(node_id)
        ):
            assert after["index"].get(node_id), (
                f"{job} cleared an active node's index entry"
            )
        if current.lifecycle_state == LifecycleState.SUPERSEDED:
            successor = current.superseded_by
            assert successor is not None or node_id in _supersedes_targets(after["edges"]), (
                f"{job} superseded node {node_id} without a successor"
            )
            if successor is not None:
                assert str(successor) in after["nodes"], (
                    f"{job} named a successor that does not exist"
                )
                successor_node = after["nodes"][str(successor)]
                assert set(original.evidence_refs) <= set(successor_node.evidence_refs), (
                    f"{job} left evidence unreachable from the successor of {node_id}"
                )
                if successor_node.node_type == NodeType.SUMMARY:
                    metadata = successor_node.metadata or {}
                    source_ids = set(metadata.get("source_node_ids") or ()) | set(
                        metadata.get("selected_source_node_ids") or ()
                    )
                    assert node_id in source_ids, (
                        f"{job} left the fact of {node_id} out of its summary"
                    )
    for edge_id, original in before["edges"].items():
        assert edge_id in after["edges"], f"{job} removed edge {edge_id}"
        assert after["edges"][edge_id] == original, f"{job} rewrote edge provenance"


def assert_unchanged(before: dict, after: dict, job: str, owner: str) -> None:
    """A scoped job left another owner's graph exactly as it was."""
    assert set(after["events"]) == set(before["events"]), f"{job} changed {owner}'s events"
    assert set(after["nodes"]) == set(before["nodes"]), f"{job} added or removed {owner}'s nodes"
    assert set(after["edges"]) == set(before["edges"]), f"{job} changed {owner}'s edges"
    assert after["index"] == before["index"], f"{job} changed {owner}'s index entries"
    for node_id, original in before["nodes"].items():
        assert after["nodes"][node_id] == original, f"{job} mutated {owner}'s node {node_id}"
    for edge_id, original in before["edges"].items():
        assert after["edges"][edge_id] == original, f"{job} mutated {owner}'s edge {edge_id}"
