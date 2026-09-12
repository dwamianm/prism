"""The shared ingestion rules prepare complete batches without durable writes."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractedRelationship, ExtractionResult
from prme.models import Event, MemoryNode
from prme.types import LifecycleState, NodeType, Scope
from tests import test_derivation_commits, test_durable_ingestion
from tests.test_extraction_updates import ingest_fact

config = test_durable_ingestion.config
user = test_durable_ingestion.user


def extraction(source, *, value="Rust", old="Python", epistemic="asserted"):
    return ExtractionResult(
        entities=[ExtractedEntity(name="Alice", entity_type="person", description="New description"),
                  ExtractedEntity(name="Bob", entity_type="person"),
                  ExtractedEntity(name=" bob ", entity_type="person")],
        facts=[ExtractedFact(subject="Alice", predicate="uses", object=value, evidence_quote=source,
                            temporal_intent="update", replaces_object=old, epistemic_type=epistemic)],
        relationships=[ExtractedRelationship(source_entity="Alice", target_entity="Bob",
                                              relationship_type="knows")],
    )


async def test_preparation_has_no_durable_side_effects_and_saved_plan_commits(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        before = [node.model_dump() for node in await engine.query_nodes(user_id=user)]
        event = Event(content="Alice switched from Python to Rust. Alice knows Bob.", role="user", user_id=user)
        await engine._event_store.append(event)
        provider = engine._vector_index._provider
        original_embed = provider.embed
        embed = AsyncMock(side_effect=original_embed)
        with monkeypatch.context() as guard:
            guard.setattr(provider, "embed", embed)
            for target, method in ((engine._graph_store, "create_node"), (engine._graph_store, "create_edge"),
                                   (engine._vector_index, "index"), (engine._lexical_index, "index")):
                guard.setattr(target, method, AsyncMock(side_effect=AssertionError("Planning cannot write")))
            plan = await engine._pipeline._prepare_plan(extraction(event.content), event)
        assert [node.model_dump() for node in await engine.query_nodes(user_id=user)] == before
        assert await engine.get_event_nodes(str(event.id), user_id=user) == []
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user) is None
        assert len(plan.nodes) == 2  # Bob once, Rust fact; existing Alice is reused.
        assert len(plan.references) == 2  # Alice and the previous Python fact.
        assert len(plan.replacements) == 1
        assert len(plan.edges) == 2
        assert embed.await_count == 1 and len(embed.call_args.args[0]) == 2
        assert all("New description" not in item.content for item in plan.embeddings)
        saved = await engine._event_store.record_derivation_plan(plan)
        monkeypatch.setattr(provider, "embed", AsyncMock(side_effect=AssertionError("Replay cannot infer")))
        await test_derivation_commits.stage(engine, saved)
        await engine._graph_store.commit_derivation(saved)
        facts = await engine.query_nodes(user_id=user, node_type=NodeType.FACT,
                                         lifecycle_states=list(LifecycleState))
        assert {node.metadata["object"]: node.lifecycle_state for node in facts} == {
            "Python": LifecycleState.SUPERSEDED, "Rust": LifecycleState.TENTATIVE,
        }


async def test_preparation_is_scoped_and_unrelated_scanned_nodes_are_not_dependencies(config, user):
    async with MemoryEngine.open(config) as engine:
        for owner, scope in ((user, Scope.PERSONAL), (user + "-other", Scope.PROJECT)):
            await engine._graph_store.create_node(MemoryNode(node_type=NodeType.ENTITY, content="Alice",
                user_id=owner, scope=scope, metadata={"entity_type": "person"}))
        unrelated = MemoryNode(node_type=NodeType.ENTITY, content="Unrelated", user_id=user,
                               scope=Scope.PROJECT, metadata={"entity_type": "person"})
        await engine._graph_store.create_node(unrelated)
        event = Event(content="Alice uses Rust", user_id=user, role="user", scope=Scope.PROJECT)
        result = extraction(event.content, old=None)
        result.entities[0].scope = "system"
        result.facts[0].scope = "personal"
        plan = await engine._pipeline._prepare_plan(result, event)
        assert len(plan.nodes) == 3 and not plan.references
        assert all((node.user_id, node.scope) == (user, Scope.PROJECT) for node in plan.nodes)
        assert plan.nodes[-1].metadata["suggested_scope"] == "personal"


async def test_empty_extraction_does_not_call_embedding_provider(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        monkeypatch.setattr(engine._vector_index._provider, "embed", AsyncMock(side_effect=AssertionError("No inputs")))
        event = Event(content="Hello", user_id=user, role="user")
        plan = await engine._pipeline._prepare_plan(ExtractionResult(), event)
        assert not plan.nodes and not plan.embeddings and not plan.references


@pytest.mark.parametrize("bad_output", [[], [[float("nan")] * 384], [[1.0, 2.0]], [[1e-100] * 384]])
async def test_invalid_embeddings_fail_before_any_graph_publication(config, user, monkeypatch, bad_output):
    async with MemoryEngine.open(config) as engine:
        monkeypatch.setattr(engine._vector_index._provider, "embed", AsyncMock(return_value=bad_output * 3))
        event = Event(content="Alice uses Rust", user_id=user, role="user")
        with pytest.raises(ValueError):
            await engine._pipeline._prepare_plan(extraction(event.content), event)
        assert await engine.query_nodes(user_id=user) == []


async def test_late_effective_update_is_preserved_without_retiring_later_fact(config, user):
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        event = Event(content="Alice switched from Python to Rust", user_id=user, role="user",
                      event_time=datetime(2000, 1, 1, tzinfo=timezone.utc))
        plan = await engine._pipeline._prepare_plan(extraction(event.content), event)
        assert not plan.replacements
        assert plan.nodes[-1].event_time == event.event_time


async def test_chain_within_one_source_retires_each_planned_fact_once(config, user):
    async with MemoryEngine.open(config) as engine:
        await ingest_fact(engine, user, "Alice uses Python", "Python")
        event = Event(content="Alice switched Python to Rust, then Rust to Java", user_id=user, role="user")
        result = extraction(event.content)
        result.facts.append(result.facts[0].model_copy(update={"object": "Java", "replaces_object": "Rust"}))
        plan = await engine._pipeline._prepare_plan(result, event)
        assert len(plan.replacements) == 2
        assert plan.replacements[1].target_id == plan.replacements[0].source_id
