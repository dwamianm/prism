"""All public raw writes must reject ambiguous clocks before durable admission."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryClient, MemoryEngine
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize("method", ["store", "ingest_fast"])
async def test_raw_write_rejects_timezone_free_clock_before_admission(
    config, user, method
):
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="timezone-aware"):
            await getattr(engine, method)(
                "Imported event", user_id=user, event_time=datetime(2025, 4, 3, 9, 15)
            )
        assert await engine._event_store.get_by_user(user) == []
        assert await engine._event_store.processing_counts(user_id=user) == (0, 0)


@pytest.mark.parametrize("method", ["store", "ingest_fast"])
@pytest.mark.parametrize(
    "clock",
    [
        None,
        datetime(2025, 4, 3, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    ],
)
async def test_raw_write_clock_survives_pending_recovery_and_restart(
    config, user, method, clock, monkeypatch
):
    async with MemoryEngine.open(config) as engine:
        with monkeypatch.context() as failure:
            failure.setattr(
                engine._vector_index, "index", AsyncMock(side_effect=OSError("offline"))
            )
            event_id = await getattr(engine, method)(
                "Imported telescope observation", user_id=user, event_time=clock
            )
        assert (await engine.get_event(event_id, user_id=user)).event_time == clock
        assert (
            await engine.processing_status(event_id, user_id=user)
        ).status == "pending"
    async with MemoryEngine.open(config) as engine:
        result = await engine.process_pending(user_id=user, budget_ms=10000)
        assert result.pending == result.failed == 0
        event = await engine.get_event(event_id, user_id=user)
        nodes = await engine.get_event_nodes(event_id, user_id=user)
        assert (
            event.event_time == clock
            and len(nodes) == 1
            and nodes[0].event_time == clock
        )
        assert await engine.get_event(event_id, user_id=user + "-other") is None


@pytest.mark.parametrize("method", ["store", "ingest_fast"])
def test_sync_raw_writes_report_the_same_clock_error(config, user, method):
    with MemoryClient(config=config) as memory:
        with pytest.raises(ValueError, match="timezone-aware"):
            getattr(memory, method)(
                "Imported event", user_id=user, event_time=datetime(2025, 4, 3, 9, 15)
            )
