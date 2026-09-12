"""Entity reuse must cover the full scoped graph without mutating prior evidence."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.ingestion.entity_merge import EntityMerger
from prme.ingestion.graph_writer import WriteQueueGraphWriter
from prme.ingestion.schema import ExtractedEntity, ExtractionResult
from prme.models import Event, MemoryNode
from prme.types import LifecycleState, NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_reuses_entity_beyond_recent_window_and_first_pages(config, user):
    async with MemoryEngine.open(config) as engine:
        writer = WriteQueueGraphWriter(engine._graph_store, engine._write_queue)
        base = uuid4().int & ~0xFFFF
        target = MemoryNode(
            id=UUID(int=base + 1500), node_type=NodeType.ENTITY,
            content="  Aster  ", user_id=user, scope=Scope.PROJECT,
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            metadata={"entity_type": "organization"},
        )
        # Populate the same scope as the target so it is genuinely beyond a page.
        for i in range(260):
            await writer.create_node(MemoryNode(
                id=UUID(int=base + 1000 + i), node_type=NodeType.ENTITY,
                content=f"Project entity {i}", user_id=user, scope=Scope.PROJECT,
                metadata={"entity_type": "organization"},
            ))
        await writer.create_node(target)
        merger = EntityMerger(engine._graph_store, writer)
        before = await engine.count_nodes(user_id=user)
        found, created = await merger.find_or_create_entity(
            "aster", "organization", user, scope=Scope.PROJECT,
        )
        assert (found, created) == (str(target.id), False)
        assert await engine.count_nodes(user_id=user) == before


@pytest.mark.parametrize("difference", ["owner", "scope", "type", "archived"])
async def test_does_not_reuse_ineligible_entity(config, user, difference):
    async with MemoryEngine.open(config) as engine:
        node = MemoryNode(
            content="Aster", node_type=NodeType.ENTITY,
            user_id=user + "-other" if difference == "owner" else user,
            scope=Scope.PROJECT if difference == "scope" else Scope.PERSONAL,
            metadata={"entity_type": "person" if difference == "type" else "organization"},
            lifecycle_state=LifecycleState.ARCHIVED if difference == "archived" else LifecycleState.TENTATIVE,
        )
        writer = WriteQueueGraphWriter(engine._graph_store, engine._write_queue)
        await writer.create_node(node)
        found, created = await EntityMerger(engine._graph_store, writer).find_or_create_entity(
            "aster", "organization", user,
        )
        assert created and found != str(node.id)


async def test_reusing_entity_does_not_reindex_uncommitted_description(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        existing = MemoryNode(
            content="Aster", node_type=NodeType.ENTITY, user_id=user,
            metadata={"entity_type": "organization", "description": "An observatory"},
        )
        await engine._graph_store.create_node(existing)
        await engine._vector_index.index(str(existing.id), "Aster: An observatory", user)
        event = Event(content="Aster is a supermarket", user_id=user, role="user")
        await engine._event_store.append(event)
        result = ExtractionResult(entities=[ExtractedEntity(
            name="Aster", entity_type="organization", description="A supermarket",
        )])
        index = AsyncMock(wraps=engine._vector_index.index)
        monkeypatch.setattr(engine._vector_index, "index", index)
        await engine._pipeline._materialize(result, event, str(event.id))
        index.assert_not_awaited()
        assert (await engine.get_node(str(existing.id))).metadata == existing.metadata


@pytest.mark.parametrize("name,kind", [("I", "person"), ("we", "organization"), ("they", "group")])
async def test_personal_references_are_event_local_and_repeatable(config, user, name, kind):
    async with MemoryEngine.open(config) as engine:
        writer = WriteQueueGraphWriter(engine._graph_store, engine._write_queue)
        merger = EntityMerger(engine._graph_store, writer)
        events = [str(uuid4()), str(uuid4())]
        first, created = await merger.find_or_create_entity(name, kind, user, evidence_event_id=events[0])
        assert created
        second, created = await merger.find_or_create_entity(name, kind, user, evidence_event_id=events[1])
        assert created and second != first
        repeated, created = await merger.find_or_create_entity(name, kind, user, evidence_event_id=events[1])
        assert not created and repeated == second
        node = await engine.get_node(second)
        assert node.metadata["identity_status"] == "unresolved_reference"
        assert node.metadata["reference_event_id"] == events[1]
        assert node.evidence_refs == [UUID(events[1])]


async def test_missing_context_or_legacy_pronoun_is_not_a_person_identity(config, user):
    async with MemoryEngine.open(config) as engine:
        event_id = str(uuid4())
        legacy = MemoryNode(content="I", node_type=NodeType.ENTITY, user_id=user,
                            metadata={"entity_type": "person"}, evidence_refs=[UUID(event_id)])
        await engine._graph_store.create_node(legacy)
        writer = WriteQueueGraphWriter(engine._graph_store, engine._write_queue)
        merger = EntityMerger(engine._graph_store, writer)
        first, _ = await merger.find_or_create_entity("I", "person", user, evidence_event_id=event_id)
        second, _ = await merger.find_or_create_entity("I", "person", user)
        third, _ = await merger.find_or_create_entity("I", "person", user)
        assert len({str(legacy.id), first, second, third}) == 4


@pytest.mark.parametrize("name,kind", [("US", "country"), ("IT", "technology"), ("I", "chemical")])
async def test_explicit_non_personal_meanings_still_reuse_named_identity(config, user, name, kind):
    async with MemoryEngine.open(config) as engine:
        writer = WriteQueueGraphWriter(engine._graph_store, engine._write_queue)
        merger = EntityMerger(engine._graph_store, writer)
        first, _ = await merger.find_or_create_entity(name, kind, user, evidence_event_id=str(uuid4()))
        second, created = await merger.find_or_create_entity(name, kind, user, evidence_event_id=str(uuid4()))
        assert not created and first == second
