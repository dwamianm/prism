"""EM01/02/06 crypto components, using real AES/RSA and frozen public test keys."""

import asyncio
import json
import pickle
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from prme.privacy.capabilities import PayloadContext, ProcessingAccess
from prme.privacy.crypto import (
    CryptoProtector, RSAKeyWrapper, canonical, decode64, decode_envelope, encode64, rsa_descriptor,
)
from prme.privacy.keys import KeyBoundary, KeyRegistry, LocalAccessAuthority
from prme.privacy.policy import MemoryRetention, PrivacyDenied, PrivacyPolicy, snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "privacy"
NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
SOURCE = b"EM01 synthetic source-only value"


def fixture_private():
    return serialization.load_pem_private_key((FIXTURES / "rsa-4096-test-key.pem").read_bytes(), password=None)


class Audit:
    def __init__(self):
        self.events, self.fail = [], False

    async def write(self, event):
        if self.fail:
            raise RuntimeError("SYNTHETIC_SECRET_PROVIDER_EXCEPTION")
        self.events.append(event)


class Fence:
    """Real shared lock for policy activation and held effects, not a boolean grant."""
    def __init__(self):
        self.lock = asyncio.Lock()
        self.denied = False

    @asynccontextmanager
    async def guard(self):
        async with self.lock:
            if self.denied:
                raise PrivacyDenied("privacy_stale_policy")
            yield
            if self.denied:
                raise PrivacyDenied("privacy_stale_policy")

    async def deny(self):
        async with self.lock:
            self.denied = True


class PendingClaim:
    # Component validator only. Real backend claims are covered separately.
    def __init__(self):
        self.pending = True

    async def validate(self, payload, access, original_snapshot):
        if not self.pending or access.work_id != payload.context.event_id or access.work_generation != 1:
            raise PrivacyDenied("privacy_processing_unavailable")


def setup_crypto(*, purpose="evidence", use="diagnostics_only", private=True, clock=lambda: NOW):
    boundary = KeyBoundary(project_id=UUID(int=1), owner_id=UUID(int=2), scope="personal")
    policy = PrivacyPolicy(policy_id=UUID(int=3), revision=1, project_id=boundary.project_id,
        owner_id=boundary.owner_id, capture="full", evidence_use=use, evidence_lifetime_seconds=3600,
        memory_retention=MemoryRetention(mode="until_deleted"), language="en")
    saved = snapshot(None, policy, NOW)
    context = PayloadContext(project_id=boundary.project_id, owner_id=boundary.owner_id, scope=boundary.scope,
        event_id=UUID(int=4), source_id=UUID(int=5), representation_id=UUID(int=6), slot_id=UUID(int=7),
        purpose=purpose, payload_kind="sanitized_input" if purpose == "processing" else "sanitized_evidence",
        policy_id=policy.policy_id, policy_revision=1)
    key = fixture_private()
    descriptor = rsa_descriptor(key.public_key(), boundary=boundary, purpose=purpose,
                               provider_id=UUID(int=8), key_id=UUID(int=9), key_version=UUID(int=10))
    wrapper = RSAKeyWrapper({descriptor.ref: key} if private else None)
    registry = KeyRegistry((descriptor,), active=(descriptor.ref,), wrappers={descriptor.ref.provider_id: wrapper})
    audit, claim, fence = Audit(), PendingClaim(), Fence()
    authority = LocalAccessAuthority(audit, processing=claim, clock=clock)
    protector = CryptoProtector(registry, authority=authority)
    return protector, saved, context, descriptor, audit, claim, fence


async def grant(protector, saved, context, *, kind="diagnostic", actions=("read",)):
    return await protector.authority.grant(kind, ((context, saved),),
                                          expires_at=NOW + timedelta(minutes=5), actions=actions)


def processing_access(saved, context):
    return ProcessingAccess(work_id=context.event_id, work_generation=1,
        lease_deadline=NOW + timedelta(minutes=5), processing_deadline=saved.processing_deadline,
        claimed_event_ids=(context.event_id,))


