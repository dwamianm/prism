"""Owner-scoped learning snapshots are bounded, durable and independent of paging."""
from uuid import uuid4

import pytest

from prme import MemoryEngine, RelevanceSubmission
from prme.types import Scope
from tests import test_durable_ingestion
from tests.previous_defaults import previous_defaults

durable_config = test_durable_ingestion.config


@pytest.fixture
def config(durable_config):
    # Learning snapshots fit the weighted formula's receipts: they keep the previous retrieval
    # defaults, which rank fusion and the reader format replaced on 2026-09-24.
    return previous_defaults(durable_config)


user = test_durable_ingestion.user


async def collect(engine, owner):
    await engine.store("telescope blue", user_id=owner, scope=Scope.PROJECT)
    await engine.store("telescope red", user_id=owner, scope=Scope.PROJECT)
    response = await engine.retrieve("telescope", user_id=owner, scope=Scope.PROJECT)
    submission = RelevanceSubmission(request_id=response.metadata.request_id,
        labels={c.node.id: i == 0 for i, c in enumerate(response.results)})
    return await engine.record_relevance(submission, user_id=owner)


async def test_public_evaluation_is_scoped_repeatable_and_survives_restart(config, user):
    async with MemoryEngine.open(config) as engine:
        record = await collect(engine, user)
        await collect(engine, user + "-other")
        weights = engine._config.scoring.model_dump_json()
        report = await engine.evaluate_learning(user_id=user, scopes=[Scope.PROJECT])
        assert report.decision == "insufficient_data"
        assert report.feedback_ids == (record.feedback_id,)
        assert report.coverage["explicit_pairs"] == 1
        assert (await engine.evaluate_learning(user_id=user, scopes=[Scope.PERSONAL])).coverage["explicit_pairs"] == 0
        assert engine._config.scoring.model_dump_json() == weights
    async with MemoryEngine.open(config) as engine:
        restored = await engine.evaluate_learning(user_id=user, scopes=[Scope.PROJECT])
        assert restored.model_dump_json() == report.model_dump_json()


async def test_feedback_admitted_after_snapshot_cut_is_not_part_of_training(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        first = await collect(engine, user)
        original = engine._relevance._query
        admitted = False

        async def after_cut(sql, *args):
            nonlocal admitted
            rows = await original(sql, *args)
            if "ORDER BY id LIMIT" in sql and not admitted:
                admitted = True
                await engine.record_relevance(RelevanceSubmission(request_id=first.request_id,
                    labels=first.labels), user_id=user)
            return rows

        monkeypatch.setattr(engine._relevance, "_query", after_cut)
        report = await engine.evaluate_learning(user_id=user, scopes=[Scope.PROJECT])
        assert admitted and report.feedback_ids == (first.feedback_id,)
        assert len(await engine.list_relevance(user_id=user)) == 2


async def test_snapshot_never_silently_truncates_feedback(config, user):
    async with MemoryEngine.open(config) as engine:
        record = await collect(engine, user)
        await engine.record_relevance(RelevanceSubmission(request_id=record.request_id,
            labels=record.labels), user_id=user)
        with pytest.raises(ValueError, match="exceeds max_records"):
            await engine.evaluate_learning(user_id=user, max_records=1)
        for invalid in (0, True, 100001):
            with pytest.raises(ValueError, match="max_records"):
                await engine.evaluate_learning(user_id=user, max_records=invalid)


async def test_snapshot_rejects_missing_and_duplicate_receipts(config, user):
    async with MemoryEngine.open(config) as engine:
        record = await collect(engine, user)
        rows = await engine._relevance._query(
            "SELECT payload FROM operations WHERE op_type = 'RETRIEVAL_REQUEST' AND target_id = $1",
            str(record.request_id))
        await engine._relevance._query(
            "INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at) "
            "VALUES ($1, 'RETRIEVAL_REQUEST', $2, $3, $4, now())",
            str(uuid4()), str(record.request_id), rows[0]["payload"], user)
        with pytest.raises(ValueError, match="Ambiguous"):
            await engine.evaluate_learning(user_id=user)
        await engine._relevance._query("DELETE FROM operations WHERE op_type = 'RETRIEVAL_REQUEST' AND target_id = $1",
                                       str(record.request_id))
        with pytest.raises(ValueError, match="missing retrieval receipt"):
            await engine.evaluate_learning(user_id=user)
