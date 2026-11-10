"""ER02 provider-state subchecks use real native custody and HMAC consumers."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

import pytest

from prme.privacy import CategoryAction, PrivacyDenied
from prme.privacy.erasure import ErasureRequest
from prme.organizer.watermarks import scope_key
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
        # The default 1 s budget is checked between items, so a loaded runner can stop after the first
        # and leave the second pending. This test is about what processing does, not how fast it is.
        assert (await engine.process_pending(user_id=owner, budget_ms=60000)).processed == 2
        records = [await engine._privacy.repository.get(event, user_id=owner) for event in events]
        assert records[0].retry == records[1].retry and records[0].retry is not None
        assert all(record.pseudonym_epoch == epoch for record in records)
        versions = {evidence.ref.key_version, pseudonym.key_version, records[0].retry.key_version}
        assert versions <= {entry.version for entry in custody.inventory() if entry.state == "ready"}
        lineage = (await engine._privacy.erasure.repository.state())[1].store_id
        assert all(entry.managed_lineages == (lineage,) for entry in custody.inventory() if entry.version in versions)
        retained = (await engine.get_event_nodes(events[1], user_id=owner))[0]
        assert "person.two@example.com" not in retained.content and "PSEUDONYM" in retained.content
        [first_node] = await engine.get_event_nodes(events[0], user_id=owner)
        watermark_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await engine.set_organizer_watermark(
            scope_key=scope_key(owner), job="deduplicate", completed_through=watermark_at,
        )
        await engine.set_organizer_watermark(
            scope_key=scope_key(None),
            job="deduplicate",
            completed_through=watermark_at,
            resume_updated_at=watermark_at,
            resume_id=str(first_node.id),
        )
        await engine.request_erasure(ErasureRequest(request_id=uuid4(), event_ids=(UUID(events[0]),)), user_id=owner)
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert status.state == "live_purged" and status.failure is None, status
        # The organizer watermark must not outlive the erased owner, and an
        # unscoped resume cursor must not retain the erased node id (#517).
        assert await engine.get_organizer_watermark(
            scope_key=scope_key(owner), job="deduplicate",
        ) is None
        unscoped = await engine.get_organizer_watermark(
            scope_key=scope_key(None), job="deduplicate",
        )
        assert unscoped is not None and unscoped.resume_cursor is None
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


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER02_erased_owner_does_not_get_a_watermark_back(tmp_path, layout, analyzer, native):
    """A post-erasure organize must not re-create the plaintext owner key (#525)."""
    engine, runtime, policy, custody, *_ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody,
        retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    engine._privacy.runtime = runtime
    owner = str(policy.owner_id)
    scope = scope_key(owner)
    completed = datetime.now(timezone.utc)
    try:
        assert await engine.set_organizer_watermark(
            scope_key=scope, job="deduplicate", completed_through=completed,
        ) is True
        await engine.store("Mara likes tea.", user_id=owner)

        await engine.request_erasure(
            ErasureRequest(request_id=uuid4(), owner_all=True), user_id=owner,
        )
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert status.state == "live_purged" and status.failure is None, (
            status.stage, status.failure, status.attempts,
        )
        assert await engine.get_organizer_watermark(scope_key=scope, job="deduplicate") is None

        # The writer refuses the scope, so a later organize that reaches it
        # (for example after the owner is admitted again) writes nothing.
        assert await engine.set_organizer_watermark(
            scope_key=scope, job="deduplicate", completed_through=completed,
        ) is False
        assert await engine.get_organizer_watermark(scope_key=scope, job="deduplicate") is None

        # The unscoped operator scope is not an owner scope and still writes.
        assert await engine.set_organizer_watermark(
            scope_key=scope_key(None), job="deduplicate", completed_through=completed,
        ) is True

        # A non-UUID owner id cannot match a UUID erasure record, so the guard
        # must not raise on a store whose privacy tables exist.
        assert await engine.set_organizer_watermark(
            scope_key=scope_key("legacy-user"), job="deduplicate", completed_through=completed,
        ) is True
        assert await engine.get_organizer_watermark(
            scope_key=scope_key("legacy-user"), job="deduplicate",
        ) is not None

        # An owner-wide purge must not report success while the plaintext
        # scope row survives, even if a writer added it after the purge. The
        # verify stage re-applies the delete and then confirms it is gone.
        [intent] = await engine._privacy.erasure.repository.intents()
        await engine._graph_store.set_organizer_watermark(
            scope_key=scope, job="deduplicate", completed_through=completed,
        )
        from prme.storage.erasure import _verify_purge
        await _verify_purge(engine._privacy.erasure, intent)
        assert await engine.get_organizer_watermark(scope_key=scope, job="deduplicate") is None
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_ER02_verification_rejects_a_resume_cursor_naming_an_erased_node(
    tmp_path, layout, analyzer, native,
):
    """The verify stage now covers the cleared resume cursor (#525)."""
    from prme.storage.erasure import _targets, _verify_purge

    engine, runtime, policy, custody, *_ = await journal_engine(tmp_path, layout, analyzer)
    runtime = replace(runtime, key_custody=custody,
        retry_committer=NativeRetryCommitter(custody, clock=runtime.clock))
    engine._privacy.runtime = runtime
    owner = str(policy.owner_id)
    try:
        event = await engine.store("Mara keeps a notebook.", user_id=owner)
        await engine.request_erasure(
            ErasureRequest(request_id=uuid4(), event_ids=(UUID(str(event)),)), user_id=owner,
        )
        [status] = await engine.process_erasures(user_id=owner, budget_ms=60000)
        assert status.state == "live_purged" and status.failure is None, status

        [intent] = await engine._privacy.erasure.repository.intents()
        async with engine._privacy.erasure.repository.session() as session:
            targets = await _targets(session, intent.request.request_id)
        [node] = [identifier for kind, identifier in targets if kind == "node"]

        # Re-create a cursor that names the erased node, as a racing organize
        # could between the purge and verification.
        now = datetime.now(timezone.utc)
        assert await engine.set_organizer_watermark(
            scope_key=scope_key(None), job="deduplicate", completed_through=now,
            resume_updated_at=now, resume_id=str(node),
        ) is True
        # Verification re-applies the cursor clear and then confirms it.
        await _verify_purge(engine._privacy.erasure, intent)
        unscoped = await engine.get_organizer_watermark(
            scope_key=scope_key(None), job="deduplicate",
        )
        assert unscoped.resume_cursor is None
    finally:
        await engine.close()

