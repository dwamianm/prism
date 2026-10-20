"""Explicit, purpose-bound key capabilities. Nothing discovers private authority."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from prme.privacy.capabilities import (
    EvidenceAccess,
    Guard,
    PayloadContext,
    ProcessingAccess,
    ProtectedPayload,
    RetryContext,
)
from prme.privacy.policy import FrozenModel, PolicySnapshot, PrivacyDenied
from prme.storage._threading import run_to_completion

Purpose = Literal["evidence", "processing", "pseudonym", "retry_commitment"]
WrapperKind = Literal["local-rsa-oaep-sha256-v1", "aws-kms-symmetric-v1"]
ReadAction = Literal["read", "processing_key", "rewrap"]
T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PrivacyDenied("privacy_configuration_invalid")
    return value.astimezone(timezone.utc)


class KeyBoundary(FrozenModel):
    project_id: UUID
    owner_id: UUID
    scope: Literal["personal", "project", "org"]

    @classmethod
    def from_context(cls, context: PayloadContext) -> KeyBoundary:
        return cls(project_id=context.project_id, owner_id=context.owner_id, scope=context.scope)

    @classmethod
    def from_snapshot(cls, snapshot: PolicySnapshot) -> KeyBoundary:
        policy = snapshot.policy
        return cls(project_id=policy.project_id, owner_id=policy.owner_id, scope=policy.scope.value)


class WrappingKeyRef(FrozenModel):
    kind: WrapperKind
    provider_id: UUID
    key_id: UUID
    key_version: UUID
    # RSA: SHA256 of public SPKI; KMS: immutable full key ARN, never an alias.
    locator: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def exact_locator(self) -> WrappingKeyRef:
        import re
        pattern = (r"[0-9a-f]{64}" if self.kind == "local-rsa-oaep-sha256-v1" else
                   r"arn:aws(?:-[a-z]+)*:kms:[a-z0-9-]+:[0-9]{12}:key/[a-zA-Z0-9-]{1,128}")
        if re.fullmatch(pattern, self.locator) is None:
            raise ValueError("privacy_configuration_invalid")
        return self


class WrappingKeyDescriptor(FrozenModel):
    ref: WrappingKeyRef
    boundary: KeyBoundary
    purpose: Literal["evidence", "processing"]
    public_key: str | None = Field(default=None, max_length=2048, repr=False)

    @model_validator(mode="after")
    def public_material(self) -> WrappingKeyDescriptor:
        if (self.public_key is not None) != (self.ref.kind == "local-rsa-oaep-sha256-v1"):
            raise ValueError("privacy_configuration_invalid")
        return self


class AuxiliaryKeyRef(FrozenModel):
    boundary: KeyBoundary
    purpose: Literal["pseudonym", "retry_commitment"]
    key_version: UUID
    epoch: UUID | None = None
    request_id: UUID | None = None
    retention_ref: UUID | None = None
    expires_at: datetime | None = None
    policy_binding: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def separation(self) -> AuxiliaryKeyRef:
        if self.purpose == "pseudonym":
            if self.epoch is None or any(x is not None for x in (
                self.request_id, self.retention_ref, self.expires_at, self.policy_binding
            )):
                raise ValueError("privacy_configuration_invalid")
        elif (self.epoch is not None or any(x is None for x in (
            self.request_id, self.retention_ref, self.expires_at, self.policy_binding
        ))):
            raise ValueError("privacy_configuration_invalid")
        if self.expires_at is not None:
            aware(self.expires_at)
        return self


class SecretKeyHandle:
    """Explicit callback access; never a serializable configuration value.

    Best-effort buffer clearing cannot recall copies made by a callback or
    promise Python heap/swap zeroization.
    """

    __slots__ = ("_reference", "_key", "_clock")

    def __init__(self, reference: AuxiliaryKeyRef, key: bytes, *,
                 clock: Callable[[], datetime] = utc_now) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise PrivacyDenied("privacy_capability_unavailable")
        self._reference = reference
        self._key = bytearray(key)
        self._clock = clock

    reference = property(lambda self: self._reference)
    purpose = property(lambda self: self._reference.purpose)
    project_id = property(lambda self: self._reference.boundary.project_id)
    owner_id = property(lambda self: self._reference.boundary.owner_id)
    scope = property(lambda self: self._reference.boundary.scope)
    epoch = property(lambda self: self._reference.epoch)
    key_version = property(lambda self: self._reference.key_version)

    def use(self, callback: Callable[[bytes], T]) -> T:
        if len(self._key) != 32 or (self._reference.expires_at is not None
                                  and aware(self._clock()) >= self._reference.expires_at):
            raise PrivacyDenied("privacy_retry_unavailable" if self.purpose == "retry_commitment"
                                else "privacy_capability_unavailable")
        return callback(bytes(self._key))

    def close(self) -> None:
        self._key[:] = b"\0" * len(self._key)
        self._key.clear()

    def __repr__(self) -> str:
        return "<SecretKeyHandle>"

    def __reduce_ex__(self, protocol: int) -> Any:
        raise TypeError("privacy_capability_not_serializable")


class PseudonymKeyProvider(Protocol):
    async def key_for(self, project_id: UUID, owner_id: UUID, scope: str,
                      epoch: UUID) -> SecretKeyHandle: ...


class RetryKeyProvider(Protocol):
    async def provision_retry_key(self, context: RetryContext, *, retention_ref: UUID,
                                  expires_at: datetime) -> SecretKeyHandle: ...
    async def get_retry_key(self, context: RetryContext, *, key_version: UUID,
                            retention_ref: UUID) -> SecretKeyHandle: ...


class AuditEvent(FrozenModel):
    schema_version: Literal[1] = 1
    operation_id: UUID
    action: ReadAction
    outcome: Literal["authorized", "finished", "failed"]
    boundary: KeyBoundary
    event_id: UUID
    slot_id: UUID
    object_generation: int
    purpose: Literal["evidence", "processing"]
    at: datetime


class AuditSink(Protocol):
    async def write(self, event: AuditEvent) -> None: ...


class FileAuditSink:
    """Explicit operator-owned content-free local audit; fsync before disclosure."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).absolute()

    async def write(self, event: AuditEvent) -> None:
        line = (event.model_dump_json() + "\n").encode()

        def append() -> None:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if os.fstat(fd).st_mode & 0o077:
                    raise PrivacyDenied("privacy_configuration_invalid")
                offset = 0
                while offset < len(line):
                    offset += os.write(fd, line[offset:])
                os.fsync(fd)
            finally:
                os.close(fd)

        try:
            await run_to_completion(append)
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_capability_unavailable") from None


