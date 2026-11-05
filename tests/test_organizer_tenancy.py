"""Tests for organizer tenant scoping (issue #66).

Two separate guarantees:

1. ``organize(user_id=...)`` reaches the jobs. It used to be accepted,
   documented, and then dropped on the floor, so a caller could scope a run,
   get a success response, and have every job touch every tenant.
2. The merging jobs never pair nodes owned by different users, even on an
   unscoped run. Exact-content dedup and string-based alias matching both
   compare content across the whole batch, so two tenants storing the same
   short or templated string used to become a "duplicate" pair, and merging
   archives one of them.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from prme.config import OrganizerConfig, PRMEConfig
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.alias_resolution import (
    AliasCandidate,
    find_aliases,
    resolve_aliases,
)
from prme.organizer.deduplication import (
    DuplicateCandidate,
    find_duplicates,
    merge_duplicates,
)
from prme.organizer.jobs import run_job
from prme.storage.engine import MemoryEngine
from prme.types import EdgeType, LifecycleState, NodeType

ACTIVE = (LifecycleState.TENTATIVE, LifecycleState.STABLE)


@pytest_asyncio.fixture
async def engine():
    with tempfile.TemporaryDirectory(prefix="prme_tenancy_") as d:
        tmp = Path(d)
        lexical_path = tmp / "lexical_index"
        lexical_path.mkdir()
        eng = await MemoryEngine.create(PRMEConfig(
            db_path=str(tmp / "memory.duckdb"),
            vector_path=str(tmp / "vectors.usearch"),
            lexical_path=str(lexical_path),
        ))
        yield eng
        await eng.close()


@pytest.fixture
def org_config():
    return OrganizerConfig()


async def _nodes_for(engine: MemoryEngine, user_id: str):
    # query_nodes defaults to active states only; the point of these tests is
    # what an organizer job did to a node, so ask for every state.
    return await engine.query_nodes(
        user_id=user_id, lifecycle_states=list(LifecycleState)
    )


async def _states_for(engine: MemoryEngine, user_id: str) -> list[LifecycleState]:
    return [n.lifecycle_state for n in await _nodes_for(engine, user_id)]


# ---------------------------------------------------------------------------
# Cross-tenant merging
# ---------------------------------------------------------------------------


async def test_identical_content_across_users_is_not_a_duplicate(
    engine, org_config
):
    # Short, templated content is what makes this collide in practice.
    for user in ("alice", "bob"):
        await engine.store("ok", user_id=user, node_type=NodeType.FACT)

    duplicates = await find_duplicates(engine, org_config)

    owners = {}
    for node in await engine.query_nodes():
        owners[str(node.id)] = node.user_id
    for dup in duplicates:
        assert owners[dup.node_a_id] == owners[dup.node_b_id], (
            f"cross-user duplicate pair: {dup!r}"
        )


async def test_deduplicate_job_leaves_both_tenants_intact(engine, org_config):
    for user in ("alice", "bob"):
        await engine.store("ok", user_id=user, node_type=NodeType.FACT)

    await run_job("deduplicate", engine, org_config, 5000.0)

    for user in ("alice", "bob"):
        states = await _states_for(engine, user)
        assert states == [LifecycleState.TENTATIVE], f"{user} was mutated: {states}"


async def test_duplicates_within_one_tenant_are_still_found(engine, org_config):
    await engine.store("ok", user_id="alice", node_type=NodeType.FACT)
    await engine.store("ok", user_id="alice", node_type=NodeType.FACT)
    await engine.store("ok", user_id="bob", node_type=NodeType.FACT)

    duplicates = await find_duplicates(engine, org_config)
    exact = [d for d in duplicates if d.match_type == "exact"]
    assert len(exact) == 1

    alice_ids = {str(n.id) for n in await _nodes_for(engine, "alice")}
    assert {exact[0].node_a_id, exact[0].node_b_id} <= alice_ids


async def test_merge_duplicates_refuses_a_cross_user_pair(engine):
    await engine.store("ok", user_id="alice", node_type=NodeType.FACT)
    await engine.store("ok", user_id="bob", node_type=NodeType.FACT)

    alice_id = str((await _nodes_for(engine, "alice"))[0].id)
    bob_id = str((await _nodes_for(engine, "bob"))[0].id)

    merged = await merge_duplicates(
        engine, [DuplicateCandidate(alice_id, bob_id, 1.0, "exact")]
    )

    assert merged == 0
    for node_id in (alice_id, bob_id):
        node = await engine.get_node(node_id, include_superseded=True)
        assert node.lifecycle_state in ACTIVE


async def test_aliases_are_not_matched_across_users(engine, org_config):
    # "PostgreSQL" and "postgres" are a known alias pair, so this only stays
    # unmatched if ownership is checked.
    await engine.store("PostgreSQL", user_id="alice", node_type=NodeType.ENTITY)
    await engine.store("postgres", user_id="bob", node_type=NodeType.ENTITY)

    aliases = await find_aliases(engine, org_config)

    assert aliases == []


async def test_alias_resolve_job_leaves_both_tenants_intact(engine, org_config):
    await engine.store("PostgreSQL", user_id="alice", node_type=NodeType.ENTITY)
    await engine.store("postgres", user_id="bob", node_type=NodeType.ENTITY)

    await run_job("alias_resolve", engine, org_config, 5000.0)

    for user in ("alice", "bob"):
        states = await _states_for(engine, user)
        assert states == [LifecycleState.TENTATIVE], f"{user} was mutated: {states}"


async def test_resolve_aliases_refuses_a_cross_user_pair(engine):
    await engine.store("PostgreSQL", user_id="alice", node_type=NodeType.ENTITY)
    await engine.store("postgres", user_id="bob", node_type=NodeType.ENTITY)

    alice_id = str((await _nodes_for(engine, "alice"))[0].id)
    bob_id = str((await _nodes_for(engine, "bob"))[0].id)

    resolved = await resolve_aliases(
        engine, [AliasCandidate(alice_id, bob_id, "abbreviation", 0.95)]
    )

    assert resolved == 0
    for node_id in (alice_id, bob_id):
        node = await engine.get_node(node_id, include_superseded=True)
        assert node.lifecycle_state in ACTIVE


# ---------------------------------------------------------------------------
# organize(user_id=...) reaches the jobs
# ---------------------------------------------------------------------------


async def test_organize_user_scope_confines_tombstone_sweep(engine):
    # ttl_days=0 expires the node the moment it is created, so the sweep is
    # eligible to archive both of these.
    for user in ("alice", "bob"):
        await engine.store(
            f"{user} ephemeral note", user_id=user, node_type=NodeType.NOTE,
            ttl_days=0,
        )

    result = await engine.organize(
        user_id="alice", jobs=["tombstone_sweep"], budget_ms=5000
    )

    assert result.per_job["tombstone_sweep"].nodes_modified == 1
    assert await _states_for(engine, "alice") == [LifecycleState.ARCHIVED]
    assert await _states_for(engine, "bob") == [LifecycleState.TENTATIVE]


async def test_tombstone_sweep_logs_the_operation_with_its_actor(engine):
    # The insert used to be handed to the write queue as a bare
    # conn.execute(), which is not awaitable, so every log silently failed
    # and the operations table stayed empty.
    await engine.store(
        "alice ephemeral note", user_id="alice", node_type=NodeType.NOTE,
        ttl_days=0,
    )

    await engine.organize(
        user_id="alice", jobs=["tombstone_sweep"], budget_ms=5000
    )

    rows = engine._conn.execute(
        "SELECT actor_id FROM operations WHERE op_type = 'TOMBSTONE_SWEEP'"
    ).fetchall()
    assert [r[0] for r in rows] == ["alice"]


async def test_organize_user_scope_confines_deduplicate(engine):
    for user in ("alice", "bob"):
        for _ in range(2):
            # Copies share validity; separate observations must stay separate.
            await engine._graph_store.create_node(MemoryNode(content="shared note", user_id=user,
                node_type=NodeType.FACT, valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc)))

    await engine.organize(user_id="alice", jobs=["deduplicate"], budget_ms=5000)

    alice_states = sorted(s.value for s in await _states_for(engine, "alice"))
    assert alice_states == ["superseded", "tentative"]
    assert await _states_for(engine, "bob") == [
        LifecycleState.TENTATIVE,
        LifecycleState.TENTATIVE,
    ]


async def test_unscoped_organize_still_covers_every_user(engine):
    for user in ("alice", "bob"):
        await engine.store(
            f"{user} ephemeral note", user_id=user, node_type=NodeType.NOTE,
            ttl_days=0,
        )

    await engine.organize(jobs=["tombstone_sweep"], budget_ms=5000)

    for user in ("alice", "bob"):
        assert await _states_for(engine, user) == [LifecycleState.ARCHIVED]


async def test_feedback_apply_rejects_user_scope(engine, org_config):
    with pytest.raises(ValueError, match="unscoped"):
        await run_job("feedback_apply", engine, org_config, 5000.0, "alice")


async def test_every_default_job_accepts_a_user_scope(engine, org_config):
    from prme.organizer.jobs import DEFAULT_JOBS

    await engine.store("alice note", user_id="alice", node_type=NodeType.FACT)

    for job_name in DEFAULT_JOBS:
        result = await run_job(job_name, engine, org_config, 500.0, "alice")
        assert result.errors == 0, f"{job_name} errored under a user scope"


async def test_organizer_raw_sql_holds_shared_connection_lock(engine, monkeypatch):
    """The write queue serializes writes, but reads share this connection too."""
    import asyncio

    original = asyncio.to_thread
    observed = []

    async def checked_to_thread(func, *args, **kwargs):
        is_retention = getattr(func, "__name__", "") == "_expire_duckdb"
        is_compaction = getattr(func, "__name__", "") == "_find_stale"
        if is_retention or is_compaction:
            observed.append(engine._graph_store._conn_lock.locked())
        return await original(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", checked_to_thread)
    await engine.store("expired note", user_id="alice", ttl_days=0)
    await engine.organize(
        user_id="alice", jobs=["tombstone_sweep", "index_compaction"], budget_ms=5000,
    )
    assert observed == [True, True]


# ---------------------------------------------------------------------------
# snapshot_generation reads only the scoped user's neighborhood (#406)
# ---------------------------------------------------------------------------


async def _shared_name_graph(engine: MemoryEngine) -> dict[str, MemoryNode]:
    """Two users with an entity of the same name, plus edges that cross users.

    Ingestion keeps entities per user, but the graph store does not check
    owners on an edge, so a crossing edge is how another user's node can sit
    one hop from an entity.
    """
    graph = engine._graph_store
    nodes = {
        "alice_acme": MemoryNode(content="Acme", user_id="alice", node_type=NodeType.ENTITY),
        "bob_acme": MemoryNode(content="Acme", user_id="bob", node_type=NodeType.ENTITY),
        "alice_fact": MemoryNode(content="Acme ships widgets", user_id="alice", node_type=NodeType.FACT),
        "bob_fact": MemoryNode(content="Acme is hiring", user_id="bob", node_type=NodeType.FACT),
        "bob_task": MemoryNode(content="Call Acme on Monday", user_id="bob", node_type=NodeType.TASK),
        "alice_decision": MemoryNode(content="Renew the Acme contract", user_id="alice",
                                     node_type=NodeType.DECISION),
    }
    for node in nodes.values():
        await graph.create_node(node)
    for source, target, owner in (
        ("alice_acme", "alice_fact", "alice"),
        ("bob_acme", "bob_fact", "bob"),
        # Crossing edges: one owned by the other user, one owned by alice.
        ("alice_acme", "bob_fact", "bob"),
        ("alice_acme", "bob_task", "alice"),
        # An edge bob owns between two of alice's nodes.
        ("alice_acme", "alice_decision", "bob"),
    ):
        await graph.create_edge(MemoryEdge(
            source_id=nodes[source].id, target_id=nodes[target].id,
            edge_type=EdgeType.RELATES_TO, user_id=owner,
        ))
    return nodes


def _capture_snapshots(monkeypatch) -> list:
    import prme.retrieval.snapshots as snapshots

    original = snapshots.generate_entity_snapshot
    captured = []

    async def recording(*args, **kwargs):
        snapshot = await original(*args, **kwargs)
        captured.append(snapshot)
        return snapshot

    monkeypatch.setattr(snapshots, "generate_entity_snapshot", recording)
    return captured


async def test_scoped_snapshot_job_leaves_out_other_users_neighbors(engine, monkeypatch):
    nodes = await _shared_name_graph(engine)
    captured = _capture_snapshots(monkeypatch)

    result = await engine.organize(
        user_id="alice", jobs=["snapshot_generation"], budget_ms=5000,
    )

    job = result.per_job["snapshot_generation"]
    assert (job.nodes_processed, job.nodes_modified, job.errors) == (1, 1, 0)
    assert len(captured) == 1
    snapshot = captured[0]
    assert snapshot.entity_node.id == nodes["alice_acme"].id
    assert [n.id for n in snapshot.facts] == [nodes["alice_fact"].id]
    assert snapshot.tasks == []
    assert "Acme is hiring" not in snapshot.summary_text
    assert "Call Acme" not in snapshot.summary_text
    bob_ids = {nodes[k].id for k in ("bob_acme", "bob_fact", "bob_task")}
    assert snapshot.relationships
    for edge in snapshot.relationships:
        assert edge.user_id == "alice"
        assert {edge.source_id, edge.target_id}.isdisjoint(bob_ids)


async def test_unscoped_snapshot_job_reads_the_whole_graph_as_before(engine, monkeypatch):
    nodes = await _shared_name_graph(engine)
    captured = _capture_snapshots(monkeypatch)

    result = await engine.organize(jobs=["snapshot_generation"], budget_ms=5000)

    job = result.per_job["snapshot_generation"]
    assert (job.nodes_processed, job.nodes_modified, job.errors) == (2, 2, 0)
    by_entity = {snap.entity_node.id: snap for snap in captured}
    assert set(by_entity) == {nodes["alice_acme"].id, nodes["bob_acme"].id}
    # An unscoped run has no owner to confine to, so the crossing edges count.
    alice = by_entity[nodes["alice_acme"].id]
    assert {n.id for n in alice.facts} == {nodes["alice_fact"].id, nodes["bob_fact"].id}
    assert [n.id for n in alice.tasks] == [nodes["bob_task"].id]
    assert len(alice.relationships) == 4


async def test_scoped_snapshot_refuses_another_users_entity(engine):
    nodes = await _shared_name_graph(engine)

    with pytest.raises(ValueError, match="not found"):
        await engine.snapshot(str(nodes["alice_acme"].id), user_id="bob")

    snapshot = await engine.snapshot(str(nodes["bob_acme"].id), user_id="bob")
    assert [n.id for n in snapshot.facts] == [nodes["bob_fact"].id]
