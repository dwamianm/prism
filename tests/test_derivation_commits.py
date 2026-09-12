"""Prepared graph batches commit all artifacts and replacements, or none."""

import asyncio
import math
import subprocess
import sys
import threading
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.models import Event, MemoryEdge, MemoryNode
from prme.models.derivation import DerivationPlan, PreparedEmbedding, PreparedLexicalDocument
from prme.types import EdgeType, EpistemicType, LifecycleState, NodeType
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def prepare(engine, user, *, persist=True):
    previous = await engine.store("Alice uses Python", user_id=user, node_type=NodeType.FACT)
    old = (await engine.get_event_nodes(previous, user_id=user))[0]
    event = Event(content="Alice switched from Python to Rust", role="user", user_id=user)
    await engine._event_store.append(event)
    entity = MemoryNode(content="Alice", node_type=NodeType.ENTITY, user_id=user, evidence_refs=[event.id])
    fact = MemoryNode(content=event.content, node_type=NodeType.FACT, user_id=user, evidence_refs=[event.id])
    edges = (MemoryEdge(source_id=entity.id, target_id=fact.id, edge_type=EdgeType.HAS_FACT,
                        user_id=user, provenance_event_id=event.id),)
    replacement = MemoryEdge(source_id=fact.id, target_id=old.id, edge_type=EdgeType.SUPERSEDES,
                             user_id=user, provenance_event_id=event.id)
    provider = engine._vector_index._provider
    vectors = await provider.embed([entity.content, fact.content])
    plan = DerivationPlan(
        event_id=event.id, user_id=user, scope=event.scope, content_hash=event.content_hash,
        nodes=(entity, fact), edges=edges, replacements=(replacement,), references=(old,),
        embeddings=tuple(PreparedEmbedding(node_id=node.id, content=node.content,
                         model=provider.model_name, version=provider.model_version,
                         dimension=provider.dimension, values=vector)
                         for node, vector in zip((entity, fact), vectors)),
        lexical_documents=(PreparedLexicalDocument(node_id=fact.id, content=fact.content),),
    )
    return await engine._event_store.record_derivation_plan(plan) if persist else plan


async def stage(engine, plan):
    if engine._conn is not None:
        for embedding in plan.embeddings:
            await engine._vector_index.stage(embedding, user_id=plan.user_id)
        await engine._vector_index.save()
        await engine._lexical_index.stage(plan)


async def test_compaction_retains_prepared_indexes_then_evicts_archived_results(config, user):
    if config.backend != "duckdb":
        pytest.skip("PostgreSQL commits derived indexes in the graph transaction")
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user)
        await stage(engine, plan)
        for owner in (None, user):
            await engine.organize(user_id=owner, jobs=["index_compaction"], budget_ms=5000)
        for embedding in plan.embeddings:
            assert engine._conn.execute("SELECT count(*) FROM vector_staging WHERE node_id = ?",
                                        [str(embedding.node_id)]).fetchone()[0] == 1
        receipt = await engine._graph_store.commit_derivation(plan)
        fact = plan.nodes[-1]
        await engine._graph_store.archive(str(fact.id))
        await engine.organize(user_id=user, jobs=["index_compaction"], budget_ms=5000)
        assert engine._conn.execute("SELECT count(*) FROM vector_staging WHERE node_id = ?",
                                    [str(fact.id)]).fetchone()[0] == 0
        assert await engine._graph_store.commit_derivation(plan) == receipt
        assert await engine.get_node(str(fact.id)) is None


