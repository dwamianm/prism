"""Simulated maintenance must use the checkpoint clock, including native writes."""

from datetime import datetime, timedelta, timezone

from prme import MemoryEngine, PRMEConfig
from prme.models.edges import MemoryEdge
from prme.organizer.consolidation import (
    MemoryCluster,
    consolidate_cluster,
    forget_consolidated,
)
from prme.types import EdgeType, LifecycleState
from simulations.harness import _freeze_time
from tests.test_durable_ingestion import MockEmbeddingProvider


def _config(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    return PRMEConfig(
        db_path=str(tmp_path / "memory.duckdb"),
        vector_path=str(tmp_path / "vectors.usearch"),
        lexical_path=str(tmp_path / "lexical"),
        database_url=None,
        organizer={"opportunistic_enabled": False},
    )


async def test_consolidation_preserves_sources_until_simulated_age_threshold(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    origin = datetime(2001, 1, 1, tzinfo=timezone.utc)
    async with MemoryEngine.open(config) as engine:
        with _freeze_time(origin):
            for i in range(3):
                await engine.store(f"Rule {i}: require approval before launch.", user_id="clock", confidence=.6)
            nodes = await engine.query_nodes(user_id="clock")
        cluster = MemoryCluster(str(nodes[0].id), [str(n.id) for n in nodes], .99, "rules")
        with _freeze_time(origin + timedelta(days=1)):
            summary = await consolidate_cluster(engine, cluster)
            retired = await forget_consolidated(engine, cluster, str(summary.id), user_id="clock", preserve_recent_days=7)
        assert retired == 0, "Real wall time must not make one-day-old simulated sources eligible for retirement"
        for node in nodes:
            assert (await engine.get_node(str(node.id))).lifecycle_state == LifecycleState.TENTATIVE
        with _freeze_time(origin + timedelta(days=8)):
            assert await forget_consolidated(engine, cluster, str(summary.id), user_id="clock", preserve_recent_days=7) == 3


async def test_simulated_lifecycle_reinforcement_and_edges_share_checkpoint_clock(tmp_path, monkeypatch):
    config = _config(tmp_path, monkeypatch)
    origin = datetime(2001, 1, 1, tzinfo=timezone.utc)
    checkpoint = origin + timedelta(days=2)
    async with MemoryEngine.open(config) as engine:
        with _freeze_time(origin):
            await engine.store("Source one", user_id="clock")
            await engine.store("Source two", user_id="clock")
            nodes = await engine.query_nodes(user_id="clock")
        with _freeze_time(checkpoint):
            await engine.promote(str(nodes[0].id), user_id="clock")
            await engine.reinforce(str(nodes[1].id), user_id="clock")
            edge = MemoryEdge(source_id=nodes[0].id, target_id=nodes[1].id,
                              edge_type=EdgeType.RELATES_TO, user_id="clock")
            await engine._graph_store.create_edge(edge)
        promoted = await engine.get_node(str(nodes[0].id))
        reinforced = await engine.get_node(str(nodes[1].id))
        assert promoted.updated_at == checkpoint
        assert reinforced.last_reinforced_at == checkpoint
        assert edge.created_at == edge.valid_from == checkpoint
        neighbors = await engine._graph_store.get_neighborhood_with_depth(
            str(nodes[0].id), max_hops=1, valid_at=checkpoint + timedelta(hours=1))
        assert nodes[1].id in {node.id for node, _depth in neighbors}
