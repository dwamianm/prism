"""Retrieval pipeline data models.

Defines all data structures for the hybrid retrieval pipeline:
QueryAnalysis, RetrievalCandidate, ScoreTrace, MemoryBundle,
RetrievalResponse, RetrievalMetadata, and ExcludedCandidate.
"""

from __future__ import annotations

import math
from typing import Literal
from uuid import UUID, uuid4

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prme.models.nodes import MemoryNode
from prme.retrieval.config import ScoringWeights
from prme.types import QueryIntent, RepresentationLevel, RetrievalMode


# Candidate source type -- which backend produced a candidate.
CandidateSource = Literal["GRAPH", "VECTOR", "LEXICAL", "PINNED"]

# Bundle section type -- grouping key for memory bundle output.
BundleSection = Literal[
    "system_instructions",
    "entity_snapshots",
    "stable_facts",
    "recent_decisions",
    "active_tasks",
    "provenance_refs",
    "contested_claims",
    "summaries",
]


class QueryAnalysis(BaseModel):
    """Output of query analysis (retrieval Stage 1).

    Captures intent classification, extracted entities, temporal signals,
    and retrieval mode for downstream pipeline stages.
    """

    query: str = Field(description="Original query text")
    intent: QueryIntent = Field(description="Classified query intent")
    entities: list[str] = Field(
        default_factory=list,
        description="Extracted entity names from query",
    )
    temporal_signals: list[dict] = Field(
        default_factory=list,
        description="Temporal signals (each has type/value/resolved keys)",
    )
    time_from: datetime | None = Field(
        default=None,
        description="Resolved start of temporal window",
    )
    time_to: datetime | None = Field(
        default=None,
        description="Resolved end of temporal window",
    )
    retrieval_mode: RetrievalMode = Field(
        default=RetrievalMode.DEFAULT,
        description="Retrieval mode controlling epistemic filtering",
    )
    request_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for this retrieval request",
    )
    is_aggregation: bool = Field(
        default=False,
        description="Whether the query requires aggregation (count/total/list-all)",
    )


class ScoreTrace(BaseModel):
    """Explainable breakdown of the 8 composite score components (RFC-0005 S7).

    Frozen for immutability -- score traces are computed once per candidate.
    """

    model_config = ConfigDict(frozen=True)

    semantic_similarity: float = Field(
        default=0.0, description="Semantic similarity score"
    )
    lexical_relevance: float = Field(
        default=0.0, description="Lexical relevance score"
    )
    graph_proximity: float = Field(
        default=0.0, description="Graph proximity score"
    )
    recency_factor: float = Field(
        default=0.0, description="Recency decay factor"
    )
    salience: float = Field(
        default=0.0, description="Salience score"
    )
    confidence: float = Field(
        default=0.0, description="Confidence score"
    )
    epistemic_weight: float = Field(
        default=0.0, description="Epistemic type weight (multiplicative)"
    )
    path_score: float = Field(
        default=0.0, description="Multi-path corroboration score"
    )
    temporal_affinity: float = Field(
        default=0.0,
        description="Temporal affinity score (0.0-1.0), active only for TEMPORAL queries",
    )
    node_type_boost: float = Field(
        default=1.0,
        description="Node-type multiplicative boost (semantic types boosted per PRIME research)",
    )
    composite_score: float = Field(
        default=0.0, description="Final composite score"
    )


