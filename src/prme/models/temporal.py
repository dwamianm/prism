"""Auditable temporal state over exact structured assertions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from prme.types import EpistemicType, LifecycleState, NodeType, Scope, SourceType


AssertionStateStatus = Literal[
    "unknown",
    "single",
    "consistent",
    "multiple",
    "contested",
]


class AssertionStateHistoricalCoverage(BaseModel):
    """Boundary for a state query that applies an ingestion cutoff."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    knowledge_at: datetime
    semantics: Literal["ingestion_cutoff"] = "ingestion_cutoff"
    exact_snapshot: Literal[False] = False
    limitations: tuple[
        Literal[
            "current_lifecycle_state",
            "current_derived_indexes",
            "mutations_not_replayed",
        ],
        ...,
    ] = (
        "current_lifecycle_state",
        "current_derived_indexes",
        "mutations_not_replayed",
    )


class AssertionStateQuery(BaseModel):
    """Select one exact owner/scope assertion attribute at a validity instant.

    Subject and predicate matching uses the same normalized-exact contract as
    assertion aggregation. ``valid_at`` is required so repeated calls do not
    silently depend on the process clock.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    predicate: str
    scope: Scope
    valid_at: AwareDatetime
    knowledge_at: AwareDatetime | None = None
    node_types: tuple[NodeType, ...] = (
        NodeType.FACT,
        NodeType.DECISION,
        NodeType.PREFERENCE,
    )
    limit: int = Field(default=1000, ge=1, le=10000, strict=True)

    @field_validator("subject", "predicate")
    @classmethod
    def validate_selector(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("assertion state selectors must be nonempty strings")
        return value

    @field_validator("node_types")
    @classmethod
    def validate_node_types(cls, values: tuple[NodeType, ...]) -> tuple[NodeType, ...]:
        if not values:
            raise ValueError("node_types cannot be empty")
        if len(set(values)) != len(values):
            raise ValueError("node_types must not contain duplicates")
        return values


class AssertionStateEntry(BaseModel):
    """One stored claim and every clock/state needed to audit its eligibility."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: UUID
    node_type: NodeType
    content: str
    subject: str
    predicate: str
    object: str
    polarity: str
    epistemic_type: EpistemicType
    source_type: SourceType
    lifecycle_state: LifecycleState
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    event_time: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    superseded_by: UUID | None = None
    evidence_refs: tuple[UUID, ...] = ()
    contradiction_node_ids: tuple[UUID, ...] = ()
    current_candidate: bool
    exclusion_reasons: tuple[str, ...] = ()


class AssertionStateValue(BaseModel):
    """One normalized value represented by current eligible claims."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    object: str
    polarity: str
    normalized_object: str
    normalized_polarity: str
    candidate_count: int = Field(ge=1)
    evidence_count: int = Field(ge=0)
    sample_node_ids: tuple[UUID, ...] = ()
    sample_evidence_refs: tuple[UUID, ...] = ()
    samples_truncated: bool = False


class AssertionStateConflict(BaseModel):
    """A contradiction edge between two exact matching assertion records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    edge_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence_ref: UUID | None = None
    created_at: AwareDatetime
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    active_between_current_candidates: bool


class AssertionState(BaseModel):
    """Exact current claim candidates plus an auditable stored timeline.

    A state is a description of accepted stored claims, not a declaration of
    real-world truth. ``multiple`` means differing claims coexist without an
    explicit contradiction; ``contested`` requires lifecycle or edge evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    user_id: str
    query: AssertionStateQuery
    normalized_subject: str
    normalized_predicate: str
    status: AssertionStateStatus
    scanned_nodes: int = Field(ge=0)
    matched_records: int = Field(ge=0)
    current_candidate_count: int = Field(ge=0)
    current_values: tuple[AssertionStateValue, ...] = ()
    current_values_truncated: bool = False
    current_candidates: tuple[AssertionStateEntry, ...] = ()
    current_candidates_truncated: bool = False
    timeline: tuple[AssertionStateEntry, ...] = ()
    timeline_truncated: bool = False
    conflicts: tuple[AssertionStateConflict, ...] = ()
    conflict_count: int = Field(ge=0)
    conflicts_truncated: bool = False
    exclusions: dict[str, int] = Field(default_factory=dict)
    stored_set_exhaustive: Literal[True] = True
    source_extraction_coverage: Literal["unknown"] = "unknown"
    semantic_equivalence: Literal["normalized_exact_only"] = "normalized_exact_only"
    real_world_coverage: Literal["unknown"] = "unknown"
    validity_semantics: Literal["valid_from_inclusive_valid_to_exclusive"] = (
        "valid_from_inclusive_valid_to_exclusive"
    )
    lifecycle_semantics: Literal["current_graph_state"] = "current_graph_state"
    state_semantics: Literal["eligible_claims_not_verified_truth"] = (
        "eligible_claims_not_verified_truth"
    )
    current_candidate_order: Literal["confidence_then_ingestion_desc"] = (
        "confidence_then_ingestion_desc"
    )
    timeline_order: Literal["validity_then_ingestion_ascending"] = (
        "validity_then_ingestion_ascending"
    )
    consistency: Literal["complete_for_unchanged_store"] = (
        "complete_for_unchanged_store"
    )
    historical_coverage: AssertionStateHistoricalCoverage | None = None
