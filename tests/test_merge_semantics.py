"""Matching strings/vectors cannot erase attribution, type or validity distinctions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.epistemic.inference import infer_source_type
from prme.models.nodes import MemoryNode
from prme.organizer.alias_resolution import AliasCandidate, find_aliases, resolve_aliases
from prme.organizer.deduplication import DuplicateCandidate, find_duplicates, merge_duplicates
from prme.types import EdgeType, LifecycleState, NodeType
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


@pytest.mark.parametrize("difference", ["node_type", "entity_type", "role", "session", "event_time", "metadata", "valid_from"])
async def test_duplicate_application_preserves_semantic_distinctions(config, user, difference):
    async with MemoryEngine.open(config) as engine:
        first = {"node_type": NodeType.ENTITY if difference == "entity_type" else NodeType.FACT}
        second = dict(first)
        if difference == "node_type":
            second["node_type"] = NodeType.ENTITY
        elif difference == "entity_type":
            first["metadata"], second["metadata"] = {"entity_type": "person"}, {"entity_type": "country"}
        elif difference == "role":
            second["role"] = "assistant"
        elif difference == "session":
            first["session_id"], second["session_id"] = "episode-a", "episode-b"
        elif difference == "event_time":
            first["event_time"] = datetime(2025, 1, 1, tzinfo=timezone.utc)
            second["event_time"] = first["event_time"] + timedelta(days=1)
        elif difference == "metadata":
            first["metadata"], second["metadata"] = {"subject": "Alice"}, {"subject": "Bob"}
        for i, values in enumerate((first, second)):
            role = values.pop("role", "user")
            # Validity is immutable. Seed explicit authored graph inputs rather
            # than attempting to mutate a stored assertion's valid_from.
            valid_from = datetime(2025, 1, 1, tzinfo=timezone.utc)
            if difference == "valid_from":
                valid_from += timedelta(days=i)
            node = MemoryNode(content="Jordan", user_id=user, valid_from=valid_from,
                              source_type=infer_source_type(values["node_type"], role), **values)
            await engine._graph_store.create_node(node)
        nodes = await engine.query_nodes(user_id=user)
        before = {str(n.id): n.model_dump_json() for n in nodes}
        pair = DuplicateCandidate(*before, 1.0, "exact")
        assert await merge_duplicates(engine, [pair]) == 0
        assert {str(n.id): n.model_dump_json() for n in await engine.query_nodes(user_id=user)} == before
        assert await engine._graph_store.get_edges(node_ids=list(before)) == []


async def test_similarity_one_cannot_supersede_opposite_claims(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        for text in ("Aurora requires approval before deployment.", "Aurora does not require approval before deployment."):
            await engine._graph_store.create_node(MemoryNode(content=text, user_id=user, node_type=NodeType.FACT,
                                                            valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc)))
        nodes = await engine.query_nodes(user_id=user)
        monkeypatch.setattr(engine._vector_index, "search", AsyncMock(return_value=[{"node_id": str(n.id), "score": 1.0} for n in nodes]))
        pairs = await find_duplicates(engine, config.organizer, user_id=user)
        assert pairs
        assert await merge_duplicates(engine, pairs) == 0
        assert len(await engine.query_nodes(user_id=user)) == 2


@pytest.mark.parametrize("alias_type", ["case_variation", "semantic"])
async def test_alias_type_mismatch_cannot_link_or_merge(config, user, monkeypatch, alias_type):
    async with MemoryEngine.open(config) as engine:
        for text, kind in (("Jordan", "person"), ("jordan", "country")):
            await engine.store(text, user_id=user, node_type=NodeType.ENTITY, metadata={"entity_type": kind})
        nodes = await engine.query_nodes(user_id=user)
        ids = [str(n.id) for n in nodes]
        monkeypatch.setattr(engine._vector_index, "search", AsyncMock(return_value=[{"node_id": nid, "score": .99} for nid in ids]))
        assert await find_aliases(engine, config.organizer, user_id=user) == []
        assert await resolve_aliases(engine, [AliasCandidate(*ids, alias_type, .99)]) == 0
        assert len(await engine.query_nodes(user_id=user)) == 2
        assert await engine._graph_store.get_edges(node_ids=ids) == []


async def test_high_semantic_similarity_is_only_an_unverified_alias_link(config, user):
    async with MemoryEngine.open(config) as engine:
        for text in ("Alice", "Alicia"):
            await engine.store(text, user_id=user, node_type=NodeType.ENTITY, metadata={"entity_type": "person"})
        ids = [str(n.id) for n in await engine.query_nodes(user_id=user)]
        assert await resolve_aliases(engine, [AliasCandidate(*ids, "semantic", .999)]) == 1
        nodes = await engine.query_nodes(user_id=user, lifecycle_states=list(LifecycleState))
        assert all(n.lifecycle_state == LifecycleState.TENTATIVE for n in nodes)
        edges = await engine._graph_store.get_edges(node_ids=ids)
        assert len(edges) == 1 and edges[0].edge_type == EdgeType.RELATES_TO
        assert edges[0].metadata["identity_verified"] is False


@pytest.mark.parametrize("alias", [False, True])
async def test_unresolved_pronouns_are_not_canonical_identities(config, user, alias):
    async with MemoryEngine.open(config) as engine:
        for _ in range(2):
            await engine.store("I", user_id=user, node_type=NodeType.ENTITY, metadata={"entity_type": "person"})
        ids = [str(n.id) for n in await engine.query_nodes(user_id=user)]
        if alias:
            assert await resolve_aliases(engine, [AliasCandidate(*ids, "semantic", 1.0)]) == 0
        else:
            assert await merge_duplicates(engine, [DuplicateCandidate(*ids, 1.0, "exact")]) == 0
        assert len(await engine.query_nodes(user_id=user)) == 2
