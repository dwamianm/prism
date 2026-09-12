"""A cancelled retrieval must not release a connection still logging in a thread."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from prme import MemoryEngine
from tests import test_durable_ingestion
from tests.test_storage_cancellation import cancel_blocked_native_call

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_cancelled_retrieval_log_finishes_before_connection_reuse(config, user, monkeypatch):
    if config.backend != "duckdb":
        pytest.skip("PostgreSQL uses native async transactions")
    async with MemoryEngine.open(config) as engine:
        finished = threading.Event()
        def execute(*args, **kwargs):
            try:
                return engine._conn.execute(*args, **kwargs)
            finally:
                finished.set()
        proxy = SimpleNamespace(execute=execute)
        monkeypatch.setattr(engine._retrieval_pipeline, "_conn", proxy)
        try:
            await cancel_blocked_native_call(
                monkeypatch, proxy, "execute",
                engine.retrieve("cobalt telescope", user_id=user),
                engine._retrieval_pipeline._conn_lock,
            )
        finally:
            assert await asyncio.to_thread(finished.wait, 5)
        assert engine._conn.execute(
            "SELECT count(*) FROM operations WHERE op_type = 'RETRIEVAL_REQUEST' AND actor_id = ?",
            [user],
        ).fetchone()[0] == 1
