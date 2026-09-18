"""Public, tenant-scoped provenance views for memory nodes."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from prme.models.edges import MemoryEdge
from prme.models.events import Event
from prme.models.nodes import MemoryNode


class OperationAuditRecord(BaseModel):
    """One immutable operation-log entry affecting a node."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    op_type: str
    target_id: str | None = None
    payload: Any
    actor_id: str | None = None
    namespace_id: str | None = None
    created_at: AwareDatetime


class NodeProvenance(BaseModel):
    """Auditable evidence and transition history for one owned memory node."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    node: MemoryNode
    evidence_events: tuple[Event, ...] = ()
    missing_evidence_refs: tuple[UUID, ...] = ()
    operations: tuple[OperationAuditRecord, ...] = ()
    contradiction_edges: tuple[MemoryEdge, ...] = ()
    next_operation_cursor: str | None = None
