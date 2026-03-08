"""Result models for organizer jobs and maintenance passes.

Defines the structured result types returned by organize(), end_session(),
and the MaintenanceRunner. All models use Pydantic for serialization and
validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class JobResult(BaseModel):
    """Result of a single organizer job execution."""

    job: str
    nodes_processed: int = 0
    nodes_modified: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)


class OrganizeResult(BaseModel):
    """Aggregate result from an organize() or end_session() call."""

    jobs_run: list[str] = Field(default_factory=list)
    jobs_skipped: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    budget_remaining_ms: float = 0.0
    per_job: dict[str, JobResult] = Field(default_factory=dict)


class ConsolidationResult(BaseModel):
    """Result of the predictive forgetting / consolidation pipeline (issue #22)."""

    clusters_found: int = 0
    nodes_consolidated: int = 0
    nodes_archived: int = 0
    summaries_created: int = 0
    duration_ms: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass
class MaintenanceResult:
    """Result of an opportunistic maintenance pass."""

    nodes_promoted: int = 0
    nodes_archived: int = 0
    nodes_deprecated: int = 0
    feedback_applied: int = 0
    duration_ms: float = 0.0
    skipped_reason: str | None = None
