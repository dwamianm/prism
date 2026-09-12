"""Pydantic request/response models for the PRME HTTP API.

All request and response bodies are defined here as Pydantic models.
These are thin DTOs — no business logic belongs here.
"""

from __future__ import annotations

from prme.models.relevance import RelevanceSubmission
from prme.models.learning import RankingMultipliers

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from prme.types import (
    EpistemicType,
    NodeType,
    RetrievalMode,
    RepresentationLevel,
    Scope,
    SourceType,
)
from prme.models.processing import ProcessingStatus


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StoreRequest(BaseModel):
    """Request body for POST /v1/store."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="Text content to store")
    user_id: str | None = Field(default=None, description="Owner; defaults to authenticated user")
    session_id: str | None = None
    role: str = Field(default="user", description="Event role")
    node_type: NodeType | None = Field(
        default=None, description="Node type (defaults to note)"
    )
    scope: Scope | None = Field(
        default=None, description="Memory scope (defaults to personal)"
    )
    epistemic_type: EpistemicType | None = Field(
        default=None, description="Epistemic classification"
    )
    source_type: SourceType | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    event_time: AwareDatetime | None = None
    ttl_days: int | None = Field(default=None, ge=0, strict=True,
                                description="Omit for configured TTL; null disables TTL; integer overrides it")
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional structured metadata"
    )


class StoreResponse(BaseModel):
    """Response body for POST /v1/store."""

    event_id: str = Field(description="ID of the persisted event")
    node_id: str | None = Field(
        default=None,
        description="ID of the created node (when available)",
    )
    processing_status: ProcessingStatus | None = Field(default=None,
        description="Direct node/index work; pending work can be retried without resubmitting the source")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    """Request body for POST /v1/ingest."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="Message text to ingest")
    user_id: str | None = Field(default=None, description="Owner; defaults to authenticated user")
    role: str = Field(default="user", description="Message role")
    session_id: str | None = None
    metadata: dict[str, Any] | None = None
    event_time: AwareDatetime | None = Field(default=None,
        description="Source time with timezone; anchors relative dates during extraction")
    wait_for_extraction: bool = Field(default=False, strict=True,
        description="Wait for extraction; failures retain the source and return its recovery receipt")
    scope: Scope | None = Field(
        default=None, description="Memory scope"
    )


class IngestResponse(BaseModel):
    """Response body for POST /v1/ingest."""

    event_id: str = Field(description="ID of the persisted event")


class ExtractionProcessRequest(BaseModel):
    """Explicit scoped extraction processing; may call the configured model."""

    user_id: str | None = None
    limit: int = Field(default=100, ge=0, le=1000)
    budget_ms: float = Field(default=5000, ge=0, allow_inf_nan=False)


class MaterializationProcessRequest(BaseModel):
    """Process saved source/index work without invoking extraction."""

    model_config = ConfigDict(extra="forbid")
    user_id: str | None = None
    budget_ms: int = Field(default=1000, ge=0, strict=True)


class AcceptedWorkErrorResponse(BaseModel):
    """A source was saved, but its requested processing did not complete."""

    detail: str
    event_id: str
    reason_code: str
    accepted: Literal[True] = True


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


class RetrievalFilters(BaseModel):
    """Typed filters; unknown keys fail instead of silently broadening a search."""

    model_config = ConfigDict(extra="forbid")
    scope: Scope | list[Scope] | None = None
    time_from: AwareDatetime | None = None
    time_to: AwareDatetime | None = None
    knowledge_at: AwareDatetime | None = None
    event_time_from: AwareDatetime | None = None
    event_time_to: AwareDatetime | None = None
    include_cross_scope: bool = True


class RetrieveRequest(BaseModel):
    """Request body for POST /v1/retrieve."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="Natural language query")
    user_id: str | None = Field(default=None, description="Owner; defaults to authenticated user")
    reference_time: AwareDatetime | None = Field(default=None, description="Clock for query dates and scoring")
    token_budget: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=0, strict=True, description="Max primary results before packing")
    min_score: float | None = Field(default=None, ge=0, allow_inf_nan=False, description="Inclusive ranking score floor, not a probability")
    mode: RetrievalMode | None = Field(default=None, description="Epistemic filtering mode within generated candidates")
    filters: RetrievalFilters | None = None
    ranking_multipliers: RankingMultipliers | None = Field(default=None,
        description="Explicit per-request ranking trial; does not activate learned weights")
    min_fidelity: RepresentationLevel | None = Field(default=None,
        description="Minimum context representation level")


class RetrieveResultItem(BaseModel):
    """Single result item in a retrieval response."""

    node_id: str
    content: str
    score: float
    node_type: str
    lifecycle_state: str
    confidence: float
    salience: float
    epistemic_type: str | None = None
    source_type: str | None = None
    scope: str | None = None
    session_id: str | None = None
    event_time: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None


class RetrieveResponse(BaseModel):
    """Response body for POST /v1/retrieve."""

    results: list[RetrieveResultItem] = Field(default_factory=list)
    bundle: dict[str, Any] | None = Field(
        default=None, description="Memory bundle"
    )
    metrics: dict[str, Any] | None = Field(
        default=None, description="Retrieval metrics"
    )


# ---------------------------------------------------------------------------
# Organize
# ---------------------------------------------------------------------------


class OrganizeRequest(BaseModel):
    """Request body for POST /v1/organize."""

    user_id: str | None = Field(default=None, description="Optional user scope")
    jobs: list[str] | None = Field(
        default=None, description="Job names to run"
    )
    budget_ms: int | None = Field(
        default=None, description="Time budget in milliseconds"
    )


class OrganizeResponse(BaseModel):
    """Response body for POST /v1/organize."""

    jobs_run: list[str] = Field(default_factory=list)
    per_job: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class NodeResponse(BaseModel):
    """Response body for a single node."""

    id: str
    user_id: str
    session_id: str | None = None
    node_type: str
    content: str
    lifecycle_state: str
    confidence: float
    salience: float
    epistemic_type: str | None = None
    source_type: str | None = None
    scope: str
    metadata: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    event_time: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    ttl_days: int | None = None
    superseded_by: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    pinned: bool = False


class NodeListResponse(BaseModel):
    """Response body for node listing."""

    nodes: list[NodeResponse] = Field(default_factory=list)
    count: int = 0


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response body for GET /v1/health."""

    status: str = "ok"
    version: str = ""


class StatsResponse(BaseModel):
    """Response body for GET /v1/stats."""

    node_count: int = 0
    event_count: int = 0
    backend: str = "duckdb"
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Application errors and FastAPI's structured validation errors."""

    detail: str | list[dict[str, Any]]


# Relevance submissions use the same validated contract as the Python API.


class RelevanceRequest(RelevanceSubmission):
    user_id: str | None = Field(default=None, description="Owner; defaults to authenticated user")
