"""ER03 real erasure leases around paused production publication/disclosure."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from prme.privacy import PrivacyDenied
from prme.privacy.erasure import ErasureRequest
from prme.privacy.local_keys import public_registry
from prme.types import Scope
from tests.privacy_journal_support import (
    LAYOUTS, SOURCE, journal_engine, processing_keys,
    isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401
from tests.test_privacy_source_routing import selection


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER03_stale_extraction_profile_and_diagnostic_cannot_finish_after_fence(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, custody, evidence, processing = await journal_engine(tmp_path, layout, analyzer)
    engine._privacy.runtime = replace(runtime, key_custody=custody)
    owner = str(policy.owner_id)
    try:
        # Each pause is immediately before the real guarded effect. Nothing
        # replaces an authority, transaction, provider open or publication.
        event = await engine.ingest(SOURCE, user_id=owner)
        entered, release = asyncio.Event(), asyncio.Event()
        publish = engine._privacy.publish
        plans = []
        async def paused_publish(record, plan, **kwargs):
            plans.append(plan)
            entered.set()
            await release.wait()
            return await publish(record, plan, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(engine._privacy, "publish", paused_publish)
            with processing_keys(runtime, custody, processing):
                task = asyncio.create_task(engine.process_extractions(user_id=owner))
                await asyncio.wait_for(entered.wait(), 15)
                ack = await engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(event),)), user_id=owner)
                assert ack.state == "blocked_from_access"
                release.set()
                assert (await asyncio.wait_for(task, 15)).processed == 0
        assert plans and plans[0].nodes
        assert not await engine._graph_store.get_nodes([str(node.id) for node in plans[0].nodes])

        source = await engine.ingest(SOURCE, user_id=owner)
        with processing_keys(runtime, custody, processing):
            assert (await engine.process_extractions(user_id=owner)).processed == 1
        entered, release = asyncio.Event(), asyncio.Event()
        original = engine._publish_prepared_profile
        async def paused_profile(plan):
            plans.append(plan)
            entered.set()
            await release.wait()
            return await original(plan)
        with monkeypatch.context() as patch:
            patch.setattr(engine, "_publish_prepared_profile", paused_profile)
            task = asyncio.create_task(engine.consolidate_knowledge(user_id=owner, scope=Scope.PERSONAL, entity_names=["Mara"]))
            await asyncio.wait_for(entered.wait(), 15)
            assert plans[-1].privacy_manifest and plans[-1].sources
            await engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(source),)), user_id=owner)
            release.set()
            with pytest.raises(PrivacyDenied):
                await asyncio.wait_for(task, 15)
        assert await engine._graph_store.get_node(str(plans[-1].node.id)) is None

        diagnostic = await engine.store("Mara likes pears. Source lilac.", retrieval_content="Mara likes pears.", user_id=owner)
        grant = await selection(engine, runtime, owner, (diagnostic,), kind="diagnostic")
        entered, release = asyncio.Event(), asyncio.Event()
        guard = engine._privacy.guard
        @asynccontextmanager
        async def paused_guard(saved, effect, **kwargs):
            if effect == "disclosure" and kwargs.get("purpose") == "evidence":
                entered.set()
                await release.wait()
            async with guard(saved, effect, **kwargs):
                yield
        with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
            runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
            with monkeypatch.context() as patch:
                patch.setattr(engine._privacy, "guard", paused_guard)
                task = asyncio.create_task(engine.read_source(diagnostic, user_id=owner, access=grant))
                await asyncio.wait_for(entered.wait(), 15)
                await engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(diagnostic),)), user_id=owner)
                release.set()
                with pytest.raises(PrivacyDenied):
                    await asyncio.wait_for(task, 15)

        # A child inherits ContextVars but cannot borrow the parent's lease.
        positive = await engine.store("Mara likes apples.", user_id=owner)
        record = await engine._privacy.repository.get(positive, user_id=owner)
        entered, release = asyncio.Event(), asyncio.Event()
        async def child():
            async with engine._privacy.erasure.guard((record,)):
                entered.set()
                await release.wait()
                assert await engine.get_event_nodes(positive, user_id=owner)
        async with engine._privacy.erasure.guard((record,)):
            task = asyncio.create_task(child())
            await asyncio.sleep(0.02)
            assert not entered.is_set()
        await asyncio.wait_for(entered.wait(), 15)
        ack_task = asyncio.create_task(engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(positive),)), user_id=owner))
        await asyncio.sleep(0.02)
        assert not ack_task.done()
        release.set()
        await asyncio.wait_for(task, 15)
        assert (await asyncio.wait_for(ack_task, 15)).state == "blocked_from_access"
        with pytest.raises(PrivacyDenied):
            await engine.get_event_nodes(positive, user_id=owner)
    finally:
        await engine.close()
