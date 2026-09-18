"""Tenant maintenance must never apply anonymous feedback to shared weights."""

import pytest
from unittest.mock import AsyncMock

from prme import MemoryEngine
from prme.organizer.jobs import run_job
from prme.quality.feedback import FeedbackSignal, FeedbackSignalType
from tests import test_durable_ingestion as fixtures

config = fixtures.config
user = fixtures.user


async def signals(engine):
    for _ in range(10):
        await engine.feedback(FeedbackSignal(query="authored", surfaced_node_ids=[],
                                             signal_type=FeedbackSignalType.CORRECTED))


@pytest.mark.parametrize("mode", ["end_session", "scoped_default", "unscoped_default"])
async def test_implicit_maintenance_preserves_global_weights_and_pending_feedback(config, user, mode):
    async with MemoryEngine.open(config) as engine:
        await signals(engine)
        original = engine._config.scoring.model_dump_json()
        if mode == "end_session":
            result = await engine.end_session(user_id=user)
        else:
            result = await engine.organize(user_id=user if mode == "scoped_default" else None,
                                           budget_ms=30000)
        assert "feedback_apply" not in result.jobs_run
        assert engine._config.scoring.model_dump_json() == original
        assert engine._retrieval_pipeline._scoring_weights.model_dump_json() == original
        assert len(engine._feedback_tracker) == 10


@pytest.mark.parametrize("direct", [False, True])
async def test_scoped_global_feedback_request_fails_without_consuming_signals(config, user, direct, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await signals(engine)
        original = engine._config.scoring.model_dump_json()
        debt = AsyncMock(side_effect=AssertionError("Invalid job selection must be rejected before work"))
        monkeypatch.setattr(engine._materialization_queue, "debt", debt)
        with pytest.raises(ValueError, match="unscoped"):
            if direct:
                await run_job("feedback_apply", engine, config.organizer, 5000, user)
            else:
                await engine.organize(user_id=user, jobs=["promote", "feedback_apply"])
        debt.assert_not_called()
        assert engine._config.scoring.model_dump_json() == original
        assert len(engine._feedback_tracker) == 10


async def test_explicit_unscoped_operator_can_apply_legacy_feedback(config, user):
    async with MemoryEngine.open(config) as engine:
        await signals(engine)
        original = engine._config.scoring.model_dump_json()
        result = await engine.organize(jobs=["feedback_apply"])
        assert result.per_job["feedback_apply"].details["scope"] == "global"
        assert engine._config.scoring.model_dump_json() != original
        assert len(engine._feedback_tracker) == 0
