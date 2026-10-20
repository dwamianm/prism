"""EM01/02/06 crypto components, using real AES/RSA and frozen public test keys."""

import asyncio
import json
import pickle
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from prme.privacy.capabilities import PayloadContext, ProcessingAccess, ProtectedPayload
from prme.privacy.crypto import (
    CryptoProtector, RSAKeyWrapper, canonical, decode64, decode_envelope, encode64, rsa_descriptor,
)
from prme.privacy.keys import KeyBoundary, KeyRegistry, LocalAccessAuthority
from prme.privacy.policy import MemoryRetention, PolicySnapshot, PrivacyDenied, PrivacyPolicy, snapshot

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


async def test_EM01_fixed_whole_envelope_compatible_vector():
    vector = json.loads((FIXTURES / "crypto-vectors-v1.json").read_text())["envelope_v1"]
    payload = ProtectedPayload.model_validate(vector["payload"])
    saved = PolicySnapshot.model_validate(vector["snapshot"])
    parsed = decode_envelope(payload, saved)
    assert parsed.payload.bytes().hex() == vector["payload_aad_hex"]
    assert parsed.wrapper.bytes().hex() == vector["wrapping_context_hex"]
    key = fixture_private().decrypt(decode64(parsed.wrapped_key, minimum=512, maximum=512),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(),
                     label=bytes.fromhex(vector["wrapping_context_hex"])))
    import hmac
    assert hmac.compare_digest(key, bytes.fromhex(vector["data_key_hex"]))
    protector, _, context, *_ = setup_crypto()
    access = await grant(protector, saved, context)
    plaintext = await protector.open(payload, access, original_snapshot=saved, guard=Fence().guard)
    assert plaintext.hex() == vector["plaintext_hex"]


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


async def test_EM02_inner_AAD_label_wrapper_revision_swaps_and_wrong_private_key():
    from cryptography.hazmat.primitives.asymmetric import rsa
    protector, saved, context, descriptor, _, _, fence = setup_crypto()
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    original = decode_envelope(payload, saved)
    altered_context = context.model_copy(update={"event_id": uuid4()})
    aad = original.payload.model_copy(update={"context": altered_context})
    wrapping = original.wrapper.model_copy(update={"payload": aad})
    forged = original.model_copy(update={"payload": aad, "wrapper": wrapping})
    access = await grant(protector, saved, altered_context)
    with pytest.raises(PrivacyDenied):
        await protector.open(protector._stored(forged), access, original_snapshot=saved, guard=fence.guard)
    # A valid RSA label alone cannot rebind a saved AES payload's AAD.
    key = fixture_private().decrypt(decode64(original.wrapped_key, minimum=512, maximum=512),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=original.wrapper.bytes()))
    wrapped = fixture_private().public_key().encrypt(key,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=wrapping.bytes()))
    forged = forged.model_copy(update={"wrapped_key": encode64(wrapped)})
    with pytest.raises(PrivacyDenied):
        await protector.open(protector._stored(forged), access, original_snapshot=saved, guard=fence.guard)
    access = await grant(protector, saved, context)
    revised = original.model_copy(update={"wrapper": original.wrapper.model_copy(update={"revision": 2})})
    second = decode_envelope(await protector.seal(SOURCE, context, original_snapshot=saved), saved)
    swapped = original.model_copy(update={"wrapped_key": second.wrapped_key})
    for invalid in (revised, swapped):
        with pytest.raises(PrivacyDenied):
            await protector.open(protector._stored(invalid), access, original_snapshot=saved, guard=fence.guard)
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    protector.registry = KeyRegistry((descriptor,), active=(descriptor.ref,),
        wrappers={descriptor.ref.provider_id: RSAKeyWrapper({descriptor.ref: wrong})})
    with pytest.raises(PrivacyDenied):
        await protector.open(payload, access, original_snapshot=saved, guard=fence.guard)