class ProcessingAuthorizer(Protocol):
    """Validate actual live work/cohort/slot state while the supplied guard holds."""

    async def validate(self, payload: ProtectedPayload, access: ProcessingAccess,
                       original_snapshot: PolicySnapshot) -> None: ...


class UnwrapPermit:
    __slots__ = ("context", "access", "action", "_active")

    def __init__(self, context: PayloadContext, access: ProcessingAccess | EvidenceAccess,
                 action: ReadAction) -> None:
        self.context, self.access, self.action, self._active = context, access, action, True

    def require(self, descriptor: WrappingKeyDescriptor) -> None:
        if (not self._active or descriptor.boundary != KeyBoundary.from_context(self.context)
                or descriptor.purpose != self.context.purpose):
            raise PrivacyDenied("privacy_capability_unavailable")

    def close(self) -> None:
        self._active = False

    def __repr__(self) -> str:
        return "<UnwrapPermit>"

    def __reduce_ex__(self, protocol: int) -> Any:
        raise TypeError("privacy_capability_not_serializable")


class KeyWrapper(Protocol):
    async def ready(self, descriptor: WrappingKeyDescriptor) -> None: ...
    async def wrap_key(self, key: bytes, descriptor: WrappingKeyDescriptor,
                       wrapping_context: bytes) -> bytes: ...
    async def unwrap_key(self, wrapped: bytes, descriptor: WrappingKeyDescriptor,
                         wrapping_context: bytes, permit: UnwrapPermit) -> bytes: ...


