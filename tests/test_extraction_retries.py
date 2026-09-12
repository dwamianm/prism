"""Transient extraction failures must not create an endless request loop."""

import asyncio
from unittest.mock import AsyncMock
import pytest

from prme.ingestion.errors import ExtractionError

from tests.test_concurrency import engine  # noqa: F401


async def test_extraction_retries_are_bounded_and_release_task_references(engine):  # noqa: F811
    pipeline = engine._pipeline
    pipeline._retry_delays = (0, 0, 0)
    provider = pipeline._extraction_provider
    provider.extract = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    with pytest.raises(ExtractionError) as failure:
        await engine.ingest("Alice uses Python", user_id="alice", wait_for_extraction=True)
    event_id = failure.value.event_id

    async def finish_retries():
        while pipeline._retry_tasks:
            await asyncio.gather(*list(pipeline._retry_tasks.values()))
            await asyncio.sleep(0)

    await asyncio.wait_for(finish_retries(), timeout=2)
    assert provider.extract.await_count == 4  # initial attempt plus three retries
    assert await engine.get_event(event_id) is not None
    assert pipeline._retry_tasks == {}


async def test_shutdown_cannot_spawn_new_retries_from_draining_work(engine):  # noqa: F811
    pipeline = engine._pipeline
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fail_after_release(*args, **kwargs):
        entered.set()
        await release.wait()
        raise RuntimeError("provider unavailable")

    pipeline._extraction_provider.extract = fail_after_release
    await engine.ingest("Alice uses Python", user_id="alice")
    await entered.wait()
    closing = asyncio.create_task(pipeline.shutdown())
    await asyncio.sleep(0)
    release.set()
    await closing
    assert pipeline._retry_tasks == {}
    assert not pipeline._background_tasks
