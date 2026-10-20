"""Bounded, resumable wrapper-only rotation over the existing admission rows."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from prme.privacy.capabilities import EvidenceAccess, Guard, ProcessingAccess
from prme.privacy.crypto import CryptoProtector, decode_envelope
from prme.privacy.keys import KeyBoundary, WrappingKeyRef
from prme.privacy.local_keys import NativeKeyCustody
from prme.privacy.policy import FrozenModel, PrivacyDenied
from prme.privacy.records import PrivacyRecord
from prme.storage._threading import run_async_to_completion
from prme.storage.privacy import PrivacyRepository


class RotationJob(FrozenModel):
    schema_version: Literal[1] = 1
    job_id: UUID
    boundary: KeyBoundary
    purpose: Literal["evidence", "processing"]
    source: WrappingKeyRef
    target: WrappingKeyRef
    cursor: UUID | None = None
    revision: int = Field(default=1, gt=0, strict=True)
    changed: int = Field(default=0, ge=0, strict=True)
    blocked: int = Field(default=0, ge=0, strict=True)
    state: Literal["running", "blocked", "complete"] = "running"

    @model_validator(mode="after")
    def versions(self):
        if (self.source == self.target or self.source.kind != "local-rsa-oaep-sha256-v1"
                or self.target.kind != self.source.kind or self.target.provider_id != self.source.provider_id):
            raise ValueError("privacy_configuration_invalid")
        return self


AccessFor = Callable[[PrivacyRecord], Awaitable[EvidenceAccess | ProcessingAccess]]
GuardFor = Callable[[PrivacyRecord], Guard]


class RotationManager:
    """Local cutover requires the same native registry lock as every writer.

    Policy/erasure authority is still required independently. Key custody does
    not implement policy revisions or certify external writers. Remote/static
    registries cannot claim this local completion guarantee.
    """

    def __init__(self, repository: PrivacyRepository, protector: CryptoProtector,
                 custody: NativeKeyCustody) -> None:
        if (protector.registry.publication_guard is None
                or protector.registry.publication_domain != str(custody.path)):
            raise PrivacyDenied("privacy_configuration_invalid")
        self.repository, self.protector, self.custody = repository, protector, custody

    def _validate(self, job: RotationJob) -> None:
        for ref in (job.source, job.target):
            descriptor = self.protector.registry.exact(ref)
            if (descriptor.boundary, descriptor.purpose) != (job.boundary, job.purpose):
                raise PrivacyDenied("privacy_boundary_mismatch")

    async def start(self, source: WrappingKeyRef, target: WrappingKeyRef, *, guard: Guard) -> RotationJob:
        original = self.protector.registry.exact(source)
        job = RotationJob(job_id=uuid4(), boundary=original.boundary, purpose=original.purpose,
                          source=source, target=target)
        self._validate(job)
        async with guard():
            if not await run_async_to_completion(self.repository.save_rotation(job)):
                raise PrivacyDenied("privacy_capability_unavailable")
        # A crash here leaves a durable, resumable job with exact source/target.
        await self.custody.activate(target, expected=source, guard=guard)
        return job

    async def step(self, job_id: UUID, *, activation_guard: Guard, access_for: AccessFor,
                   guard_for: GuardFor, limit: int = 128) -> RotationJob:
        job = await self.repository.rotation_job(job_id)
        if job is None:
            raise PrivacyDenied("privacy_capability_unavailable")
        self._validate(job)
        await self.custody.activate(job.target, expected=job.source, guard=activation_guard)
        if job.state == "complete":
            return job
        if job.state == "blocked":
            updated = job.model_copy(update={"cursor": None, "blocked": 0, "state": "running",
                                             "revision": job.revision + 1})
            await self._checkpoint(job, updated)
            job = updated
        rows = await self.repository.page(user_id=str(job.boundary.owner_id), scope=job.boundary.scope,
                                          after=job.cursor, limit=limit)
        for record in rows:
            changed, blocked = 0, 0
            payload = getattr(record, job.purpose)
            if KeyBoundary.from_snapshot(record.snapshot) == job.boundary and payload is not None:
                try:
                    parsed = decode_envelope(payload, record.snapshot)
                    if parsed.wrapper.ref == job.source:
                        access = await access_for(record)
                        async with self.protector.rewrap_held(
                            payload, access, job.target, original_snapshot=record.snapshot,
                            guard=guard_for(record),
                        ) as replacement:
                            after = record.model_copy(update={job.purpose: replacement})
                            # The actual CAS settles before releasing private/current authority.
                            replaced = await run_async_to_completion(self.repository.replace_envelope(record, after))
                            changed, blocked = int(replaced), int(not replaced)
                except PrivacyDenied:
                    blocked = 1
            updated = job.model_copy(update={"cursor": record.event_id, "changed": job.changed + changed,
                                             "blocked": job.blocked + blocked, "revision": job.revision + 1})
            await self._checkpoint(job, updated)
            job = updated
        if len(rows) < limit:
            # Old prepared writes are fenced at admission. Hold the same native
            # lock during completion so activation cannot race this declaration.
            async with activation_guard(), self.protector.registry.writers_held():
                if self.protector.registry.writer(job.boundary, job.purpose).ref != job.target:
                    raise PrivacyDenied("privacy_capability_unavailable")
                updated = job.model_copy(update={"state": "blocked" if job.blocked else "complete",
                                                 "revision": job.revision + 1})
                await self._checkpoint(job, updated)
                job = updated
        return job

    async def _checkpoint(self, before: RotationJob, after: RotationJob) -> None:
        if not await run_async_to_completion(self.repository.save_rotation(after, before=before)):
            # Another resumable worker made progress; never overwrite its cursor.
            raise PrivacyDenied("privacy_capability_unavailable")
