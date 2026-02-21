"""Retrieval pipeline data models.

Defines all data structures for the hybrid retrieval pipeline:
QueryAnalysis, RetrievalCandidate, ScoreTrace, MemoryBundle,
RetrievalResponse, RetrievalMetadata, and ExcludedCandidate.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from prme.models.nodes import MemoryNode
from prme.types import QueryIntent, RepresentationLevel, RetrievalMode


# Candidate source type -- which backend produced a candidate.
CandidateSource = Literal["GRAPH", "VECTOR", "LEXICAL", "PINNED"]

# Bundle section type -- grouping key for memory bundle output.
BundleSection = Literal[
    "entity_snapshots",
    "stable_facts",
    "recent_decisions",
    "active_tasks",
    "provenance_refs",
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
    composite_score: float = Field(
        default=0.0, description="Final composite score"
    )


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
    score_trace: ScoreTrace | None = Field(
        default=None, description="Full score breakdown (always-on)"
    )
    representation: RepresentationLevel | None = Field(
        default=None, description="Set in packing stage"
    )
    token_cost: int = Field(
        default=0, description="Estimated token cost (set in packing stage)"
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


class RetrievalMetadata(BaseModel):
    """Metadata about a retrieval operation.

    Captures timing, backend usage, candidate counts, and configuration version
    for observability and debugging.
    """

    request_id: UUID = Field(description="Request identifier from QueryAnalysis")
    candidates_generated: dict[str, int] = Field(
        default_factory=dict,
        description="Per-backend candidate counts",
    )
    candidates_filtered: int = Field(
        default=0, description="Candidates removed by filtering"
    )
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
