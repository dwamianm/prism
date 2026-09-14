"""Observable state for restart-safe source and index processing."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from prme.models.nodes import MemoryNode


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