async def test_EM02_private_permit_is_bound_to_exact_slot_wrapper_and_bytes():
    protector, saved, context, descriptor, _, _, fence = setup_crypto()
    one = await protector.seal(SOURCE, context, original_snapshot=saved)
    two = await protector.seal(SOURCE, context.model_copy(update={"event_id": uuid4(), "slot_id": uuid4()}),
                               original_snapshot=saved)
    first, second = decode_envelope(one, saved), decode_envelope(two, saved)
    access = await grant(protector, saved, context)
    wrapper = protector.registry.wrapper(descriptor)
    async with protector.authority.authorize(one, access, saved, guard=fence.guard, action="read") as permit:
        assert len(await wrapper.unwrap_key(decode64(first.wrapped_key, minimum=512, maximum=512),
                                             descriptor, first.wrapper.bytes(), permit)) == 32
        with pytest.raises(PrivacyDenied):
            await wrapper.unwrap_key(decode64(second.wrapped_key, minimum=512, maximum=512),
                                     descriptor, second.wrapper.bytes(), permit)
        with pytest.raises(PrivacyDenied):
            await wrapper.unwrap_key(decode64(first.wrapped_key, minimum=512, maximum=512),
                                     descriptor, second.wrapper.bytes(), permit)
    with pytest.raises(PrivacyDenied):
        await wrapper.unwrap_key(decode64(first.wrapped_key, minimum=512, maximum=512),
                                 descriptor, first.wrapper.bytes(), permit)


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


async def test_EM06_standard_explicit_runtime_and_expiry_selected_ids():
    clock = [NOW]
    protector, saved, context, descriptor, _, _, fence = setup_crypto(use="runtime", clock=lambda: clock[0])
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    access = await grant(protector, saved, context, kind="runtime")
    assert await protector.open(payload, access, original_snapshot=saved, guard=fence.guard) == SOURCE
    other = await protector.seal(SOURCE, context.model_copy(update={"event_id": uuid4()}), original_snapshot=saved)
    calls = 0
    wrapper = protector.registry.wrapper(descriptor)
    unwrap = wrapper.unwrap_key
    async def counted(*args):
        nonlocal calls
        calls += 1
        return await unwrap(*args)
    wrapper.unwrap_key = counted
    with pytest.raises(PrivacyDenied):
        await protector.open(other, access, original_snapshot=saved, guard=fence.guard)
    clock[0] = access.expires_at
    with pytest.raises(PrivacyDenied):
        await protector.open(payload, access, original_snapshot=saved, guard=fence.guard)
    assert calls == 0


async def test_EM06_multiple_selected_final_lease_and_nonreentrant_open_evidence():
    protector, saved, context, descriptor, audit, _, fence = setup_crypto(use="runtime")
    contexts = (context, context.model_copy(update={"event_id": uuid4(), "slot_id": uuid4()}))
    payloads = tuple([await protector.seal(SOURCE, c, original_snapshot=saved) for c in contexts])
    access = await protector.authority.grant("runtime", tuple((c, saved) for c in contexts),
                                             expires_at=NOW + timedelta(minutes=1))
    # Preserve bounded evidence of why nested open_held is not a batching API.
    async with protector.open_held(payloads[0], access, original_snapshot=saved, guard=fence.guard):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(protector.open(payloads[1], access, original_snapshot=saved,
                                                   guard=fence.guard), timeout=0.02)
    for payload in payloads:
        async with protector.open_held(payload, access, original_snapshot=saved, guard=fence.guard) as source:
            assert source == SOURCE
    calls = 0
    wrapper = protector.registry.wrapper(descriptor)
    async def forbidden(*args):
        nonlocal calls
        calls += 1
        raise AssertionError("metadata lease unwrapped a key")
    wrapper.unwrap_key = forbidden
    before = len(audit.events)
    async with protector.hold_evidence_access(tuple((p, saved) for p in payloads), access, guard=fence.guard):
        async def guarded_effect():
            async with fence.guard():
                return "effect_guard_entered"
        assert await asyncio.wait_for(guarded_effect(), timeout=0.1) == "effect_guard_entered"
        revocation = asyncio.create_task(protector.authority.revoke(access.grant_id))
        await asyncio.sleep(0.02)
        assert not revocation.done()
    await revocation
    assert calls == 0 and len(audit.events) == before + 4


