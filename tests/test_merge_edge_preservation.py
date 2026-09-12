"""Canonicalization must retain relationship validity/provenance and retry safely."""

from datetime import datetime, timedelta, timezone
import asyncio

import pytest

from prme import MemoryEngine
from prme.models import Event, MemoryEdge, MemoryNode
from prme.organizer.alias_resolution import AliasCandidate, resolve_aliases
from prme.organizer.deduplication import DuplicateCandidate, _transfer_edges, merge_duplicates
from prme.types import EdgeType, LifecycleState, NodeType
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


async def seed(engine, user, alias=False):
    stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    event = Event(content="Authored qualified relationship", role="user", user_id=user)
    await engine._event_store.append(event)
    nodes = []
    for index, text in enumerate(("PostgreSQL", "postgres" if alias else "PostgreSQL", "Aster", "Birch")):
        node = MemoryNode(content=text, node_type=NodeType.ENTITY, user_id=user,
                          valid_from=stamp, confidence_base=.9 if index == 0 else .5)
        await engine._graph_store.create_node(node)
        nodes.append(node)
    keep, duplicate, outgoing, incoming = nodes
    edges = []
    for source, target in ((duplicate, outgoing), (incoming, duplicate)):
        edge = MemoryEdge(source_id=source.id, target_id=target.id, user_id=user,
                          edge_type=EdgeType.RELATES_TO, confidence=.42, valid_from=stamp,
                          valid_to=stamp + timedelta(days=1), provenance_event_id=event.id,
                          metadata={"qualification": "before launch"}, created_at=stamp)
        await engine._graph_store.create_edge(edge)
        edges.append(edge)
    # Compare copies to the durable source, including the storage backend's
    # float32 confidence representation, rather than pre-storage Python floats.
    return keep, duplicate, await engine._graph_store.get_edges(node_ids=[str(duplicate.id)])


async def test_transferred_edges_retain_fields_and_repeat_identity_after_reopen(config, user):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, originals = await seed(engine, user)
        await _transfer_edges(engine, str(duplicate.id), str(keep.id))
        transferred = await engine._graph_store.get_edges(node_ids=[str(keep.id)])
        assert len(transferred) == 2
        for old in originals:
            source = keep.id if old.source_id == duplicate.id else old.source_id
            target = keep.id if old.target_id == duplicate.id else old.target_id
            new = next(e for e in transferred if (e.source_id, e.target_id) == (source, target))
            assert new.model_dump(exclude={"id", "source_id", "target_id"}) == old.model_dump(exclude={"id", "source_id", "target_id"})
        before = {str(e.id): e.model_dump() for e in transferred}
    async with MemoryEngine.open(config) as engine:
        await _transfer_edges(engine, str(duplicate.id), str(keep.id))
        assert {str(e.id): e.model_dump() for e in await engine._graph_store.get_edges(node_ids=[str(keep.id)])} == before


@pytest.mark.parametrize("alias", [False, True])
async def test_failed_edge_copy_preserves_active_source_and_retry_does_not_duplicate_edges(config, user, alias, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, originals = await seed(engine, user, alias)
        graph = engine._graph_store
        create = graph.create_edge
        calls = 0
        async def fail_second_copy(edge):
            nonlocal calls
            if edge.edge_type != EdgeType.SUPERSEDES:
                calls += 1
                if calls == 2:
                    raise RuntimeError("Authored edge-copy failure")
            return await create(edge)
        async def apply():
            if alias:
                return await resolve_aliases(engine, [AliasCandidate(str(keep.id), str(duplicate.id), "abbreviation", .99)])
            return await merge_duplicates(engine, [DuplicateCandidate(str(keep.id), str(duplicate.id), 1., "exact")])
        with monkeypatch.context() as patch:
            patch.setattr(graph, "create_edge", fail_second_copy)
            assert await apply() == 0
        assert (await engine.get_node(str(duplicate.id), user_id=user)).lifecycle_state == LifecycleState.TENTATIVE
        assert await apply() == 1
        copied = [e for e in await graph.get_edges(node_ids=[str(keep.id)]) if e.edge_type != EdgeType.SUPERSEDES]
        assert len(copied) == 2
        assert all(e.valid_to == originals[0].valid_to and e.provenance_event_id == originals[0].provenance_event_id for e in copied)


async def test_concurrent_identical_edge_transfers_converge(config, user):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        await asyncio.gather(*(_transfer_edges(engine, str(duplicate.id), str(keep.id)) for _ in range(3)))
        assert len(await engine._graph_store.get_edges(node_ids=[str(keep.id)])) == 2


async def test_copy_acknowledgment_failure_verifies_committed_values(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        create = engine._graph_store.create_edge
        async def committed_then_failed(edge):
            await create(edge)
            raise RuntimeError("Authored acknowledgment loss")
        with monkeypatch.context() as patch:
            patch.setattr(engine._graph_store, "create_edge", committed_then_failed)
            await _transfer_edges(engine, str(duplicate.id), str(keep.id))
        assert len(await engine._graph_store.get_edges(node_ids=[str(keep.id)])) == 2


async def test_explicit_self_relationship_remains_one_self_relationship(config, user):
    async with MemoryEngine.open(config) as engine:
        keep, duplicate, _ = await seed(engine, user)
        await engine._graph_store.create_edge(MemoryEdge(source_id=duplicate.id, target_id=duplicate.id,
            edge_type=EdgeType.RELATES_TO, user_id=user, metadata={"relation": "self reference"}))
        await _transfer_edges(engine, str(duplicate.id), str(keep.id))
        edges = await engine._graph_store.get_edges(node_ids=[str(keep.id)])
        assert len(edges) == 3
        assert len([e for e in edges if e.source_id == e.target_id == keep.id]) == 1
