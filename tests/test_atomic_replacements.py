"""Replacement failures cannot retire usable knowledge or leave partial edges."""

from uuid import uuid4

import pytest

from prme import MemoryEngine
from prme.ingestion.errors import ExtractionError, MaterializationError
from prme.models import MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401
from tests.test_extraction_updates import ingest_fact


async def test_failed_embedding_preparation_preserves_old_fact_without_publishing_new_fact(config, user, monkeypatch):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        original = (await engine.query_nodes(user_id=user, node_type=NodeType.FACT))[0]
        engine._pipeline._retry_delays = ()
        real_embed = engine._vector_index._provider.embed

        async def fail_replacement(texts):
            if any("Rust" in text for text in texts):
                # Inference runs before publication and outside graph locks.
                assert (await engine._graph_store.get_node(str(original.id))).lifecycle_state == LifecycleState.TENTATIVE
                raise RuntimeError("Injected embedding failure")
            return await real_embed(texts)

        monkeypatch.setattr(engine._vector_index._provider, "embed", fail_replacement)
        with pytest.raises(ExtractionError) as failure:
            await ingest_fact(engine, user, "Alice switched from Python to Rust", "Rust",
                              temporal_intent="update", replaces_object="Python")
        assert isinstance(failure.value.__cause__, MaterializationError)
        nodes = await engine.query_nodes(user_id=user, node_type=NodeType.FACT,
                                         lifecycle_states=list(LifecycleState))
        assert len(nodes) == 1 and nodes[0].id == original.id
        assert nodes[0].lifecycle_state == LifecycleState.TENTATIVE
        assert nodes[0].superseded_by is None
        assert await engine._graph_store.get_edges(target_id=str(original.id), edge_type=EdgeType.SUPERSEDES) == []


async def test_batch_failure_rolls_back_previous_state_pointer_and_edge(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        nodes = [MemoryNode(user_id=user, node_type=NodeType.FACT, content=str(i)) for i in range(3)]
        for node in nodes:
            await graph.create_node(node)
        with pytest.raises(ValueError):
            await graph.supersede_many([
                (str(nodes[0].id), str(nodes[1].id), None),
                (str(nodes[2].id), str(uuid4()), None),
            ])
        for node in nodes:
            restored = await graph.get_node(str(node.id))
            assert restored.lifecycle_state == LifecycleState.TENTATIVE
            assert restored.superseded_by is None
        assert await graph.get_edges(source_id=str(nodes[1].id), edge_type=EdgeType.SUPERSEDES) == []


@pytest.mark.parametrize("boundary", ["user", "scope", "self", "retired"])
async def test_invalid_replacement_cannot_mutate_old_fact(config, user, boundary):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        old = MemoryNode(user_id=user, node_type=NodeType.FACT, content="old")
        new = MemoryNode(user_id=user + "-other" if boundary == "user" else user,
                         scope=Scope.PROJECT if boundary == "scope" else Scope.PERSONAL,
                         node_type=NodeType.FACT, content="new",
                         lifecycle_state=LifecycleState.ARCHIVED if boundary == "retired" else LifecycleState.TENTATIVE)
        await graph.create_node(old)
        await graph.create_node(new)
        with pytest.raises(ValueError):
            await graph.supersede(str(old.id), str(old.id if boundary == "self" else new.id))
        restored = await graph.get_node(str(old.id))
        assert restored.lifecycle_state == LifecycleState.TENTATIVE and restored.superseded_by is None


async def test_edge_write_failure_rolls_back_single_replacement(config, user, monkeypatch):  # noqa: F811
    if config.database_url:
        pytest.skip("DuckDB edge failure hook; PostgreSQL transaction rollback covered by batch failure")
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        old = MemoryNode(user_id=user, node_type=NodeType.FACT, content="old")
        new = MemoryNode(user_id=user, node_type=NodeType.FACT, content="new")
        await graph.create_node(old)
        await graph.create_node(new)

        def fail_edge(_):
            raise RuntimeError("Injected edge failure")

        monkeypatch.setattr(graph, "_create_edge_sync", fail_edge)
        with pytest.raises(RuntimeError):
            await graph.supersede(str(old.id), str(new.id))
        restored = await graph.get_node(str(old.id))
        assert restored.lifecycle_state == LifecycleState.TENTATIVE and restored.superseded_by is None
