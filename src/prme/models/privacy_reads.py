"""Closed, transient views of protected sources and their ordinary history.

These are response envelopes, not replacements for immutable journal codecs.
They deliberately have no source body, source checksum or extensible payload.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.privacy.policy import FrozenModel
from prme.types import Scope

SourceState = Literal[
    "available", "sealed", "not_captured", "expired", "erased",
    "key_unavailable", "missing", "legacy_unclassified",
]

SOURCE_LIMITATION = (
    "Only admitted memory is included. Source evidence is unavailable to this "
    "ordinary read. Source quotations and source-only details cannot be recovered "
    "from this context."
)


class SourceAvailability(FrozenModel):
    schema_version: Literal[1] = 1
    source_id: UUID
    representation_id: UUID
    policy_id: UUID
    policy_revision: int = Field(gt=0, strict=True)
    state: SourceState


class SourceUnavailable(FrozenModel):
    """Identical response for absent and foreign selected identities."""

    schema_version: Literal[1] = 1
    status: Literal["unavailable"] = "unavailable"


class LegacySourceAvailability(FrozenModel):
    schema_version: Literal[1] = 1
    state: Literal["legacy_unclassified"] = "legacy_unclassified"


class ProtectedEventView(FrozenModel):
    schema_version: Literal[1] = 1
    kind: Literal["protected_event"] = "protected_event"
    id: UUID
    user_id: str
    scope: Scope
    timestamp: AwareDatetime
    event_time: AwareDatetime | None = None
    source_availability: SourceAvailability | LegacySourceAvailability

    @property
    def content(self) -> str:
        """Compatibility with readers that already treated protected bodies as empty."""
        return ""


class ProtectedExtractionView(FrozenModel):
    schema_version: Literal[1] = 1
    kind: Literal["protected_extraction"] = "protected_extraction"
    event_id: UUID
    source_availability: SourceAvailability
    status: Literal["unavailable"] = "unavailable"


class ProtectedOperationView(FrozenModel):
    id: UUID
    op_type: Literal["unavailable"] = "unavailable"
    target_id: UUID | None = None
    created_at: AwareDatetime
    payload_status: Literal["unavailable"] = "unavailable"


class ProtectedProvenanceView(FrozenModel):
    schema_version: Literal[1] = 1
    kind: Literal["protected_provenance"] = "protected_provenance"
    node: MemoryNode
    evidence_events: tuple[ProtectedEventView, ...] = ()
    missing_evidence_refs: tuple[UUID, ...] = ()
    operations: tuple[ProtectedOperationView, ...] = ()
    contradiction_edges: tuple[MemoryEdge, ...] = ()
    next_operation_cursor: str | None = None


class ProtectedReceiptView(FrozenModel):
    """Content-free inspection; PV-05 owns the persisted receipt family."""

    schema_version: Literal[1] = 1
    kind: Literal["protected_receipt"] = "protected_receipt"
    receipt_id: UUID
    user_id: str
    created_at: AwareDatetime
    source_availability: tuple[SourceAvailability, ...] = ()
    detail_status: Literal["unavailable"] = "unavailable"
    ranking_replay: Literal["unavailable"] = "unavailable"


class SanitizedEvidenceView(FrozenModel):
    """Explicit capability result; never consumed by ordinary graph ingestion."""

    schema_version: Literal[1] = 1
    representation: Literal["sanitized"] = "sanitized"
    event_id: UUID
    source_id: UUID
    representation_id: UUID
    content: str = Field(repr=False)
    speaker: str | None = None
    role: Literal["user", "human", "participant", "assistant", "tool", "system"]
    event_time: AwareDatetime | None = None
    sanitizer_version: str
    registry_version: str
