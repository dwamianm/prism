"""AP06/CL03/ER05 component subchecks, never complete integration-case passes."""

import asyncio

import pytest

from prme.privacy.capabilities import ProcessingAccess
from prme.privacy.policy import PrivacyDenied
from tests.privacy_journal_support import (
    LAYOUTS,
    SOURCE,
    SOURCE_ONLY,
    journal_engine,
    operation_bytes,
    processing_keys,
    isolated_privacy_postgres,
    local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_AP06_component_actual_retry_and_terminal_cleanup(
    tmp_path, layout, analyzer, native
):
    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path, layout, analyzer, capture="none", evidence_lifetime_seconds=None
    )
    owner = str(policy.owner_id)
    try:
        event_id = await engine.ingest(SOURCE, user_id=owner)
        pending = await engine._privacy.repository.get(event_id, user_id=owner)
        assert pending.processing and pending.evidence is None
        assert not runtime.extractor.sends  # Enqueue-only, including shutdown.
        runtime.extractor.fail = True
        with processing_keys(runtime, custody, processing):
            failed = await engine.process_extractions(user_id=owner)
            assert failed.processed == 0
        checkpoint = await engine._privacy.repository.get(event_id, user_id=owner)
        assert (
            checkpoint.processing.context.object_generation
            > pending.processing.context.object_generation
        )
        assert checkpoint.processing_attempt == 0
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
        runtime.extractor.fail = False
        assert await engine._event_store.extraction_work.retry(event_id, user_id=owner)
        with processing_keys(runtime, custody, processing):
            complete = await engine.process_extractions(user_id=owner)
        assert complete.processed == 1
        record = await engine._privacy.repository.get(event_id, user_id=owner)
        assert record.processing is None and record.processing_attempt == 0
        assert record.processing_state == "complete" and record.evidence is None
        nodes = await engine.get_event_nodes(event_id, user_id=owner)
        assert any("tea" in node.content for node in nodes)
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
        assert not await engine._event_store.extraction_work.retry(
            event_id, user_id=owner
        )
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER05_component_expiry_removes_slot_without_mutable_work(
    tmp_path, layout, analyzer, native
):
    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path, layout, analyzer
    )
    owner = str(policy.owner_id)
    try:
        event_id = await engine.ingest(SOURCE, user_id=owner)
        record = await engine._privacy.repository.get(event_id, user_id=owner)
        claim = await engine._event_store.extraction_work.claim(
            user_id=owner, event_id=event_id
        )
        assert claim.lease_expires_at <= record.snapshot.processing_deadline
        runtime.clock.now = record.snapshot.processing_deadline
        # Missing mutable work cannot hide an expired independent slot.
        await engine._privacy.repository._query(
            "DELETE FROM event_extractions WHERE event_id=?", (event_id,)
        )
        assert await engine._privacy.processing.expire(user_id=owner) == 1
        after = await engine._privacy.repository.get(event_id, user_id=owner)
        assert after.processing is None and after.processing_state == "expired"
        assert not await engine._event_store.extraction_work.retry(
            event_id, user_id=owner
        )
        with pytest.raises(PrivacyDenied):
            await engine._privacy.resume(
                await engine._event_store.get(event_id), claim=claim
            )
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
        assert await engine._privacy.processing.expire(user_id=owner) == 0
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_AP06_component_cancellation_retires_actual_claim(
    tmp_path, layout, analyzer, native
):
    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path, layout, analyzer
    )
    owner = str(policy.owner_id)
    entered = asyncio.Event()

    async def pause():
        entered.set()
        await asyncio.Event().wait()

    runtime.extractor.pause = pause
    try:
        event_id = await engine.ingest(SOURCE, user_id=owner)
        before = await engine._privacy.repository.get(event_id, user_id=owner)
        with processing_keys(runtime, custody, processing):
            task = asyncio.create_task(engine.process_extractions(user_id=owner))
            await asyncio.wait_for(entered.wait(), 15)
            status = await engine._event_store.extraction_work.status(
                event_id, user_id=owner
            )
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            after = await engine._privacy.repository.get(event_id, user_id=owner)
            assert after.processing is None and after.processing_state == "cancelled"
            access = ProcessingAccess(
                work_id=before.event_id,
                work_generation=status.generation,
                lease_deadline=status.lease_expires_at,
                processing_deadline=before.snapshot.processing_deadline,
                claimed_event_ids=(before.event_id,),
            )
            with pytest.raises(PrivacyDenied):
                await runtime.protector.open(
                    before.processing,
                    access,
                    original_snapshot=before.snapshot,
                    guard=lambda: engine._privacy.guard(
                        before.snapshot, "disclosure", processing=True
                    ),
                )
        assert not await engine._event_store.extraction_work.retry(
            event_id, user_id=owner
        )
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_CL03_component_saved_plan_recovers_without_private_keys(
    tmp_path, layout, analyzer, native, monkeypatch
):
    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path, layout, analyzer
    )
    owner = str(policy.owner_id)
    publish = engine._pipeline._publish_plan

    async def fail_stage(*args, **kwargs):
        raise RuntimeError(SOURCE_ONLY)

    try:
        event_id = await engine.ingest(SOURCE, user_id=owner)
        monkeypatch.setattr(engine._pipeline, "_publish_plan", fail_stage)
        with processing_keys(runtime, custody, processing):
            failed = await engine.process_extractions(user_id=owner)
        assert failed.processed == 0
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=owner)
        assert plan and any("tea" in node.content for node in plan.nodes)
        assert SOURCE_ONLY not in plan.model_dump_json()
        sends = len(runtime.extractor.sends), len(runtime.embedder.sends)
        monkeypatch.setattr(engine._pipeline, "_publish_plan", publish)
        assert await engine._event_store.extraction_work.retry(event_id, user_id=owner)
        # No unlocked processing or diagnostic/evidence key is present.
        result = await engine.process_extractions(user_id=owner)
        assert result.processed == 1
        assert sends == (len(runtime.extractor.sends), len(runtime.embedder.sends))
        assert (
            await engine._privacy.repository.get(event_id, user_id=owner)
        ).processing is None
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_AP06_component_multiple_input_allowances_and_claims(
    tmp_path, layout, analyzer, native
):
    from prme.privacy.admission import AdmissionCoordinator
    from prme.privacy.sanitizer import MAX_INPUT_BYTES, MAX_LEAVES, DETECTOR_SECONDS

    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path, layout, analyzer
    )
    owner = str(policy.owner_id)
    try:
        ids = await engine.ingest_batch(
            [{"content": SOURCE}, {"content": SOURCE}], user_id=owner
        )
        assert len(ids) == 2 and not runtime.extractor.sends
        balances = []
        with processing_keys(runtime, custody, processing):
            for event_id in ids:
                record = await engine._privacy.repository.get(event_id, user_id=owner)
                claim = await engine._event_store.extraction_work.claim(
                    user_id=owner, event_id=event_id
                )
                access = ProcessingAccess(
                    work_id=record.event_id,
                    work_generation=claim.generation,
                    lease_deadline=claim.lease_expires_at,
                    processing_deadline=record.snapshot.processing_deadline,
                    claimed_event_ids=(record.event_id,),
                )
                raw = await runtime.protector.open(
                    record.processing,
                    access,
                    original_snapshot=record.snapshot,
                    guard=lambda: engine._privacy.guard(
                        record.snapshot, "disclosure", processing=True
                    ),
                )
                used = AdmissionCoordinator.load_source(raw).continuation
                balances.append(
                    (
                        MAX_INPUT_BYTES - used.input_bytes,
                        int(DETECTOR_SECONDS * 1e9) - used.detector_nanoseconds,
                        MAX_LEAVES - used.leaves,
                    )
                )
                await engine._privacy.resume(
                    await engine._event_store.get(event_id), claim=claim
                )
        assert all(
            sum(values) <= limit
            for values, limit in zip(
                zip(*balances),
                (MAX_INPUT_BYTES, int(DETECTOR_SECONDS * 1e9), MAX_LEAVES),
            )
        )
        assert len(runtime.extractor.sends) == 2
        for item in ids:
            assert (
                await engine._privacy.repository.get(item, user_id=owner)
            ).processing is None
        assert SOURCE_ONLY.encode() not in await operation_bytes(engine)
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER05_component_missing_mutable_rows_never_authorize_plan(
    tmp_path, layout, analyzer, native, monkeypatch
):
    engine, runtime, policy, custody, evidence, processing = await journal_engine(
        tmp_path, layout, analyzer
    )
    owner = str(policy.owner_id)
    publish = engine._pipeline._publish_plan

    async def stop_after_prepare(*args, **kwargs):
        raise RuntimeError(SOURCE_ONLY)

    try:
        event_id = await engine.ingest(SOURCE, user_id=owner)
        monkeypatch.setattr(engine._pipeline, "_publish_plan", stop_after_prepare)
        with processing_keys(runtime, custody, processing):
            await engine.process_extractions(user_id=owner)
        plan = await engine._event_store.get_derivation_plan(event_id, user_id=owner)
        assert plan and plan.nodes
        monkeypatch.setattr(engine._pipeline, "_publish_plan", publish)
        for delete in (None, "event_extractions", "privacy_admissions"):
            if delete:
                await engine._privacy.repository._query(
                    f"DELETE FROM {delete} WHERE event_id=?", (event_id,)
                )
            with pytest.raises(PrivacyDenied):
                await publish(plan, claim=None)
        assert not await engine._graph_store.get_nodes(
            [str(node.id) for node in plan.nodes]
        )
    finally:
        await engine.close()
