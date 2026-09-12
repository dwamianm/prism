"""Derivation revisions cannot reuse identities which reclamation may remove."""
from uuid import uuid4

import pytest

from prme import MemoryEngine
from tests import test_durable_ingestion
from tests.test_extraction_work import prepare

config = test_durable_ingestion.config
user = test_durable_ingestion.user


@pytest.mark.parametrize("reuse_plan_id", [False, True])
async def test_revised_plan_cannot_claim_previous_revision_identities(config, user, reuse_plan_id):
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        work = engine._event_store.extraction_work
        await work.fail(claim, error='StaleDerivationPlanError')
        await work.replan(str(event.id), user_id=user)
        successor = await work.claim(user_id=user)
        replacement = plan.model_copy(update={'id': plan.id if reuse_plan_id else uuid4(), 'revision': 2})
        with pytest.raises(ValueError, match='artifact identity belongs'):
            await engine._event_store.record_derivation_plan(replacement, claim=successor)
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user) is None
        assert (await work.status(str(event.id), user_id=user)).plan_id is None
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user, revision=1) == plan


async def test_legacy_overlap_is_backfilled_as_ambiguous_without_changing_journals(config, user):
    import json
    async with MemoryEngine.open(config) as engine:
        event, claim, plan = await prepare(engine, user)
        legacy = plan.model_copy(update={'id': uuid4(), 'revision': 2})
        async with engine._event_store.extraction_work.session(transaction=True) as session:
            await session.execute(
                "INSERT INTO operations (id, op_type, target_id, payload, actor_id, namespace_id) "
                "VALUES ($1, 'DERIVATION_PREPARED', $2, $3, 'legacy-test', $4)",
                legacy.prepared_operation_id, str(event.id),
                json.dumps({'plan': legacy.model_dump_json(), 'checksum': legacy.checksum}), plan.scope.value,
            )
            await session.execute('DELETE FROM derivation_registered_plans WHERE operation_id = $1', plan.prepared_operation_id)
            for node in plan.nodes:
                await session.execute('DELETE FROM derivation_artifact_owners WHERE node_id = $1', str(node.id))
    for _ in range(2):
        async with MemoryEngine.open(config) as engine:
            for expected in (plan, legacy):
                assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user, revision=expected.revision) == expected
            async with engine._event_store.extraction_work.session() as session:
                for node in plan.nodes:
                    rows = await session.fetch('SELECT operation_id FROM derivation_artifact_owners WHERE node_id = $1', str(node.id))
                    assert rows == [{'operation_id': None}]
                rows = await session.fetch('SELECT count(*) AS count FROM derivation_registered_plans WHERE event_id = $1', str(event.id))
                assert rows[0]['count'] == 2


async def test_corrupt_legacy_plan_does_not_prevent_reading_source(config, user):
    import json
    from prme.storage.derivation_registry import _PENDING
    async with MemoryEngine.open(config) as engine:
        event, _, plan = await prepare(engine, user)
        async with engine._event_store.extraction_work.session(transaction=True) as session:
            await session.execute('DELETE FROM derivation_registered_plans WHERE operation_id = $1', plan.prepared_operation_id)
            await session.execute('UPDATE operations SET payload = $1 WHERE id = $2',
                json.dumps({'plan': plan.model_dump_json(), 'checksum': '0' * 64}), plan.prepared_operation_id)
    async with MemoryEngine.open(config) as engine:
        assert (await engine.get_event(str(event.id), user_id=user)).content == event.content
        async with engine._event_store.extraction_work.session() as session:
            assert plan.prepared_operation_id in {row['id'] for row in await session.fetch(_PENDING)}
        with pytest.raises(ValueError, match='checksum'):
            await engine._event_store.get_derivation_plan(str(event.id), user_id=user)


async def test_plan_and_artifact_reservations_roll_back_together(config, user, monkeypatch):
    from prme.models import Event
    from tests.test_extraction_work import extraction
    from prme.storage import derivation_registry
    async with MemoryEngine.open(config) as engine:
        event = Event(content='Alice uses Rust', user_id=user, role='user')
        await engine._event_store.append(event, defer_extraction=True)
        claim = await engine._event_store.extraction_work.claim(user_id=user, event_id=str(event.id))
        plan = await engine._pipeline._prepare_plan(extraction(event.content, old=None), event)
        if config.backend == 'duckdb':
            original = derivation_registry.register_duck
            def fault(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError('Fault after registration')
            method = 'register_duck'
        else:
            original = derivation_registry.register_pg
            async def fault(*args, **kwargs):
                await original(*args, **kwargs)
                raise OSError('Fault after registration')
            method = 'register_pg'
        with monkeypatch.context() as patch:
            patch.setattr(derivation_registry, method, fault)
            with pytest.raises(OSError, match='after registration'):
                await engine._event_store.record_derivation_plan(plan, claim=claim)
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user) is None
        async with engine._event_store.extraction_work.session() as session:
            assert await session.fetch('SELECT * FROM derivation_artifact_owners WHERE operation_id = $1', plan.prepared_operation_id) == []
            assert await session.fetch('SELECT * FROM derivation_registered_plans WHERE operation_id = $1', plan.prepared_operation_id) == []
        assert await engine._event_store.record_derivation_plan(plan, claim=claim) == plan


async def test_concurrent_sources_cannot_allocate_the_same_artifact(config, user):
    import asyncio
    from prme.models import Event
    from prme.models.derivation import DerivationPlan
    from tests.test_extraction_work import extraction
    async with MemoryEngine.open(config) as engine:
        records = []
        for owner in (user, user + '-other'):
            event = Event(content='Alice uses Rust', user_id=owner, role='user')
            await engine._event_store.append(event, defer_extraction=True)
            claim = await engine._event_store.extraction_work.claim(user_id=owner, event_id=str(event.id))
            plan = await engine._pipeline._prepare_plan(extraction(event.content, old=None), event)
            if records:
                plan = DerivationPlan.model_validate_json(plan.model_dump_json().replace(
                    str(plan.nodes[0].id), str(records[0][2].nodes[0].id)))
            records.append((event, claim, plan))
        outcomes = await asyncio.gather(*(engine._event_store.record_derivation_plan(plan, claim=claim)
            for _, claim, plan in records), return_exceptions=True)
        assert sum(isinstance(outcome, DerivationPlan) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, Exception) for outcome in outcomes) == 1
        persisted = [await engine._event_store.get_derivation_plan(str(event.id), user_id=event.user_id)
                     for event, _, _ in records]
        assert sum(plan is not None for plan in persisted) == 1
        loser = next(record for record, outcome in zip(records, outcomes) if isinstance(outcome, Exception))
        with pytest.raises(ValueError, match='artifact identity belongs'):
            await engine._event_store.record_derivation_plan(loser[2], claim=loser[1])
