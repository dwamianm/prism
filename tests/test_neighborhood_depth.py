"""A neighborhood walk returns each node once at its shortest distance, however many paths lead to it (#259)."""

import os
import random
from uuid import uuid4

import pytest

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, NodeType, Scope


@pytest.fixture(params=["duckdb", "postgres"])
async def store(request, isolated_graph_store):
    if request.param == "duckdb":
        yield isolated_graph_store
        return
    if not os.environ.get("PRME_TEST_DATABASE_URL"):
        pytest.skip("PRME_TEST_DATABASE_URL not set")
    import asyncpg

    from prme.storage.pg.graph_store import PgGraphStore
    from prme.storage.pg.schema import initialize_pg_database

    pool = await asyncpg.create_pool(os.environ["PRME_TEST_DATABASE_URL"], min_size=1, max_size=3)
    await initialize_pg_database(pool)
    yield PgGraphStore(pool)
    await pool.close()


async def build(store, *, nodes, pairs, user):
    made = []
    for i in range(nodes):
        node = MemoryNode(user_id=user, node_type=NodeType.ENTITY, scope=Scope.PERSONAL, content=f"n{i}")
        await store.create_node(node)
        made.append(node)
    for a, b in pairs:
        kind = EdgeType.MENTIONS if (a + b) % 2 else EdgeType.HAS_FACT
        await store.create_edge(MemoryEdge(source_id=made[a].id, target_id=made[b].id, edge_type=kind, user_id=user))
    return made


def distances(pairs, start, max_hops):
    """Shortest distance to every node within ``max_hops``, edges followed both ways."""
    reached, frontier = {start: 0}, [start]
    for depth in range(1, max_hops + 1):
        following = []
        for node in frontier:
            for a, b in pairs:
                for here, there in ((a, b), (b, a)):
                    if here == node and there not in reached:
                        reached[there] = depth
                        following.append(there)
        frontier = following
    del reached[start]
    return reached


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_neighborhood_depths_match_shortest_distances_on_a_graph_with_cycles_and_hubs(store, seed):
    rng = random.Random(seed)
    user = f"hood-{uuid4()}"
    # Three hubs take most edges; parallel edges, 2-cycles and self-loops are all allowed.
    pairs = [(rng.choice([0, 1, 2, rng.randrange(40)]), rng.randrange(40)) for _ in range(150)]
    pairs += [(3, 4), (4, 3), (5, 5)]
    made = await build(store, nodes=40, pairs=pairs, user=user)
    ids = {node.id: i for i, node in enumerate(made)}
    for start in (0, 3, 17, 39):
        for hops in (1, 2, 3):
            found = await store.get_neighborhood_with_depth(str(made[start].id), max_hops=hops)
            assert {ids[node.id]: depth for node, depth in found} == distances(pairs, start, hops)
            plain = await store.get_neighborhood(str(made[start].id), max_hops=hops)
            assert sorted(node.content for node in plain) == sorted(f"n{i}" for i in distances(pairs, start, hops))


async def test_neighborhood_respects_edge_types(store):
    user = f"hood-{uuid4()}"
    made = await build(store, nodes=4, pairs=[], user=user)
    for a, b, kind in ((0, 1, EdgeType.MENTIONS), (1, 2, EdgeType.HAS_FACT), (2, 3, EdgeType.MENTIONS)):
        await store.create_edge(MemoryEdge(source_id=made[a].id, target_id=made[b].id, edge_type=kind, user_id=user))
    found = await store.get_neighborhood_with_depth(
        str(made[0].id), max_hops=3, edge_types=[EdgeType.MENTIONS],
    )
    assert {node.content: depth for node, depth in found} == {"n1": 1}
