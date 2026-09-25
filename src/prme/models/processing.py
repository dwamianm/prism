"""Observable state for restart-safe source and index processing."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from prme.models.nodes import MemoryNode
from prme.models.speaker import MAX_SPEAKER_LENGTH, normalize_speaker
from prme.types import Scope


class FastIngestItem(BaseModel):
    """One raw source accepted by ``ingest_fast_many()``.

    The owner is supplied once on the batch call. Every other field can vary by
    item while retaining the same semantics as ``ingest_fast()``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    role: str = "user"
    speaker: str | None = Field(
        default=None,
        description=(
            f"Optional name of who said this (at most {MAX_SPEAKER_LENGTH} characters), "
            "kept with the source and shown by the reader context format"
        ),
    )
    session_id: str | None = None
    metadata: dict[str, Any] | None = None
    scope: Scope = Scope.PERSONAL
    event_time: AwareDatetime | None = Field(default=None)

    @field_validator("speaker")
    @classmethod
    def validate_speaker(cls, value: str | None) -> str | None:
        return normalize_speaker(value)


class ProcessingStatus(BaseModel):
    """Durable materialization status from direct storage or ingestion."""

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    status: Literal["pending", "complete"]
    attempts: int
    last_error: str | None = None
    updated_at: datetime


class ProcessingResult(BaseModel):
    """One scoped processing pass; failed items remain in pending."""

    model_config = ConfigDict(frozen=True)

    processed: int
    pending: int
    failed: int


class StoreReceipt(BaseModel):
    """Structured result for a completed direct ``store()`` call.

    ``store()`` keeps returning its historical event-ID string. Callers that
    need to operate on the created node can use ``store_with_receipt()`` and
    avoid a separate event-to-node lookup.
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID
    node: MemoryNode
    processing_status: ProcessingStatus

    @property
    def node_id(self) -> UUID:
        """ID accepted by node lifecycle methods."""
        return self.node.id