async def test_commit_and_concurrent_replay_preserve_one_receipt_and_fixed_artifacts(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user)
        monkeypatch.setattr(engine._vector_index._provider, "embed", AsyncMock(side_effect=AssertionError("commit must not call a provider")))
        await stage(engine, plan)
        await stage(engine, plan)
        assert await engine.get_event_nodes(str(plan.event_id), user_id=user) == []
        receipts = await asyncio.gather(*[engine._graph_store.commit_derivation(plan) for _ in range(4)])
        assert all(receipt == receipts[0] for receipt in receipts)
        assert {node.id for node in await engine.get_event_nodes(str(plan.event_id), user_id=user)} == {node.id for node in plan.nodes}
        old = await engine._graph_store.get_node(str(plan.references[0].id), include_superseded=True)
        assert old.lifecycle_state == LifecycleState.SUPERSEDED
        assert old.superseded_by == plan.replacements[0].source_id
        for edge in plan.edges + plan.replacements:
            actual = await engine._graph_store.get_edges(source_id=str(edge.source_id), target_id=str(edge.target_id))
            assert [item.id for item in actual] == [edge.id]
        fact = plan.nodes[-1]
        await engine.archive(str(fact.id))
        replayed = await engine._graph_store.commit_derivation(plan)
        assert replayed == receipts[0]
        assert await engine.get_node(str(fact.id)) is None
    async with MemoryEngine.open(config) as engine:
        saved = await engine._event_store.get_derivation_plan(str(plan.event_id), user_id=user)
        assert saved.checksum == plan.checksum
        assert await engine._graph_store.commit_derivation(saved) == receipts[0]
        assert await engine._event_store.get_derivation_plan(str(plan.event_id), user_id=user + "-other") is None


@pytest.mark.parametrize("kind,position", [("node", 1), ("node", 2), ("edge", 1), ("edge", 2)])
async def test_each_graph_write_failure_rolls_back_the_whole_derivation(config, user, monkeypatch, kind, position):
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user)
        await stage(engine, plan)
        graph = engine._graph_store
        name = f"_create_{kind}_sync" if config.backend == "duckdb" else f"_create_{kind}_on_connection"
        original = getattr(graph, name)
        calls = 0
        if config.backend == "duckdb":
            def fail_after_write(*args, **kwargs):
                nonlocal calls
                result = original(*args, **kwargs)
                calls += 1
                if calls == position:
                    raise RuntimeError("injected post-write failure")
                return result
        else:
            async def fail_after_write(*args, **kwargs):
                nonlocal calls
                result = await original(*args, **kwargs)
                calls += 1
                if calls == position:
                    raise RuntimeError("injected post-write failure")
                return result
        with monkeypatch.context() as failure:
            failure.setattr(graph, name, fail_after_write)
            with pytest.raises(RuntimeError, match="injected"):
                await graph.commit_derivation(plan)
        assert await engine.get_event_nodes(str(plan.event_id), user_id=user) == []
        assert (await engine.get_node(str(plan.references[0].id))).lifecycle_state == plan.references[0].lifecycle_state
        for edge in plan.edges + plan.replacements:
            assert await graph.get_edges(source_id=str(edge.source_id), target_id=str(edge.target_id)) == []
        assert await engine.get_event(str(plan.event_id)) is not None
        await graph.commit_derivation(plan)
        assert len(await engine.get_event_nodes(str(plan.event_id), user_id=user)) == 2


async def test_changed_dependency_rejects_plan_before_publication(config, user):
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user)
        await stage(engine, plan)
        await engine._graph_store.update_node(str(plan.references[0].id), metadata={"changed": True})
        with pytest.raises(ValueError, match="dependency changed"):
            await engine._graph_store.commit_derivation(plan)
        assert await engine.get_event_nodes(str(plan.event_id), user_id=user) == []


async def test_hypothetical_replacement_cannot_retire_prior_knowledge(config, user):
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user, persist=False)
        plan.nodes[-1].epistemic_type = EpistemicType.HYPOTHETICAL
        plan = await engine._event_store.record_derivation_plan(plan)
        await stage(engine, plan)
        with pytest.raises(ValueError, match="cannot retire"):
            await engine._graph_store.commit_derivation(plan)
        assert await engine.get_event_nodes(str(plan.event_id), user_id=user) == []
        assert await engine.get_node(str(plan.references[0].id)) is not None


async def test_database_timezone_change_does_not_invalidate_unchanged_dependencies(config, user):
    if config.backend != "duckdb":
        pytest.skip("asyncpg decodes TIMESTAMPTZ as UTC independently of server timezone")
    async with MemoryEngine.open(config) as engine:
        engine._conn.execute("SET TimeZone = 'UTC'")
        plan = await prepare(engine, user)
        await stage(engine, plan)
        engine._conn.execute("SET TimeZone = 'Asia/Tokyo'")
        receipt = await engine._graph_store.commit_derivation(plan)
        assert receipt.plan_id == plan.id