def test_EM01_fixed_AES256_GCM_CAVP_vectors():
    fixture = json.loads((FIXTURES / "crypto-vectors-v1.json").read_text())
    assert len(fixture["aes_gcm"]) == 3
    for vector in fixture["aes_gcm"]:
        key, nonce, plaintext, aad, ciphertext, tag = (
            bytes.fromhex(vector[field]) for field in ("Key", "IV", "PT", "AAD", "CT", "Tag"))
        assert len(key) == 32 and len(nonce) == 12 and len(tag) == 16
        assert AESGCM(key).encrypt(nonce, plaintext, aad) == ciphertext + tag
        assert AESGCM(key).decrypt(nonce, ciphertext + tag, aad) == plaintext


async def test_EM01_real_random_keys_nonces_and_independent_library_decrypt():
    protector, saved, context, descriptor, *_ = setup_crypto()
    first = await protector.seal(SOURCE, context, original_snapshot=saved)
    second = await protector.seal(SOURCE, context.model_copy(update={"slot_id": uuid4()}), original_snapshot=saved)
    one, two = decode_envelope(first, saved), decode_envelope(second, saved)
    private = fixture_private()
    keys = []
    for envelope in (one, two):
        key = private.decrypt(decode64(envelope.wrapped_key, minimum=512, maximum=512),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(),
                         label=envelope.wrapper.bytes()))
        keys.append(key)
        assert AESGCM(key).decrypt(decode64(envelope.nonce, minimum=12, maximum=12),
            decode64(envelope.ciphertext, minimum=16, maximum=1024), envelope.payload.bytes()) == SOURCE
    assert keys[0] != keys[1] and one.nonce != two.nonce
    assert SOURCE.decode() not in first.model_dump_json()
    assert encode64(keys[0]) not in first.model_dump_json()
    assert one.wrapper.ref == descriptor.ref


@pytest.mark.parametrize("field", ["nonce", "ciphertext", "wrapped_key"])
async def test_EM02_tamper_never_releases_plaintext(field):
    protector, saved, context, _, audit, _, fence = setup_crypto()
    access = await grant(protector, saved, context)
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    encoded = json.loads(payload.envelope)
    raw = bytearray(decode64(encoded[field], minimum=1, maximum=6144))
    raw[-1] ^= 1
    encoded[field] = encode64(bytes(raw))
    bad = payload.model_copy(update={"envelope": canonical(encoded).decode()})
    with pytest.raises(PrivacyDenied):
        await protector.open(bad, access, original_snapshot=saved, guard=fence.guard)
    assert not any(e.outcome == "finished" for e in audit.events)


@pytest.mark.parametrize("field,value", [
    ("project_id", UUID(int=99)), ("owner_id", UUID(int=99)), ("scope", "project"),
    ("event_id", UUID(int=99)), ("source_id", UUID(int=99)), ("representation_id", UUID(int=99)),
    ("slot_id", UUID(int=99)), ("object_generation", 2), ("policy_id", UUID(int=99)),
    ("policy_revision", 2), ("purpose", "processing"), ("payload_kind", "sanitized_input"),
])
async def test_EM02_context_substitution(field, value):
    protector, saved, context, _, _, _, fence = setup_crypto()
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    access = await grant(protector, saved, context)
    bad = payload.model_copy(update={"context": context.model_copy(update={field: value})})
    with pytest.raises(PrivacyDenied):
        await protector.open(bad, access, original_snapshot=saved, guard=fence.guard)


@pytest.mark.parametrize("field", ["evidence_deadline", "memory_deadline", "processing_deadline", "references", "policy"])
async def test_EM02_original_composed_snapshot_is_bound(field):
    protector, saved, context, _, _, _, fence = setup_crypto()
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    access = await grant(protector, saved, context)
    value = (NOW + timedelta(seconds=42) if field.endswith("deadline") else () if field == "references"
             else saved.policy.model_copy(update={"minimal_max_bytes": 32}))
    changed = saved.model_copy(update={field: value})
    with pytest.raises(PrivacyDenied):
        await protector.open(payload, access, original_snapshot=changed, guard=fence.guard)


