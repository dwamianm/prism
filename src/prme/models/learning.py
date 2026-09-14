"""Versioned inputs and inspectable results for scoped retrieval learning."""
import hashlib
import json
import math
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

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
    baseline_pairwise_accuracy: float = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_pairwise_accuracy: float = Field(ge=0, le=1, allow_inf_nan=False)
    baseline_judged_ndcg: float = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_judged_ndcg: float = Field(ge=0, le=1, allow_inf_nan=False)


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

    @model_validator(mode="after")
    def valid_result(self):
        if not self.user_id.strip():
            raise ValueError("Learning evaluation requires an owner")
        normalized_scopes = (
            tuple(sorted(set(self.scopes), key=lambda scope: scope.value))
            if self.scopes is not None else None
        )
        if self.scopes != normalized_scopes:
            raise ValueError("Learning evaluation scopes must be unique and sorted")
        if len(self.feedback_ids) != len(set(self.feedback_ids)):
            raise ValueError("Learning evaluation feedback identities must be unique")
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in self.receipt_checksums.values()
        ):
            raise ValueError("Learning evaluation receipt checksums must be SHA-256 digests")
        expected_coverage_keys = {
            "feedback_records", "requests_with_pairs", "training_queries",
            "validation_queries", "explicit_pairs",
        }
        if set(self.coverage) != expected_coverage_keys or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in self.coverage.values()
        ):
            raise ValueError("Learning evaluation coverage is invalid")
        if self.coverage["feedback_records"] != len(self.feedback_ids):
            raise ValueError("Learning evaluation feedback coverage is inconsistent")
        if self.decision == "insufficient_data":
            if (
                self.multipliers != RankingMultipliers()
                or self.queries
                or any(value is not None for value in (
                    self.training_loss_before, self.training_loss_after,
                    self.validation_ndcg_gain, self.validation_pairwise_gain,
                    self.validation_gain_interval,
                ))
            ):
                raise ValueError("Insufficient learning evaluation is inconsistent")
            return self
        if not self.queries:
            raise ValueError("Evaluated learning result requires query metrics")
        if tuple(sorted(self.queries, key=lambda item: item.group_id)) != self.queries:
            raise ValueError("Learning query results must be sorted")
        if len({item.group_id for item in self.queries}) != len(self.queries):
            raise ValueError("Learning query group identities must be unique")
        request_ids = [request_id for item in self.queries for request_id in item.request_ids]
        if (
            any(not item.request_ids for item in self.queries)
            or len(request_ids) != len(set(request_ids))
            or not set(request_ids) <= set(self.receipt_checksums)
        ):
            raise ValueError("Learning query receipt identities are invalid")
        train_count = sum(item.split == "train" for item in self.queries)
        validation = [item for item in self.queries if item.split == "validation"]
        if self.coverage["requests_with_pairs"] != len(request_ids) or self.coverage[
            "training_queries"
        ] != train_count or self.coverage["validation_queries"] != len(validation):
            raise ValueError("Learning query coverage is inconsistent")
        if (
            train_count < self.config.min_training_queries
            or len(validation) < self.config.min_validation_queries
            or any(value is None for value in (
                self.training_loss_before, self.training_loss_after,
                self.validation_ndcg_gain, self.validation_pairwise_gain,
                self.validation_gain_interval,
            ))
        ):
            raise ValueError("Learning evaluation lacks required validation evidence")
        assert self.training_loss_before is not None and self.training_loss_after is not None
        if (
            not math.isfinite(self.training_loss_before)
            or not math.isfinite(self.training_loss_after)
            or self.training_loss_before < 0
            or self.training_loss_after < 0
            or self.training_loss_after > self.training_loss_before + 1e-12
        ):
            raise ValueError("Learning training losses are invalid")
        ndcg_diffs = [
            item.candidate_judged_ndcg - item.baseline_judged_ndcg
            for item in validation
        ]
        pairwise_diffs = [
            item.candidate_pairwise_accuracy - item.baseline_pairwise_accuracy
            for item in validation
        ]
        ndcg_gain = sum(ndcg_diffs) / len(ndcg_diffs)
        pairwise_gain = sum(pairwise_diffs) / len(pairwise_diffs)
        if (
            not math.isclose(self.validation_ndcg_gain, ndcg_gain, rel_tol=0, abs_tol=1e-12)
            or not math.isclose(
                self.validation_pairwise_gain, pairwise_gain, rel_tol=0, abs_tol=1e-12,
            )
        ):
            raise ValueError("Learning validation aggregates are inconsistent")
        assert self.validation_gain_interval is not None
        interval = self.validation_gain_interval
        if (
            any(not math.isfinite(value) or not -1 <= value <= 1 for value in interval)
            or interval[0] > interval[1]
        ):
            raise ValueError("Learning validation interval is invalid")
        improved = (
            ndcg_gain >= self.config.min_ndcg_gain
            and interval[0] > 0
            and pairwise_gain >= -1e-12
        )
        expected_decision = "improved_on_observed_candidates" if improved else "no_improvement"
        if self.decision != expected_decision:
            raise ValueError("Learning decision does not match its validation evidence")
        return self


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
    baseline_recall: float = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_recall: float = Field(ge=0, le=1, allow_inf_nan=False)
    baseline_ndcg: float = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_ndcg: float = Field(ge=0, le=1, allow_inf_nan=False)
    baseline_mrr: float = Field(ge=0, le=1, allow_inf_nan=False)
    candidate_mrr: float = Field(ge=0, le=1, allow_inf_nan=False)


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
    trials: tuple[FullRetrievalTrial, ...]
    queries: tuple[FullRetrievalQueryResult, ...]
    limitations: tuple[str, ...] = (
        "Relevant node identities are supplied by the evaluator and are not inferred from exposure.",
        "The memory artifact digest and fixed-request checks bind the trial; callers must prevent writes and maintenance during paired execution.",
        "A positive retrieval holdout does not establish answer quality or transfer to another feature identity.",
    )

    @model_validator(mode="after")
    def valid_result(self):
        if not self.trials or not self.queries:
            raise ValueError("Full retrieval evaluation requires trials and query results")
        normalized_scopes = (
            tuple(sorted(set(self.scopes), key=lambda scope: scope.value))
            if self.scopes is not None else None
        )
        if not self.user_id.strip() or self.scopes != normalized_scopes:
            raise ValueError("Full retrieval evaluation owner/scopes are invalid")
        if tuple(sorted(
            self.trials, key=lambda item: (item.group_id, str(item.baseline_request_id))
        )) != self.trials or tuple(sorted(
            self.queries, key=lambda item: item.group_id
        )) != self.queries:
            raise ValueError("Full retrieval evidence must be sorted")
        if len({item.group_id for item in self.queries}) != len(self.queries):
            raise ValueError("Full retrieval query group identities must be unique")
        feature_hash = hashlib.sha256(json.dumps(
            self.feature_identity, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode()).hexdigest()
        if feature_hash != self.feature_identity_sha256:
            raise ValueError("Full retrieval feature identity checksum mismatch")
        query_ids = [
            request_id
            for query in self.queries
            for request_ids in (query.baseline_request_ids, query.candidate_request_ids)
            for request_id in request_ids
        ]
        trial_ids = [
            request_id
            for trial in self.trials
            for request_id in (trial.baseline_request_id, trial.candidate_request_id)
        ]
        if (
            len(query_ids) != len(set(query_ids))
            or len(trial_ids) != len(set(trial_ids))
            or set(query_ids) != set(trial_ids)
        ):
            raise ValueError("Full retrieval query and trial receipt identities differ")
        grouped_trials: dict[str, list[FullRetrievalTrial]] = {}
        for trial in self.trials:
            grouped_trials.setdefault(trial.group_id, []).append(trial)
        if set(grouped_trials) != {query.group_id for query in self.queries}:
            raise ValueError("Full retrieval trial and query groups differ")
        for query in self.queries:
            grouped = grouped_trials[query.group_id]
            if query.baseline_request_ids != tuple(
                item.baseline_request_id for item in grouped
            ) or query.candidate_request_ids != tuple(
                item.candidate_request_id for item in grouped
            ):
                raise ValueError("Full retrieval query groups do not match their trials")
        if set(self.receipt_checksums) != set(trial_ids):
            raise ValueError("Full retrieval receipt checksums do not cover its trials")
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in self.receipt_checksums.values()
        ):
            raise ValueError("Full retrieval receipt checksums must be SHA-256 digests")
        if self.coverage != {
            "trials": len(self.trials),
            "query_groups": len(self.queries),
            "relevant_nodes": sum(len(trial.relevant_node_ids) for trial in self.trials),
        }:
            raise ValueError("Full retrieval coverage does not match its evidence")
        expected = {
            "baseline_recall": sum(item.baseline_recall for item in self.queries) / len(self.queries),
            "candidate_recall": sum(item.candidate_recall for item in self.queries) / len(self.queries),
            "baseline_ndcg": sum(item.baseline_ndcg for item in self.queries) / len(self.queries),
            "candidate_ndcg": sum(item.candidate_ndcg for item in self.queries) / len(self.queries),
            "baseline_mrr": sum(item.baseline_mrr for item in self.queries) / len(self.queries),
            "candidate_mrr": sum(item.candidate_mrr for item in self.queries) / len(self.queries),
        }
        if any(
            not math.isclose(getattr(self, key), value, rel_tol=0, abs_tol=1e-12)
            for key, value in expected.items()
        ):
            raise ValueError("Full retrieval aggregate metrics do not match query results")
        if not math.isclose(
            self.recall_gain, self.candidate_recall - self.baseline_recall,
            rel_tol=0, abs_tol=1e-12,
        ):
            raise ValueError("Full retrieval recall gain is inconsistent")
        if not math.isclose(
            self.ndcg_gain, self.candidate_ndcg - self.baseline_ndcg,
            rel_tol=0, abs_tol=1e-12,
        ):
            raise ValueError("Full retrieval NDCG gain is inconsistent")
        if not math.isclose(
            self.mrr_gain, self.candidate_mrr - self.baseline_mrr,
            rel_tol=0, abs_tol=1e-12,
        ):
            raise ValueError("Full retrieval MRR gain is inconsistent")
        regressions = sum(
            item.candidate_ndcg < item.baseline_ndcg - 1e-12 for item in self.queries
        ) / len(self.queries)
        if not math.isclose(self.regression_fraction, regressions, rel_tol=0, abs_tol=1e-12):
            raise ValueError("Full retrieval regression fraction is inconsistent")
        if len(self.queries) < self.config.min_query_groups:
            if self.ndcg_gain_interval is not None or self.decision != "insufficient_data":
                raise ValueError("Insufficient full retrieval decision is inconsistent")
        else:
            if self.ndcg_gain_interval is None:
                raise ValueError("Full retrieval NDCG interval is missing")
            interval = self.ndcg_gain_interval
            if (
                any(not math.isfinite(value) or not -1 <= value <= 1 for value in interval)
                or interval[0] > interval[1]
            ):
                raise ValueError("Full retrieval NDCG interval is invalid")
            improved = (
                self.ndcg_gain >= self.config.min_ndcg_gain
                and interval[0] > 0
                and self.candidate_recall >= self.baseline_recall - 1e-12
                and self.regression_fraction <= self.config.max_regression_fraction
            )
            expected_decision = "improved_full_retrieval" if improved else "no_improvement"
            if self.decision != expected_decision:
                raise ValueError("Full retrieval decision does not match its evidence")
        input_value = {
            "user_id": self.user_id,
            "scopes": [scope.value for scope in self.scopes] if self.scopes is not None else None,
            "proposal_input_checksum": self.proposal_input_checksum,
            "memory_artifact_sha256": self.memory_artifact_sha256,
            "baseline_multipliers": self.baseline_multipliers.model_dump(mode="json"),
            "candidate_multipliers": self.candidate_multipliers.model_dump(mode="json"),
            "base_scoring": self.base_scoring.model_dump(mode="json"),
            "config": self.config.model_dump(mode="json"),
            "trials": [trial.model_dump(mode="json") for trial in self.trials],
            "receipt_checksums": {
                str(key): value for key, value in sorted(
                    self.receipt_checksums.items(), key=lambda item: str(item[0])
                )
            },
        }
        expected_input_checksum = hashlib.sha256(json.dumps(
            input_value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode()).hexdigest()
        if self.input_checksum != expected_input_checksum:
            raise ValueError("Full retrieval input checksum mismatch")
        return self


class RankingProfileApplication(BaseModel):
    """Compact immutable scoring input kept on an active scope pointer."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    profile_id: UUID
    multipliers: RankingMultipliers
    feature_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_scoring: ScoringWeights


class RankingProfile(BaseModel):
    """Immutable scoped profile backed by both proposal and final holdout gates."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    profile_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(min_length=1)
    scopes: tuple[Scope, ...] | None
    surface: Literal["results"] = "results"
    created_at: AwareDatetime
    baseline_profile_id: UUID | None = None
    multipliers: RankingMultipliers
    proposal: LearningEvaluation
    holdout: FullRetrievalEvaluation

    @model_validator(mode="after")
    def valid_evidence(self):
        if not self.user_id.strip():
            raise ValueError("Ranking profile owner cannot be blank")
        expected_scopes = (
            tuple(sorted(set(self.scopes), key=lambda scope: scope.value))
            if self.scopes is not None else None
        )
        if self.scopes != expected_scopes:
            raise ValueError("Ranking profile scopes must be unique and sorted")
        if self.proposal.user_id != self.user_id or self.holdout.user_id != self.user_id:
            raise ValueError("Ranking profile evidence must belong to its owner")
        if self.proposal.scopes != self.scopes or self.holdout.scopes != self.scopes:
            raise ValueError("Ranking profile evidence must match its scopes")
        if self.proposal.surface != self.surface or self.holdout.surface != self.surface:
            raise ValueError("Ranking profile evidence must match its surface")
        if self.proposal.decision != "improved_on_observed_candidates":
            raise ValueError("Ranking profile requires a positive proposal evaluation")
        if self.holdout.decision != "improved_full_retrieval":
            raise ValueError("Ranking profile requires a positive full-retrieval holdout")
        if self.proposal.input_checksum != self.holdout.proposal_input_checksum:
            raise ValueError("Ranking profile holdout does not identify its proposal")
        if self.multipliers != self.proposal.multipliers or self.multipliers != self.holdout.candidate_multipliers:
            raise ValueError("Ranking profile evidence does not agree on multipliers")
        if self.baseline_profile_id is None and self.holdout.baseline_multipliers != RankingMultipliers():
            raise ValueError("A non-unity holdout baseline requires baseline_profile_id")
        return self

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def matches_evidence(self, other: "RankingProfile") -> bool:
        fields = set(type(self).model_fields) - {"created_at"}
        return all(getattr(self, field) == getattr(other, field) for field in fields)

    @property
    def application(self) -> RankingProfileApplication:
        return RankingProfileApplication(
            profile_id=self.profile_id,
            multipliers=self.multipliers,
            feature_identity_sha256=self.holdout.feature_identity_sha256,
            base_scoring=self.holdout.base_scoring,
        )


class RankingProfileState(BaseModel):
    """Append-only activation pointer change for one owner and exact scope set."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    change_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(min_length=1)
    scopes: tuple[Scope, ...] | None
    action: Literal["activate", "deactivate", "rollback"]
    previous_profile_id: UUID | None
    active_profile_id: UUID | None
    application: RankingProfileApplication | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    changed_at: AwareDatetime

    @model_validator(mode="after")
    def valid_change(self):
        expected_scopes = (
            tuple(sorted(set(self.scopes), key=lambda scope: scope.value))
            if self.scopes is not None else None
        )
        if not self.user_id.strip() or self.scopes != expected_scopes:
            raise ValueError("Ranking profile state owner/scopes are invalid")
        if self.action == "activate" and self.active_profile_id is None:
            raise ValueError("Activation requires a profile")
        if self.action == "deactivate" and self.active_profile_id is not None:
            raise ValueError("Deactivation must restore baseline scoring")
        if self.application is not None and (
            self.active_profile_id is None
            or self.application.profile_id != self.active_profile_id
        ):
            raise ValueError("Ranking profile state application is inconsistent")
        if self.previous_profile_id == self.active_profile_id:
            raise ValueError("Ranking profile state changes cannot be no-ops")
        return self

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class RankingProfileStatus(BaseModel):
    """Inspectable profile plus whether it owns the current scope pointer."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    profile: RankingProfile
    active: bool
    latest_change: RankingProfileState | None = None
