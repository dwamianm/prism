"""Versioned scoring weights and packing configuration.

Provides ScoringWeights (frozen, deterministically versioned) and
PackingConfig for the hybrid retrieval pipeline. Default instances
are exported as module-level constants.
"""

from __future__ import annotations

import hashlib
import math
import warnings
from typing import Any, Literal

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

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
        default=0.02,
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
    node_type_boost: dict[str, float] = Field(
        default={
            "fact": 1.15,
            "preference": 1.15,
            "decision": 1.15,
            "summary": 1.10,
            "instruction": 1.15,
        },
        description=(
            "Per-node-type multiplicative boost to composite score. "
            "Types not listed default to 1.0. Semantic memory types "
            "(fact, preference, summary) are boosted over episodic types "
            "(event) per PRIME dual-memory research."
        ),
    )
    relevance_floor: float = Field(
        default=0.35,
        description=(
            "When the query-dependent signals (semantic + lexical) are below "
            "this threshold, the composite score is capped at the relevance "
            "value. This prevents query-independent signals (recency, salience, "
            "confidence) from inflating scores for irrelevant candidates, "
            "enabling abstention. Set to 0.0 to disable."
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
        ntb_sorted = ",".join(
            f"{k}={v}" for k, v in sorted(self.node_type_boost.items())
        )
        payload = (
            f"{self.w_semantic}:{self.w_lexical}:{self.w_graph}:"
            f"{self.w_recency}:{self.w_salience}:{self.w_confidence}:"
            f"{self.w_epistemic}:{self.w_paths}:{self.recency_lambda}:"
            f"{self.temporal_boost}:{self.relevance_floor}:{ntb_sorted}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


class PackingConfig(BaseModel):
    """Configuration for the context packing stage.

    Controls token budget, representation fidelity, and per-backend
    candidate limits for the retrieval pipeline.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    multipath_ordering: Literal["density", "score", "balanced"] = Field(
        default="balanced",
        description=(
            "Order multi-path candidates by score per token ('density'), "
            "composite score ('score'), or reserve the highest-scored ordinary "
            "multi-path candidate then use score / full_tokens**0.25 ('balanced'). "
            "Balanced is the evidence-backed default; density and score remain "
            "available for compatibility and workload-specific evaluation. "
            "Other priority tiers and whole-output token limits are unchanged."
        ),
    )
    context_guidance_mode: Literal["off", "temporal", "all"] = Field(
        default="temporal",
        description=(
            "Add token-counted task guidance after memory selection. 'temporal' "
            "is the evidence-backed default and guides only date arithmetic and "
            "ordering queries; 'off' disables guidance; 'all' also enables "
            "experimental current-state and personalization guidance. Guidance "
            "is omitted when it cannot fit without displacing a memory record."
        ),
    )
    context_format: Literal["auditable", "compact"] = Field(
        default="auditable",
        description=(
            "Render packed records as self-describing JSON objects ('auditable') "
            "or schema-declared JSON arrays with short bundle-local references "
            "('compact'). Both formats retain type, scope, epistemic state, "
            "lifecycle, source provenance, temporal fields, and the complete "
            "selected representation text."
        ),
    )
    token_budget: int = Field(
        default=4096, ge=0, description="Default context budget in tokens"
    )
    tokenizer: str = Field(
        default="cl100k_base", description="Tiktoken encoding for the entire rendered memory context",
    )
    min_fidelity: RepresentationLevel = Field(
        default=RepresentationLevel.REFERENCE,
        description="Minimum representation level for packed candidates",
    )
    overhead_tokens: int = Field(
        default=100,
        ge=0,
        description="Additional caller-reserved tokens beyond the measured memory context",
    )
    chars_per_token: float = Field(
        default=4.2,
        gt=0,
        description=(
            "Deprecated compatibility field. Context budgets use the configured "
            "tiktoken tokenizer over the complete rendered output; changing this "
            "value has no effect."
        ),
    )
    graph_max_candidates: int = Field(
        default=150, ge=0,
        description="Max candidates from graph traversal",
    )
    vector_k: int = Field(
        default=500, ge=0, description="Max candidates from vector search"
    )
    lexical_k: int = Field(
        default=500, ge=0, description="Max candidates from lexical search"
    )
    graph_max_hops: int = Field(
        default=3, ge=1, le=3,
        description="Max hops for graph neighborhood (1-3 per RFC)",
    )
    cross_scope_top_n: int = Field(
        default=5, ge=0,
        description="Top-N threshold for cross-scope hints [HYPOTHESIS]",
    )
    cross_scope_token_budget: int = Field(
        default=512,
        ge=0,
        description=(
            "Deprecated compatibility field. Cross-scope hints are separate "
            "scored response records capped by cross_scope_top_n; changing this "
            "value has no effect."
        ),
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
        default=3.0,
        description="Multiplier for candidate k values on aggregation/count queries",
    )
    aggregation_k_max: int = Field(
        default=2000,
        description="Hard cap on candidate k values after aggregation multiplier",
    )

    @model_validator(mode="before")
    @classmethod
    def warn_ignored_compatibility_fields(cls, value: Any) -> Any:
        """Expose legacy settings that no longer affect packing behavior."""
        if isinstance(value, dict):
            ignored = []
            chars_per_token = value.get("chars_per_token", 4.2)
            if (
                isinstance(chars_per_token, (int, float))
                and math.isfinite(chars_per_token)
                and chars_per_token > 0
                and chars_per_token != 4.2
            ):
                ignored.append("chars_per_token")
            cross_scope_token_budget = value.get("cross_scope_token_budget", 512)
            if (
                isinstance(cross_scope_token_budget, int)
                and cross_scope_token_budget >= 0
                and cross_scope_token_budget != 512
            ):
                ignored.append("cross_scope_token_budget")
            if ignored:
                warnings.warn(
                    f"PackingConfig {', '.join(ignored)} is deprecated and ignored; "
                    "context uses exact tokenizer counts and cross-scope hints use "
                    "cross_scope_top_n.",
                    FutureWarning,
                    stacklevel=2,
                )
        return value


# Module-level default instances.
DEFAULT_SCORING_WEIGHTS = ScoringWeights()
DEFAULT_PACKING_CONFIG = PackingConfig()