@pytest.mark.parametrize("gap", ["revoke", "expiry", "fence", "after_entry_fence"])
async def test_EM06_final_lease_gap_denies_before_downstream_effect(gap):
    clock = [NOW]
    protector, saved, context, _, _, _, fence = setup_crypto(use="runtime", clock=lambda: clock[0])
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    access = await grant(protector, saved, context, kind="runtime")
    assert await protector.open(payload, access, original_snapshot=saved, guard=fence.guard) == SOURCE
    if gap == "revoke":
        await protector.authority.revoke(access.grant_id)
    elif gap == "expiry":
        clock[0] = access.expires_at
    elif gap == "fence":
        await fence.deny()
    effect, policy_changed = False, False
    with pytest.raises(PrivacyDenied):
        async with protector.hold_evidence_access(((payload, saved),), access, guard=fence.guard):
            if gap == "after_entry_fence":
                await asyncio.wait_for(fence.deny(), timeout=0.1)
                policy_changed = True
            async with fence.guard():
                effect = True
    assert not effect
    if gap == "after_entry_fence":
        assert policy_changed, "final lease prevented the real current-policy transition"


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


async def expiry_case(purpose, deadline):
    """Real crypto, with one independently selected finite deadline."""
    clock = [NOW]
    protector, saved, context, descriptor, audit, claim, fence = setup_crypto(
        purpose=purpose, use="runtime", clock=lambda: clock[0])
    changes = {}
    if deadline == "memory":
        changes["memory_retention"] = MemoryRetention(mode="erase_after", seconds=1)
    elif deadline == "evidence":
        changes["evidence_lifetime_seconds"] = 1
    elif deadline == "processing":
        changes["processing_ceiling_seconds"] = 1
    saved = snapshot(None, saved.policy.model_copy(update=changes), NOW)
    payload = await protector.seal(SOURCE, context, original_snapshot=saved)
    access = (processing_access(saved, context) if purpose == "processing"
              else await grant(protector, saved, context, actions=("read", "rewrap")))
    expires = (access.expires_at if deadline == "grant" else access.lease_deadline if deadline == "lease"
               else getattr(saved, f"{deadline}_deadline"))
    return SimpleNamespace(protector=protector, saved=saved, payload=payload, access=access,
                           descriptor=descriptor, audit=audit, claim=claim, fence=fence,
                           clock=clock, expires=expires)


@pytest.mark.parametrize("exposure", ["permit", "plaintext", "final_lease"])
@pytest.mark.parametrize("deadline", ["grant", "evidence", "memory"])
async def test_EM06_expiry_during_audit_denies_before_effect(exposure, deadline):
    case = await expiry_case("evidence", deadline)
    selections = ((case.payload, case.saved),)
    if exposure == "final_lease":
        # Expiry of an earlier selection must not be hidden by the last audit.
        other_saved = snapshot(None, case.saved.policy.model_copy(update={
            "evidence_lifetime_seconds": 3600,
            "memory_retention": MemoryRetention(mode="until_deleted")}), NOW)
        other_context = case.payload.context.model_copy(update={"event_id": uuid4(), "slot_id": uuid4()})
        other = await case.protector.seal(SOURCE, other_context, original_snapshot=other_saved)
        selections += ((other, other_saved),)
        case.access = await case.protector.authority.grant("runtime",
            tuple((p.context, saved) for p, saved in selections), expires_at=NOW + timedelta(minutes=5))
    write = case.audit.write
    authorized = 0
    async def expire_during_audit(event):
        nonlocal authorized
        await write(event)
        if event.outcome == "authorized":
            authorized += 1
            if authorized == len(selections):
                case.clock[0] = case.expires
    case.audit.write = expire_during_audit
    wrapper = case.protector.registry.wrapper(case.descriptor)
    unwrap, private_calls, effects = wrapper.unwrap_key, [], []
    async def counted_unwrap(*args):
        private_calls.append("unwrap")
        return await unwrap(*args)
    wrapper.unwrap_key = counted_unwrap
    with pytest.raises(PrivacyDenied):
        if exposure == "permit":
            async with case.protector.authority.authorize(case.payload, case.access, case.saved,
                                                          guard=case.fence.guard, action="read"):
                effects.append("permit")
        elif exposure == "plaintext":
            async with case.protector.open_held(case.payload, case.access,
                    original_snapshot=case.saved, guard=case.fence.guard):
                effects.append("plaintext")
        else:
            async with case.protector.hold_evidence_access(selections, case.access, guard=case.fence.guard):
                effects.append("final_lease")
    assert effects == [], f"expired {deadline} reached {exposure} caller"
    assert private_calls == [], "expired audit reached private provider"
    assert not any(event.outcome == "finished" for event in case.audit.events)