@pytest.mark.parametrize("corruption", ["owner", "scope", "source", "dangling_edge", "nonfinite_vector", "missing_vector"])
def test_malformed_plan_is_rejected_before_storage(corruption):
    event = Event(content="A synthetic fact", role="user", user_id="alice")
    node = MemoryNode(content=event.content, user_id="alice", node_type=NodeType.FACT, evidence_refs=[event.id])
    values = dict(event_id=event.id, user_id="alice", scope=event.scope, content_hash=event.content_hash,
                  nodes=(node,), embeddings=(PreparedEmbedding(node_id=node.id, content=node.content,
                    model="model", version="1", dimension=2, values=(1, 0)),))
    plan = DerivationPlan(**values).model_dump()
    if corruption == "owner":
        plan["nodes"][0]["user_id"] = "bob"
    elif corruption == "scope":
        plan["nodes"][0]["scope"] = "org"
    elif corruption == "source":
        plan["nodes"][0]["evidence_refs"] = [uuid4()]
    elif corruption == "dangling_edge":
        plan["edges"] = [MemoryEdge(source_id=node.id, target_id=uuid4(), edge_type=EdgeType.HAS_FACT,
                                   user_id="alice", provenance_event_id=event.id).model_dump()]
    elif corruption == "nonfinite_vector":
        plan["embeddings"][0]["values"] = [float("nan"), 1]
    else:
        plan["embeddings"] = []
    with pytest.raises(ValueError):
        DerivationPlan.model_validate(plan)


async def test_plan_journal_converges_and_rejects_payload_mutation(config, user):
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user)
        alternative = plan.model_copy(update={"id": uuid4()})
        saved = await engine._event_store.record_derivation_plan(alternative)
        assert saved.id == plan.id
        changed = plan.model_copy(deep=True)
        changed.nodes[0].content = "A different entity"
        with pytest.raises(ValueError, match="different payload"):
            await engine._event_store.record_derivation_plan(changed)
        with pytest.raises(ValueError, match="exact journaled"):
            await engine._graph_store.commit_derivation(alternative)


async def test_local_graph_commit_requires_durable_prepared_vectors(config, user):
    if config.backend != "duckdb":
        pytest.skip("PostgreSQL vectors commit inside the graph transaction")
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user)
        with pytest.raises(ValueError, match="durably staged"):
            await engine._graph_store.commit_derivation(plan)
        assert await engine.get_event_nodes(str(plan.event_id), user_id=user) == []


async def test_prepared_vectors_keep_exact_signed_zero_across_journal_roundtrip(config, user):
    event = Event(content="A synthetic vector", user_id=user, role="user")
    node = MemoryNode(content=event.content, node_type=NodeType.FACT, user_id=user, evidence_refs=[event.id])
    plan = DerivationPlan(
        event_id=event.id, user_id=user, scope=event.scope, content_hash=event.content_hash,
        nodes=(node,), embeddings=(PreparedEmbedding(node_id=node.id, content=node.content,
            model="signed-zero-test", version="1", dimension=2, values=(-0.0, 1.0)),),
    )
    async with MemoryEngine.open(config) as engine:
        await engine._event_store.append(event)
        stored = await engine._event_store.record_derivation_plan(plan)
        assert stored.checksum == plan.checksum
        assert math.copysign(1, stored.embeddings[0].values[0]) == -1
    async with MemoryEngine.open(config) as engine:
        stored = await engine._event_store.get_derivation_plan(str(event.id), user_id=user)
        assert stored.checksum == plan.checksum


