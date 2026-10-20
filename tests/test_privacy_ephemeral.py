"""AP06 genuine transient preparation, including failure before durability."""

import asyncio

import pytest

from prme.privacy import PrivacyDenied
from tests.privacy_fakes import make_runtime, open_engine, policy_for

SOURCE = "Alex does not like tea unless it is cold."


async def test_AP06_sync_success_and_provider_failure_require_resubmission(tmp_path):
    r = make_runtime()
    p = policy_for(r, durable_processing_input=False)
    engine, _, _ = await open_engine(tmp_path, runtime=r, policy=p)
    owner = str(p.owner_id)
    try:
        r.extractor.fail = True
        with pytest.raises(PrivacyDenied, match="resubmit_required"):
            await engine.ingest(SOURCE, user_id=owner, wait_for_extraction=True)
        assert await engine.get_events(owner) == []
        assert await engine._privacy.repository.for_owner(owner) == ()
        assert await engine._event_store.extraction_work.counts(user_id=owner) == (0, 0)
        r.extractor.fail = False
        event_id = await engine.ingest(SOURCE, user_id=owner, wait_for_extraction=True)
        assert await engine.get_event_nodes(event_id, user_id=owner)
        record = await engine._privacy.repository.get(event_id, user_id=owner)
        assert record.processing is None and record.evidence is None
    finally:
        await engine.close()


async def test_AP06_while_provider_is_paused_no_source_or_job_exists(tmp_path):
    r = make_runtime()
    p = policy_for(r, durable_processing_input=False)
    engine, _, _ = await open_engine(tmp_path, runtime=r, policy=p)
    entered, resume = asyncio.Event(), asyncio.Event()

    async def pause():
        entered.set()
        await resume.wait()

    r.extractor.pause = pause
    task = asyncio.create_task(engine.ingest(SOURCE, user_id=str(p.owner_id), wait_for_extraction=True))
    try:
        await entered.wait()
        assert await engine._event_store.get_by_user(str(p.owner_id)) == []
        assert await engine._privacy.repository.for_owner(str(p.owner_id)) == ()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await engine._event_store.get_by_user(str(p.owner_id)) == []
    finally:
        resume.set()
        await engine.close()


@pytest.mark.parametrize("method,kwargs", [("ingest", {}), ("ingest_fast", {}), ("store", {"extract": True})])
async def test_AP06_async_refusal_precedes_input_admission(tmp_path, method, kwargs):
    r = make_runtime()
    p = policy_for(r, durable_processing_input=False)
    engine, _, _ = await open_engine(tmp_path, runtime=r, policy=p)
    try:
        with pytest.raises(PrivacyDenied, match="async_forbidden"):
            await getattr(engine, method)(SOURCE, user_id=str(p.owner_id), **kwargs)
        assert not r.sanitizer.calls and not r.embedder.sends and not r.extractor.sends
        assert await engine.get_events(str(p.owner_id)) == []
    finally:
        await engine.close()
