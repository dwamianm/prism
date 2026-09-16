"""Explicit revision recovery preserves journals and rejects obsolete workers."""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.errors import ExtractionError
from prme.models.derivation import DerivationPlan, canonical_hash
from prme.models.extraction_work import StaleExtractionClaimError
from prme.storage.extraction_work import _Session
from prme.types import NodeType
from tests import test_durable_ingestion
from tests.test_extraction_journal import extraction
from tests.test_extraction_work import prepare, set_expiry

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def test_public_replan_recovers_changed_dependency_after_restart(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        await engine.store("Alice", user_id=user, node_type=NodeType.ENTITY, metadata={"entity_type": "person"})
        engine._pipeline._retry_delays = ()
        engine._pipeline._extraction_provider.extract = AsyncMock(return_value=extraction())
        commit = engine._graph_store.commit_derivation
        claims = []
        async def changed_before_commit(plan, *, claim=None):
            claims.append(claim)
            assert plan.references
            await engine._graph_store.update_node(str(plan.references[0].id), metadata={"entity_type": "person", "changed": True})
            return await commit(plan, claim=claim)
        with monkeypatch.context() as fault:
            fault.setattr(engine._graph_store, "commit_derivation", changed_before_commit)
            with pytest.raises(ExtractionError) as failed:
                await engine.ingest("Alice uses Python.", user_id=user, wait_for_extraction=True)
        event_id = failed.value.event_id
        first = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        status = await engine.extraction_status(event_id, user_id=user)
        assert status.last_error == "StaleDerivationPlanError" and status.plan_revision == 1
        assert await engine.get_event_nodes(event_id, user_id=user) == []
        queued = await engine.retry_extraction(event_id, user_id=user, replan=True)
        assert queued.plan_id is None and queued.plan_revision == 2 and queued.generation > claims[0].generation
        assert await engine._event_store.get_derivation_plan(event_id, user_id=user) is None
        assert await engine._event_store.get_derivation_plan(event_id, user_id=user, revision=1) == first
        assert await engine.retry_extraction(event_id, user_id=user, replan=True) == queued
        with pytest.raises(StaleExtractionClaimError):
            await engine._event_store.record_derivation_plan(first, claim=claims[0])
    async with MemoryEngine.open(config) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=AssertionError("Use journaled extraction"))
        result = await engine.process_extractions(user_id=user)
        assert (result.processed, result.pending, result.failed) == (1, 0, 0)
        current = await engine._event_store.get_derivation_plan(event_id, user_id=user)
        assert current.revision == 2 and current.id != first.id
        assert not {node.id for node in first.nodes} & {node.id for node in current.nodes}
        assert await engine._event_store.get_derivation_plan(event_id, user_id=user, revision=1) == first
        assert await engine._event_store.get_derivation_plan(event_id, user_id=user + "-other", revision=1) is None
        receipt = await engine._event_store.get_derivation_receipt(event_id.upper(), user_id=user)
        assert receipt.plan_id == current.id
        complete = await engine.extraction_status(event_id, user_id=user)
        assert complete.generation == receipt.generation and complete.plan_revision == 2
        assert await engine.retry_extraction(event_id, user_id=user, replan=True) == complete
        assert {node.id for node in await engine.get_event_nodes(event_id, user_id=user)} == {node.id for node in current.nodes}
        with pytest.raises(ValueError, match="exact journaled"):
            await engine._graph_store.commit_derivation(first, claim=claims[0])


async def test_replan_cannot_preempt_live_or_foreign_work_and_concurrent_requests_converge(config, user):
    async with MemoryEngine.open(config) as engine:
        event, claim, old = await prepare(engine, user)
        work = engine._event_store.extraction_work
        assert not await work.replan(str(event.id), user_id=user)
        await set_expiry(engine, str(event.id), datetime(2000, 1, 1, tzinfo=timezone.utc))
        assert not await work.replan(str(event.id), user_id=user + "-other")
        outcomes = await asyncio.gather(*(work.replan(str(event.id), user_id=user) for _ in range(3)))
        assert outcomes.count(True) == 1
        status = await work.status(str(event.id), user_id=user)
        assert status.plan_revision == 2 and status.generation == claim.generation + 1
        successor = await work.claim(user_id=user)
        assert successor.plan_revision == 2 and successor.generation == status.generation + 1
        with pytest.raises(ValueError, match="current work revision"):
            await engine._event_store.record_derivation_plan(old, claim=successor)


