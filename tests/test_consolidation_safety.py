"""Consolidation must not erase omitted qualifiers or cross a namespace."""

from datetime import datetime, timezone

from prme import MemoryEngine
from prme.organizer.consolidation import MemoryCluster, consolidate_cluster, forget_consolidated
from prme.types import LifecycleState, Scope
from tests.test_durable_ingestion import config, user  # noqa: F401


async def _cluster(engine, owner):
    for i in range(4):
        await engine.store(
            f"Deployment rule {i}: use blue unless the account is suspended.",
            user_id=owner, confidence=.7 - i * .1, scope=Scope.PROJECT,
            event_time=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
        )
    nodes = await engine.query_nodes(user_id=owner)
    nodes.sort(key=lambda n: -n.confidence)
    return MemoryCluster(str(nodes[0].id), [str(n.id) for n in nodes], .99, "deployment"), nodes


async def test_unrepresented_sources_remain_retrievable(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        cluster, nodes = await _cluster(engine, user)
        summary = await consolidate_cluster(engine, cluster)
        assert summary.scope == Scope.PROJECT
        assert "3 of 4" in summary.content
        for n in nodes[:3]:
            assert n.content in summary.content
            assert str(n.event_time) in summary.content
            assert set(n.evidence_refs) <= set(summary.evidence_refs)
        retired = await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0)
        assert retired == 3
        omitted = await engine.get_node(str(nodes[3].id), include_superseded=True)
        assert omitted.lifecycle_state == LifecycleState.TENTATIVE
        result = await engine.retrieve("Deployment rule 3", user_id=user, scope=Scope.PROJECT)
        assert nodes[3].id in {c.node.id for c in result.results}


async def test_changed_sources_and_pinned_memories_are_preserved(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        cluster, nodes = await _cluster(engine, user)
        summary = await consolidate_cluster(engine, cluster)
        await engine._graph_store.update_node(str(nodes[0].id), event_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
        await engine._graph_store.update_node(str(nodes[1].id), pinned=True)
        assert await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0) == 1
        for n in nodes[:2]:
            assert (await engine.get_node(str(n.id), include_superseded=True)).lifecycle_state == LifecycleState.TENTATIVE


async def test_retired_summary_does_not_authorize_further_retirement(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        cluster, _ = await _cluster(engine, user)
        summary = await consolidate_cluster(engine, cluster)
        await engine.archive(str(summary.id), user_id=user)
        assert await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0) == 0


async def test_cross_scope_cluster_cannot_move_private_content(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        cluster, _ = await _cluster(engine, user)
        await engine.store("private medical appointment", user_id=user, scope=Scope.PERSONAL)
        private = (await engine.query_nodes(user_id=user, scope=Scope.PERSONAL))[0]
        cluster.member_ids.append(str(private.id))
        summary = await consolidate_cluster(engine, cluster)
        assert "medical" not in summary.content
        assert private.id not in summary.evidence_refs
        await forget_consolidated(engine, cluster, str(summary.id), user_id=user, preserve_recent_days=0)
        assert (await engine.get_node(str(private.id), include_superseded=True)).lifecycle_state == LifecycleState.TENTATIVE
