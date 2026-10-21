"""Closed, non-content erasure requests and managed-boundary outcomes.

Erasure is independent of lifecycle archival. None of these outcomes certifies
physical media wiping, consumer recall, or freshness after rolling back both the
pack and its independent local ledger.
"""
from __future__ import annotations

from datetime import datetime
from typing import AsyncContextManager, Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator

from prme.privacy.policy import FrozenModel, PolicyReference
from prme.types import Scope

ErasureMode = Literal["evidence", "processing", "memory"]


class ErasureRequest(FrozenModel):
    schema_version: Literal[1] = 1
    request_id: UUID
    scope: Scope = Scope.PERSONAL
    mode: ErasureMode = "memory"
    event_ids: tuple[UUID, ...] = ()
    node_ids: tuple[UUID, ...] = ()
    owner_all: bool = False

    @model_validator(mode="after")
    def selection(self):
        if (self.owner_all == bool(self.event_ids or self.node_ids)
                or len(self.event_ids) + len(self.node_ids) > 128
                or len(set(self.event_ids)) != len(self.event_ids)
                or len(set(self.node_ids)) != len(self.node_ids)
                or (self.node_ids and self.mode != "memory")):
            raise ValueError("privacy_configuration_invalid")
        return self


class ErasureIntent(FrozenModel):
    schema_version: Literal[1] = 1
    request: ErasureRequest
    store_id: UUID
    project_id: UUID
    owner_id: UUID
    created_at: datetime
    reason: Literal["requested", "expired"] = "requested"
    # Internal provenance closure, separate from the caller's retry identity.
    # None identifies older node requests whose coverage is not established.
    source_event_ids: tuple[UUID, ...] | None = None

    @model_validator(mode="after")
    def clock(self):
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("privacy_configuration_invalid")
        return self


class LedgerHead(FrozenModel):
    ledger_id: UUID
    store_id: UUID
    sequence: int = Field(ge=0, strict=True)
    token: UUID


class LedgerEntry(FrozenModel):
    head: LedgerHead
    previous_token: UUID
    intent: ErasureIntent


class ErasureImpactCounts(FrozenModel):
    """Stored objects in this projection, not physical bytes or backup copies."""
    sources: int = Field(ge=0, strict=True)
    memories: int = Field(ge=0, strict=True)
    derivatives: int = Field(ge=0, strict=True)
    independent_survivors: int = Field(ge=0, strict=True)


class ErasureImpact(FrozenModel):
    """Read-only scoped projection; confirmation is not an erasure permit.

    The revision binds opaque identities/generations and current authority,
    never source text/checksums. The eventual request UUID is excluded. Callers
    own actor binding and reservation expiry; submission checks current impact.
    """
    schema_version: Literal[1] = 1
    project_id: UUID
    owner_id: UUID
    scope: Scope
    mode: ErasureMode
    policy_id: UUID
    policy_revision: int = Field(gt=0, strict=True)
    policy_references: tuple[PolicyReference, ...]
    ledger_head: LedgerHead
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    counts: ErasureImpactCounts
    facts_may_remain: bool
    backup_copies: Literal["not_managed"] = "not_managed"
    external_copies: Literal["not_managed"] = "not_managed"
    physical_wipe: Literal[False] = False


class ErasureLedger(Protocol):
    """Independent authority. Its guard serializes intent and every live effect.

    Registration is explicit and permitted only for a new unbound store. A
    provider must not silently register an unknown restored identity.
    """
    ledger_id: UUID
    def guard(self) -> AsyncContextManager[None]: ...
    async def register_store(self, store_id: UUID, project_id: UUID) -> LedgerHead: ...
    async def current_head(self, store_id: UUID) -> LedgerHead: ...
    async def entries_since(self, checkpoint: LedgerHead) -> tuple[LedgerEntry, ...]: ...
    async def append_intent(self, intent: ErasureIntent) -> LedgerEntry: ...


class ErasureStatus(FrozenModel):
    schema_version: Literal[1] = 1
    request_id: UUID
    mode: ErasureMode
    state: Literal["requested", "blocked_from_access", "live_purged", "backup_expiry_pending", "complete_within_managed_boundary"]
    stage: Literal["fence", "discover", "sql", "indexes", "keys", "verify"] = "fence"
    failure: Literal["storage_unavailable", "index_unavailable", "provider_unavailable", "coverage_unavailable"] | None = None
    attempts: int = Field(default=0, ge=0)
    facts_may_remain: bool
    logical_live_copies: Literal["pending", "purged"] = "pending"
    backup_copies: Literal["not_managed", "pending", "expired"] = "not_managed"
    external_copies: Literal["not_managed"] = "not_managed"
    physical_wipe: Literal[False] = False


class ErasedOperation(FrozenModel):
    """Replacement record, never a redaction masquerading as an old checksum."""
    schema_version: Literal[1] = 1
    kind: Literal["erased_operation"] = "erased_operation"
    operation_id: UUID
    request_id: UUID
    original_type: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]*$")
    owner_id: UUID
    scope: Scope
    state: Literal["erased"] = "erased"
    replay: Literal["unavailable"] = "unavailable"
    checksum: Literal["unavailable"] = "unavailable"
