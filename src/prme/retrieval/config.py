"""Versioned scoring weights and packing configuration.

Provides ScoringWeights (frozen, deterministically versioned) and
PackingConfig for the hybrid retrieval pipeline. Default instances
are exported as module-level constants.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prme.types import RepresentationLevel


class ScoringWeights(BaseModel):
    """Versioned scoring weights for the composite score formula (RFC-0005).

    Frozen for immutability. Produces a deterministic version_id hash
    from all weight values for config traceability.

    The six additive weights (semantic, lexical, graph, recency, salience,
    confidence) must sum to 1.0. Epistemic weight is multiplicative and
    paths weight is a tiebreaker -- neither is included in the sum.
    """

    model_config = ConfigDict(frozen=True)

    w_semantic: float = Field(
        default=0.25, description="Semantic similarity weight"
    )
    w_lexical: float = Field(
        default=0.20, description="Lexical relevance weight"
    )
    w_graph: float = Field(
        default=0.20, description="Graph proximity weight"
    )
    w_recency: float = Field(
        default=0.10, description="Recency factor weight"
    )
    w_salience: float = Field(
        default=0.10, description="Salience weight"
    )
    w_confidence: float = Field(
        default=0.15, description="Confidence weight"
    )
    w_epistemic: float = Field(
        default=0.05, description="Epistemic weight (multiplicative, not additive)"
    )
    w_paths: float = Field(
        default=0.00, description="Multi-path corroboration weight (tiebreaker only)"
    )
    recency_lambda: float = Field(
        default=0.01,
        description="Decay rate for recency factor: exp(-lambda * days)",
    )
    temporal_boost: float = Field(
        default=0.15,
        description=(
            "Extra weight for temporal affinity when query intent is TEMPORAL. "
            "Added as a bonus on top of the additive score (not included in "
            "the sum-to-1.0 constraint). Max +0.15 to composite score."
        ),
    )

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringWeights:
        """Verify additive weights sum to 1.0 (within tolerance).

        Epistemic (multiplicative) and paths (tiebreaker) are excluded
        from the sum constraint.
        """
        additive_sum = (
            self.w_semantic
            + self.w_lexical
            + self.w_graph
            + self.w_recency
            + self.w_salience
            + self.w_confidence
        )
        if abs(additive_sum - 1.0) > 1e-6:
            msg = (
                f"Additive weights must sum to 1.0, got {additive_sum:.6f}. "
                f"(semantic={self.w_semantic}, lexical={self.w_lexical}, "
                f"graph={self.w_graph}, recency={self.w_recency}, "
                f"salience={self.w_salience}, confidence={self.w_confidence})"
            )
            raise ValueError(msg)
        return self

    @property
    def version_id(self) -> str:
        """Deterministic SHA-256 hash (first 12 chars) of all weight values.

        Enables config traceability -- every retrieval response records
        which scoring config version produced it.
        """
        payload = (
            f"{self.w_semantic}:{self.w_lexical}:{self.w_graph}:"
            f"{self.w_recency}:{self.w_salience}:{self.w_confidence}:"
            f"{self.w_epistemic}:{self.w_paths}:{self.recency_lambda}:"
            f"{self.temporal_boost}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


class PackingConfig(BaseModel):
    """Configuration for the context packing stage.

    Controls token budget, representation fidelity, and per-backend
    candidate limits for the retrieval pipeline.
    """

    token_budget: int = Field(
        default=4096, description="Default context budget in tokens"
    )
    min_fidelity: RepresentationLevel = Field(
        default=RepresentationLevel.REFERENCE,
        description="Minimum representation level for packed candidates",
    )
    overhead_tokens: int = Field(
        default=100,
        description="Reserved tokens for JSON envelope and separators",
    )
    chars_per_token: float = Field(
        default=4.2,
        description="Character-based token estimation default [HYPOTHESIS]",
    )
    graph_max_candidates: int = Field(
        default=75,
        description="Max candidates from graph traversal",
    )
    vector_k: int = Field(
        default=100, description="Max candidates from vector search"
    )
    lexical_k: int = Field(
        default=100, description="Max candidates from lexical search"
    )
    graph_max_hops: int = Field(
        default=3, description="Max hops for graph neighborhood (1-3 per RFC)"
    )
    cross_scope_top_n: int = Field(
        default=5,
        description="Top-N threshold for cross-scope hints [HYPOTHESIS]",
    )
    cross_scope_token_budget: int = Field(
        default=512,
        description="Separate token budget for cross-scope hints [HYPOTHESIS]",
    )
    session_context_window: int = Field(
        default=3,
        description=(
            "Number of adjacent turns to include before and after a retrieved "
            "node from the same session_id. Set to 0 to disable session context "
            "expansion. Only applied to the top session_context_top_k scored "
            "candidates."
        ),
    )
    session_context_top_k: int = Field(
        default=20,
        description=(
            "Number of top-scored candidates to expand with session context. "
            "Limits the expansion to avoid blowing up the candidate list."
        ),
    )
    session_context_score_decay: float = Field(
        default=0.85,
        description=(
            "Score multiplier for session-context expanded nodes. Applied to "
            "the triggering node's composite_score so context nodes rank just "
            "below the node that caused their inclusion."
        ),
    )
    aggregation_k_multiplier: float = Field(
        default=2.5,
        description="Multiplier for candidate k values on aggregation/count queries",
    )
    aggregation_k_max: int = Field(
        default=500,
        description="Hard cap on candidate k values after aggregation multiplier",
    )


# Module-level default instances.
DEFAULT_SCORING_WEIGHTS = ScoringWeights()
DEFAULT_PACKING_CONFIG = PackingConfig()
