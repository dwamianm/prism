"""Recovery and concurrency guarantees for extractive summary publication."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from prme import MemoryEngine
from prme.organizer.consolidation import consolidate_cluster
from prme.types import LifecycleState, NodeType
from tests.test_consolidation_safety import _cluster
from tests.test_durable_ingestion import config, user  # noqa: F401


async def _summaries(engine, owner):
    return await engine.query_nodes(
        user_id=owner,
        node_type=NodeType.SUMMARY,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=100,
    )


async def test_repeated_unchanged_cluster_reuses_one_summary(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        cluster, _ = await _cluster(engine, user)
        first = await consolidate_cluster(engine, cluster)
        second = await consolidate_cluster(engine, cluster)

        assert second.id == first.id
        assert [node.id for node in await _summaries(engine, user)] == [first.id]
        edges = await engine._graph_store.get_edges(source_id=str(first.id))
        assert len(edges) == 3


async def test_changed_source_atomically_replaces_summary(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        cluster, nodes = await _cluster(engine, user)
        first = await consolidate_cluster(engine, cluster)
        await engine._graph_store.update_node(
            str(nodes[0].id), event_time=datetime(2025, 1, 1, tzinfo=timezone.utc)
        )

        second = await consolidate_cluster(engine, cluster)

        assert second.id != first.id
        assert [node.id for node in await _summaries(engine, user)] == [second.id]
        retired = await engine.get_node(str(first.id), include_superseded=True)
        assert retired is not None
        assert retired.lifecycle_state == LifecycleState.ARCHIVED


async def test_interrupted_publication_resumes_without_reembedding(
    config,  # noqa: F811
    user,  # noqa: F811
    monkeypatch,
):
    cluster = None
    async with MemoryEngine.open(config) as engine:
        cluster, _ = await _cluster(engine, user)
        original = engine._graph_store.publish_consolidation

        async def interrupted(_plan):
            raise RuntimeError("injected publication interruption")

        monkeypatch.setattr(engine._graph_store, "publish_consolidation", interrupted)
        with pytest.raises(RuntimeError, match="injected publication interruption"):
            await consolidate_cluster(engine, cluster)
        monkeypatch.setattr(engine._graph_store, "publish_consolidation", original)

    import prme.storage.embedding as embedding

    async def unexpected_embedding(*_args, **_kwargs):
        raise AssertionError("recovery repeated embedding work")

    monkeypatch.setattr(embedding, "encode_texts", unexpected_embedding)
    async with MemoryEngine.open(config) as engine:
        summary = await consolidate_cluster(engine, cluster)
        assert summary.lifecycle_state == LifecycleState.TENTATIVE
        assert len(await _summaries(engine, user)) == 1


async def test_concurrent_identical_publications_have_one_identity(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        cluster, _ = await _cluster(engine, user)
        first, second = await asyncio.gather(
            consolidate_cluster(engine, cluster),
            consolidate_cluster(engine, cluster),
        )

        assert first.id == second.id
        assert [node.id for node in await _summaries(engine, user)] == [first.id]


async def test_independent_engines_converge_on_one_summary(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as first_engine, MemoryEngine.open(config) as second_engine:
        cluster, _ = await _cluster(first_engine, user)
        first, second = await asyncio.gather(
            consolidate_cluster(first_engine, cluster),
            consolidate_cluster(second_engine, cluster),
        )

        assert first.id == second.id
        assert [node.id for node in await _summaries(first_engine, user)] == [first.id]


def test_convergence_requires_an_undeclared_adjacent_sibling():
    from types import SimpleNamespace
    from uuid import UUID, uuid4, uuid5

    from prme.storage.consolidation_publication import _converged_publication_id

    key = "4f0d5c88-1e39-4d2d-9c2f-0f4a4d4fbf25"
    request_hash = "a" * 64
    sibling = str(uuid5(UUID(key), f"{request_hash}:0"))
    prior = (sibling, request_hash, key, "0")

    def plan(**overrides):
        values = {"key": key, "request_hash": request_hash, "generation": 1, "previous": ()}
        values.update(overrides)
        return SimpleNamespace(**values)

    assert _converged_publication_id(plan(), [prior]) == sibling
    assert _converged_publication_id(
        plan(previous=(SimpleNamespace(id=uuid4()),)), [prior]
    ) == sibling
    assert _converged_publication_id(
        plan(previous=(SimpleNamespace(id=UUID(sibling)),)), [prior]
    ) is None
    assert _converged_publication_id(plan(request_hash="b" * 64), [prior]) is None
    assert _converged_publication_id(plan(key="0" * 8 + "-0000-4000-8000-" + "0" * 12), [prior]) is None
    assert _converged_publication_id(plan(generation=2), [prior]) is None
    assert _converged_publication_id(plan(), [prior, prior]) is None
    assert _converged_publication_id(
        plan(),
        [(str(uuid5(UUID(key), f"{request_hash}:9")), request_hash, key, "0")],
    ) is None
    assert _converged_publication_id(
        plan(), [(sibling, request_hash, key, "unknown")]
    ) is None


async def test_a_publication_that_reads_the_lineage_early_converges(
    config,  # noqa: F811
    user,  # noqa: F811
    monkeypatch,
):
    """A sibling that publishes between this plan's lineage and generation reads.

    The plan snapshot says there is no prior summary, then the sibling's
    publication moves the generation. The plan's request is identical to the
    one the sibling published, so publication must return the sibling's
    summary instead of raising a stale-lineage error.
    """
    async with MemoryEngine.open(config) as first_engine, MemoryEngine.open(config) as second_engine:
        cluster, _ = await _cluster(first_engine, user)
        original = second_engine._graph_store.consolidation_generation
        at_generation_read = asyncio.Event()
        sibling_published = asyncio.Event()

        async def stalled_generation(key):
            at_generation_read.set()
            await asyncio.wait_for(sibling_published.wait(), 10)
            return await original(key)

        monkeypatch.setattr(
            second_engine._graph_store, "consolidation_generation", stalled_generation
        )
        racing = asyncio.create_task(consolidate_cluster(second_engine, cluster))
        assert await asyncio.wait_for(at_generation_read.wait(), 10)
        published = await consolidate_cluster(first_engine, cluster)
        sibling_published.set()
        converged = await racing

        assert converged.id == published.id
        assert [node.id for node in await _summaries(first_engine, user)] == [published.id]


async def test_duck_source_mutation_conflicts_with_publication_claim(
    config,  # noqa: F811
    user,  # noqa: F811
    monkeypatch,
):
    import threading

    import duckdb

    if config.database_url:
        pytest.skip("PostgreSQL serializes dependencies with row locks")
    from prme.storage import consolidation_publication as publication

    async with MemoryEngine.open(config) as first_engine, MemoryEngine.open(config) as second_engine:
        cluster, nodes = await _cluster(first_engine, user)
        first = await consolidate_cluster(first_engine, cluster)
        await first_engine._graph_store.update_node(
            str(nodes[0].id), event_time=datetime(2025, 1, 1, tzinfo=timezone.utc)
        )
        entered, release = threading.Event(), threading.Event()

        def pause(stage):
            if stage == "validated":
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("test release missing")

        monkeypatch.setattr(publication, "_checkpoint", pause)
        task = asyncio.create_task(consolidate_cluster(first_engine, cluster))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(duckdb.TransactionException):
                await second_engine._graph_store.update_node(str(nodes[0].id), pinned=True)
        finally:
            release.set()
        second = await task

        assert second.id != first.id
        source = await first_engine.get_node(str(nodes[0].id))
        assert source is not None and source.pinned is False
