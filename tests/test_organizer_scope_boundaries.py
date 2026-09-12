"""Organizer matching must preserve personal/project boundaries within one owner."""
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.organizer.alias_resolution import AliasCandidate, find_aliases, resolve_aliases
from prme.organizer.deduplication import DuplicateCandidate, find_duplicates, merge_duplicates
from prme.types import LifecycleState, NodeType, Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def seed(engine, user, *, aliases=False):
    contents = ("PostgreSQL", "postgres") if aliases else ("The telescope is blue",) * 2
    for scope, content in zip((Scope.PERSONAL, Scope.PROJECT), contents):
        await engine.store(content, user_id=user, scope=scope,
                           node_type=NodeType.ENTITY if aliases else NodeType.FACT)
    return [str(node.id) for node in await engine.query_nodes(user_id=user)]


@pytest.mark.parametrize("aliases", [False, True])
async def test_string_matching_does_not_pair_same_owner_across_scopes(config, user, monkeypatch, aliases):
    async with MemoryEngine.open(config) as engine:
        await seed(engine, user, aliases=aliases)
        monkeypatch.setattr(engine._vector_index, "search", AsyncMock(return_value=[]))
        finder = find_aliases if aliases else find_duplicates
        assert await finder(engine, config.organizer, user_id=user) == []


@pytest.mark.parametrize("aliases", [False, True])
async def test_semantic_matching_checks_scope_even_if_index_returns_foreign_scope(config, user, monkeypatch, aliases):
    async with MemoryEngine.open(config) as engine:
        for scope, content in ((Scope.PERSONAL, "Private telescope"), (Scope.PROJECT, "Team observatory")):
            await engine.store(content, user_id=user, scope=scope,
                               node_type=NodeType.ENTITY if aliases else NodeType.FACT)
        ids = [str(node.id) for node in await engine.query_nodes(user_id=user)]
        search = AsyncMock(return_value=[{"node_id": node_id, "score": .99} for node_id in ids])
        monkeypatch.setattr(engine._vector_index, "search", search)
        finder = find_aliases if aliases else find_duplicates
        assert await finder(engine, config.organizer, user_id=user) == []
        assert {tuple(call.kwargs["scope"]) for call in search.call_args_list} == {
            (Scope.PERSONAL.value,), (Scope.PROJECT.value,),
        }


@pytest.mark.parametrize("mode", ["duplicate", "alias_merge", "alias_link"])
async def test_apply_rejects_cross_scope_pair_before_any_evidence_or_edge_write(config, user, mode):
    async with MemoryEngine.open(config) as engine:
        ids = await seed(engine, user, aliases=mode != "duplicate")
        before = [(await engine.get_node(node_id)).model_dump_json() for node_id in ids]
        if mode == "duplicate":
            count = await merge_duplicates(engine, [DuplicateCandidate(*ids, 1.0, "exact")])
        else:
            confidence = .95 if mode == "alias_merge" else .85
            count = await resolve_aliases(engine, [AliasCandidate(*ids, "semantic", confidence)])
        assert count == 0
        assert [(await engine.get_node(node_id)).model_dump_json() for node_id in ids] == before
        for node_id in ids:
            assert await engine._graph_store.get_edges(node_ids=[node_id]) == []
    async with MemoryEngine.open(config) as engine:
        for node_id in ids:
            node = await engine.get_node(node_id, user_id=user)
            assert node.lifecycle_state == LifecycleState.TENTATIVE
            assert len(node.evidence_refs) == 1


@pytest.mark.parametrize("aliases", [False, True])
async def test_same_scope_matches_still_merge(config, user, monkeypatch, aliases):
    async with MemoryEngine.open(config) as engine:
        contents = ("PostgreSQL", "postgres") if aliases else ("The telescope is blue",) * 2
        for content in contents:
            await engine.store(content, user_id=user, scope=Scope.PROJECT,
                               node_type=NodeType.ENTITY if aliases else NodeType.FACT)
        monkeypatch.setattr(engine._vector_index, "search", AsyncMock(return_value=[]))
        finder, apply = (find_aliases, resolve_aliases) if aliases else (find_duplicates, merge_duplicates)
        pairs = await finder(engine, config.organizer, user_id=user)
        assert len(pairs) == 1
        assert await apply(engine, pairs) == 1
        nodes = await engine.query_nodes(user_id=user, lifecycle_states=list(LifecycleState))
        assert sorted(node.lifecycle_state.value for node in nodes) == ["superseded", "tentative"]
        assert all(node.scope == Scope.PROJECT for node in nodes)


@pytest.mark.parametrize("aliases", [False, True])
async def test_semantic_matching_retains_only_same_namespace_candidates(config, user, monkeypatch, aliases):
    async with MemoryEngine.open(config) as engine:
        for owner, scope, content in (
            (user, Scope.PROJECT, "Team telescope"),
            (user, Scope.PROJECT, "Group observatory"),
            (user, Scope.PERSONAL, "Private instrument"),
            (user + "-other", Scope.PROJECT, "Foreign apparatus"),
        ):
            await engine.store(content, user_id=owner, scope=scope,
                               node_type=NodeType.ENTITY if aliases else NodeType.FACT)
        nodes = await engine.query_nodes(user_id=user)
        foreign = await engine.query_nodes(user_id=user + "-other")
        search = AsyncMock(return_value=[{"node_id": str(node.id), "score": .99} for node in nodes + foreign])
        monkeypatch.setattr(engine._vector_index, "search", search)
        finder = find_aliases if aliases else find_duplicates
        pairs = await finder(engine, config.organizer, user_id=user)
        assert len(pairs) == 1
        pair = pairs[0]
        actual = {pair.entity_a_id, pair.entity_b_id} if aliases else {pair.node_a_id, pair.node_b_id}
        assert actual == {str(node.id) for node in nodes if node.scope == Scope.PROJECT}
