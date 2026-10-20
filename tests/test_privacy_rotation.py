"""EM04: three real stored objects; wrapper CAS, interruption and fencing."""

from datetime import timedelta
import asyncio
import threading

import pytest

from prme.privacy.crypto import CryptoProtector, decode_envelope
from prme.privacy.local_keys import public_registry
from prme.privacy.policy import PrivacyDenied
from prme.privacy.rotation import RotationManager
from tests.privacy_fakes import isolated_privacy_postgres  # noqa: F401
from tests.test_privacy_ciphertext_storage import LAYOUTS, MEMORY, SOURCE, real_engine
from tests.test_privacy_keys import native as native_fixture  # noqa: F401


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM04_interrupted_rewrap_exact_versions_and_fence(tmp_path, layout, native):
    engine, runtime, policy, custody, original, _ = await real_engine(tmp_path, layout)
    repo, authority = engine._privacy.repository, runtime.protector.authority
    target = await custody.provision_wrapping(original.boundary, "evidence", key_id=original.ref.key_id)
    try:
        for i in range(3):
            await engine.store(f"{SOURCE}_{i}", retrieval_content=MEMORY, user_id=str(policy.owner_id))
        before = sorted(await repo.for_owner(str(policy.owner_id)), key=lambda record: record.event_id)
        fenced = before[1].event_id
        with custody.unlocked((original.ref,), expires_at=runtime.clock() + timedelta(minutes=5)) as private:
            protector = CryptoProtector(public_registry(custody.path, private_wrapper=private), authority=authority)
            manager = RotationManager(repo, protector, custody)
            def activation():
                return runtime.authority.guard(before[0].snapshot, "publication")
            async def access(record):
                if record.event_id == fenced:
                    raise PrivacyDenied("privacy_stale_policy")
                return await authority.grant("diagnostic", ((record.evidence.context, record.snapshot),),
                    expires_at=runtime.clock() + timedelta(minutes=5), actions=("rewrap", "read"))
            def guard(record):
                return lambda: runtime.authority.guard(record.snapshot, "publication")
            job = await manager.start(original.ref, target.ref, guard=activation)
            interrupted = await manager.step(job.job_id, activation_guard=activation,
                                              access_for=access, guard_for=guard, limit=1)
            assert interrupted.changed == 1 and interrupted.state == "running"
            # Reconstruct the coordinator from its persisted checkpoint.
            manager = RotationManager(repo, protector, custody)
            resumed = await manager.step(job.job_id, activation_guard=activation, access_for=access, guard_for=guard)
            assert resumed.changed == 2 and resumed.blocked == 1 and resumed.state == "blocked"
            after = sorted(await repo.for_owner(str(policy.owner_id)), key=lambda record: record.event_id)
            for old, new in zip(before, after, strict=True):
                old_parsed, new_parsed = decode_envelope(old.evidence, old.snapshot), decode_envelope(new.evidence, new.snapshot)
                assert (new_parsed.payload, new_parsed.nonce, new_parsed.ciphertext) == (
                    old_parsed.payload, old_parsed.nonce, old_parsed.ciphertext)
                assert new.evidence.key_version == (original.ref.key_version if old.event_id == fenced else target.ref.key_version)
                # Retained backups remain readable with the retained old key.
                grant = await authority.grant("diagnostic", ((old.evidence.context, old.snapshot),),
                                             expires_at=runtime.clock() + timedelta(minutes=1))
                assert SOURCE.encode() in await protector.open(old.evidence, grant, original_snapshot=old.snapshot, guard=guard(old))
            fenced = None
            complete = await manager.step(job.job_id, activation_guard=activation, access_for=access, guard_for=guard)
            assert complete.state == "complete" and complete.changed == 3
            assert await manager.step(job.job_id, activation_guard=activation, access_for=access, guard_for=guard) == complete
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM04_interruption_before_CAS_and_lost_checkpoint(tmp_path, layout, native, monkeypatch):
    engine, runtime, policy, custody, original, _ = await real_engine(tmp_path, layout)
    repo, authority = engine._privacy.repository, runtime.protector.authority
    try:
        event_id = await engine.store(SOURCE, retrieval_content=MEMORY, user_id=str(policy.owner_id))
        before = await repo.get(event_id, user_id=str(policy.owner_id))
        target = await custody.provision_wrapping(original.boundary, "evidence")
        with custody.unlocked((original.ref,), expires_at=runtime.clock() + timedelta(minutes=5)) as private:
            protector = CryptoProtector(public_registry(custody.path, private_wrapper=private), authority=authority)
            manager = RotationManager(repo, protector, custody)
            def guard():
                return runtime.authority.guard(before.snapshot, "publication")
            async def access(record):
                return await authority.grant("diagnostic", ((record.evidence.context, record.snapshot),),
                    expires_at=runtime.clock() + timedelta(minutes=5), actions=("rewrap",))
            job = await manager.start(original.ref, target.ref, guard=guard)
            cas = repo.replace_envelope
            # Use a BaseException that simulates process interruption without
            # pytest's special KeyboardInterrupt handling.
            class Interrupted(BaseException):
                pass
            async def interrupt(*args):
                raise Interrupted()
            monkeypatch.setattr(repo, "replace_envelope", interrupt)
            with pytest.raises(Interrupted):
                await manager.step(job.job_id, activation_guard=guard, access_for=access, guard_for=lambda _: guard)
            assert await repo.get(event_id, user_id=str(policy.owner_id)) == before
            monkeypatch.setattr(repo, "replace_envelope", cas)
            checkpoint = manager._checkpoint
            async def lose_checkpoint(*args):
                raise Interrupted()
            monkeypatch.setattr(manager, "_checkpoint", lose_checkpoint)
            with pytest.raises(Interrupted):
                await manager.step(job.job_id, activation_guard=guard, access_for=access, guard_for=lambda _: guard)
            after = await repo.get(event_id, user_id=str(policy.owner_id))
            assert after.evidence.key_version == target.ref.key_version
            assert not await repo.replace_envelope(before, after)
            monkeypatch.setattr(manager, "_checkpoint", checkpoint)
            resumed = await manager.step(job.job_id, activation_guard=guard, access_for=access, guard_for=lambda _: guard)
            assert resumed.state == "complete"
            assert await repo.get(event_id, user_id=str(policy.owner_id)) == after
    finally:
        await engine.close()


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_EM04_cancelled_CAS_keeps_authority_until_native_settles(tmp_path, layout, native, monkeypatch):
    from prme.storage._threading import run_to_completion
    engine, runtime, policy, custody, original, _ = await real_engine(tmp_path, layout)
    repo, authority = engine._privacy.repository, runtime.protector.authority
    entered, release = threading.Event(), threading.Event()
    try:
        event_id = await engine.store(SOURCE, retrieval_content=MEMORY, user_id=str(policy.owner_id))
        before = await repo.get(event_id, user_id=str(policy.owner_id))
        target = await custody.provision_wrapping(original.boundary, "evidence")
        with custody.unlocked((original.ref,), expires_at=runtime.clock() + timedelta(minutes=5)) as private:
            protector = CryptoProtector(public_registry(custody.path, private_wrapper=private), authority=authority)
            manager = RotationManager(repo, protector, custody)
            def guard():
                return runtime.authority.guard(before.snapshot, "publication")
            async def access(record):
                return await authority.grant("diagnostic", ((record.evidence.context, record.snapshot),),
                    expires_at=runtime.clock() + timedelta(minutes=5), actions=("rewrap",))
            job = await manager.start(original.ref, target.ref, guard=guard)
            cas = repo.replace_envelope
            def native_wait():
                entered.set()
                if not release.wait(5):
                    raise AssertionError("test native operation not released")
            async def pending_cas(*args):
                await run_to_completion(native_wait)
                return await cas(*args)
            monkeypatch.setattr(repo, "replace_envelope", pending_cas)
            task = asyncio.create_task(manager.step(job.job_id, activation_guard=guard, access_for=access,
                                                    guard_for=lambda _: guard))
            await asyncio.to_thread(entered.wait, 5)
            assert entered.is_set()
            task.cancel()
            acquired = asyncio.Event()
            async def policy_change():
                async with guard():
                    acquired.set()
            activation = asyncio.create_task(policy_change())
            await asyncio.sleep(0.02)
            assert not acquired.is_set() and not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await activation
            current = await repo.get(event_id, user_id=str(policy.owner_id))
            assert current.evidence.key_version == target.ref.key_version
            assert (await repo.rotation_job(job.job_id)).cursor is None
    finally:
        release.set()
        await engine.close()
