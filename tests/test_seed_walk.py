"""One graph walk from all seeds gives what the per-seed walks give (BOUNDED-GRAPH-WALK.md, 2026-11-12).

``walk_from_seeds`` replaces one ``get_neighborhood_with_depth`` walk per seed
in the graph candidate stage. These tests build random graphs with hubs,
cycles, edges to another user's nodes, unaccepted alias proposals, retired
nodes and validity windows, and check that the one walk returns every node
the per-seed walks return, at the same fewest hops, a seed at its fewest hops
from another seed, that ``walk_links`` gives the links of the edge records the
seed reach count read before, and that the candidate stage gives the same
candidates, records, proximities and seed reach counts on both paths.
``tests/test_pg_seed_walk.py`` runs the same checks on PostgreSQL.
"""

import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.retrieval.candidates import _generate_graph_candidates
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import QueryAnalysis
from prme.types import EdgeType, LifecycleState, NodeType, QueryIntent, RetrievalMode, Scope

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SUMMARY_HIDDEN = {"summary_publication_kind": "hierarchical_source_excerpts_v2"}


class PerSeed:
    """The store without ``walk_from_seeds``, so the candidate stage walks from each seed in turn."""

    def __init__(self, store):
        self._store = store

    def __getattr__(self, name):
        if name == "walk_from_seeds":
            raise AttributeError(name)
        return getattr(self._store, name)


async def random_graph(store, rng, *, dangling: bool):
    """A graph of 60 nodes: 12 seed entities, three hubs, another user's nodes and every filter case."""
    user, other = f"walk-{uuid4()}", f"other-{uuid4()}"
    kinds = [NodeType.FACT, NodeType.NOTE, NodeType.EVENT, NodeType.PREFERENCE, NodeType.SUMMARY, NodeType.ENTITY]
    nodes = []
    for i in range(60):
        seed = i < 12
        node_type = NodeType.ENTITY if seed else rng.choice(kinds)
        metadata = None
        if node_type == NodeType.ENTITY and rng.random() < .2:
            metadata = {"owner_name": "Sam"}
        elif node_type == NodeType.SUMMARY and rng.random() < .5:
            metadata = dict(SUMMARY_HIDDEN)
        elif rng.random() < .3:
            metadata = {"note": i}
        start = T0 + timedelta(days=rng.randrange(10))
        nodes.append(MemoryNode(
            user_id=other if not seed and rng.random() < .1 else user,
            node_type=node_type,
            scope=rng.choice([Scope.PERSONAL, Scope.PROJECT]),
            content=f"Seed{i}" if seed else f"n{i}",
            metadata=metadata,
            confidence=rng.choice([.4, .5, .5, .7, .9]),
            lifecycle_state=rng.choice([LifecycleState.STABLE] * 6 + [LifecycleState.SUPERSEDED, LifecycleState.TENTATIVE]),
            valid_from=start,
            valid_to=start + timedelta(days=rng.randrange(1, 10)) if rng.random() < .3 else None,
            created_at=T0 + timedelta(seconds=i * 7 + rng.random()),
        ))
    for node in nodes:
        await store.create_node(node)
    hubs = [12, 13, 14]
    for _ in range(170):
        a = rng.choice(hubs + [rng.randrange(60)] * 3)
        b = rng.randrange(60)
        alias = rng.random() < .08
        await store.create_edge(MemoryEdge(
            source_id=nodes[a].id, target_id=nodes[b].id,
            edge_type=EdgeType.RELATES_TO if alias else rng.choice([EdgeType.MENTIONS, EdgeType.HAS_FACT]),
            user_id=user,
            metadata={"relation": "alias", "identity_verified": False} if alias else None,
        ))
    if dangling:
        # An edge to a node that no longer exists: it belongs to nobody, so the walk passes it.
        await store.create_edge(MemoryEdge(
            source_id=nodes[0].id, target_id=uuid4(), edge_type=EdgeType.MENTIONS, user_id=user,
        ))
    return user, nodes


async def per_seed_depths(store, seeds, *, max_hops, valid_at, user):
    depths = {}
    for seed in seeds:
        for node, depth in await store.get_neighborhood_with_depth(
            seed, max_hops=max_hops, valid_at=valid_at, user_id=user,
        ):
            if str(node.id) != seed:
                depths[str(node.id)] = min(depth, depths.get(str(node.id), depth))
    return depths


async def check_walk(store, seed: int, *, dangling: bool):
    rng = random.Random(seed)
    user, nodes = await random_graph(store, rng, dangling=dangling)
    by_id = {str(node.id): node for node in nodes}
    for count in (1, 2, 5, 12):
        seeds = [str(node.id) for node in rng.sample(nodes[:12], count)]
        for max_hops in (1, 2, 3):
            for valid_at in (None, T0 + timedelta(days=5)):
                walked = await store.walk_from_seeds(seeds, max_hops=max_hops, valid_at=valid_at, user_id=user)
                found = {node.id: depth for node, depth in walked}
                assert len(found) == len(walked), "a node came back twice"
                assert found == await per_seed_depths(store, seeds, max_hops=max_hops, valid_at=valid_at, user=user)
                for node, _depth in walked:
                    full = by_id[node.id]
                    assert (node.node_type, node.user_id, node.scope) == (full.node_type, full.user_id, full.scope)
                    assert node.confidence == pytest.approx(full.confidence)
                    assert (node.created_at, node.valid_from, node.valid_to) == (
                        full.created_at, full.valid_from, full.valid_to)
                    if node.node_type in (NodeType.ENTITY, NodeType.SUMMARY):
                        assert node.metadata == full.metadata
                    else:
                        assert node.metadata is None
    return user, nodes


