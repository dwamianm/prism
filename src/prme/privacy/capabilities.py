"""Explicit downstream capabilities. No permissive production implementation.

An authority guard must serialize revision/erasure activation with its effect,
including across processes when deployed that way. Providers must acquire the
supplied guard for *each* transmission, including SDK retries. Merely checking a
boolean before awaiting a provider does not satisfy this protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncContextManager, Callable, Literal, Protocol
from uuid import UUID

from pydantic import Field

from prme.privacy.policy import FrozenModel, PolicySnapshot

if TYPE_CHECKING:
    from prme.privacy.crypto import ProcessingKeyLease
    from prme.privacy.erasure import ErasureLedger

Effect = Literal["admission", "transmission", "publication", "disclosure"]
Guard = Callable[[], AsyncContextManager[None]]


class PolicyAuthority(Protocol):
    def guard(self, snapshot: PolicySnapshot, effect: Effect) -> AsyncContextManager[None]: ...


@dataclass(repr=False)
class OperationBudget:
    """Private shared accounting seam for the bounded sanitizer, never persisted."""
    operation_id: UUID
    state: Any = None


@dataclass(frozen=True, repr=False)
class BudgetContinuation:
    """Non-content accounting carried only inside authenticated processing input.

    A live-claim owner must prevent replay/concurrent reuse. This value is not a
    public budget grant and is never reconstructed from caller metadata.
    """
    operation_id: UUID
    input_bytes: int
    detector_nanoseconds: int
    leaves: int


@dataclass(frozen=True, repr=False)
class SourceOnlyRegion:
    """Trusted engine classification of a sanitized source passage."""
    path: tuple[str | int, ...]
    start: int
    end: int


@dataclass(frozen=True, repr=False)
class AdmissionContent:
    # Canonical finite JSON contains all named representations and metadata.
    # An immutable string prevents nested caller mutation while awaiting a lock.
    fields_json: str
    snapshot: PolicySnapshot
    mode: Literal["ingress", "query", "generated_output"]
    budget: OperationBudget
    issued_tokens: tuple[str, ...] = ()
    pseudonym_epoch: UUID | None = None
    source: SanitizedContent | None = None
    source_role: str = "user"
    source_time: str | None = None


@dataclass(frozen=True, repr=False)
class SanitizedContent:
    fields_json: str
    source_id: UUID
    representation_id: UUID
    private_checksum: str
    sanitizer_version: str
    registry_version: str
    detector_versions: tuple[str, ...] = ()
    action_counts: tuple[tuple[str, int], ...] = ()
    issued_tokens: tuple[str, ...] = ()
    pseudonym_epoch: UUID | None = None
    complete: bool = True
    unambiguous: bool = True
    representation_role: Literal["sanitized_source", "admitted_memory"] = "sanitized_source"
    source_snapshot: PolicySnapshot | None = None
    continuation: BudgetContinuation | None = None
    source_only: tuple[SourceOnlyRegion, ...] = ()
    source_role: str = "user"
    source_time: str | None = None


class Sanitizer(Protocol):
    async def admit(self, content: AdmissionContent) -> SanitizedContent: ...


class PayloadContext(FrozenModel):
    schema_version: Literal[1] = 1
    project_id: UUID
    owner_id: UUID
    scope: str
    event_id: UUID
    source_id: UUID
    representation_id: UUID
    slot_id: UUID
    purpose: Literal["processing", "evidence"]
    payload_kind: Literal["sanitized_input", "sanitized_evidence"]
    object_generation: int = Field(default=1, gt=0, strict=True)
    policy_id: UUID
    policy_revision: int = Field(gt=0, strict=True)


class ProtectedPayload(FrozenModel):
    """Opaque serialized envelope; duplicated selectors are verified by protector."""
    context: PayloadContext
    envelope: str = Field(repr=False)
    provider_id: UUID
    key_id: UUID
    key_version: UUID
    envelope_revision: int = Field(default=1, gt=0, strict=True)


class ProcessingAccess(FrozenModel):
    work_id: UUID
    work_generation: int = Field(gt=0, strict=True)
    lease_deadline: datetime
    processing_deadline: datetime
    claimed_event_ids: tuple[UUID, ...]


class EvidenceAccess(FrozenModel):
    kind: Literal["runtime", "diagnostic"]
    selected_ids: tuple[UUID, ...]
    expires_at: datetime
    grant_id: UUID


class PayloadProtector(Protocol):
    def writer_guard(self, payloads: tuple[ProtectedPayload, ...]) -> AsyncContextManager[None]: ...
    async def ready(self, snapshot: PolicySnapshot, purposes: tuple[str, ...]) -> None: ...
    async def seal(self, plaintext: bytes, context: PayloadContext, *,
                   original_snapshot: PolicySnapshot) -> ProtectedPayload: ...
    async def open(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess,
                   *, original_snapshot: PolicySnapshot, guard: Guard) -> bytes: ...
    def open_held(self, payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess, *,
                  original_snapshot: PolicySnapshot, guard: Guard) -> AsyncContextManager[bytes]: ...
    def hold_evidence_access(self, selections: tuple[tuple[ProtectedPayload, PolicySnapshot], ...],
                             access: EvidenceAccess, *, guard: Guard) -> AsyncContextManager[None]: ...
    def unwrap_processing_key_held(self, payload: ProtectedPayload, access: ProcessingAccess, *,
                                  original_snapshot: PolicySnapshot,
                                  guard: Guard) -> AsyncContextManager[ProcessingKeyLease]: ...


class PrivacyExtractor(Protocol):
    provider_name: str
    async def extract(self, content: SanitizedContent, snapshot: PolicySnapshot, *, guard: Guard) -> Any: ...


class PrivacyEmbedder(Protocol):
    model_name: str
    model_version: str
    dimension: int
    async def encode(self, texts: tuple[str, ...], snapshot: PolicySnapshot, *, guard: Guard) -> list[list[float]]: ...


class RetryContext(FrozenModel):
    domain: Literal["prme-admission-retry-v1"] = "prme-admission-retry-v1"
    request_id: UUID
    snapshot: PolicySnapshot


class RetryCommitment(FrozenModel):
    context: RetryContext
    key_version: UUID
    retention_ref: UUID
    value: str = Field(repr=False)


class RetryCommitter(Protocol):
    """Distinct request-scoped random-key purpose, never an unkeyed hash.

    Missing/retired keys deny; verification must not provision replacement keys.
    Actual key custody and full comparison/purge lifecycle are PV-03/PV-06.
    """
    async def commit(self, request: bytes, context: RetryContext) -> RetryCommitment: ...
    async def verify(self, request: bytes, commitment: RetryCommitment) -> bool: ...


@dataclass(frozen=True)
class PrivacyRuntime:
    project_id: UUID
    authority: PolicyAuthority
    sanitizer: Sanitizer
    protector: PayloadProtector
    embedder: PrivacyEmbedder
    extractor: PrivacyExtractor | None = None
    retry_committer: RetryCommitter | None = None
    erasure_ledger: ErasureLedger | None = None
    key_custody: object | None = None
    allow_unlimited_evidence: bool = False
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc), repr=False)