async def test_EM02_noncanonical_unknown_wrapper_and_selector_refusal():
    protector, saved, context, _, _, _, fence = setup_crypto()
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    access = await grant(protector, saved, context)
    unknown = json.loads(payload.envelope)
    unknown["wrapper"]["ref"]["kind"] = "custom-cipher"
    for bad in (
        payload.model_copy(update={"envelope": " " + payload.envelope}),
        payload.model_copy(update={"envelope": payload.envelope[:-1] + ',"schema_version":1}'}),
        payload.model_copy(update={"envelope": canonical(unknown).decode()}),
        payload.model_copy(update={"key_version": uuid4()}),
    ):
        with pytest.raises(PrivacyDenied):
            await protector.open(bad, access, original_snapshot=saved, guard=fence.guard)


async def test_EM06_public_writer_grants_audit_and_metadata():
    writer, saved, context, _, _, _, fence = setup_crypto(private=False)
    await writer.ready(saved, ("evidence",))
    payload = await writer.seal(SOURCE, context, original_snapshot=saved)
    access = await grant(writer, saved, context)
    with pytest.raises(PrivacyDenied):
        await writer.open(payload, access, original_snapshot=saved, guard=fence.guard)
    metadata = await writer.inspect_metadata(payload, original_snapshot=saved,
        boundary=KeyBoundary.from_context(context), guard=fence.guard)
    assert not metadata["private_key_checked"] and not metadata["ciphertext_authenticated"]
    assert SOURCE.decode() not in json.dumps(metadata)
    reader, _, _, _, audit, _, _ = setup_crypto()
    runtime = await grant(reader, saved, context, kind="runtime")
    with pytest.raises(PrivacyDenied):
        await reader.open(payload, runtime, original_snapshot=saved, guard=fence.guard)
    diagnostic = await grant(reader, saved, context)
    audit.fail = True
    with pytest.raises(PrivacyDenied) as error:
        await reader.open(payload, diagnostic, original_snapshot=saved, guard=fence.guard)
    assert "SYNTHETIC_SECRET" not in str(error.value)
    with pytest.raises(PrivacyDenied):
        LocalAccessAuthority(audit, isolation="service_isolated")
    with pytest.raises(TypeError):
        pickle.dumps(reader.registry.wrapper(reader.registry.inventory()[0]))


async def test_EM06_actual_response_scope_orders_revocation():
    protector, saved, context, _, _, _, fence = setup_crypto()
    access = await grant(protector, saved, context)
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    entered, send = asyncio.Event(), asyncio.Event()

    async def transport():
        async with protector.open_held(payload, access, original_snapshot=saved, guard=fence.guard) as body:
            entered.set()
            await send.wait()
            assert body == SOURCE
            return "response_sent"

    response = asyncio.create_task(transport())
    await entered.wait()
    revoke = asyncio.create_task(fence.deny())
    await asyncio.sleep(0)
    assert not revoke.done()
    send.set()
    assert await response == "response_sent"
    await revoke
    with pytest.raises(PrivacyDenied):
        await protector.open(payload, access, original_snapshot=saved, guard=fence.guard)


async def test_EM06_processing_only_key_response_and_expired_handle():
    protector, saved, context, _, _, claim, fence = setup_crypto(purpose="processing")
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    access = processing_access(saved, context)
    async with protector.unwrap_processing_key_held(payload, access, original_snapshot=saved, guard=fence.guard) as lease:
        envelope = decode_envelope(payload, saved)
        assert lease.context == context and lease.envelope_revision == 1
        assert lease.use(lambda key: AESGCM(key).decrypt(
            decode64(envelope.nonce, minimum=12, maximum=12),
            decode64(envelope.ciphertext, minimum=16, maximum=1024), envelope.payload.bytes())) == SOURCE
        with pytest.raises(TypeError):
            pickle.dumps(lease)
    with pytest.raises(PrivacyDenied):
        lease.use(len)
    claim.pending = False
    with pytest.raises(PrivacyDenied):
        async with protector.unwrap_processing_key_held(payload, access, original_snapshot=saved, guard=fence.guard):
            pytest.fail("terminal work obtained a processing key")