async def test_replan_transition_and_journal_are_atomic(config, user, monkeypatch):
    async with MemoryEngine.open(config) as engine:
        event, claim, old = await prepare(engine, user)
        work = engine._event_store.extraction_work
        await work.fail(claim, error="StaleDerivationPlanError")
        before = await work.status(str(event.id), user_id=user)
        execute = _Session.execute
        async def fail_after_journal(self, sql, *args):
            await execute(self, sql, *args)
            if "DERIVATION_REPLAN_REQUESTED" in sql:
                raise RuntimeError("Injected replan journal failure")
        with monkeypatch.context() as fault:
            fault.setattr(_Session, "execute", fail_after_journal)
            with pytest.raises(RuntimeError, match="Injected"):
                await work.replan(str(event.id), user_id=user)
        assert await work.status(str(event.id), user_id=user) == before
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user) == old
        assert await work.replan(str(event.id), user_id=user)


@pytest.mark.parametrize("policy", [
    "source_passage_v1",
    "typed_references_v2",
    "relationship_claims_v3",
    "event_local_references_v4",
    "claim_qualifiers_v5",
    "grounded_quantities_v6",
    "temporal_validity_v7",
])
async def test_existing_plan_checksums_and_journals_still_load(config, user, policy):
    async with MemoryEngine.open(config) as engine:
        event, _, plan = await prepare(engine, user)
        plan = plan.model_copy(update={"materialization_policy": policy})
        original = plan.model_dump(mode="json")
        original.pop("revision")
        checksum = canonical_hash(original)
        assert plan.checksum == checksum
        assert DerivationPlan.model_validate(original).checksum == checksum
        # Simulate the pre-revision on-disk payload; no original checksum changes.
        payload = json.dumps({"plan": json.dumps(original), "checksum": checksum})
        if engine._conn is not None:
            engine._conn.execute("UPDATE operations SET payload = ? WHERE id = ?", [payload, plan.prepared_operation_id])
        else:
            async with engine._pool.acquire() as conn:
                await conn.execute("UPDATE operations SET payload = $1::jsonb WHERE id = $2", payload, plan.prepared_operation_id)
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user) == plan


async def test_legacy_work_schema_migration_survives_abrupt_exit(config, user, monkeypatch):
    if config.backend != "duckdb":
        pytest.skip("DuckDB schema WAL recovery regression")
    import duckdb
    from tests.test_derivation_ingestion import test_process_exit_during_public_ingestion_replays_same_plan
    async with MemoryEngine.open(config) as engine:
        old_event, _, old_plan = await prepare(engine, user + "-legacy")
        before = await engine.extraction_status(str(old_event.id), user_id=user + "-legacy")
    with duckdb.connect(config.db_path) as conn:
        conn.execute("ALTER TABLE event_extractions DROP COLUMN plan_revision")
        conn.execute("CHECKPOINT")
    # First reopened writer migrates, journals a plan, then exits without close.
    await test_process_exit_during_public_ingestion_replays_same_plan(config, user, monkeypatch, "plan")
    async with MemoryEngine.open(config) as engine:
        assert await engine.extraction_status(str(old_event.id), user_id=user + "-legacy") == before
        assert await engine._event_store.get_derivation_plan(str(old_event.id), user_id=user + "-legacy") == old_plan


async def test_abrupt_exit_after_revision_switch_keeps_old_plan_and_new_work(config, user):
    if config.backend != "duckdb":
        pytest.skip("Local WAL recovery; PostgreSQL transition rollback is covered separately")
    import os
    from pathlib import Path
    import subprocess
    import sys
    async with MemoryEngine.open(config) as engine:
        event, claim, old = await prepare(engine, user)
        await engine._event_store.extraction_work.fail(claim, error="StaleDerivationPlanError")
    script = """
import asyncio, os, sys
from prme import MemoryEngine, PRMEConfig
import prme.storage.engine as engine_module
class Provider:
    model_name = 'durability-test'
    model_version = '1'
    dimension = 384
    async def embed(self, texts):
        raise AssertionError('Queuing a revision must not infer')
async def main():
    db, vector, lexical, user, event_id = sys.argv[1:]
    engine_module.create_embedding_provider = lambda _: Provider()
    config = PRMEConfig(db_path=db, vector_path=vector, lexical_path=lexical,
                        organizer={'opportunistic_enabled': False})
    async with MemoryEngine.open(config) as engine:
        status = await engine.retry_extraction(event_id, user_id=user, replan=True)
        assert status.plan_revision == 2 and status.plan_id is None
        os._exit(42)
asyncio.run(main())
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script, config.db_path,
        config.vector_path, config.lexical_path, user, str(event.id)], capture_output=True, timeout=30, env=env)
    assert result.returncode == 42, result.stderr.decode()
    async with MemoryEngine.open(config) as engine:
        status = await engine.extraction_status(str(event.id), user_id=user)
        assert status.status == "pending" and status.plan_revision == 2 and status.plan_id is None
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user, revision=1) == old
        assert await engine._event_store.get_derivation_plan(str(event.id), user_id=user) is None
        assert await engine.retry_extraction(str(event.id), user_id=user, replan=True) == status
