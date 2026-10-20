"""ER02 provider-state subchecks use real native custody and HMAC consumers."""
from dataclasses import replace
from datetime import timedelta
import json
from uuid import UUID, uuid4

import pytest

from prme.privacy import CategoryAction, PrivacyDenied
from prme.privacy.erasure import ErasureRequest
from prme.privacy.keys import KeyBoundary
from prme.privacy.local_keys import public_registry
from prme.privacy.retry_commitments import NativeRetryCommitter
from prme.privacy.sanitizer import LocalSanitizer
from tests.privacy_journal_support import (
    LAYOUTS, journal_engine, isolated_privacy_postgres, local_analyzer_fixture,  # noqa: F401
)
from tests.privacy_journal_support import native as native_fixture  # noqa: F401
from tests.privacy_fakes import open_engine
from tests.test_privacy_erasure_recovery import reopen
from tests.test_privacy_source_routing import selection


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
        lineage = (await engine._privacy.erasure.repository.state())[1].store_id
        assert all(entry.managed_lineages == (lineage,) for entry in custody.inventory() if entry.version in versions)
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


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER02_shared_custody_store_survives_pending_retirement(tmp_path, layout, analyzer, native):
    engine, runtime, policy, custody, evidence, _ = await journal_engine(tmp_path, layout, analyzer)
    epoch = uuid4()
    policy = policy.model_copy(update={"category_actions": (CategoryAction(category="email", action="pseudonymize"),)})
    engine._privacy.default = policy
    await custody.provision_pseudonym_key(KeyBoundary.from_snapshot(
        engine._privacy.effective(None, user_id=str(policy.owner_id), scope=policy.scope)), epoch)
    runtime = replace(runtime, key_custody=custody,
        retry_committer=NativeRetryCommitter(custody, clock=runtime.clock),
        sanitizer=LocalSanitizer(analyzer=analyzer, key_provider=custody, pseudonym_epoch=epoch))
    engine._privacy.runtime = runtime
    # B uses the same writer/provider without the optional destructive capability.
    survivor, survivor_runtime, _ = await open_engine(tmp_path / "independent", runtime=replace(runtime, key_custody=None), policy=policy)
    owner, retry_id = str(policy.owner_id), uuid4()
    items = [{"content": "Contact person.shared@example.com."}]
    try:
        events = await engine.ingest_fast_many(items, user_id=owner, request_id=retry_id)
        retained = await survivor.ingest_fast_many(items, user_id=owner, request_id=retry_id)
        assert (await engine.process_pending(user_id=owner)).processed == 1
        assert (await survivor.process_pending(user_id=owner)).processed == 1
        first = await engine._privacy.repository.get(events[0], user_id=owner)
        second = await survivor._privacy.repository.get(retained[0], user_id=owner)
        assert first.evidence.key_version == second.evidence.key_version == evidence.ref.key_version
        assert first.retry == second.retry and first.retry is not None
        expected = (await survivor.get_event_nodes(retained[0], user_id=owner))[0].content
        assert "PSEUDONYM" in expected and "person.shared@example.com" not in expected
        async def prove_survivor():
            grant = await selection(survivor, survivor_runtime, owner, retained, kind="diagnostic")
            with custody.unlocked((evidence.ref,), expires_at=runtime.clock() + timedelta(minutes=2)) as private:
                survivor_runtime.protector.registry = public_registry(custody.path, private_wrapper=private)
                assert (await survivor.read_source(retained[0], user_id=owner, access=grant)).content == expected
            survivor_runtime.protector.registry = public_registry(custody.path)
            assert await survivor.ingest_fast_many(items, user_id=owner, request_id=retry_id) == retained
            event = await survivor.store(items[0]["content"], user_id=owner)
            assert (await survivor.get_event_nodes(event, user_id=owner))[0].content == expected
        await prove_survivor()
        request = ErasureRequest(request_id=uuid4(), event_ids=(UUID(events[0]),))
        await engine.request_erasure(request, user_id=owner)
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        # These real unwrap, pseudonym and HMAC paths must remain available.
        await prove_survivor()
        assert status.stage == "keys" and status.failure == "provider_unavailable"
        assert status.logical_live_copies == "pending" and status.state == "blocked_from_access"
        with pytest.raises(PrivacyDenied, match="privacy_erased"):
            await engine.get_event_nodes(events[0], user_id=owner)
        survivor, survivor_runtime = await reopen(survivor, survivor_runtime)
        await prove_survivor()
        assert await engine.request_erasure(request, user_id=owner) == status
        [retried] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert retried.failure == "provider_unavailable" and retried.attempts == status.attempts + 1
        await prove_survivor()
        # Actual unbound public encryption makes exclusive-use history unknown.
        await runtime.protector.seal(b"synthetic unbound provider use", second.evidence.context, original_snapshot=second.snapshot)
        assert next(entry for entry in custody.inventory() if entry.version == evidence.ref.key_version).managed_lineages is None
        [unbound] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert unbound.failure == "provider_unavailable" and unbound.logical_live_copies == "pending"
        await prove_survivor()
        # A genuine old public-entry shape has no exclusive-use evidence.
        legacy = json.loads(custody.path.read_text())
        for entry in legacy["entries"]:
            entry.pop("managed_lineages", None)
        custody.path.write_text(json.dumps(legacy))
        assert all(entry.managed_lineages is None for entry in custody.inventory())
        [unknown] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert unknown.failure == "provider_unavailable" and unknown.logical_live_copies == "pending"
        await prove_survivor()
    finally:
        await survivor.close()
        await engine.close()
