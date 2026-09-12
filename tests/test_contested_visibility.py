"""Unresolved claims stay visible through each ordinary graph read path."""


from prme import MemoryEngine
from prme.models import MemoryEdge, MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_active_visibility_agrees_for_single_batch_and_neighborhood_reads(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        graph = engine._graph_store
        root = MemoryNode(user_id=user, node_type=NodeType.ENTITY, content="deployment")
        await graph.create_node(root)
        nodes = []
        for state in LifecycleState:
            node = MemoryNode(user_id=user, node_type=NodeType.FACT, content=state.value,
                              lifecycle_state=state)
            await graph.create_node(node)
            await graph.create_edge(MemoryEdge(source_id=root.id, target_id=node.id,
                                               user_id=user, edge_type=EdgeType.RELATES_TO))
            nodes.append(node)
        active = {n.id for n in nodes if n.lifecycle_state in (
            LifecycleState.TENTATIVE, LifecycleState.STABLE, LifecycleState.CONTESTED,
        )}
        for node in nodes:
            found = await engine.get_node(str(node.id), user_id=user)
            assert (found is not None) == (node.id in active)
            assert await engine.get_node(str(node.id), user_id=user + "-other") is None
        assert {n.id for n in await graph.get_nodes([str(n.id) for n in nodes])} == active
        assert {n.id for n in await graph.get_neighborhood(str(root.id), max_hops=1)} == active
        assert {n.id for n in await graph.get_nodes([str(n.id) for n in nodes], include_superseded=True)} == {n.id for n in nodes}


async def test_hybrid_retrieval_returns_and_labels_unresolved_claims(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        for content in ("the deployment region is east", "the deployment region is west"):
            await engine.store(content, user_id=user, node_type=NodeType.FACT)
        nodes = await engine.scan_nodes(user_id=user)
        assert len(nodes) == 2
        ids = [str(n.id) for n in nodes]
        await engine._graph_store.contradict(*ids)
        response = await engine.retrieve("deployment region", user_id=user)
        results = {str(c.node.id): c for c in response.results}
        assert set(results) == set(ids)
        assert all(c.conflict_flag and str(c.contradicts_id) in ids for c in results.values())
        assert all(c.node.lifecycle_state == LifecycleState.CONTESTED for c in results.values())
        await engine._graph_store.resolve_contradiction(*ids)
        response = await engine.retrieve("deployment region", user_id=user)
        assert {str(c.node.id) for c in response.results} == {ids[0]}
        assert not response.results[0].conflict_flag
