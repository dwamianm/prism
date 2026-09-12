"""Observable state for restart-safe raw-event processing."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProcessingStatus(BaseModel):
    """Durable raw-source indexing status from fast or LLM ingestion."""

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
