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

from prme.privacy.policy import FrozenModel
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
