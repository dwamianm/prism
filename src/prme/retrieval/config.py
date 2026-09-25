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

# Rank constant for fusion="rrf" when none is given.
DEFAULT_RRF_K = 60
# Opt-in rank fusion terms, unset by default and recorded only in version 19
# receipts (issue #168).
RANK_FUSION_OPT_INS = ("rrf_recency_boost", "rrf_tie_break")
# Settings that only rank fusion reads; weighted scoring drops them.
RANK_FUSION_ONLY_SETTINGS = ("rrf_k", *RANK_FUSION_OPT_INS)


class ScoringWeights(BaseModel):
    """Versioned scoring weights for the composite score formula (RFC-0005).

    Frozen for immutability. Produces a deterministic version_id hash
    from all weight values for config traceability.

    The six additive weights (semantic, lexical, graph, recency, salience,
    confidence) must sum to 1.0. Epistemic weight is multiplicative and
    paths weight is a tiebreaker -- neither is included in the sum.

    ``fusion="rrf"`` replaces the weighted sum with reciprocal rank fusion of
    each candidate's semantic and lexical ranks (score formula version 2).
    The additive weights are then unused, but they are still validated and
    recorded. A weighted configuration leaves ``fusion`` and ``rrf_k`` out of
    its serialized form, and any configuration leaves the unset opt-in rank
    fusion terms out, so receipts, ranking profiles and evaluations written
    before they existed keep their exact bytes and checksums.
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
    current_update_multiplier: float = Field(
        default=1.30,
        ge=1.0,
        le=2.0,
        description=(
            "[HYPOTHESIS] Score multiplier for the newest explicit update on a "
            "current-state query. The adjustment remains subject to the relevance "
            "floor and is recorded in score provenance. Set to 1.0 to disable."
        ),
    )
    # Stored receipts, ranking profiles and evaluations omit a weighted
    # fusion, so a missing value must always mean "weighted". To make rank
    # fusion a product default, change PRMEConfig.scoring's default instead of
    # this field's default.
    fusion: Literal["weighted", "rrf"] = Field(
        default="weighted",
        exclude_if=lambda value: value == "weighted",
        description=(
            "How candidate signals are combined. 'weighted' is the default "
            "weighted sum above (score formula version 1). 'rrf' is opt-in "
            "reciprocal rank fusion of each candidate's semantic and lexical "
            "ranks within the candidate pool (formula version 2), scaled so a "
            "candidate ranked first on both scores 1.0. Epistemic, node-type "
            "and temporal adjustments then apply as multipliers relative to "
            "the pool's largest value, so an adjustment every candidate shares "
            "is 1.0. Graph proximity, salience and confidence are not used, "
            "recency is used only through the opt-in rrf_recency_boost, and "
            "the query-specific weight shifts and learned ranking multipliers "
            "are not used. Scores are rank-based, so min_score compares against "
            "each result's semantic_relevance, the semantic cosine similarity of "
            "the memory behind it, instead of the fused score. A floor tuned on "
            "weighted scores does not carry over, and a low-cosine exact keyword "
            "match is filtered out when a floor is set. When the vector path "
            "fails or detects an embedding mismatch and no result has a cosine, "
            "min_score is skipped and the response metadata sets "
            "min_score_skipped."
        ),
    )
    rrf_k: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        exclude_if=lambda value: value is None,
        description=(
            "[HYPOTHESIS] Rank constant k in 1 / (k + rank) for fusion='rrf'; "
            f"{DEFAULT_RRF_K} when unset. Larger values flatten the difference "
            "between top ranks. Unset for weighted fusion, which ignores a "
            "supplied value with a warning."
        ),
    )
    # Omitted when unset, so configurations, receipts and version ids that do
    # not use them keep the bytes they had before they existed (issue #168).
    rrf_recency_boost: float | None = Field(
        default=None,
        gt=0,
        le=4,
        exclude_if=lambda value: value is None,
        description=(
            "[HYPOTHESIS] Opt-in, fusion='rrf' only: on current-state questions, "
            "multiply each fused score by 1 + rrf_recency_boost x its recency, "
            "relative to the pool's largest value, so the newer of two "
            "conflicting memories can rank first. Recency is computed as the "
            "weighted formula computes it on those questions with its default "
            "weights: exp(-lambda x days before the newest candidate, by event "
            "time, else the time it was stored), with "
            "lambda at least 0.05, doubled up to 1.0 for update wording such as "
            "'switched to' or 'no longer'. A factor "
            "is at most 1.0, so no candidate rises above first place on both "
            "channels. Unset, rank fusion ignores recency. Weighted fusion "
            "ignores a supplied value with a warning."
        ),
    )
    rrf_tie_break: Literal["event_time"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Opt-in, fusion='rrf' only: 'event_time' puts candidates whose "
            "fused scores are equal in order of event time (else the time they "
            "were stored), newest first, instead of path count and node ID "
            "order. Older candidates lose less than 1e-11, which is below the "
            "1e-10 step of the rounded fused scores, so fused scores that "
            "differ keep their order; later adjustments with other "
            "coefficients can reorder scores that differ by less than 1e-11. "
            "Memories stated at the same time still fall back to path count and "
            "node ID, as do all equal scores when it is unset. Weighted fusion "
            "ignores a supplied value with a warning."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def rank_fusion_settings_only_for_rank_fusion(cls, value: Any) -> Any:
        """Give rank fusion its default constant and drop its settings from weighted scoring.

        Dropping stray settings keeps one serialized form per configuration
        and lets an operator turn rank fusion off by removing only the fusion
        setting.
        """
        if not isinstance(value, dict):
            return value
        rank_fused = value.get("fusion", "weighted") == "rrf"
        if rank_fused and value.get("rrf_k") is None:
            return {**value, "rrf_k": DEFAULT_RRF_K}
        stray = [name for name in RANK_FUSION_ONLY_SETTINGS if value.get(name) is not None]
        if not rank_fused and stray:
            one = len(stray) == 1
            warnings.warn(
                f"ScoringWeights {', '.join(stray)} {'applies' if one else 'apply'} only when "
                f"fusion is 'rrf' and {'is' if one else 'are'} ignored.",
                UserWarning,
                stacklevel=2,
            )
            return {key: item for key, item in value.items() if key not in stray}
        return value

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringWeights:
        """Verify additive weights sum to 1.0 (within tolerance).

        Epistemic (multiplicative) and paths (tiebreaker) are excluded
        from the sum constraint. ``rrf_k`` is set exactly when fusion is
        ``"rrf"``, and the other rank fusion settings only then.
        """
        # Both checks back up the before-validator, which drops stray settings
        # from any dict input; only input that skips it can reach them.
        if (self.fusion == "rrf") != (self.rrf_k is not None):
            raise ValueError("rrf_k is set exactly when fusion is 'rrf'")
        if self.fusion != "rrf" and self.rank_fusion_opt_ins:
            raise ValueError(
                f"{', '.join(self.rank_fusion_opt_ins)} apply only when fusion is 'rrf'"
            )
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
    def rank_fusion_opt_ins(self) -> dict[str, Any]:
        """The opt-in rank fusion terms that are set, by name; empty when none is."""
        return {
            name: value for name in RANK_FUSION_OPT_INS
            if (value := getattr(self, name)) is not None
        }

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
            f"{self.temporal_boost}:{self.relevance_floor}:"
            f"{self.current_update_multiplier}:{ntb_sorted}"
        )
        if self.fusion != "weighted":
            # Weighted configurations keep the version they had before
            # rank fusion existed, and rank fusion without the settings below
            # keeps the version it had before them.
            payload += f":{self.fusion}:{self.rrf_k}"
            if self.rrf_recency_boost is not None:
                payload += f":recency={self.rrf_recency_boost}"
            if self.rrf_tie_break is not None:
                payload += f":tie_break={self.rrf_tie_break}"
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
    context_format: Literal["auditable", "compact", "reader"] = Field(
        default="auditable",
        description=(
            "Render packed records as self-describing JSON objects ('auditable'), "
            "schema-declared JSON arrays with short bundle-local references "
            "('compact'), or one reader-facing line per record ('reader'). "
            "Auditable and compact retain type, scope, epistemic state, "
            "lifecycle, source provenance and temporal fields in the context. "
            "Reader shows only the event date or explicit validity window, tags "
            "for non-default epistemic and lifecycle states, and the text; the "
            "complete record stays in the bundle sections and the receipt. All "
            "three formats keep the complete selected representation text."
        ),
    )
    context_citations: bool = Field(
        default=False,
        description=(
            "Prefix each reader-format record with a short bundle-local "
            "reference such as [m3] and fill MemoryBundle.context_references. "
            "Applies only to context_format='reader': compact records always "
            "carry references and auditable records carry full node IDs."
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
        description=(
            "Minimum representation level for packed candidates. The "
            "'key_value' and 'reference' fallbacks render a node ID and type "
            "(key_value adds a confidence) but no memory text. A text-bearing "
            "level ('structured', 'prose' or 'full', which pack the same "
            "records) keeps them out of the context in every format and also "
            "excludes records whose text is blank; excluded records are listed "
            "in MemoryBundle.excluded_ids. The reader format always behaves "
            "this way."
        ),
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
            "below the node that caused their inclusion. Under rank fusion, "
            "session_context_rank_fusion_score_decay replaces it when set."
        ),
    )
    # Omitted when unset, so configurations, receipts and benchmark variants
    # that do not use it keep the bytes and identity they had before it existed.
    session_context_rank_fusion_score_decay: float | None = Field(
        default=None,
        gt=0,
        le=1,
        exclude_if=lambda value: value is None,
        description=(
            "Opt-in score multiplier for session-context expanded nodes whose "
            "trigger was scored by rank fusion (ScoringWeights.fusion='rrf'), "
            "in place of session_context_score_decay. Unset, rank fusion uses "
            "session_context_score_decay. Fused scores are compressed: with "
            "rrf_k=60, 0.85 of a first-place score outranks every candidate "
            "from about twelfth place down on both channels, so neighbors can "
            "crowd primary evidence out of the context. 0.6 places a "
            "first-place trigger's neighbors below about the fortieth "
            "candidate ranked on both channels, though still above any "
            "candidate found by one channel alone, which scores at most 0.5. "
            "[HYPOTHESIS]"
        ),
    )
    episode_context_top_k: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of session-scoped episodes to route with deterministic BM25 "
            "before packing. Zero disables two-stage episode reconstruction. "
            "[HYPOTHESIS]"
        ),
    )
    episode_context_local_k: int = Field(
        default=8,
        ge=1,
        description=(
            "Maximum query-relevant records promoted from each routed episode. "
            "[HYPOTHESIS]"
        ),
    )
    episode_context_score_decay: float = Field(
        default=0.95,
        gt=0,
        le=1,
        description=(
            "Score inherited by routed episode records from the strongest record "
            "in that episode. [HYPOTHESIS]"
        ),
    )
    evidence_projection_top_k: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of top-ranked exact evidence groups whose derived candidates "
            "are replaced by their active direct source nodes. Zero disables "
            "source projection. [HYPOTHESIS]"
        ),
    )
    evidence_projection_max_sources: int = Field(
        default=1,
        ge=1,
        description=(
            "Maximum direct source nodes retained for each projected evidence "
            "group. [HYPOTHESIS]"
        ),
    )
    evidence_projection_score_decay: float = Field(
        default=1.0,
        gt=0,
        le=1,
        description=(
            "Score inherited by a direct source from the strongest derived "
            "candidate in its exact evidence group. [HYPOTHESIS]"
        ),
    )
    evidence_augmentation_top_k: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of top-ranked exact evidence groups whose active direct "
            "sources are added beside derived candidates. Zero disables dual "
            "representation. [HYPOTHESIS]"
        ),
    )
    evidence_augmentation_max_sources: int = Field(
        default=1,
        ge=1,
        description=(
            "Maximum direct sources added for each augmented evidence group. "
            "[HYPOTHESIS]"
        ),
    )
    evidence_augmentation_score_decay: float = Field(
        default=0.99,
        gt=0,
        le=1,
        description=(
            "Score inherited by an augmented direct source from the strongest "
            "derived candidate in its exact evidence group. [HYPOTHESIS]"
        ),
    )
    evidence_augmentation_anchor_policy: Literal["all", "non_entity"] = Field(
        default="all",
        description=(
            "Which ranked candidates may route direct evidence. 'all' preserves "
            "the original policy. 'non_entity' prevents entity-name matches from "
            "treating an entire source passage as query support while allowing "
            "later non-entity groups to fill the top-k quota. [HYPOTHESIS]"
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

    @model_validator(mode="after")
    def exclusive_evidence_representation(self) -> PackingConfig:
        if (
            self.evidence_projection_top_k > 0
            and self.evidence_augmentation_top_k > 0
        ):
            raise ValueError(
                "Evidence projection and augmentation cannot both be enabled"
            )
        return self

    @model_validator(mode="after")
    def citations_require_reader_format(self) -> PackingConfig:
        if self.context_citations and self.context_format != "reader":
            raise ValueError(
                "context_citations applies only to context_format='reader'; "
                "compact records always carry references and auditable records "
                "carry full node IDs"
            )
        return self


# Module-level default instances.
DEFAULT_SCORING_WEIGHTS = ScoringWeights()
DEFAULT_PACKING_CONFIG = PackingConfig()
