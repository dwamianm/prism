"""One failed search backend must not prevent indexing in the healthy one."""

from unittest.mock import AsyncMock, Mock

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
                if hasattr(engine._lexical_index, "replace_many"):
                    outage.setattr(engine._lexical_index, "replace_many", AsyncMock(side_effect=OSError("offline")))
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
            if failed == 'lexical' and hasattr(index, 'replace_many'):
                outage.setattr(index, 'replace_many', AsyncMock(side_effect=RuntimeError('offline')))
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
        pytest.skip("Local snapshot/commit failure boundaries")
    # Force a scheduled vector snapshot, rather than assuming that direct
    # materialization rewrites the entire vector index on every insertion.
    local = config.model_copy(update={"vector_save_interval": 1})
    async with MemoryEngine.open(local) as engine:
        event_id = await engine.ingest_fast(CONTENT, user_id=user)
        healthy = engine._lexical_index if failed == "vector" else engine._vector_index
        persist = "replace_many" if failed == "vector" else "index"
        committed = AsyncMock(wraps=getattr(healthy, persist))
        with monkeypatch.context() as outage:
            outage.setattr(healthy, persist, committed)
            broken = getattr(engine, f"_{failed}_index")
            if failed == "vector":
                outage.setattr(broken, "_save_snapshot", Mock(side_effect=OSError("disk")))
            else:
                # Fail the native durable boundary for both the batch attempt
                # and its per-document fallback, not an unused flush wrapper.
                original_writer = broken._ensure_writer
                class FailedCommit:
                    def __init__(self, writer):
                        self.writer = writer
                    def __getattr__(self, name):
                        return getattr(self.writer, name)
                    def commit(self):
                        raise OSError('disk')
                outage.setattr(broken, '_ensure_writer', lambda: FailedCommit(original_writer()))
            result = await engine.process_pending(user_id=user)
            assert (result.processed, result.pending, result.failed) == (0, 1, 1)
            committed.assert_awaited()
            assert [hit["node_id"] for hit in await healthy_hits(engine, failed, user)] == [event_id]
            if failed == "lexical":
                assert engine._conn.execute(
                    "SELECT count(*) FROM vector_metadata JOIN vector_payloads USING (vector_key) WHERE node_id = ?",
                    [event_id],
                ).fetchone()[0] == 1


async def test_both_indexes_failing_preserves_source_and_pending_work(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        event_id = await engine.ingest_fast(CONTENT, user_id=user)
        vector = AsyncMock(side_effect=RuntimeError("offline"))
        lexical = AsyncMock(side_effect=OSError("offline"))
        with monkeypatch.context() as outage:
            outage.setattr(engine._vector_index, "index", vector)
            outage.setattr(engine._lexical_index, "index", lexical)
            if hasattr(engine._lexical_index, 'replace_many'):
                outage.setattr(engine._lexical_index, 'replace_many', AsyncMock(side_effect=OSError('offline')))
            result = await engine.process_pending(user_id=user)
        assert (result.processed, result.pending, result.failed) == (0, 1, 1)
        vector.assert_awaited_once()
        lexical.assert_awaited_once()
        assert (await engine.get_event(event_id)).content == CONTENT
        assert (await engine.processing_status(event_id, user_id=user)).status == "pending"
        assert (await engine.process_pending(user_id=user)).processed == 1