class ScoreAdjustment(BaseModel):
    """An ordered, recorded operation after the base composite score."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["neural_blend", "session_decay"]
    coefficient: float = Field(allow_inf_nan=False)
    neural_score: float | None = Field(default=None, allow_inf_nan=False, ge=0, le=1)
    source_node_id: UUID

    @model_validator(mode="after")
    def validate_operation(self):
        if (self.kind == "neural_blend") != (self.neural_score is not None):
            raise ValueError("Only neural blending requires a neural score")
        if self.kind == "neural_blend" and not 0 <= self.coefficient <= 1:
            raise ValueError("Neural prior weight must be between zero and one")
        return self


class ScoreProvenance(BaseModel):
    """Replay a score using saved features, without mutable graph state.

    Version 1 fixes the composite formula, including ten-decimal rounding,
    the relevance cap, and the order of subsequent score operations. Applied
    weights can differ from the request's configured weights.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    formula_version: Literal[1] = 1
    base_node_id: UUID
    trace: ScoreTrace
    weights: ScoringWeights
    adjustments: tuple[ScoreAdjustment, ...] = ()

    @model_validator(mode="after")
    def validate_components(self):
        values = list(self.trace.model_dump().values())
        values.extend(v for k, v in self.weights.model_dump().items() if k != "node_type_boost")
        values.extend(self.weights.node_type_boost.values())
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Score provenance components must be finite")
        if self.replay_base_score() != self.trace.composite_score:
            raise ValueError("Score provenance does not reproduce the base trace")
        return self

    def replay_base_score(self) -> float:
        """Recompute the recorded base score, including its relevance cap."""
        t, w = self.trace, self.weights
        additive = (w.w_semantic * t.semantic_similarity
                    + w.w_lexical * t.lexical_relevance
                    + w.w_graph * t.graph_proximity
                    + w.w_recency * t.recency_factor
                    + w.w_salience * t.salience
                    + w.w_confidence * t.confidence)
        score = additive * t.epistemic_weight
        if w.temporal_boost > 0:
            score += w.temporal_boost * t.temporal_affinity
        score *= t.node_type_boost
        relevance = t.semantic_similarity + t.lexical_relevance
        if w.relevance_floor > 0 and relevance < w.relevance_floor:
            score = min(score, relevance)
        return round(score, 10)

    def replay_score(self) -> float:
        """Apply saved neural blends and session inheritance in order."""
        score = self.replay_base_score()
        for operation in self.adjustments:
            if operation.kind == "neural_blend":
                assert operation.neural_score is not None
                score = ((1 - operation.coefficient) * operation.neural_score
                         + operation.coefficient * score)
            else:
                score *= operation.coefficient
        return score


class RetrievalCandidate(BaseModel):
    """Enriched candidate carrying all score components.

    Produced by candidate generation, enriched through scoring and packing.
    """

    node: MemoryNode = Field(description="The memory node candidate")
    paths: list[str] = Field(
        default_factory=list,
        description="Which backends produced this candidate",
    )
    path_count: int = Field(
        default=0,
        description="Number of backends that produced this candidate",
    )
    semantic_score: float = Field(
        default=0.0, description="Raw semantic similarity score"
    )
    lexical_score: float = Field(
        default=0.0, description="Raw lexical relevance score"
    )
    graph_proximity: float = Field(
        default=0.0,
        description="Graph proximity (1-hop=1.0, 2-hop=0.7, 3-hop=0.4)",
    )
    composite_score: float = Field(
        default=0.0, description="Final composite score after scoring stage"
    )
    reranker_score: float | None = Field(
        default=None, ge=0, le=1,
        description="Normalized neural score when reranked; not a calibrated relevance probability",
    )
    score_trace: ScoreTrace | None = Field(
        default=None, description="Base score breakdown before optional neural blending"
    )
    score_provenance: ScoreProvenance | None = Field(
        default=None, description="Applied weights and ordered operations for exact score replay"
    )
    representation: RepresentationLevel | None = Field(
        default=None, description="Set in packing stage"
    )
    token_cost: int = Field(
        default=0, description="Estimated token cost (set in packing stage)"
    )
    rendered_text: str | None = Field(
        default=None, description="Faithful text at the selected representation level",
    )
    conflict_flag: bool = Field(
        default=False,
        description="Whether this node has an unresolved contradiction (CONTESTED state)",
    )
    contradicts_id: UUID | None = Field(
        default=None,
        description="ID of the contradicting node, if this node is CONTESTED",
    )


