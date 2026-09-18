"""Public, dependency-light models for temporal relation retrieval metadata."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


Operation = Literal[
    "elapsed_between",
    "elapsed_since_question",
    "order",
    "absolute_date",
    "event_at_query_date",
    "duration_from_text",
    "schedule",
    "unsupported",
]
TemporalRelationStatus = Literal[
    "empty_context",
    "unsupported",
    "validation_rejected",
    "gate_rejected",
    "packing_rejected",
    "provider_error",
    "accepted",
]


class ResolverAudit(BaseModel):
    """Non-secret audit fields returned by a resolver provider."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    provider: str
    model: str
    model_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provider_version: str | None = None
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempts: int = Field(ge=1)
    schema_repairs: int = Field(ge=0, le=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    elapsed_ms: float = Field(ge=0)


class GateAudit(BaseModel):
    """Auditable relation-level result from the independent semantic gate."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    provider: str
    model: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    probabilities: tuple[float, ...]
    minimum_probability: float = Field(ge=0, le=1)
    attempts: int = Field(ge=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    elapsed_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_probabilities(self) -> GateAudit:
        if not self.probabilities or any(
            value < 0 or value > 1 for value in self.probabilities
        ):
            raise ValueError("gate probabilities must be a nonempty [0, 1] tuple")
        if self.minimum_probability != min(self.probabilities):
            raise ValueError("minimum_probability must match the operand minimum")
        return self


class TemporalRelationMetadata(BaseModel):
    """Public outcome and complete non-secret provenance for one enrichment."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    protocol: Literal["temporal_relation_v1"] = "temporal_relation_v1"
    status: TemporalRelationStatus
    confirmation_protocol_aligned: bool
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Operation | None = None
    value: str | None = None
    evidence_ids: tuple[UUID, ...] = ()
    validation_errors: tuple[str, ...] = ()
    resolver: ResolverAudit | None = None
    gate: GateAudit | None = None
    gate_threshold: float = Field(ge=0, le=1)
    control_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    guidance_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    dropped_record_ids: tuple[UUID, ...] = ()
    error_stage: Literal["resolver", "gate"] | None = None
    error_type: str | None = None
    elapsed_ms: float = Field(ge=0)
