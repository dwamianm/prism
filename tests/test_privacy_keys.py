"""Real crypto and purpose custody; the native adapter double is not OS proof."""

import asyncio
import json
import pickle
from datetime import timedelta
from uuid import uuid4

import pytest

from prme.privacy.capabilities import RetryContext
from prme.privacy.crypto import CryptoProtector
from prme.privacy.keys import KeyBoundary
from prme.privacy.local_keys import (
    MAX_PARTS, PART_SIZE, NativeKeyCustody, _open_native, public_registry, read_public_store,
)
from prme.privacy.policy import PrivacyDenied
from tests.test_privacy_crypto import Fence, NOW, setup_crypto


class NativeDouble:
    """Only OS get/set/delete is replaced; all encoding and crypto are real."""
    def __init__(self):
        self.values = {}
        self.fail_after = None
        self.writes = 0
        self.reads = 0

    def get_password(self, service, username):
        self.reads += 1
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        if self.fail_after is not None and self.writes >= self.fail_after:
            raise RuntimeError("synthetic provider failure")
        assert len(password) <= PART_SIZE
        self.writes += 1
        self.values[service, username] = password

    def delete_password(self, service, username):
        del self.values[service, username]


@pytest.fixture
def native(monkeypatch):
    backend = NativeDouble()
    monkeypatch.setattr("prme.privacy.local_keys._open_native", lambda name: backend)
    return backend


@pytest.fixture
def custody(tmp_path, native):
    return NativeKeyCustody.create(tmp_path / "public.json", backend="macos", clock=lambda: NOW)


@pytest.mark.parametrize("name", ["auto", "null", "plaintext", "file", "chainer", "fail"])
def test_no_insecure_backend_fallback(name):
    with pytest.raises(PrivacyDenied):
        _open_native(name)


async def test_native_multipart_readback_key_loss_recovery(custody, native, tmp_path):
    _, saved, context, *_ = setup_crypto()
    boundary = KeyBoundary.from_context(context)
    descriptor = await custody.provision_wrapping(boundary, "evidence")
    state = read_public_store(custody.path)
    assert state.entries[0].active and state.entries[0].state == "ready"
    assert 2 < len(native.values) <= MAX_PARTS + 1
    assert all(len(value.encode("utf-16-le")) <= 2 * PART_SIZE for value in native.values.values())
    # No secret-loader call occurs during public construction/encryption.
    reads = native.reads
    public = CryptoProtector(public_registry(custody.path))
    payload = await public.seal(b"PV321_SOURCE_KEYLOSS", context, original_snapshot=saved)
    assert native.reads == reads
    pack = tmp_path / "pack"
    pack.mkdir()
    recovery = tmp_path / "recovery.pem"
    await custody.export_recovery(descriptor.ref, recovery, passphrase=b"synthetic-passphrase-321", pack_path=pack)
    assert recovery.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    assert not recovery.stat().st_mode & 0o077
    with pytest.raises(PrivacyDenied):
        await custody.export_recovery(descriptor.ref, pack / "bad.pem", passphrase=b"synthetic-passphrase-321", pack_path=pack)
    native.values.clear()
    with pytest.raises(PrivacyDenied):
        with custody.unlocked((descriptor.ref,), expires_at=NOW + timedelta(minutes=1)):
            pass
    with pytest.raises(PrivacyDenied):
        await custody.import_recovery(descriptor.ref, recovery, passphrase=b"wrong-passphrase")
    await custody.import_recovery(descriptor.ref, recovery, passphrase=b"synthetic-passphrase-321")
    with custody.unlocked((descriptor.ref,), expires_at=NOW + timedelta(minutes=1)) as wrapper:
        assert wrapper.isolation == "same_process"
        assert descriptor.ref in wrapper._private_keys
    assert not wrapper._private_keys
    assert payload.key_version == descriptor.ref.key_version
    await custody.retire(descriptor.ref.key_version)
    assert not native.values
    with pytest.raises(PrivacyDenied):
        await custody.import_recovery(descriptor.ref, recovery, passphrase=b"synthetic-passphrase-321")
    assert read_public_store(custody.path).entries[0].state == "retired"


async def test_interrupted_native_parts_never_activate(custody, native):
    _, saved, context, *_ = setup_crypto()
    native.fail_after = 1
    with pytest.raises(PrivacyDenied):
        await custody.provision_wrapping(KeyBoundary.from_context(context), "evidence")
    entry = custody.inventory()[0]
    assert entry.state == "pending" and not entry.active
    with pytest.raises(PrivacyDenied):
        await CryptoProtector(public_registry(custody.path)).ready(saved, ("evidence",))
    native.fail_after = None
    with pytest.raises(PrivacyDenied):
        await custody.resume(entry.version)
    await custody.retire(entry.version)
    assert not native.values
    replacement = await custody.provision_wrapping(KeyBoundary.from_context(context), "evidence")
    assert replacement.ref.key_version != entry.version


