"""ER02 provider-state subchecks use real native custody and HMAC consumers."""
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from prme.privacy import CategoryAction, PrivacyDenied
from prme.privacy.erasure import ErasureRequest
from prme.privacy.keys import KeyBoundary
from prme.privacy.retry_commitments import NativeRetryCommitter
from prme.privacy.sanitizer import LocalSanitizer
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER02_shared_pseudonym_retry_and_wrapping_keys_retire_only_after_last_reference(tmp_path, layout, analyzer, native, monkeypatch):
    engine, runtime, policy, custody, evidence, _ = await journal_engine(tmp_path, layout, analyzer)
    epoch = uuid4()
    policy = policy.model_copy(update={"category_actions": (CategoryAction(category="email", action="pseudonymize"),)})
    engine._privacy.default = policy
    pseudonym = await custody.provision_pseudonym_key(KeyBoundary.from_snapshot(
        engine._privacy.effective(None, user_id=str(policy.owner_id), scope=policy.scope)), epoch)
    runtime = replace(runtime, key_custody=custody,
        retry_committer=NativeRetryCommitter(custody, clock=runtime.clock),
        sanitizer=LocalSanitizer(analyzer=analyzer, key_provider=custody, pseudonym_epoch=epoch))
    engine._privacy.runtime = runtime
    owner, request_id = str(policy.owner_id), uuid4()
    items = [{"content": "Contact person.one@example.com."}, {"content": "Contact person.two@example.com."}]
    try:
        events = await engine.ingest_fast_many(items, user_id=owner, request_id=request_id)
        assert await engine.ingest_fast_many(items, user_id=owner, request_id=request_id) == events
        assert (await engine.process_pending(user_id=owner)).processed == 2
        records = [await engine._privacy.repository.get(event, user_id=owner) for event in events]
        assert records[0].retry == records[1].retry and records[0].retry is not None
        assert all(record.pseudonym_epoch == epoch for record in records)
        versions = {evidence.ref.key_version, pseudonym.key_version, records[0].retry.key_version}
        assert versions <= {entry.version for entry in custody.inventory() if entry.state == "ready"}
        retained = (await engine.get_event_nodes(events[1], user_id=owner))[0]
        assert "person.two@example.com" not in retained.content and "PSEUDONYM" in retained.content
        await engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(events[0]),)), user_id=owner)
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert status.state == "live_purged" and status.failure is None, status
        assert versions <= {entry.version for entry in custody.inventory() if entry.state == "ready"}
        assert (await engine.get_event_nodes(events[1], user_id=owner))[0].content == retained.content
        await engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(events[1]),)), user_id=owner)
        def unavailable(*args):
            raise OSError("synthetic native private-part deletion unavailable")
        with monkeypatch.context() as patch:
            patch.setattr(custody._native._backend, "delete_password", unavailable)
            for _ in range(len(versions) + 1):
                statuses = await engine.process_erasures(user_id=owner, budget_ms=60000)
                assert any(status.failure == "provider_unavailable" and status.logical_live_copies == "pending" for status in statuses), statuses
        statuses = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert all(status.state == "live_purged" and status.failure is None for status in statuses)
        assert versions <= {entry.version for entry in custody.inventory() if entry.state == "retired"}
        with pytest.raises(PrivacyDenied):
            await custody.key_for(policy.project_id, policy.owner_id, policy.scope.value, epoch)
        with pytest.raises(PrivacyDenied):
            await runtime.retry_committer.verify(b"original retry is unavailable", records[0].retry)
        for event in events:
            record = await engine._privacy.repository.get(event, user_id=owner)
            assert record.retry is None and record.evidence is None and record.pseudonym_epoch is None
    finally:
        await engine.close()
