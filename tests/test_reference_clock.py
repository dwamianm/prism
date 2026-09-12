"""Public historical-query clock contract, independent of benchmark labels."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from prme import MemoryEngine
from tests.test_durable_ingestion import config, user  # noqa: F401


async def test_same_clock_replays_scores_and_is_logged(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        await engine.store("The release uses Python", user_id=user)
        base = datetime.now(timezone.utc) + timedelta(days=1)
        first = await engine.retrieve("release language", user_id=user, reference_time=base)
        later = await engine.retrieve("release language", user_id=user, reference_time=base + timedelta(days=365))
        again = await engine.retrieve("release language", user_id=user, reference_time=base)
        assert first.metadata.reference_time == base
        assert first.score_traces == again.score_traces
        assert [c.node.id for c in first.results] == [c.node.id for c in again.results]
        assert first.score_traces[0].salience > later.score_traces[0].salience
        if engine._conn is not None:
            payload = engine._conn.execute(
                "SELECT payload FROM operations WHERE target_id = ?",
                [str(first.metadata.request_id)],
            ).fetchone()[0]
            assert json.loads(payload)["reference_time"] == base.isoformat()


async def test_engine_rejects_naive_reference_time(config, user):  # noqa: F811
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="reference_time must include a timezone"):
            await engine.retrieve("hello", user_id=user, reference_time=datetime(2024, 1, 1))