class KeyRegistry:
    """Explicit exact-version registry; active selection is used only by writers."""

    def __init__(self, descriptors: tuple[WrappingKeyDescriptor, ...], *,
                 active: tuple[WrappingKeyRef, ...], wrappers: Mapping[UUID, KeyWrapper],
                 refresh: Callable[[], tuple[tuple[WrappingKeyDescriptor, ...], tuple[WrappingKeyRef, ...]]] | None = None,
                 publication_guard: Guard | None = None) -> None:
        self._refresh_source = refresh
        self.publication_guard = publication_guard
        self.publication_domain: str | None = None
        self._wrappers = dict(wrappers)
        self._load(descriptors, active)

    def _load(self, descriptors, active) -> None:
        self._descriptors = {item.ref: item for item in descriptors}
        if len(self._descriptors) != len(descriptors):
            raise PrivacyDenied("privacy_configuration_invalid")
        self._active: dict[tuple[KeyBoundary, str], WrappingKeyDescriptor] = {}
        for ref in active:
            if ref not in self._descriptors:
                raise PrivacyDenied("privacy_configuration_invalid")
            descriptor = self._descriptors[ref]
            key = (descriptor.boundary, descriptor.purpose)
            if key in self._active:
                raise PrivacyDenied("privacy_configuration_invalid")
            self._active[key] = descriptor

    def _refresh(self) -> None:
        if self._refresh_source is not None:
            self._load(*self._refresh_source())

    def exact(self, ref: WrappingKeyRef) -> WrappingKeyDescriptor:
        self._refresh()
        try:
            return self._descriptors[ref]
        except (KeyError, TypeError):
            raise PrivacyDenied("privacy_capability_unavailable") from None

    def writer(self, boundary: KeyBoundary, purpose: str) -> WrappingKeyDescriptor:
        self._refresh()
        try:
            return self._active[boundary, purpose]
        except KeyError:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    def wrapper(self, descriptor: WrappingKeyDescriptor) -> KeyWrapper:
        if self.exact(descriptor.ref) != descriptor:
            raise PrivacyDenied("privacy_boundary_mismatch")
        try:
            return self._wrappers[descriptor.ref.provider_id]
        except KeyError:
            raise PrivacyDenied("privacy_capability_unavailable") from None

    def inventory(self) -> tuple[WrappingKeyDescriptor, ...]:
        self._refresh()
        return tuple(self._descriptors.values())

    @asynccontextmanager
    async def writers_held(self) -> AsyncIterator[None]:
        if self.publication_guard is None:
            yield
        else:
            async with self.publication_guard():
                yield


@dataclass(frozen=True, repr=False)
class _Grant:
    access: EvidenceAccess
    selections: tuple[tuple[PayloadContext, PolicySnapshot], ...]
    actions: tuple[ReadAction, ...]