@pytest.mark.parametrize("exposure,purpose,deadline,wait_at", [
    ("plaintext", "evidence", "grant", "unwrap"),
    ("plaintext", "evidence", "evidence", "unwrap"),
    ("plaintext", "evidence", "memory", "unwrap"),
    ("plaintext", "processing", "lease", "unwrap"),
    ("processing_key", "processing", "processing", "unwrap"),
    ("processing_key", "processing", "memory", "unwrap"),
    ("rewrap", "evidence", "grant", "wrap"),
    ("rewrap", "evidence", "evidence", "wrap"),
    ("rewrap", "evidence", "memory", "wrap"),
    ("rewrap", "processing", "lease", "wrap"),
    ("rewrap", "processing", "processing", "wrap"),
    ("rewrap", "processing", "memory", "wrap"),
])
async def test_EM06_expiry_during_provider_denies_before_effect(exposure, purpose, deadline, wait_at):
    case = await expiry_case(purpose, deadline)
    wrapper = case.protector.registry.wrapper(case.descriptor)
    method = getattr(wrapper, f"{wait_at}_key")
    async def expire_during_provider(*args):
        result = await method(*args)
        case.clock[0] = case.expires
        return result
    setattr(wrapper, f"{wait_at}_key", expire_during_provider)
    effects = []
    with pytest.raises(PrivacyDenied):
        if exposure == "plaintext":
            async with case.protector.open_held(case.payload, case.access,
                    original_snapshot=case.saved, guard=case.fence.guard):
                effects.append("plaintext")
        elif exposure == "processing_key":
            async with case.protector.unwrap_processing_key_held(case.payload, case.access,
                    original_snapshot=case.saved, guard=case.fence.guard) as lease:
                effects.append("processing_key_lease")
                lease.use(lambda key: effects.append("processing_key_bytes"))
        else:
            async with case.protector.rewrap_held(case.payload, case.access, case.descriptor.ref,
                    original_snapshot=case.saved, guard=case.fence.guard):
                effects.append("rewrap_CAS")
    assert effects == [], f"expired {deadline} reached {exposure} after {wait_at}"
    assert not any(event.outcome == "finished" for event in case.audit.events)


@pytest.mark.parametrize("deadline", ["lease", "processing", "memory"])
async def test_EM06_expiry_during_processing_validation_denies_before_permit(deadline):
    case = await expiry_case("processing", deadline)
    validate = case.claim.validate
    async def expire_during_validation(*args):
        await validate(*args)
        case.clock[0] = case.expires
    case.claim.validate = expire_during_validation
    effects = []
    with pytest.raises(PrivacyDenied):
        async with case.protector.authority.authorize(case.payload, case.access, case.saved,
                                                      guard=case.fence.guard, action="processing_key"):
            effects.append("permit")
    assert effects == [], f"expired {deadline} reached permit after claim validation"
    assert case.audit.events == [], "expired validation was recorded as authorized"


@pytest.mark.parametrize("deadline", ["lease", "processing", "memory"])
async def test_EM06_expiry_inside_processing_lease_denies_before_callback(deadline):
    case = await expiry_case("processing", deadline)
    effects = []
    with pytest.raises(PrivacyDenied):
        async with case.protector.unwrap_processing_key_held(case.payload, case.access,
                original_snapshot=case.saved, guard=case.fence.guard) as lease:
            case.clock[0] = case.expires
            lease.use(lambda key: effects.append("processing_key_bytes"))
    assert effects == [], f"expired {deadline} reached processing key callback"


@pytest.mark.parametrize("exposure", ["plaintext_return", "final_lease_return"])
async def test_EM06_expiry_during_completion_audit_denies_return(exposure):
    case = await expiry_case("evidence", "grant")
    write = case.audit.write
    async def expire_during_completion(event):
        await write(event)
        if event.outcome == "finished":
            case.clock[0] = case.expires
    case.audit.write = expire_during_completion
    effects, denied = [], False
    try:
        if exposure == "plaintext_return":
            await case.protector.open(case.payload, case.access,
                                     original_snapshot=case.saved, guard=case.fence.guard)
        else:
            async with case.protector.hold_evidence_access(
                    ((case.payload, case.saved),), case.access, guard=case.fence.guard):
                pass
        effects.append(exposure)
    except PrivacyDenied:
        denied = True
    assert effects == [], f"expired completion audit reached {exposure}"
    assert denied
