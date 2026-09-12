"""Developer-facing durable extraction recovery through MemoryEngine."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.errors import ExtractionError
from prme.ingestion.schema import ExtractionResult
from tests import test_durable_ingestion
from tests.test_extraction_journal import extraction

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_failed_extraction_survives_restart_and_explicit_scoped_processing(config, user):
    async with MemoryEngine.open(config) as engine:
        pipeline = engine._pipeline
        pipeline._retry_delays = ()
        pipeline._extraction_provider.extract = AsyncMock(side_effect=RuntimeError("private provider credential detail"))
        with pytest.raises(ExtractionError) as failure:
            await engine.ingest("Alice uses Python.", user_id=user, wait_for_extraction=True)
        event_id = failure.value.event_id
        status = await engine.extraction_status(event_id, user_id=user)
        assert status.status == "failed" and status.phase == "extraction"
        assert status.attempts == status.generation == 1 and status.last_error == "RuntimeError"
        assert "credential" not in status.model_dump_json()
    async with MemoryEngine.open(config) as engine:
        provider = AsyncMock(return_value=extraction())
        engine._pipeline._extraction_provider.extract = provider
        assert await engine.extraction_status(event_id, user_id=user) == status
        assert await engine.retry_extraction(event_id, user_id=user + "-other") is None
        assert (await engine.process_extractions(user_id=user + "-other")).processed == 0
        assert provider.await_count == 0
        # Ordinary retrieval repairs only raw indexing, never queued inference.
        await engine.retrieve("Python", user_id=user)
        assert provider.await_count == 0
        queued = await engine.retry_extraction(event_id, user_id=user)
        assert queued.status == "pending" and queued.attempts == 1
        assert (await engine.process_extractions(user_id=user, budget_ms=0)).pending == 1
        assert provider.await_count == 0
        result = await engine.process_extractions(user_id=user)
        assert (result.processed, result.pending, result.failed) == (1, 0, 0)
        complete = await engine.extraction_status(event_id, user_id=user)
        assert complete.status == complete.phase == "complete"
        assert complete.attempts == complete.generation == 2 and complete.last_error is None
        assert provider.await_count == 1
        assert (await engine.retry_extraction(event_id, user_id=user)) == complete
        assert (await engine.process_extractions(user_id=user)).processed == 0
        assert provider.await_count == 1


async def test_processing_completed_empty_extraction_has_a_receipt(config, user):
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult())
        event_id = await engine.ingest("Hello", user_id=user, wait_for_extraction=True)
        status = await engine.extraction_status(event_id, user_id=user)
        receipt = await engine._event_store.get_derivation_receipt(event_id, user_id=user)
        assert status.status == "complete" and status.plan_id == receipt.plan_id
        assert receipt.generation == 1 and not receipt.node_ids
        assert (await engine.processing_status(event_id, user_id=user)).status == "pending"


async def test_heartbeat_keeps_long_running_provider_work_owned(config, user):
    async with MemoryEngine.open(config) as engine:
        pipeline = engine._pipeline
        pipeline._extraction_lease_seconds = 0.15
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args, **kwargs):
            entered.set()
            await release.wait()
            return ExtractionResult()
        pipeline._extraction_provider.extract = slow
        event_id = await engine.ingest("Hello", user_id=user)
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.sleep(0.35)
            assert await engine._event_store.extraction_work.claim(user_id=user) is None
            assert (await engine.extraction_status(event_id, user_id=user)).generation == 1
        finally:
            release.set()
            await asyncio.gather(*list(pipeline._background_tasks))
        assert (await engine.extraction_status(event_id, user_id=user)).status == "complete"


async def test_cancelled_provider_work_is_left_retryable(config, user):
    async with MemoryEngine.open(config) as engine:
        pipeline = engine._pipeline
        entered = asyncio.Event()
        async def interrupted(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        pipeline._extraction_provider.extract = interrupted
        event_id = await engine.ingest("Hello", user_id=user)
        await asyncio.wait_for(entered.wait(), 5)
        tasks = list(pipeline._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        status = await engine.extraction_status(event_id, user_id=user)
        assert status.status == "pending" and status.last_error == "Cancelled"
        pipeline._extraction_provider.extract = AsyncMock(return_value=ExtractionResult())
        assert (await engine.process_extractions(user_id=user)).processed == 1


async def test_exit_after_source_acceptance_preserves_unstarted_extraction_job(config, user):
    if config.backend != "duckdb":
        pytest.skip("Local process exit; transaction admission is covered on both backends")
    script = """
import asyncio, os, sys
from prme import MemoryEngine, PRMEConfig
import prme.storage.engine as module
class Provider:
    model_name = 'durability-test'
    model_version = '1'
    dimension = 384
    async def embed(self, texts):
        raise AssertionError('No inference before acceptance')
async def main():
    db, vector, lexical, user = sys.argv[1:]
    module.create_embedding_provider = lambda _: Provider()
    config = PRMEConfig(db_path=db, vector_path=vector, lexical_path=lexical,
                        organizer={'opportunistic_enabled': False})
    async with MemoryEngine.open(config) as engine:
        original = engine._event_store.append
        async def accepted_then_exit(*args, **kwargs):
            await original(*args, **kwargs)
            os._exit(42)
        engine._event_store.append = accepted_then_exit
        await engine.ingest('Hello', user_id=user)
asyncio.run(main())
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, config.db_path, config.vector_path,
                         config.lexical_path, user], capture_output=True, timeout=30, env=env,
    )
    assert completed.returncode == 42, completed.stderr.decode()
    async with MemoryEngine.open(config) as engine:
        event = (await engine.get_events(user))[0]
        status = await engine.extraction_status(str(event.id), user_id=user)
        assert status.status == "pending" and status.attempts == 0
        provider = AsyncMock(return_value=ExtractionResult())
        engine._pipeline._extraction_provider.extract = provider
        result = await engine.process_extractions(user_id=user)
        assert result.processed == 1 and result.pending == 0
        assert provider.await_count == 1
        assert (await engine.extraction_status(str(event.id), user_id=user)).status == "complete"