async def test_wrong_source_hash_and_unjournaled_plan_cannot_publish(config, user):
    async with MemoryEngine.open(config) as engine:
        event = Event(content="Empty extraction", user_id=user, role="user")
        await engine._event_store.append(event)
        plan = DerivationPlan(event_id=event.id, user_id=user, scope=event.scope, content_hash=event.content_hash)
        with pytest.raises(ValueError, match="exact journaled"):
            await engine._graph_store.commit_derivation(plan)
        with pytest.raises(ValueError, match="immutable source"):
            await engine._event_store.record_derivation_plan(plan.model_copy(update={"content_hash": "0" * 64}))
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user) is None
        await engine._event_store.record_derivation_plan(plan)
        receipt = await engine._graph_store.commit_derivation(plan)
        assert receipt.node_ids == () and receipt.edge_ids == ()


async def test_independent_reader_never_observes_a_partial_batch(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user)
        await stage(engine, plan)
        graph = engine._graph_store
        entered, release = threading.Event(), threading.Event()
        name = "_create_node_sync" if config.backend == "duckdb" else "_create_node_on_connection"
        original = getattr(graph, name)
        first = True
        if config.backend == "duckdb":
            def blocked(*args, **kwargs):
                nonlocal first
                result = original(*args, **kwargs)
                if first:
                    first = False
                    entered.set()
                    assert release.wait(10)
                return result
        else:
            async def blocked(*args, **kwargs):
                nonlocal first
                result = await original(*args, **kwargs)
                if first:
                    first = False
                    entered.set()
                    assert await asyncio.to_thread(release.wait, 10)
                return result
        monkeypatch.setattr(graph, name, blocked)
        task = asyncio.create_task(graph.commit_derivation(plan))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            ids = [str(node.id) for node in plan.nodes]
            if config.backend == "duckdb":
                import duckdb
                def read_independently():
                    with duckdb.connect(config.db_path) as conn:
                        return conn.execute("SELECT count(*) FROM nodes WHERE id IN (?, ?)", ids).fetchone()[0]
                count = await asyncio.to_thread(read_independently)
            else:
                async with engine._pool.acquire() as conn:
                    count = await conn.fetchval("SELECT count(*) FROM nodes WHERE id = ANY($1::uuid[])", ids)
            assert count == 0
        finally:
            release.set()
            await task
        assert len(await engine.get_event_nodes(str(plan.event_id), user_id=user)) == 2


@pytest.mark.parametrize("checkpoint", ["node", "replacement", "committed"])
async def test_process_exit_recovers_the_same_plan_without_partial_graph(config, user, checkpoint):
    if config.backend != "duckdb":
        pytest.skip("Local process exit and WAL recovery")
    async with MemoryEngine.open(config) as engine:
        plan = await prepare(engine, user)
        await stage(engine, plan)
    script = '''
import asyncio, os, sys
from prme import MemoryEngine, PRMEConfig
async def main():
    engine = await MemoryEngine.create(PRMEConfig.model_validate_json(sys.argv[1]))
    plan = await engine._event_store.get_derivation_plan(sys.argv[2], user_id=sys.argv[3])
    stage = sys.argv[4]
    graph = engine._graph_store
    if stage == "node":
        original = graph._create_node_sync
        def fault(node):
            original(node)
            os._exit(42)
        graph._create_node_sync = fault
    if stage == "replacement":
        original = graph._create_edge_sync
        def fault(edge):
            original(edge)
            if edge.edge_type.value == "supersedes":
                os._exit(42)
        graph._create_edge_sync = fault
    await graph.commit_derivation(plan)
    os._exit(42)
asyncio.run(main())
'''
    process = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, config.model_dump_json(), str(plan.event_id), user, checkpoint],
        capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 42, process.stderr
    async with MemoryEngine.open(config) as engine:
        nodes = await engine.get_event_nodes(str(plan.event_id), user_id=user)
        assert len(nodes) == (2 if checkpoint == "committed" else 0)
        old = await engine._graph_store.get_node(str(plan.references[0].id), include_superseded=True)
        assert old.lifecycle_state == (LifecycleState.SUPERSEDED if checkpoint == "committed" else plan.references[0].lifecycle_state)
        saved = await engine._event_store.get_derivation_plan(str(plan.event_id), user_id=user)
        assert saved.checksum == plan.checksum
        first = await engine._graph_store.commit_derivation(saved)
        second = await engine._graph_store.commit_derivation(saved)
        assert first == second and first.node_ids == tuple(node.id for node in plan.nodes)