class LocalAccessAuthority:
    """Explicit same-process grants, with no implicit diagnostic unlock.

    The injected guard orders current policy/erasure with effects. The injected
    processing validator is mandatory for processing. A Python caller able to
    replace these capabilities is inside the trusted process boundary.
    """

    isolation = "same_process"

    def __init__(self, audit: AuditSink, *, processing: ProcessingAuthorizer | None = None,
                 clock: Callable[[], datetime] = utc_now, isolation: str = "same_process") -> None:
        if isolation != "same_process":
            raise PrivacyDenied("privacy_configuration_invalid")
        self.audit, self.processing, self.clock = audit, processing, clock
        self._grants: dict[UUID, _Grant] = {}
        self._lock = asyncio.Lock()

    async def grant(self, kind: Literal["runtime", "diagnostic"],
                    selections: tuple[tuple[PayloadContext, PolicySnapshot], ...], *,
                    expires_at: datetime, actions: tuple[ReadAction, ...] = ("read",)) -> EvidenceAccess:
        now, expires = aware(self.clock()), aware(expires_at)
        if (not 1 <= len(selections) <= 128 or not 0 < (expires - now).total_seconds() <= 900
                or not actions or any(a not in {"read", "rewrap"} for a in actions)
                or (kind == "runtime" and actions != ("read",))):
            raise PrivacyDenied("privacy_configuration_invalid")
        boundaries = {KeyBoundary.from_context(c) for c, _ in selections}
        if len(boundaries) != 1 or any(c.purpose != "evidence" for c, _ in selections):
            raise PrivacyDenied("privacy_boundary_mismatch")
        ids = tuple(dict.fromkeys(c.event_id for c, _ in selections))
        access = EvidenceAccess(kind=kind, selected_ids=ids, expires_at=expires, grant_id=uuid4())
        async with self._lock:
            self._grants[access.grant_id] = _Grant(access, selections, actions)
        return access

    async def revoke(self, grant_id: UUID) -> None:
        async with self._lock:
            self._grants.pop(grant_id, None)

    async def _validate(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess,
                        snapshot: PolicySnapshot, action: ReadAction) -> None:
        context, now = payload.context, aware(self.clock())
        if KeyBoundary.from_context(context) != KeyBoundary.from_snapshot(snapshot):
            raise PrivacyDenied("privacy_boundary_mismatch")
        if snapshot.memory_deadline is not None and now >= snapshot.memory_deadline:
            raise PrivacyDenied("privacy_expired")
        if isinstance(access, ProcessingAccess):
            if (context.purpose != "processing" or action not in {"read", "processing_key", "rewrap"}
                    or not snapshot.policy.durable_processing_input or self.processing is None
                    or context.event_id not in access.claimed_event_ids
                    or not 1 <= len(access.claimed_event_ids) <= 128
                    or len(set(access.claimed_event_ids)) != len(access.claimed_event_ids)
                    or access.processing_deadline != snapshot.processing_deadline
                    or now >= min(aware(access.lease_deadline), aware(access.processing_deadline))):
                raise PrivacyDenied("privacy_processing_unavailable")
            await self.processing.validate(payload, access, snapshot)
        else:
            grant = self._grants.get(access.grant_id)
            if (context.purpose != "evidence" or snapshot.policy.capture == "none"
                    or action == "processing_key" or grant is None or grant.access != access
                    or (context, snapshot) not in grant.selections or action not in grant.actions
                    or now >= aware(access.expires_at)
                    or (snapshot.evidence_deadline is not None and now >= snapshot.evidence_deadline)
                    or (access.kind == "runtime" and snapshot.policy.evidence_use != "runtime")):
                raise PrivacyDenied("privacy_capability_unavailable")

    @asynccontextmanager
    async def authorize(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess,
                        snapshot: PolicySnapshot, *, guard: Guard, action: ReadAction) -> AsyncIterator[UnwrapPermit]:
        async with self._lock, guard():
            await self._validate(payload, access, snapshot, action)
            context = payload.context
            base = dict(operation_id=uuid4(), action=action, boundary=KeyBoundary.from_context(context),
                        event_id=context.event_id, slot_id=context.slot_id,
                        object_generation=context.object_generation, purpose=context.purpose)
            await self.audit.write(AuditEvent(**base, outcome="authorized", at=aware(self.clock())))
            permit = UnwrapPermit(context, access, action)
            outcome = "failed"
            try:
                yield permit
                await self._validate(payload, access, snapshot, action)
                outcome = "finished"
            finally:
                permit.close()
                await self.audit.write(AuditEvent(**base, outcome=outcome, at=aware(self.clock())))