class MemoryBundle(BaseModel):
    """Context-packed output for a retrieval request.

    Groups ranked candidates into semantic sections with token budget tracking.
    """

    sections: dict[str, list[RetrievalCandidate]] = Field(
        default_factory=dict,
        description="Candidates grouped by section type",
    )
    included_count: int = Field(
        default=0, description="Number of candidates included in bundle"
    )
    excluded_ids: list[UUID] = Field(
        default_factory=list,
        description="IDs of candidates dropped for budget",
    )
    tokens_used: int = Field(
        default=0, description="Total tokens consumed by bundle"
    )
    token_budget: int = Field(
        default=0, description="Token budget for this bundle"
    )
    budget_remaining: int = Field(
        default=0, description="Remaining token budget"
    )
    min_fidelity: RepresentationLevel = Field(
        default=RepresentationLevel.REFERENCE,
        description="Minimum representation level used",
    )
    rendered_context: str = Field(default="", description="The exact context counted against the budget")
    tokenizer: str | None = Field(default=None, description="Encoding used for tokens_used")

    def render(self) -> str:
        """Return the already-budgeted context; do not reconstruct full nodes."""
        return self.rendered_context

    def render_system_instructions(self) -> str:
        """Render system instructions as a formatted prompt block.

        Returns a string suitable for injection as system-level context
        before factual content. Returns empty string if no instructions
        are present.

        Returns:
            Formatted system instructions block, or empty string.
        """
        instructions = self.sections.get("system_instructions", [])
        if not instructions:
            return ""
        lines = ["## System Instructions"]
        for candidate in instructions:
            content = candidate.rendered_text if candidate.rendered_text is not None else candidate.node.content
            lines.append(f"- {content}")
        return "\n".join(lines) + "\n"


class RetrievalMetadata(BaseModel):
    """Metadata about a retrieval operation.

    Captures timing, backend usage, candidate counts, and configuration version
    for observability and debugging.
    """

    request_id: UUID = Field(description="Request identifier from QueryAnalysis")
    receipt_persisted: bool = Field(default=False, description="An owner-scoped feedback receipt was durably logged")
    reference_time: datetime | None = Field(
        default=None, description="UTC clock used for relative dates and scoring decay",
    )
    candidates_generated: dict[str, int] = Field(
        default_factory=dict,
        description="Per-backend candidate counts",
    )
    candidates_filtered: int = Field(
        default=0, description="Candidates removed by filtering"
    )
    min_score: float | None = None
    result_limit: int | None = None
    candidates_included: int = Field(
        default=0, description="Candidates included in final response"
    )
    scoring_config_version: str = Field(
        description="ScoringWeights version_id used for this retrieval"
    )
    timing_ms: float = Field(
        default=0.0, description="Total retrieval time in milliseconds"
    )
    backends_used: list[str] = Field(
        default_factory=list, description="List of backends queried"
    )
    embedding_mismatch: bool = Field(
        default=False,
        description="Flag if embedding model mismatch detected (per research)",
    )
    backend_failures: dict[str, str] = Field(
        default_factory=dict,
        description="Failed primary candidate paths with sanitized reason codes; empty means no detected failure",
    )


class FilterMetadata(BaseModel):
    """Metadata about active filters for debugging and explainability."""

    scope_filter: list[str] | None = Field(
        default=None, description="Active scope filter values"
    )
    time_from: datetime | None = Field(
        default=None, description="Active temporal window start"
    )
    time_to: datetime | None = Field(
        default=None, description="Active temporal window end"
    )
    cross_scope_enabled: bool = Field(
        default=False, description="Whether cross-scope hints were requested"
    )


class ExcludedCandidate(BaseModel):
    """Record of a candidate excluded from final results.

    For full candidate audit trail -- captures why each candidate was dropped.
    """

    node_id: UUID = Field(description="ID of the excluded node")
    reason: str = Field(
        description="Exclusion reason (e.g., 'epistemic_filtered', "
        "'below_threshold', 'budget_exceeded')"
    )
    composite_score: float | None = Field(
        default=None,
        description="Composite score at time of exclusion (if scored)",
    )


class RetrievalResponse(BaseModel):
    """Top-level response for a retrieval request.

    Contains the packed memory bundle, scored results, metadata, and
    always-on score traces for full explainability.
    """

    bundle: MemoryBundle = Field(description="Context-packed memory bundle")
    results: list[RetrievalCandidate] = Field(
        default_factory=list,
        description="Scored and ranked results before packing",
    )
    metadata: RetrievalMetadata = Field(
        description="Retrieval operation metadata"
    )
    score_traces: list[ScoreTrace] = Field(
        default_factory=list,
        description="Always-on score traces (one per result)",
    )
    filter_metadata: FilterMetadata | None = Field(
        default=None,
        description="Metadata about active filters (scope, temporal, cross-scope)",
    )
    excluded: list[ExcludedCandidate] = Field(default_factory=list, description="Epistemic and selection exclusions; packing exclusions are in bundle.excluded_ids")
    cross_scope_hints: list[RetrievalCandidate] = Field(
        default_factory=list,
        description="Highly relevant results from outside the requested scope",
    )
