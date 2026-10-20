"""Closed AES-256-GCM payloads with separately authenticated, replaceable wrappers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal, TypeVar

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field

from prme.privacy.capabilities import EvidenceAccess, Guard, PayloadContext, ProcessingAccess, ProtectedPayload
from prme.privacy.keys import (
    KeyBoundary,
    KeyRegistry,
    LocalAccessAuthority,
    ReadAction,
    UnwrapPermit,
    WrappingKeyDescriptor,
    WrappingKeyRef,
    aware,
    utc_now,
)
from prme.privacy.policy import FrozenModel, PolicySnapshot, PrivacyDenied

MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_ENVELOPE_BYTES = 24 * 1024 * 1024
T = TypeVar("T")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def encode64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode64(value: str, *, minimum: int, maximum: int) -> bytes:
    if not isinstance(value, str) or not minimum * 4 // 3 <= len(value) <= (maximum + 2) * 4 // 3:
        raise PrivacyDenied("privacy_capability_unavailable")
    try:
        result = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if not minimum <= len(result) <= maximum or encode64(result) != value:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise PrivacyDenied("privacy_capability_unavailable") from None


def _snapshot_metadata(snapshot: PolicySnapshot) -> dict[str, Any]:
    # Re-validate even model_copy/model_construct inputs before canonical binding.
    saved = PolicySnapshot.model_validate_json(snapshot.model_dump_json())
    values = saved.model_dump(mode="json")
    for field in ("admitted_at", "evidence_deadline", "memory_deadline", "processing_deadline"):
        clock: datetime | None = getattr(saved, field)
        values[field] = aware(clock).isoformat(timespec="microseconds") if clock is not None else None
    return {"domain": "prme-policy-snapshot-v1", "snapshot": values}


def policy_binding(snapshot: PolicySnapshot) -> str:
    """Digest of policy metadata only, never source content or a private checksum."""
    return hashlib.sha256(canonical(_snapshot_metadata(snapshot))).hexdigest()


class PolicyBinding(FrozenModel):
    domain: Literal["prme-policy-snapshot-v1"] = "prme-policy-snapshot-v1"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PayloadAAD(FrozenModel):
    schema_version: Literal[1] = 1
    domain: Literal["prme-payload-aad-v1"] = "prme-payload-aad-v1"
    algorithm: Literal["aes-256-gcm"] = "aes-256-gcm"
    context: PayloadContext
    policy_binding: PolicyBinding

    def bytes(self) -> bytes:
        return canonical(self.model_dump(mode="json"))


class WrapperContext(FrozenModel):
    schema_version: Literal[1] = 1
    domain: Literal["prme-wrapper-v1"] = "prme-wrapper-v1"
    payload: PayloadAAD
    ref: WrappingKeyRef
    revision: int = Field(gt=0, strict=True)

    def bytes(self) -> bytes:
        return canonical(self.model_dump(mode="json"))


class CipherEnvelope(FrozenModel):
    schema_version: Literal[1] = 1
    payload: PayloadAAD
    nonce: str = Field(min_length=16, max_length=16, repr=False)
    ciphertext: str = Field(min_length=22, max_length=(MAX_PAYLOAD_BYTES + 18) * 4 // 3, repr=False)
    wrapper: WrapperContext
    wrapped_key: str = Field(min_length=1, max_length=8192, repr=False)

    def encoded(self) -> str:
        return canonical(self.model_dump(mode="json")).decode("ascii")


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def validate_context(context: PayloadContext, snapshot: PolicySnapshot) -> PayloadAAD:
    context = PayloadContext.model_validate_json(context.model_dump_json())
    if (KeyBoundary.from_context(context) != KeyBoundary.from_snapshot(snapshot)
            or (context.policy_id, context.policy_revision) != (snapshot.policy.policy_id, snapshot.policy.revision)
            or context.payload_kind != ("sanitized_input" if context.purpose == "processing" else "sanitized_evidence")
            or (context.purpose == "evidence" and snapshot.policy.capture == "none")
            or (context.purpose == "processing" and not snapshot.policy.durable_processing_input)):
        raise PrivacyDenied("privacy_boundary_mismatch")
    return PayloadAAD(context=context, policy_binding=PolicyBinding(sha256=policy_binding(snapshot)))


def decode_envelope(payload: ProtectedPayload, original_snapshot: PolicySnapshot) -> CipherEnvelope:
    """Shared bounded parser for local and hosted adapters; no decrypt or key lookup."""
    try:
        if not isinstance(payload.envelope, str) or len(payload.envelope) > MAX_ENVELOPE_BYTES:
            raise ValueError()
        envelope = CipherEnvelope.model_validate(json.loads(payload.envelope, object_pairs_hook=_pairs))
        expected = validate_context(payload.context, original_snapshot)
        if (envelope.encoded() != payload.envelope or envelope.payload != expected
                or envelope.wrapper.payload != expected
                or (payload.provider_id, payload.key_id, payload.key_version, payload.envelope_revision) != (
                    envelope.wrapper.ref.provider_id, envelope.wrapper.ref.key_id,
                    envelope.wrapper.ref.key_version, envelope.wrapper.revision)):
            raise ValueError()
        decode64(envelope.nonce, minimum=12, maximum=12)
        decode64(envelope.ciphertext, minimum=16, maximum=MAX_PAYLOAD_BYTES + 16)
        decode64(envelope.wrapped_key, minimum=1, maximum=6144)
        return envelope
    except PrivacyDenied:
        raise
    except Exception:
        raise PrivacyDenied("privacy_capability_unavailable") from None


def _public_key(descriptor: WrappingKeyDescriptor) -> rsa.RSAPublicKey:
    try:
        if descriptor.ref.kind != "local-rsa-oaep-sha256-v1" or descriptor.public_key is None:
            raise ValueError()
        der = decode64(descriptor.public_key, minimum=1, maximum=1024)
        key = serialization.load_der_public_key(der)
        if (not isinstance(key, rsa.RSAPublicKey) or key.key_size != 4096
                or key.public_numbers().e != 65537 or hashlib.sha256(der).hexdigest() != descriptor.ref.locator):
            raise ValueError()
        return key
    except Exception:
        raise PrivacyDenied("privacy_capability_unavailable") from None


def rsa_descriptor(key: rsa.RSAPublicKey, *, boundary: KeyBoundary, purpose: Literal["evidence", "processing"],
                   provider_id: Any, key_id: Any, key_version: Any) -> WrappingKeyDescriptor:
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    result = WrappingKeyDescriptor(
        ref=WrappingKeyRef(kind="local-rsa-oaep-sha256-v1", provider_id=provider_id, key_id=key_id,
                           key_version=key_version, locator=hashlib.sha256(der).hexdigest()),
        boundary=boundary, purpose=purpose, public_key=encode64(der))
    _public_key(result)
    return result


class RSAKeyWrapper:
    """Public-only by default. Private keys must be explicitly supplied/unlocked."""

    isolation = "same_process"

    def __init__(self, private_keys: dict[WrappingKeyRef, rsa.RSAPrivateKey] | None = None, *,
                 expires_at: datetime | None = None, clock: Callable[[], datetime] = utc_now) -> None:
        self._private_keys = dict(private_keys or {})
        self._expires_at, self._clock = expires_at, clock

    async def ready(self, descriptor: WrappingKeyDescriptor) -> None:
        _public_key(descriptor)

    async def wrap_key(self, key: bytes, descriptor: WrappingKeyDescriptor, wrapping_context: bytes) -> bytes:
        if len(key) != 32:
            raise PrivacyDenied("privacy_capability_unavailable")
        return _public_key(descriptor).encrypt(
            key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=wrapping_context))

    async def unwrap_key(self, wrapped: bytes, descriptor: WrappingKeyDescriptor,
                         wrapping_context: bytes, permit: UnwrapPermit) -> bytes:
        permit.require(descriptor)
        try:
            if self._expires_at is not None and aware(self._clock()) >= aware(self._expires_at):
                raise ValueError()
            public = _public_key(descriptor)
            key = self._private_keys[descriptor.ref]
            if len(wrapped) != 512 or key.public_key().public_numbers() != public.public_numbers():
                raise ValueError()
            result = key.decrypt(
                wrapped, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                      algorithm=hashes.SHA256(), label=wrapping_context))
            if len(result) != 32:
                raise ValueError()
            return result
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    def close(self) -> None:
        self._private_keys.clear()

    def __repr__(self) -> str:
        return "<RSAKeyWrapper>"

    def __reduce_ex__(self, protocol: int) -> Any:
        raise TypeError("privacy_capability_not_serializable")


class ProcessingKeyLease:
    """A processing-only key response, valid only inside its held authorization."""

    __slots__ = ("context", "key_ref", "envelope_revision", "_key", "_permit")

    def __init__(self, envelope: CipherEnvelope, key: bytes, permit: UnwrapPermit) -> None:
        if envelope.payload.context.purpose != "processing" or len(key) != 32:
            raise PrivacyDenied("privacy_processing_unavailable")
        self.context, self.key_ref = envelope.payload.context, envelope.wrapper.ref
        self.envelope_revision = envelope.wrapper.revision
        self._key, self._permit = bytearray(key), permit

    def use(self, callback: Callable[[bytes], T]) -> T:
        if len(self._key) != 32 or not self._permit._active:
            raise PrivacyDenied("privacy_processing_unavailable")
        return callback(bytes(self._key))

    def close(self) -> None:
        self._key[:] = b"\0" * len(self._key)
        self._key.clear()

    def __repr__(self) -> str:
        return "<ProcessingKeyLease>"

    def __reduce_ex__(self, protocol: int) -> Any:
        raise TypeError("privacy_capability_not_serializable")


class CryptoProtector:
    """Concrete encryption over the existing admission slots and capability hooks."""

    def __init__(self, registry: KeyRegistry, *, authority: LocalAccessAuthority | None = None) -> None:
        self.registry, self.authority = registry, authority

    async def validate_writers(self, payloads: tuple[ProtectedPayload, ...]) -> None:
        """Called inside the admission publication guard, through durable commit.

        Activation must hold that same authority. A previously prepared envelope
        may fail after rotation; it must never enter storage under a retired
        writer selection. Reads still resolve the envelope's exact version.
        """
        for payload in payloads:
            current = self.registry.writer(KeyBoundary.from_context(payload.context), payload.context.purpose)
            if (payload.provider_id, payload.key_id, payload.key_version) != (
                    current.ref.provider_id, current.ref.key_id, current.ref.key_version):
                raise PrivacyDenied("privacy_capability_unavailable")

    @asynccontextmanager
    async def writer_guard(self, payloads: tuple[ProtectedPayload, ...]) -> AsyncIterator[None]:
        async with self.registry.writers_held():
            await self.validate_writers(payloads)
            yield

    async def ready(self, snapshot: PolicySnapshot, purposes: tuple[str, ...]) -> None:
        try:
            for purpose in purposes:
                if purpose not in {"evidence", "processing"}:
                    raise ValueError()
                descriptor = self.registry.writer(KeyBoundary.from_snapshot(snapshot), purpose)
                await self.registry.wrapper(descriptor).ready(descriptor)
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    async def seal(self, plaintext: bytes, context: PayloadContext, *,
                   original_snapshot: PolicySnapshot) -> ProtectedPayload:
        try:
            if not isinstance(plaintext, bytes) or len(plaintext) > MAX_PAYLOAD_BYTES:
                raise ValueError()
            aad = validate_context(context, original_snapshot)
            descriptor = self.registry.writer(KeyBoundary.from_context(context), context.purpose)
            key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad.bytes())
            wrapping = WrapperContext(payload=aad, ref=descriptor.ref, revision=1)
            wrapped = await self.registry.wrapper(descriptor).wrap_key(key, descriptor, wrapping.bytes())
            if not isinstance(wrapped, bytes) or not 1 <= len(wrapped) <= 6144:
                raise ValueError()
            envelope = CipherEnvelope(payload=aad, nonce=encode64(nonce), ciphertext=encode64(ciphertext),
                                      wrapper=wrapping, wrapped_key=encode64(wrapped))
            return self._stored(envelope)
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    @staticmethod
    def _stored(envelope: CipherEnvelope) -> ProtectedPayload:
        ref = envelope.wrapper.ref
        return ProtectedPayload(context=envelope.payload.context, envelope=envelope.encoded(),
                                provider_id=ref.provider_id, key_id=ref.key_id, key_version=ref.key_version,
                                envelope_revision=envelope.wrapper.revision)

    @asynccontextmanager
    async def _key_held(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess, *,
                        original_snapshot: PolicySnapshot, guard: Guard,
                        action: ReadAction) -> AsyncIterator[tuple[CipherEnvelope, bytes, UnwrapPermit]]:
        try:
            envelope = decode_envelope(payload, original_snapshot)
            if self.authority is None:
                raise PrivacyDenied("privacy_capability_unavailable")
            descriptor = self.registry.exact(envelope.wrapper.ref)
            async with self.authority.authorize(payload, access, original_snapshot, guard=guard, action=action) as permit:
                permit.require(descriptor)
                key = await self.registry.wrapper(descriptor).unwrap_key(
                    decode64(envelope.wrapped_key, minimum=1, maximum=6144),
                    descriptor, envelope.wrapper.bytes(), permit)
                if not isinstance(key, bytes) or len(key) != 32:
                    raise PrivacyDenied("privacy_capability_unavailable")
                yield envelope, key, permit
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    @asynccontextmanager
    async def open_held(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess, *,
                        original_snapshot: PolicySnapshot, guard: Guard) -> AsyncIterator[bytes]:
        async with self._key_held(payload, access, original_snapshot=original_snapshot,
                                  guard=guard, action="read") as (envelope, key, _):
            try:
                plaintext = AESGCM(key).decrypt(
                    decode64(envelope.nonce, minimum=12, maximum=12),
                    decode64(envelope.ciphertext, minimum=16, maximum=MAX_PAYLOAD_BYTES + 16),
                    envelope.payload.bytes())
            except Exception:
                raise PrivacyDenied("privacy_capability_unavailable") from None
            yield plaintext

    async def open(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess, *,
                   original_snapshot: PolicySnapshot, guard: Guard) -> bytes:
        async with self.open_held(payload, access, original_snapshot=original_snapshot, guard=guard) as plaintext:
            return plaintext

    @asynccontextmanager
    async def unwrap_processing_key_held(self, payload: ProtectedPayload, access: ProcessingAccess, *,
                                         original_snapshot: PolicySnapshot, guard: Guard) -> AsyncIterator[ProcessingKeyLease]:
        if not isinstance(access, ProcessingAccess) or payload.context.purpose != "processing":
            raise PrivacyDenied("privacy_processing_unavailable")
        async with self._key_held(payload, access, original_snapshot=original_snapshot,
                                  guard=guard, action="processing_key") as (envelope, key, permit):
            lease = ProcessingKeyLease(envelope, key, permit)
            try:
                yield lease
            finally:
                lease.close()

    @asynccontextmanager
    async def rewrap_held(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess,
                          target: WrappingKeyRef, *, original_snapshot: PolicySnapshot,
                          guard: Guard) -> AsyncIterator[ProtectedPayload]:
        async with self._key_held(payload, access, original_snapshot=original_snapshot,
                                  guard=guard, action="rewrap") as (envelope, key, permit):
            descriptor = self.registry.exact(target)
            permit.require(descriptor)
            wrapping = WrapperContext(payload=envelope.payload, ref=target, revision=envelope.wrapper.revision + 1)
            wrapped = await self.registry.wrapper(descriptor).wrap_key(key, descriptor, wrapping.bytes())
            candidate = envelope.model_copy(update={"wrapper": wrapping, "wrapped_key": encode64(wrapped)})
            result = self._stored(candidate)
            decode_envelope(result, original_snapshot)
            yield result

    async def rewrap(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess,
                     target: WrappingKeyRef, *, original_snapshot: PolicySnapshot, guard: Guard) -> ProtectedPayload:
        async with self.rewrap_held(payload, access, target, original_snapshot=original_snapshot, guard=guard) as result:
            return result

    async def inspect_metadata(self, payload: ProtectedPayload, *, original_snapshot: PolicySnapshot,
                               boundary: KeyBoundary, guard: Guard) -> dict[str, Any]:
        """Availability metadata only; presence cannot certify decryptability."""
        async with guard():
            if boundary != KeyBoundary.from_context(payload.context):
                raise PrivacyDenied("privacy_boundary_mismatch")
            envelope = decode_envelope(payload, original_snapshot)
            known = envelope.wrapper.ref in {d.ref for d in self.registry.inventory()}
            return {"context": payload.context.model_dump(mode="json"),
                    "key_ref": envelope.wrapper.ref.model_dump(mode="json"),
                    "envelope_revision": payload.envelope_revision, "public_descriptor_known": known,
                    "ciphertext_authenticated": False, "private_key_checked": False}
