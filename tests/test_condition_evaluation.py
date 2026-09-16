"""Conditional claims have an explicit, auditable, retry-safe lifecycle."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from prme import ConditionEvaluationMethod, ConditionState, MemoryEngine
from prme.storage.condition_evaluation import read_record
from prme.types import EpistemicType, RetrievalMode, Scope
from tests import test_durable_ingestion


config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def _records(engine, node_id):
    graph = engine._graph_store
    if engine._config.backend == "postgres":
        async with graph._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT payload FROM operations WHERE target_id=$1 "
                "AND op_type='EPISTEMIC_TRANSITION' ORDER BY created_at,id",
                node_id,
            )
            return [read_record(row["payload"]) for row in rows]
    async with graph._conn_lock:
        rows = graph._conn.execute(
            "SELECT payload FROM operations WHERE target_id=? "
            "AND op_type='EPISTEMIC_TRANSITION' ORDER BY created_at,id",
            [node_id],
        ).fetchall()
        return [read_record(row[0]) for row in rows]


async def _conditional(engine, owner, *, scope=Scope.PERSONAL):
    event_id = await engine.store(
        "If the release is approved, deploy Atlas.",
        user_id=owner,
        scope=scope,
        epistemic_type=EpistemicType.CONDITIONAL,
        metadata={
            "condition": "the release is approved",
            "condition_state": "unknown",
        },
    )
    return (await engine.get_event_nodes(event_id, user_id=owner))[0]


async def test_condition_evaluation_controls_retrieval_and_survives_restart(config, user):
    evaluated_at = datetime(2026, 9, 13, 12, 30, tzinfo=timezone.utc)
    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        node = await _conditional(engine, user, scope=Scope.PROJECT)
        evidence = await engine.store(
            "Release Atlas was approved.", user_id=user, scope=Scope.PROJECT
        )
        hidden = await engine.retrieve("deploy Atlas", user_id=user, scope=Scope.PROJECT)
        assert str(node.id) not in {str(item.node.id) for item in hidden.results}

        updated = await engine.evaluate_condition(
            str(node.id),
            ConditionState.TRUE,
            user_id=user,
            evidence_id=evidence,
            request_id=request_id,
            evaluation_method=ConditionEvaluationMethod.TOOL,
            reason="Approval service returned approved",
            actor_id="release-controller",
            evaluated_at=evaluated_at,
        )
        assert updated.epistemic_type == EpistemicType.CONDITIONAL
        assert updated.metadata["condition_state"] == "true"
        assert updated.metadata["condition_evaluation_method"] == "tool"
        assert updated.metadata["condition_evaluated_by"] == "release-controller"
        assert updated.metadata["condition_evaluated_at"] == evaluated_at.isoformat()
        assert updated.metadata["condition_evaluation_evidence_id"] == evidence
        assert evidence in {str(ref) for ref in updated.evidence_refs}
        visible = await engine.retrieve("deploy Atlas", user_id=user, scope=Scope.PROJECT)
        result = next(item for item in visible.results if item.node.id == node.id)
        assert result.score_trace.epistemic_weight == 0.9

        records = await _records(engine, str(node.id))
        assert len(records) == 1
        record = records[0]
        assert record.from_state == ConditionState.UNKNOWN
        assert record.to_state == ConditionState.TRUE
        assert record.before.metadata["condition_state"] == "unknown"
        assert record.after == updated
        assert record.evidence_id and str(record.evidence_id) == evidence

    async with MemoryEngine.open(config) as engine:
        restored = await engine.get_node(str(node.id), user_id=user)
        assert restored == updated
        replayed = await engine.evaluate_condition(
            str(node.id),
            "true",
            user_id=user,
            evidence_id=evidence,
            request_id=request_id,
            evaluation_method="tool",
            reason="Approval service returned approved",
            actor_id="release-controller",
            evaluated_at=evaluated_at,
        )
        assert replayed == restored
        assert len(await _records(engine, str(node.id))) == 1

        rejected = await engine.evaluate_condition(
            str(node.id), "false", user_id=user, request_id=str(uuid4()),
            reason="Approval was withdrawn",
        )
        assert rejected.metadata["condition_state"] == "false"
        hidden = await engine.retrieve("deploy Atlas", user_id=user, scope=Scope.PROJECT)
        assert str(node.id) not in {str(item.node.id) for item in hidden.results}
        explicit = await engine.retrieve(
            "deploy Atlas", user_id=user, scope=Scope.PROJECT,
            retrieval_mode=RetrievalMode.EXPLICIT,
        )
        assert str(node.id) in {str(item.node.id) for item in explicit.results}


async def test_condition_evaluation_rejects_invalid_or_cross_scope_inputs(config, user):
    async with MemoryEngine.open(config) as engine:
        node = await _conditional(engine, user)
        ordinary_event = await engine.store("Ordinary fact", user_id=user)
        ordinary = (await engine.get_event_nodes(ordinary_event, user_id=user))[0]
        foreign_evidence = await engine.store("Private evidence", user_id=user + "-other")
        before = await engine.get_node(str(node.id), user_id=user)

        with pytest.raises(ValueError, match="not found"):
            await engine.evaluate_condition(str(node.id), "true", user_id=user + "-other")
        with pytest.raises(ValueError, match="Only conditional"):
            await engine.evaluate_condition(str(ordinary.id), "true", user_id=user)
        with pytest.raises(ValueError, match="Evidence event"):
            await engine.evaluate_condition(
                str(node.id), "true", user_id=user, evidence_id=foreign_evidence
            )
        with pytest.raises(ValueError, match="timezone"):
            await engine.evaluate_condition(
                str(node.id), "true", user_id=user, evaluated_at=datetime(2026, 1, 1)
            )
        with pytest.raises(ValueError, match="state must be"):
            await engine.evaluate_condition(str(node.id), "maybe", user_id=user)
        assert await engine.get_node(str(node.id), user_id=user) == before
        assert await _records(engine, str(node.id)) == []


async def test_new_conditionals_cannot_bypass_the_evaluation_journal(config, user):
    async with MemoryEngine.open(config) as engine:
        with pytest.raises(ValueError, match="metadata.condition"):
            await engine.store(
                "If approved, deploy.", user_id=user,
                epistemic_type=EpistemicType.CONDITIONAL,
            )
        with pytest.raises(ValueError, match="must start"):
            await engine.store(
                "If approved, deploy.", user_id=user,
                epistemic_type=EpistemicType.CONDITIONAL,
                metadata={"condition": "approved", "condition_state": "true"},
            )
        event_id = await engine.store(
            "If approved, deploy.", user_id=user,
            epistemic_type=EpistemicType.CONDITIONAL,
            metadata={"condition": "  approved  "},
        )
        node = (await engine.get_event_nodes(event_id, user_id=user))[0]
        assert node.metadata == {"condition": "approved", "condition_state": "unknown"}


async def test_condition_request_identity_is_atomic_under_concurrency(config, user):
    request_id = str(uuid4())
    async with MemoryEngine.open(config) as engine:
        node = await _conditional(engine, user)
        outcomes = await asyncio.gather(*(
            engine.evaluate_condition(
                str(node.id), "true", user_id=user, request_id=request_id
            )
            for _ in range(3)
        ))
        assert all(value.metadata["condition_state"] == "true" for value in outcomes)
        assert len(await _records(engine, str(node.id))) == 1

        before = await engine.get_node(str(node.id), user_id=user)
        with pytest.raises(ValueError, match="request_id"):
            await engine.evaluate_condition(
                str(node.id), "false", user_id=user, request_id=request_id
            )
        assert await engine.get_node(str(node.id), user_id=user) == before


async def test_condition_mutation_and_journal_roll_back_together(config, user, monkeypatch):
    from prme.storage import condition_evaluation

    async with MemoryEngine.open(config) as engine:
        node = await _conditional(engine, user)
        before = await engine.get_node(str(node.id), user_id=user)

        def fail(stage):
            if stage == "journal":
                raise RuntimeError("Authored rollback")

        with monkeypatch.context() as fault:
            fault.setattr(condition_evaluation, "_checkpoint", fail)
            with pytest.raises(RuntimeError, match="Authored rollback"):
                await engine.evaluate_condition(str(node.id), "true", user_id=user)
        assert await engine.get_node(str(node.id), user_id=user) == before
        assert await _records(engine, str(node.id)) == []
