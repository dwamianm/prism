"""One failed search backend must not prevent indexing in the healthy one."""

from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


CONTENT = "The cobalt telescope belongs to Aster Observatory."


async def healthy_hits(engine, failed, owner):
    if failed == "vector":
        return await engine._lexical_index.search("cobalt telescope", owner, limit=10)
    return await engine._vector_index.search(CONTENT, owner)


@pytest.mark.parametrize("failed", ["vector", "lexical"])
async def test_store_indexes_healthy_backend_during_outage(config, user, monkeypatch, failed):
    async with MemoryEngine.open(config) as engine:
        with monkeypatch.context() as outage:
            if failed == "vector":
                outage.setattr(engine._vector_index._provider, "embed", AsyncMock(side_effect=RuntimeError("offline")))
            else:
                outage.setattr(engine._lexical_index, "index", AsyncMock(side_effect=OSError("offline")))
            event_id = await engine.store(CONTENT, user_id=user)
            nodes = await engine.get_event_nodes(event_id, user_id=user)
            assert len(nodes) == 1
            node_id = str(nodes[0].id)
            assert [hit["node_id"] for hit in await healthy_hits(engine, failed, user)] == [node_id]
            assert await healthy_hits(engine, failed, user + "-other") == []
            response = await engine.retrieve("cobalt telescope", user_id=user)
            assert node_id in [str(result.node.id) for result in response.results]

    async with MemoryEngine.open(config) as engine:
        assert await engine.get_event(event_id) is not None
        assert [hit["node_id"] for hit in await healthy_hits(engine, failed, user)] == [node_id]


@pytest.mark.parametrize("failed", ["vector", "lexical"])
async def test_pending_source_retains_healthy_index_and_retries_after_restart(config, user, monkeypatch, failed):
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest_fast(CONTENT, user_id=user)
        with monkeypatch.context() as outage:
            index = getattr(engine, f"_{failed}_index")
            outage.setattr(index, "index", AsyncMock(side_effect=RuntimeError("offline")))
            result = await engine.process_pending(user_id=user)
            assert (result.processed, result.pending, result.failed) == (0, 1, 1)
            assert [hit["node_id"] for hit in await healthy_hits(engine, failed, user)] == [event_id]
            assert await healthy_hits(engine, failed, user + "-other") == []

    async with MemoryEngine.open(config) as engine:
        assert [hit["node_id"] for hit in await healthy_hits(engine, failed, user)] == [event_id]
        assert (await engine.processing_status(event_id, user_id=user)).status == "pending"
        result = await engine.process_pending(user_id=user)
        assert (result.processed, result.pending, result.failed) == (1, 0, 0)
        assert await engine.count_nodes(user_id=user) == 1
        for backend in ("vector", "lexical"):
            assert [hit["node_id"] for hit in await healthy_hits(engine, backend, user)] == [event_id]
        assert (await engine.process_pending(user_id=user)).processed == 0


@pytest.mark.parametrize("failed", ["vector", "lexical"])
async def test_flush_failure_does_not_skip_other_backend(config, user, monkeypatch, failed):
    if config.backend != "duckdb":
        pytest.skip("Local indexes require explicit snapshot/commit flushing")
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest_fast(CONTENT, user_id=user)
        healthy = engine._lexical_index if failed == "vector" else engine._vector_index
        persist = "flush" if failed == "vector" else "save"
        committed = AsyncMock(wraps=getattr(healthy, persist))
        with monkeypatch.context() as outage:
            outage.setattr(healthy, persist, committed)
            broken = getattr(engine, f"_{failed}_index")
            outage.setattr(broken, "save" if failed == "vector" else "flush", AsyncMock(side_effect=OSError("disk")))
            result = await engine.process_pending(user_id=user)
            assert (result.processed, result.pending, result.failed) == (0, 1, 1)
            committed.assert_awaited()
            assert [hit["node_id"] for hit in await healthy_hits(engine, failed, user)] == [event_id]


async def test_both_indexes_failing_preserves_source_and_pending_work(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest_fast(CONTENT, user_id=user)
        vector = AsyncMock(side_effect=RuntimeError("offline"))
        lexical = AsyncMock(side_effect=OSError("offline"))
        with monkeypatch.context() as outage:
            outage.setattr(engine._vector_index, "index", vector)
            outage.setattr(engine._lexical_index, "index", lexical)
            result = await engine.process_pending(user_id=user)
        assert (result.processed, result.pending, result.failed) == (0, 1, 1)
        vector.assert_awaited_once()
        lexical.assert_awaited_once()
        assert (await engine.get_event(event_id)).content == CONTENT
        assert (await engine.processing_status(event_id, user_id=user)).status == "pending"
        assert (await engine.process_pending(user_id=user)).processed == 1
