"""Durable extraction state, separate from raw-source indexing."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ExtractionClaim(BaseModel):
    """Internal fencing token; possession does not replace database validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: UUID
    user_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    attempts: int = Field(ge=1)
    lease_expires_at: AwareDatetime


class ExtractionStatus(BaseModel):
    """Owned extraction work and its last durable progress boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: UUID
    status: Literal["pending", "running", "complete", "failed"]
    phase: Literal["extraction", "preparation", "publication", "complete"]
    attempts: int = Field(ge=0)
    generation: int = Field(ge=0)
    plan_id: UUID | None = None
    lease_expires_at: AwareDatetime | None = None
    next_attempt_at: AwareDatetime
    last_error: str | None = None
    updated_at: AwareDatetime


class ExtractionProcessingResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    processed: int = Field(ge=0)
    pending: int = Field(ge=0)
    failed: int = Field(ge=0)


class StaleExtractionClaimError(RuntimeError):
    """The work lease expired or another generation owns publication."""
