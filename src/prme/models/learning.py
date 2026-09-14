"""Versioned inputs and inspectable results for scoped retrieval learning."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from prme.retrieval.config import ScoringWeights
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


class FullRetrievalTrial(BaseModel):
    """One paired baseline/candidate retrieval with complete positive labels."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    group_id: str = Field(min_length=1, max_length=512)
    baseline_request_id: UUID
    candidate_request_id: UUID
    relevant_node_ids: tuple[UUID, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def valid_identity(self):
        if not self.group_id.strip():
            raise ValueError("group_id cannot be blank")
        if self.baseline_request_id == self.candidate_request_id:
            raise ValueError("A full-retrieval trial requires two different requests")
        if len(self.relevant_node_ids) != len(set(self.relevant_node_ids)):
            raise ValueError("relevant_node_ids must be unique")
        return self


class FullRetrievalEvaluationConfig(BaseModel):
    """Conservative acceptance gates for a final full-pipeline holdout."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    seed: int = 42
    k: int = Field(default=10, ge=1, le=1000)
    min_query_groups: int = Field(default=20, ge=2, le=100000)
    bootstrap_samples: int = Field(default=2000, ge=100, le=10000)
    min_ndcg_gain: float = Field(default=.01, ge=0, le=1, allow_inf_nan=False)
    max_regression_fraction: float = Field(default=.1, ge=0, le=1, allow_inf_nan=False)


class FullRetrievalQueryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    group_id: str
    baseline_request_ids: tuple[UUID, ...]
    candidate_request_ids: tuple[UUID, ...]
    baseline_recall: float
    candidate_recall: float
    baseline_ndcg: float
    candidate_ndcg: float
    baseline_mrr: float
    candidate_mrr: float


class FullRetrievalEvaluation(BaseModel):
    """Auditable final holdout over separately executed retrieval pipelines."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    algorithm: Literal["paired_full_retrieval_v1"] = "paired_full_retrieval_v1"
    user_id: str = Field(min_length=1)
    scopes: tuple[Scope, ...] | None
    surface: Literal["results"] = "results"
    config: FullRetrievalEvaluationConfig
    proposal_input_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_checksums: dict[UUID, str]
    feature_identity: dict[str, JsonValue]
    feature_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_scoring: ScoringWeights
    baseline_multipliers: RankingMultipliers
    candidate_multipliers: RankingMultipliers
    decision: Literal["insufficient_data", "no_improvement", "improved_full_retrieval"]
    baseline_recall: float
    candidate_recall: float
    recall_gain: float
    baseline_ndcg: float
    candidate_ndcg: float
    ndcg_gain: float
    ndcg_gain_interval: tuple[float, float] | None = None
    baseline_mrr: float
    candidate_mrr: float
    mrr_gain: float
    regression_fraction: float
    coverage: dict[str, int]
    queries: tuple[FullRetrievalQueryResult, ...]
    limitations: tuple[str, ...] = (
        "Relevant node identities are supplied by the evaluator and are not inferred from exposure.",
        "The memory artifact digest and fixed-request checks bind the trial; callers must prevent writes and maintenance during paired execution.",
        "A positive retrieval holdout does not establish answer quality or transfer to another feature identity.",
    )
