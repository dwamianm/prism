"""Retrieval pipeline data models.

Defines all data structures for the hybrid retrieval pipeline:
QueryAnalysis, RetrievalCandidate, ScoreTrace, MemoryBundle,
RetrievalResponse, RetrievalMetadata, and ExcludedCandidate.
"""

from __future__ import annotations

import math
from typing import Any, Literal, TYPE_CHECKING
from uuid import UUID, uuid4

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prme.models.nodes import MemoryNode
from prme.models.learning import RankingMultipliers
from prme.retrieval.config import ScoringWeights
from prme.retrieval.temporal_relation_models import TemporalRelationMetadata
from prme.types import QueryIntent, RepresentationLevel, RetrievalMode

if TYPE_CHECKING:
    from prme.models.value_bindings import (
        RetrievedValueBinding,
        ToolArgumentResolution,
    )


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
    temporal_signals: list[dict[str, Any]] = Field(
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
    kind: Literal[
        "neural_blend",
        "session_decay",
        "episode_decay",
        "evidence_projection",
        "evidence_augmentation",
        "current_update",
        "neural_rank_assignment",
    ]
    coefficient: float = Field(allow_inf_nan=False)
    neural_score: float | None = Field(default=None, allow_inf_nan=False, ge=0, le=1)
    source_node_id: UUID

    @model_validator(mode="after")
    def validate_operation(self) -> ScoreAdjustment:
        if (self.kind == "neural_blend") != (self.neural_score is not None):
            raise ValueError("Only neural blending requires a neural score")
        if self.kind == "neural_blend" and not 0 <= self.coefficient <= 1:
            raise ValueError("Neural prior weight must be between zero and one")
        if self.kind == "current_update" and not 1 <= self.coefficient <= 2:
            raise ValueError("Current-update multiplier must be between one and two")
        if self.kind == "neural_rank_assignment" and self.coefficient < 0:
            raise ValueError("Assigned ranking score must be nonnegative")
        if self.kind in {"evidence_projection", "evidence_augmentation"} and not (
            0 < self.coefficient <= 1
        ):
            raise ValueError("Evidence-context coefficient must be in (0, 1]")
        return self


# Most that the opt-in tie-break (ScoringWeights.rrf_tie_break) takes from a
# fused score. Fused scores are rounded to ten decimals, so two that differ do
# so by at least 1e-10 and keep their order.
RANK_FUSION_TIE_BREAK_SCALE = 1e-11


class RankFusion(BaseModel):
    """Saved inputs of a rank-fused score (score formula version 2).

    Ranks are competition ranks within the scored candidate pool (tied
    channel scores share the better rank); ``None`` means the candidate is not
    on that channel. Each factor is the candidate's adjustment divided by the
    largest one in the pool, so it lies in [0, 1] and is exactly 1.0 when every
    candidate shares a positive value. ``recency_boost_factor`` is present only
    when ``ScoringWeights.rrf_recency_boost`` applied to the question (the raw
    recency is the trace's ``recency_factor``), and ``tie_break`` exactly when
    ``ScoringWeights.rrf_tie_break`` is set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    semantic_rank: int | None = Field(default=None, ge=1)
    lexical_rank: int | None = Field(default=None, ge=1)
    epistemic_factor: float = Field(ge=0, le=1, allow_inf_nan=False)
    node_type_factor: float = Field(ge=0, le=1, allow_inf_nan=False)
    temporal_factor: float = Field(ge=0, le=1, allow_inf_nan=False)
    # Omitted when unset, so provenance scored without them keeps its bytes.
    recency_boost_factor: float | None = Field(
        default=None, ge=0, le=1, allow_inf_nan=False, exclude_if=lambda value: value is None,
    )
    # The candidate's place in the pool by event time, newest first, as a
    # fraction: 0 for the newest, and equal times share a place.
    tie_break: float | None = Field(
        default=None, ge=0, lt=1, allow_inf_nan=False, exclude_if=lambda value: value is None,
    )

    def score(self, k: int) -> float:
        """The fused score, scaled so first place on both channels is 1.0."""
        fused = sum(1 / (k + rank) for rank in (self.semantic_rank, self.lexical_rank)
                    if rank is not None)
        value = (fused * (k + 1) / 2
                 * self.epistemic_factor * self.node_type_factor * self.temporal_factor)
        if self.recency_boost_factor is not None:
            value *= self.recency_boost_factor
        score = round(value, 10)
        if self.tie_break is not None and score > 0:
            # Only orders scores that are equal at ten decimals; the smallest
            # positive one, 1e-10, stays positive.
            score -= self.tie_break * RANK_FUSION_TIE_BREAK_SCALE
        return score


class ScoreProvenance(BaseModel):
    """Replay a score using saved features, without mutable graph state.

    Version 1 fixes the composite formula, including ten-decimal rounding,
    the relevance cap, and the order of subsequent score operations. Applied
    weights can differ from the request's configured weights. Version 2 is
    reciprocal rank fusion (``ScoringWeights.fusion == "rrf"``): its saved
    ranks and pool-relative factors are in ``rank_fusion``, and the
    subsequent score operations are the same.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    formula_version: Literal[1, 2] = 1
    base_node_id: UUID
    trace: ScoreTrace
    weights: ScoringWeights
    adjustments: tuple[ScoreAdjustment, ...] = ()
    # Omitted from version 1 provenance, which keeps the bytes it had before
    # version 2 existed.
    rank_fusion: RankFusion | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_components(self) -> ScoreProvenance:
        expected_version = 2 if self.weights.fusion == "rrf" else 1
        if self.formula_version != expected_version:
            raise ValueError(
                f"Fusion '{self.weights.fusion}' requires score formula version {expected_version}"
            )
        if self.formula_version == 2 and self.rank_fusion is None:
            raise ValueError("Score formula version 2 requires rank fusion inputs")
        if self.formula_version == 1 and self.rank_fusion is not None:
            raise ValueError("Rank fusion inputs require score formula version 2")
        if self.rank_fusion is not None:
            if (self.rank_fusion.recency_boost_factor is not None
                    and self.weights.rrf_recency_boost is None):
                raise ValueError("A rank fusion recency boost factor requires rrf_recency_boost")
            if (self.rank_fusion.tie_break is not None) != (self.weights.rrf_tie_break is not None):
                raise ValueError("A rank fusion tie-break is recorded exactly when rrf_tie_break is set")
        values = list(self.trace.model_dump().values())
        # Numeric settings only: node-type boosts follow, and fusion is a label.
        values.extend(
            value for value in (getattr(self.weights, name) for name in type(self.weights).model_fields)
            if isinstance(value, (int, float))
        )
        values.extend(self.weights.node_type_boost.values())
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Score provenance components must be finite")
        if self.replay_base_score() != self.trace.composite_score:
            raise ValueError("Score provenance does not reproduce the base trace")
        return self

    def replay_base_score(self) -> float:
        """Recompute the recorded base score, including its relevance cap."""
        if self.rank_fusion is not None:
            if self.weights.rrf_k is None:
                raise ValueError("Rank fusion replay requires rrf_k")
            return self.rank_fusion.score(self.weights.rrf_k)
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
            elif operation.kind == "neural_rank_assignment":
                # Explicit opt-in ordinal remapping. The preceding neural blend
                # retains the raw model score; this is an assigned ranking
                # score on the original prefix's scale, not a probability.
                score = operation.coefficient
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
    # Omitted under weighted scoring, so results and bundles keep the bytes
    # they had before rank fusion recorded it.
    semantic_relevance: float | None = Field(
        default=None, ge=0, allow_inf_nan=False,
        exclude_if=lambda value: value is None,
        description=(
            "Rank fusion only: the semantic cosine similarity that min_score "
            "compares against instead of the rank-based composite_score (see "
            "rank_fusion_relevance). Unlike ScoringWeights.relevance_floor, it "
            "does not include the lexical score"
        ),
    )
    # Working state for rank_fusion_relevance, never serialized: the largest
    # relevance among the memories that added this candidate as session,
    # episode or evidence context, whether or not their score replaced its own.
    context_relevance: float = Field(default=0.0, ge=0, allow_inf_nan=False, exclude=True)
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


def rank_fusion_relevance(candidate: RetrievalCandidate) -> float:
    """Return the semantic cosine that ``min_score`` gates under rank fusion.

    A rank-fused score says where a candidate ranks in its pool, so the best
    of an unrelated pool still scores near 1.0 (issue #110). The cosine is the
    only absolute signal, because lexical scores are min-max normalized per
    query. Session, episode and evidence context count the relevance of the
    memory that pulled them in: through ``context_relevance``, and through the
    score provenance they inherit, whose trace holds that memory's cosine. The
    largest of these and the candidate's own cosine counts. A candidate that
    the vector search did not return has no cosine of its own. The value is
    never below 0, so a zero floor keeps everything.
    """
    inherited = (
        candidate.score_provenance.trace.semantic_similarity
        if candidate.score_provenance is not None else 0.0
    )
    return max(0.0, candidate.semantic_score, inherited, candidate.context_relevance)


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
        description=(
            "IDs of candidates dropped for budget, or because they have no "
            "memory text to show at the allowed representation levels"
        ),
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
    coverage_notice: str | None = Field(
        default=None,
        description="System-authored coverage boundary included in rendered_context",
    )
    context_guidance: str | None = Field(
        default=None,
        description=(
            "Optional task guidance included only when it fits without changing "
            "the selected memory records"
        ),
    )
    context_format: Literal["auditable", "compact", "reader"] = Field(
        default="auditable",
        description="Serialization format used by rendered_context",
    )
    context_references: dict[str, UUID] = Field(
        default_factory=dict,
        description=(
            "Bundle-local references (compact format, or reader format with "
            "citations) mapped to full memory node IDs"
        ),
    )

    def render(self) -> str:
        """Return the already-budgeted context; do not reconstruct full nodes."""
        return self.rendered_context

    def ensure_citable(self) -> None:
        """Raise when packed reader-format records carry no citation references.

        Answerability and claim verification cite records through the tokens in
        the rendered context. Reader records have such tokens only when packed
        with ``context_citations=True``; failing loudly avoids a silent
        abstention.
        """
        if (
            self.context_format == "reader"
            and not self.context_references
            and any(self.sections.values())
        ):
            raise ValueError(
                "This reader-format bundle has no citation references; pack it "
                "with PackingConfig(context_format='reader', context_citations=True)"
            )

    def resolve_context_ref(self, reference: str) -> UUID:
        """Resolve a bundle-local context reference such as ``m3`` to a node ID."""
        try:
            return self.context_references[reference]
        except KeyError as exc:
            raise ValueError(f"Unknown context reference: {reference}") from exc

    def value_bindings(self) -> tuple[RetrievedValueBinding, ...]:
        """Return typed presentation/lookup pairs visible in this exact context."""
        from prme.models.value_bindings import retrieved_value_bindings

        return retrieved_value_bindings(self)

    def render_value_bindings(self) -> str:
        """Render visible bindings as a deterministic, separately budgetable block."""
        from prme.models.value_bindings import render_value_bindings

        return render_value_bindings(self)

    def resolve_tool_arguments(
        self, arguments: dict[str, Any]
    ) -> ToolArgumentResolution:
        """Resolve exact complete tool values using bindings visible in context."""
        from prme.models.value_bindings import resolve_tool_arguments

        return resolve_tool_arguments(self, arguments)

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


AggregationLimitation = Literal[
    "semantic_matching",
    "candidate_limit",
    "backend_failure",
    "score_floor",
    "result_limit",
    "token_budget",
]


class AggregationCoverage(BaseModel):
    """Coverage boundary for a natural-language count or list request.

    Hybrid retrieval can surface useful evidence but cannot prove that a
    semantic criterion matched every stored real-world item. ``exhaustive`` is
    therefore deliberately false; complete stored-record traversal is exposed
    separately through ``scan_nodes`` and ``iter_nodes``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["semantic_candidates", "candidate_limited", "context_limited"]
    exhaustive: Literal[False] = False
    candidate_count: int = Field(ge=0, description="Unique candidates before explicit selection")
    selected_count: int = Field(ge=0, description="Candidates returned after score/count selection")
    context_count: int = Field(ge=0, description="Selected candidates present in the packed context")
    limitations: tuple[AggregationLimitation, ...] = ("semantic_matching",)
    candidate_limit_paths: tuple[str, ...] = ()


HistoricalLimitation = Literal[
    "current_lifecycle_state",
    "current_derived_indexes",
    "mutations_not_replayed",
]


class HistoricalCoverage(BaseModel):
    """Boundary for ``knowledge_at`` ingestion-time filtering.

    The current engine applies the cutoff to candidates produced by current
    graph and search-index state. It does not replay lifecycle transitions,
    corrections, organizer mutations, or evicted index entries.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    knowledge_at: datetime
    semantics: Literal["ingestion_cutoff"] = "ingestion_cutoff"
    exact_snapshot: Literal[False] = False
    limitations: tuple[HistoricalLimitation, ...] = (
        "current_lifecycle_state",
        "current_derived_indexes",
        "mutations_not_replayed",
    )


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
    max_per_source: int | None = Field(
        default=None,
        description=(
            "Optional maximum returned nodes for one exact nonempty evidence "
            "set and byte-identical source passage"
        ),
    )
    max_per_evidence: int | None = Field(
        default=None,
        description=(
            "Optional maximum returned nodes for one exact nonempty evidence set, "
            "regardless of whether their content differs"
        ),
    )
    candidates_included: int = Field(
        default=0, description="Candidates included in final response"
    )
    scoring_config_version: str = Field(
        description="Configured base ScoringWeights version; applied weights are recorded in score provenance"
    )
    ranking_multipliers: RankingMultipliers | None = None
    ranking_profile_id: UUID | None = None
    ranking_profile_status: Literal[
        "none", "applied", "inapplicable", "request_override"
    ] = "none"
    ranking_profile_reason: Literal[
        "feature_identity_mismatch", "base_scoring_mismatch", "explicit_multipliers",
        "rank_fusion_scoring",
    ] | None = None
    timing_ms: float = Field(
        default=0.0, description="Pipeline time including receipt logging; excludes engine startup and queue draining"
    )
    receipt_logging_ms: float = Field(default=0.0, ge=0, description="Receipt construction and operation-log latency")
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
    min_score_skipped: bool = Field(
        default=False,
        description=(
            "Rank fusion only: min_score was not applied, because the vector path "
            "failed or detected an embedding mismatch (see backend_failures) and "
            "no result had a semantic cosine to compare with it. Results keep their "
            "fused order and are not filtered by min_score; limit and the source "
            "and evidence caps still apply. Cross-scope hints skip it too unless "
            "one of them has a cosine"
        ),
    )
    aggregation_coverage: AggregationCoverage | None = Field(
        default=None,
        description="Explicit non-exhaustive coverage for detected count/list queries",
    )
    historical_coverage: HistoricalCoverage | None = Field(
        default=None,
        description="Explicit non-snapshot boundary when knowledge_at is requested",
    )
    temporal_relation: TemporalRelationMetadata | None = Field(
        default=None,
        description=(
            "Outcome and non-secret provider provenance when opt-in temporal "
            "relation enrichment ran for this retrieval"
        ),
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
    # Omitted under weighted scoring, which keeps its serialized exclusions.
    semantic_relevance: float | None = Field(
        default=None, ge=0, allow_inf_nan=False,
        exclude_if=lambda value: value is None,
        description="Rank fusion only: the semantic cosine that min_score compares against",
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
