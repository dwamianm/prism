"""Pydantic request/response models for the PRME HTTP API.

All request and response bodies are defined here as Pydantic models.
These are thin DTOs — no business logic belongs here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from prme.types import (
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StoreRequest(BaseModel):
    """Request body for POST /v1/store."""

    content: str = Field(description="Text content to store")
    user_id: str = Field(description="Owner user ID")
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


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    """Request body for POST /v1/ingest."""

    content: str = Field(description="Message text to ingest")
    user_id: str = Field(description="Owner user ID")
    role: str = Field(default="user", description="Message role")
    namespace: str | None = Field(
        default=None, description="Optional namespace"
    )
    scope: Scope | None = Field(
        default=None, description="Memory scope"
    )


class IngestResponse(BaseModel):
    """Response body for POST /v1/ingest."""

    event_id: str = Field(description="ID of the persisted event")


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


class RetrieveRequest(BaseModel):
    """Request body for POST /v1/retrieve."""

    query: str = Field(description="Natural language query")
    user_id: str = Field(description="User ID for scoping")
    limit: int | None = Field(default=None, description="Max results")
    mode: str | None = Field(
        default=None,
        description="Retrieval mode (default or explicit)",
    )
    filters: dict[str, Any] | None = Field(
        default=None, description="Optional filters"
    )


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
    """Standard error response."""

    detail: str
