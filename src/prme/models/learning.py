"""Versioned inputs and inspectable results for offline relevance learning."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from prme.types import Scope


class RankingMultipliers(BaseModel):
    """Positive adjustments to the six applied additive weights.

    Normalize their weighted sum before scoring. Unity is the existing scorer.
    These do not change decay clocks, epistemic multipliers or relevance caps.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    semantic: float = Field(default=1, ge=.25, le=4, allow_inf_nan=False)
    lexical: float = Field(default=1, ge=.25, le=4, allow_inf_nan=False)
    graph: float = Field(default=1, ge=.25, le=4, allow_inf_nan=False)
    recency: float = Field(default=1, ge=.25, le=4, allow_inf_nan=False)
    salience: float = Field(default=1, ge=.25, le=4, allow_inf_nan=False)
    confidence: float = Field(default=1, ge=.25, le=4, allow_inf_nan=False)


class LearningConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    seed: int = 42
    validation_fraction: float = Field(default=.3, gt=0, lt=1, allow_inf_nan=False)
    min_training_queries: int = Field(default=20, ge=2)
    min_validation_queries: int = Field(default=20, ge=2)
    regularization: float = Field(default=.01, gt=0, allow_inf_nan=False)
    margin_scale: float = Field(default=5, gt=0, le=100, allow_inf_nan=False)
    passes_per_step: int = Field(default=4, ge=1, le=20)
    ndcg_k: int = Field(default=10, ge=1, le=1000)
    bootstrap_samples: int = Field(default=2000, ge=100, le=10000)
    min_ndcg_gain: float = Field(default=.01, ge=0, le=1, allow_inf_nan=False)
    max_pairs_per_request: int = Field(default=10000, ge=1, le=1000000)


class QueryLearningResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    group_id: str
    split: Literal["train", "validation"]
    request_ids: tuple[UUID, ...]
    baseline_pairwise_accuracy: float
    candidate_pairwise_accuracy: float
    baseline_judged_ndcg: float
    candidate_judged_ndcg: float


class LearningEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    algorithm: Literal["pairwise_weight_multipliers_v1"] = "pairwise_weight_multipliers_v1"
    user_id: str
    scopes: tuple[Scope, ...] | None
    surface: Literal["results", "context"]
    config: LearningConfig
    input_checksum: str
    feedback_ids: tuple[UUID, ...]
    receipt_checksums: dict[UUID, str]
    multipliers: RankingMultipliers
    decision: Literal["insufficient_data", "no_improvement", "improved_on_observed_candidates"]
    training_loss_before: float | None = None
    training_loss_after: float | None = None
    validation_ndcg_gain: float | None = None
    validation_pairwise_gain: float | None = None
    validation_gain_interval: tuple[float, float] | None = None
    coverage: dict[str, int]
    exclusions: dict[str, int]
    queries: tuple[QueryLearningResult, ...] = ()
    limitations: tuple[str, ...] = (
        "Fixed observed candidates, features, reranker membership and session lineage; not full retrieval replay.",
        "Only explicitly judged candidates enter metrics; missing labels are not negatives.",
        "Repeated normalized queries form one group; paraphrases need caller-supplied group identities.",
        "Offline improvement is not authorization or sufficient evidence for profile activation.",
    )
