"""Structured, exhaustive aggregation over stored assertion nodes."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from prme.types import LifecycleState, NodeType, RetrievalMode, Scope


AssertionField = Literal["subject", "predicate", "object", "polarity"]
QuantityGroupField = Literal["subject", "predicate", "object", "polarity", "unit"]


class AssertionQuery(BaseModel):
    """Exact selectors and grouping for stored structured assertions.

    Values match after Unicode, case, and whitespace normalization. Predicate
    matching also treats spaces and hyphens as underscores. Matching is exact
    after that normalization; this API does not infer semantic equivalence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subjects: tuple[str, ...] = ()
    predicates: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    polarities: tuple[str, ...] = ("positive",)
    group_by: tuple[AssertionField, ...] = ("object",)
    scopes: tuple[Scope, ...] = ()
    node_types: tuple[NodeType, ...] = (
        NodeType.FACT,
        NodeType.DECISION,
        NodeType.PREFERENCE,
    )
    lifecycle_states: tuple[LifecycleState, ...] | None = None
    retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT
    event_time_from: AwareDatetime | None = None
    event_time_to: AwareDatetime | None = None
    valid_at: AwareDatetime | None = None
    knowledge_at: AwareDatetime | None = None
    group_limit: int = Field(default=1000, ge=0, le=10000, strict=True)
    sample_limit: int = Field(default=10, ge=0, le=100, strict=True)

    @field_validator("subjects", "predicates", "objects", "polarities")
    @classmethod
    def validate_selectors(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("assertion selectors must be nonempty strings")
        return values

    @field_validator("group_by", "scopes", "node_types")
    @classmethod
    def validate_unique_nonempty(cls, values: tuple) -> tuple:
        if not values:
            raise ValueError(
                "group_by, scopes, and node_types cannot be empty when supplied"
            )
        if len(set(values)) != len(values):
            raise ValueError(
                "group_by, scopes, and node_types must not contain duplicates"
            )
        return values

    @model_validator(mode="after")
    def validate_windows(self) -> AssertionQuery:
        if (
            self.event_time_from is not None
            and self.event_time_to is not None
            and self.event_time_from > self.event_time_to
        ):
            raise ValueError("event_time_from must not be after event_time_to")
        return self


class AssertionGroup(BaseModel):
    """One exact normalized group of matching assertion records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: dict[AssertionField, str]
    normalized_values: dict[AssertionField, str]
    occurrence_count: int = Field(ge=1)
    evidence_count: int = Field(ge=0)
    sample_node_ids: tuple[UUID, ...] = ()
    sample_evidence_refs: tuple[UUID, ...] = ()
    samples_truncated: bool = False
    earliest_event_time: AwareDatetime | None = None
    latest_event_time: AwareDatetime | None = None


class AssertionAggregation(BaseModel):
    """Complete stored-set result for an unchanged memory pack.

    ``stored_set_exhaustive`` only covers structured assertions already stored
    in the selected owner and filters. It does not claim that extraction found
    every source assertion or that the stored set represents the real world.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    user_id: str
    query: AssertionQuery
    scanned_nodes: int = Field(ge=0)
    matched_records: int = Field(ge=0)
    distinct_count: int = Field(ge=0)
    groups: tuple[AssertionGroup, ...] = ()
    groups_truncated: bool = False
    stored_set_exhaustive: Literal[True] = True
    source_extraction_coverage: Literal["unknown"] = "unknown"
    semantic_equivalence: Literal["normalized_exact_only"] = "normalized_exact_only"
    real_world_coverage: Literal["unknown"] = "unknown"
    consistency: Literal["complete_for_unchanged_store"] = (
        "complete_for_unchanged_store"
    )
    exclusions: dict[str, int] = Field(default_factory=dict)


class QuantityAggregationQuery(BaseModel):
    """Exact selectors and grouping for source-grounded stored quantities."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subjects: tuple[str, ...] = ()
    predicates: tuple[str, ...] = ()
    predicate_prefixes: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    polarities: tuple[str, ...] = ("positive",)
    units: tuple[str, ...] = ()
    group_by: tuple[QuantityGroupField, ...] = ("unit",)
    scopes: tuple[Scope, ...] = ()
    node_types: tuple[NodeType, ...] = (
        NodeType.FACT,
        NodeType.DECISION,
        NodeType.PREFERENCE,
    )
    lifecycle_states: tuple[LifecycleState, ...] | None = None
    retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT
    event_time_from: AwareDatetime | None = None
    event_time_to: AwareDatetime | None = None
    valid_at: AwareDatetime | None = None
    knowledge_at: AwareDatetime | None = None
    group_limit: int = Field(default=1000, ge=0, le=10000, strict=True)
    sample_limit: int = Field(default=10, ge=0, le=100, strict=True)

    @field_validator(
        "subjects",
        "predicates",
        "predicate_prefixes",
        "objects",
        "polarities",
        "units",
    )
    @classmethod
    def validate_selectors(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("quantity selectors must be nonempty strings")
        return values

    @field_validator("group_by", "scopes", "node_types")
    @classmethod
    def validate_unique_nonempty(cls, values: tuple) -> tuple:
        if not values:
            raise ValueError("group_by, scopes, and node_types cannot be empty when supplied")
        if len(set(values)) != len(values):
            raise ValueError("group_by, scopes, and node_types must not contain duplicates")
        return values

    @field_validator("group_by")
    @classmethod
    def require_unit_group(cls, values: tuple[QuantityGroupField, ...]) -> tuple[QuantityGroupField, ...]:
        if "unit" not in values:
            raise ValueError("quantity group_by must include unit because unit conversion is disabled")
        return values

    @model_validator(mode="after")
    def validate_windows(self) -> QuantityAggregationQuery:
        if (
            self.event_time_from is not None
            and self.event_time_to is not None
            and self.event_time_from > self.event_time_to
        ):
            raise ValueError("event_time_from must not be after event_time_to")
        return self


class QuantitySample(BaseModel):
    """One source-grounded quantity contribution with its provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    node_id: UUID
    value: Decimal
    unit: str
    source_text: str
    evidence_refs: tuple[UUID, ...] = ()


class QuantityGroup(BaseModel):
    """Exact decimal statistics for one normalized assertion/unit group."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    values: dict[QuantityGroupField, str]
    normalized_values: dict[QuantityGroupField, str]
    value_count: int = Field(ge=1)
    total: Decimal
    minimum: Decimal
    maximum: Decimal
    evidence_count: int = Field(ge=0)
    samples: tuple[QuantitySample, ...] = ()
    samples_truncated: bool = False
    earliest_event_time: AwareDatetime | None = None
    latest_event_time: AwareDatetime | None = None


class QuantityAggregation(BaseModel):
    """Exact stored decimal totals without implicit unit conversion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    user_id: str
    query: QuantityAggregationQuery
    scanned_nodes: int = Field(ge=0)
    matched_quantity_records: int = Field(ge=0)
    distinct_count: int = Field(ge=0)
    groups: tuple[QuantityGroup, ...] = ()
    groups_truncated: bool = False
    stored_set_exhaustive: Literal[True] = True
    source_extraction_coverage: Literal["unknown"] = "unknown"
    semantic_equivalence: Literal[
        "normalized_exact_only",
        "normalized_exact_and_predicate_prefix",
    ] = "normalized_exact_only"
    real_world_coverage: Literal["unknown"] = "unknown"
    unit_conversion: Literal["none"] = "none"
    consistency: Literal["complete_for_unchanged_store"] = (
        "complete_for_unchanged_store"
    )
    exclusions: dict[str, int] = Field(default_factory=dict)
