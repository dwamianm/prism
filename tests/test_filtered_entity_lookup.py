"""Research prototype (research/write-rate-2026-11): the filtered entity lookup finds what the full scan finds."""

from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine
from prme.ingestion.entity_merge import EntityMerger
from prme.ingestion.graph_writer import WriteQueueGraphWriter
from prme.ingestion.planning import PlanningGraph
from prme.models import Event, MemoryNode
from prme.types import LifecycleState, NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


def entity(name: str, owner: str, *, kind: str = "organization", node_id: UUID | None = None,
           scope: Scope = Scope.PERSONAL, state: LifecycleState = LifecycleState.TENTATIVE) -> MemoryNode:
    return MemoryNode(id=node_id or uuid4(), node_type=NodeType.ENTITY, content=name, user_id=owner, scope=scope,
                      metadata={"entity_type": kind}, lifecycle_state=state)


@pytest.mark.parametrize("name", ["aster", "  ASTER ", "50%_off", "back\\slash"])
async def test_filtered_lookup_matches_full_scan(config, user, name):
    async with MemoryEngine.open(config) as engine:
        writer = WriteQueueGraphWriter(engine._graph_store, engine._write_queue)
        base = uuid4().int & ~0xFFFF
        # Over a page of other entities, names that contain the target's name, and the ineligible copies.
        for index in range(300):
            await writer.create_node(entity(f"Project {index}", user, node_id=UUID(int=base + index)))
        for decoy in ("asterisk", "50%_offer", "50x_off", "back\\slash room"):
            await writer.create_node(entity(decoy, user))
        await writer.create_node(entity(name.strip(), user, kind="person"))
        await writer.create_node(entity(name.strip(), user, state=LifecycleState.ARCHIVED))
        await writer.create_node(entity(name.strip(), user + "-other"))
        target = entity(f" {name.strip().title()} ", user, node_id=UUID(int=base + 5000))
        await writer.create_node(target)
        full = await EntityMerger(engine._graph_store, writer).find_entity(name, "organization", user)
        filtered = await EntityMerger(engine._graph_store, writer, filter_by_name=True).find_entity(
            name, "organization", user)
        assert full == filtered == str(target.id)
        assert await EntityMerger(engine._graph_store, writer, filter_by_name=True).find_entity(
            "absent", "organization", user) is None


async def test_planning_scan_filters_planned_nodes_the_same_way(config, user):
    async with MemoryEngine.open(config) as engine:
        writer = WriteQueueGraphWriter(engine._graph_store, engine._write_queue)
        stored = entity("Aster", user)
        await writer.create_node(stored)
        await writer.create_node(entity("Birch", user))
        event = Event(content="Aster and Cedar", user_id=user, role="system")
        graph = PlanningGraph(engine._graph_store, event)
        planned = entity("Cedar", user)
        await graph.create_node(planned)
        everything = await graph.scan_nodes(user_id=user, scope=Scope.PERSONAL, node_type=NodeType.ENTITY)
        assert {node.content for node in everything} == {"Aster", "Birch", "Cedar"}
        cedar = await graph.scan_nodes(user_id=user, scope=Scope.PERSONAL, node_type=NodeType.ENTITY,
                                       content_contains_any=["cedar"])
        assert [node.id for node in cedar] == [planned.id]
        aster = await graph.scan_nodes(user_id=user, scope=Scope.PERSONAL, node_type=NodeType.ENTITY,
                                       content_contains_any=["ASTER"])
        assert [node.id for node in aster] == [stored.id]


async def test_option_is_off_by_default_and_reaches_the_pipeline(config):
    from prme import PRMEConfig

    assert PRMEConfig(_env_file=None).enable_filtered_entity_lookup is False
    async with MemoryEngine.open(config.model_copy(update={"enable_filtered_entity_lookup": True})) as engine:
        assert engine._pipeline._entity_merger._filter_by_name is True
    async with MemoryEngine.open(config) as engine:
        assert engine._pipeline._entity_merger._filter_by_name is False