async def test_complete_pending_native_readback_can_resume(custody, native, monkeypatch):
    _, saved, context, *_ = setup_crypto()
    original = custody._replace
    def interrupted(state, entry):
        if entry.state == "ready":
            raise PrivacyDenied("privacy_capability_unavailable")
        original(state, entry)
    monkeypatch.setattr(custody, "_replace", interrupted)
    with pytest.raises(PrivacyDenied):
        await custody.provision_wrapping(KeyBoundary.from_context(context), "evidence")
    entry = custody.inventory()[0]
    assert entry.state == "pending"
    monkeypatch.setattr(custody, "_replace", original)
    await custody.resume(entry.version)
    assert custody.inventory()[0].state == "ready"
    assert not custody.inventory()[0].active
    await custody.activate(entry.wrapping.ref, guard=Fence().guard)
    await CryptoProtector(public_registry(custody.path)).ready(saved, ("evidence",))


@pytest.mark.parametrize("failure", ["missing", "swapped", "corrupt", "marker"])
async def test_native_parts_authenticate_exact_key_and_boundary(custody, native, failure):
    _, _, context, *_ = setup_crypto()
    descriptor = await custody.provision_wrapping(KeyBoundary.from_context(context), "evidence")
    keys = list(native.values)
    parts = [key for key in keys if not key[1].endswith("complete")]
    if failure == "missing":
        del native.values[parts[0]]
    elif failure == "swapped":
        native.values[parts[0]], native.values[parts[1]] = native.values[parts[1]], native.values[parts[0]]
    elif failure == "corrupt":
        native.values[parts[0]] = "x" + native.values[parts[0]][1:]
    else:
        native.values[keys[-1]] = json.dumps({"version": 1, "parts": MAX_PARTS + 1, "sha256": "0" * 64})
    with pytest.raises(PrivacyDenied):
        with custody.unlocked((descriptor.ref,), expires_at=NOW + timedelta(seconds=20)):
            pass


async def test_pseudonym_retry_keys_separate_exact_lifetimes(custody, native):
    _, saved, context, *_ = setup_crypto()
    boundary, epoch = KeyBoundary.from_context(context), uuid4()
    pseudonym = await custody.provision_pseudonym_key(boundary, epoch)
    handle = await custody.key_for(boundary.project_id, boundary.owner_id, boundary.scope, epoch)
    assert handle.purpose == "pseudonym" and handle.epoch == epoch
    fingerprint = handle.use(lambda key: __import__("hashlib").sha256(key).hexdigest())
    handle.close()
    with pytest.raises(PrivacyDenied):
        handle.use(len)
    with pytest.raises(TypeError):
        pickle.dumps(handle)
    retry = RetryContext(request_id=uuid4(), snapshot=saved)
    retention = uuid4()
    retry_handle = await custody.provision_retry_key(retry, retention_ref=retention, expires_at=NOW + timedelta(seconds=30))
    assert retry_handle.purpose == "retry_commitment"
    assert retry_handle.use(lambda key: __import__("hashlib").sha256(key).hexdigest()) != fingerprint
    retrieved = await custody.get_retry_key(retry, key_version=retry_handle.key_version, retention_ref=retention)
    assert retrieved.reference == retry_handle.reference
    for args in ((uuid4(), boundary.owner_id, boundary.scope, epoch),
                 (boundary.project_id, boundary.owner_id, boundary.scope, uuid4())):
        with pytest.raises(PrivacyDenied):
            await custody.key_for(*args)
    await custody.retire(pseudonym.key_version)
    with pytest.raises(PrivacyDenied):
        await custody.key_for(boundary.project_id, boundary.owner_id, boundary.scope, epoch)
    with pytest.raises(PrivacyDenied):
        await custody.provision_pseudonym_key(boundary, epoch)
    custody.clock = lambda: NOW + timedelta(minutes=1)
    with pytest.raises(PrivacyDenied, match="privacy_retry_unavailable"):
        await custody.get_retry_key(retry, key_version=retry_handle.key_version, retention_ref=retention)


async def test_existing_writer_waits_for_commit_then_refreshes(custody, native):
    _, saved, context, *_ = setup_crypto()
    boundary = KeyBoundary.from_context(context)
    first = await custody.provision_wrapping(boundary, "evidence")
    writer = CryptoProtector(public_registry(custody.path))
    prepared = await writer.seal(b"PV321_SOURCE_QUIESCENCE", context, original_snapshot=saved)
    second = await custody.provision_wrapping(boundary, "evidence", key_id=first.ref.key_id)
    fence = Fence()
    async with writer.writer_guard((prepared,)):
        activation = asyncio.create_task(custody.activate(second.ref, expected=first.ref, guard=fence.guard))
        await asyncio.sleep(0.03)
        assert not activation.done()
    await activation
    with pytest.raises(PrivacyDenied):
        async with writer.writer_guard((prepared,)):
            pytest.fail("old prepared envelope reached persistence")
    new = await writer.seal(b"PV321_SOURCE_QUIESCENCE", context, original_snapshot=saved)
    assert new.key_version == second.ref.key_version