async def check_links(store, seed: int, *, dangling: bool):
    """``walk_links`` gives the two ends of the edges ``get_edges`` gives, less self links and unaccepted aliases."""
    from prme.retrieval.candidates import _unverified_alias

    rng = random.Random(seed)
    user, nodes = await random_graph(store, rng, dangling=dangling)
    loop = nodes[3]
    await store.create_edge(MemoryEdge(source_id=loop.id, target_id=loop.id, edge_type=EdgeType.MENTIONS,
                                       user_id=user))
    for count in (1, 5, 20, 60):
        ids = [str(node.id) for node in rng.sample(nodes, count)]
        want = sorted(
            (str(edge.source_id), str(edge.target_id))
            for edge in await store.get_edges(node_ids=ids)
            if edge.source_id != edge.target_id and not _unverified_alias(edge)
        )
        assert sorted(await store.walk_links(ids)) == want
    assert await store.walk_links([]) == []


async def check_candidates(store, seed: int):
    rng = random.Random(seed)
    user, nodes = await random_graph(store, rng, dangling=False)
    analysis = QueryAnalysis(query="what links the seeds", intent=QueryIntent.SEMANTIC, entities=["Seed"],
                             retrieval_mode=RetrievalMode.DEFAULT)
    for cap in (8, 150):
        config = PackingConfig(graph_max_candidates=cap)
        for options in ({}, {"scope": [Scope.PERSONAL]}, {"time_from": T0 + timedelta(days=3)},
                        {"time_to": T0 + timedelta(days=4)}):
            one = await _generate_graph_candidates(analysis, store, user, config, seed_reach=True, **options)
            each = await _generate_graph_candidates(analysis, PerSeed(store), user, config, seed_reach=True,
                                                    **options)
            assert [c["node"].model_dump(exclude={"last_reinforced_at"}) for c in one] == [
                c["node"].model_dump(exclude={"last_reinforced_at"}) for c in each]
            assert [(c["graph_proximity"], c["graph_seed_reach"]) for c in one] == [
                (c["graph_proximity"], c["graph_seed_reach"]) for c in each]
            assert all(isinstance(c["node"], MemoryNode) for c in one)
            assert len(one) <= cap


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
async def test_one_walk_reaches_the_nodes_of_the_per_seed_walks_at_the_same_hops(isolated_graph_store, seed):
    await check_walk(isolated_graph_store, seed, dangling=True)


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_the_walk_links_are_the_edges_a_walk_steps_along(isolated_graph_store, seed):
    await check_links(isolated_graph_store, seed, dangling=True)


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_the_candidate_stage_gives_the_same_candidates_with_one_walk(isolated_graph_store, seed):
    await check_candidates(isolated_graph_store, seed)


async def test_a_seed_is_reached_only_from_another_seed(isolated_graph_store):
    store = isolated_graph_store
    user = f"walk-{uuid4()}"
    a, b, c, lone = (MemoryNode(user_id=user, node_type=NodeType.ENTITY, content=name) for name in "ABCL")
    middle = MemoryNode(user_id=user, node_type=NodeType.FACT, content="A and B")
    for node in (a, b, c, lone, middle):
        await store.create_node(node)
    for source, target in ((a, middle), (middle, b), (b, c)):
        await store.create_edge(MemoryEdge(source_id=source.id, target_id=target.id, edge_type=EdgeType.MENTIONS,
                                           user_id=user))
    walked = await store.walk_from_seeds([str(a.id), str(c.id), str(lone.id)], max_hops=3, user_id=user)
    # A is 3 hops from C, C is 3 hops from A, and nothing reaches the lone seed.
    assert {node.id: depth for node, depth in walked} == {
        str(middle.id): 1, str(b.id): 1, str(a.id): 3, str(c.id): 3}
    walked = await store.walk_from_seeds([str(a.id), str(c.id)], max_hops=2, user_id=user)
    assert {node.id: depth for node, depth in walked} == {str(middle.id): 1, str(b.id): 1}


async def test_the_walk_never_steps_onto_another_users_node(isolated_graph_store):
    store = isolated_graph_store
    user, other = f"walk-{uuid4()}", f"other-{uuid4()}"
    seed = MemoryNode(user_id=user, node_type=NodeType.ENTITY, content="Acme")
    foreign = MemoryNode(user_id=other, node_type=NodeType.FACT, content="theirs")
    behind = MemoryNode(user_id=user, node_type=NodeType.FACT, content="only through theirs")
    for node in (seed, foreign, behind):
        await store.create_node(node)
    for source, target in ((seed, foreign), (foreign, behind)):
        await store.create_edge(MemoryEdge(source_id=source.id, target_id=target.id, edge_type=EdgeType.MENTIONS,
                                           user_id=user))
    assert await store.walk_from_seeds([str(seed.id)], max_hops=3, user_id=user) == []
    # Another user's seed has no walk.
    assert await store.walk_from_seeds([str(foreign.id)], max_hops=3, user_id=user) == []


async def test_a_store_without_the_walk_keeps_the_per_seed_walks(isolated_graph_store):
    calls = []

    class Counting(PerSeed):
        async def get_neighborhood_with_depth(self, node_id, **kwargs):
            calls.append(node_id)
            return await self._store.get_neighborhood_with_depth(node_id, **kwargs)

    user, nodes = await random_graph(isolated_graph_store, random.Random(7), dangling=False)
    analysis = QueryAnalysis(query="seeds", intent=QueryIntent.SEMANTIC, entities=["Seed"],
                             retrieval_mode=RetrievalMode.DEFAULT)
    await _generate_graph_candidates(analysis, Counting(isolated_graph_store), user, PackingConfig())
    seeds = {str(node.id) for node in nodes[:12] if node.lifecycle_state != LifecycleState.SUPERSEDED}
    assert set(calls) == seeds and len(calls) == len(seeds)
