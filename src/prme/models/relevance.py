"""Immutable retrieval snapshots and explicit, scoped relevance judgments."""
from datetime import datetime
import hashlib
import math
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import (AwareDatetime, BaseModel, ConfigDict, Field, StrictBool,
                      SerializerFunctionWrapHandler, model_serializer, model_validator)

from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.models import ScoreProvenance, ScoreTrace
from prme.types import RepresentationLevel, RetrievalMode, Scope


class ReceiptCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_id: UUID
    scope: Scope
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score: float = Field(allow_inf_nan=False)
    trace: ScoreTrace | None
    reranker_score: float | None = Field(default=None, allow_inf_nan=False)
    representation: RepresentationLevel | None = None
    token_cost: int = Field(default=0, ge=0)
    in_context: bool = False
    has_content: bool = False

    @model_validator(mode="after")
    def valid_trace_and_exposure(self):
        if self.trace is not None and not all(math.isfinite(v) for v in self.trace.model_dump().values()):
            raise ValueError("Receipt score components must be finite")
        if self.has_content and (not self.in_context or self.representation not in {
            RepresentationLevel.FULL, RepresentationLevel.PROSE, RepresentationLevel.STRUCTURED}):
            raise ValueError("Content credit requires a content-bearing context entry")
        return self


RankingPolicy = Literal["score_path_id", "reranked_prefix", "score_id"]


class RetrievalReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] = 1
    request_id: UUID
    user_id: str = Field(min_length=1)
    query: str
    reference_time: AwareDatetime
    scopes: tuple[Scope, ...] | None
    scoring: ScoringWeights
    packing: PackingConfig
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    min_score: float | None = None
    result_limit: int | None = None
    retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT
    time_from: AwareDatetime | None = None
    time_to: AwareDatetime | None = None
    candidates: tuple[ReceiptCandidate, ...]
    score_provenance: dict[UUID, ScoreProvenance] | None = None
    ranking_policy: RankingPolicy | None = None
    execution: RetrievalExecution | None = None

    @model_validator(mode="before")
    @classmethod
    def historical_packing_policy(cls, value):
        if isinstance(value, dict):
            version = value.get("schema_version", 1)
            updates_to_value: dict[str, Any] = {}

            scoring = value.get("scoring")
            provenance = value.get("score_provenance")
            if version < 9:
                if isinstance(scoring, dict) and "current_update_multiplier" not in scoring:
                    updates_to_value["scoring"] = {
                        **scoring,
                        "current_update_multiplier": 1.0,
                    }
                if isinstance(provenance, dict):
                    updated_provenance = {}
                    changed = False
                    for node_id, item in provenance.items():
                        if (
                            isinstance(item, dict)
                            and isinstance(item.get("weights"), dict)
                            and "current_update_multiplier" not in item["weights"]
                        ):
                            updated_provenance[node_id] = {
                                **item,
                                "weights": {
                                    **item["weights"],
                                    "current_update_multiplier": 1.0,
                                },
                            }
                            changed = True
                        else:
                            updated_provenance[node_id] = item
                    if changed:
                        updates_to_value["score_provenance"] = updated_provenance
            else:
                if not (
                    isinstance(scoring, ScoringWeights)
                    or (
                        isinstance(scoring, dict)
                        and "current_update_multiplier" in scoring
                    )
                ):
                    raise ValueError(
                        "Version 9 requires an explicit current-update multiplier"
                    )
                if isinstance(provenance, dict) and any(
                    not (
                        isinstance(item, ScoreProvenance)
                        or (
                            isinstance(item, dict)
                            and (
                                isinstance(item.get("weights"), ScoringWeights)
                                or (
                                    isinstance(item.get("weights"), dict)
                                    and "current_update_multiplier" in item["weights"]
                                )
                            )
                        )
                    )
                    for item in provenance.values()
                ):
                    raise ValueError(
                        "Version 9 requires applied current-update multipliers"
                    )

            if not isinstance(value.get("packing"), dict):
                return {**value, **updates_to_value} if updates_to_value else value
            packing = value["packing"]
            updates = {}
            if "multipath_ordering" not in packing:
                if version in (1, 2, 3):
                    # Historical omission always means density, independently
                    # of any future application default. Do not mutate input.
                    updates["multipath_ordering"] = "density"
                if version in (4, 5, 6, 7):
                    raise ValueError("Versions 4 through 7 require an explicit packing ordering")
            if "context_guidance_mode" not in packing:
                if version in (1, 2, 3, 4, 5):
                    # Guidance did not exist in these schemas. Preserve that
                    # behavior even though the application default has changed.
                    updates["context_guidance_mode"] = "off"
                if version in (6, 7):
                    raise ValueError("Versions 6 and 7 require an explicit context guidance mode")
            if "context_format" not in packing:
                if version in (1, 2, 3, 4, 5, 6):
                    updates["context_format"] = "auditable"
                if version in (7, 8, 9, 10, 11, 12):
                    raise ValueError(
                        "Versions 7 through 12 require an explicit context format"
                    )
            episode_fields = (
                "episode_context_top_k",
                "episode_context_local_k",
                "episode_context_score_decay",
            )
            missing_episode_fields = [
                field for field in episode_fields if field not in packing
            ]
            if missing_episode_fields:
                if version in (1, 2, 3, 4, 5, 6, 7):
                    updates.update(
                        episode_context_top_k=0,
                        episode_context_local_k=8,
                        episode_context_score_decay=0.95,
                    )
                if version in (8, 9, 10, 11, 12):
                    raise ValueError(
                        "Versions 8 through 12 require explicit episode context settings"
                    )
            evidence_fields = (
                "evidence_projection_top_k",
                "evidence_projection_max_sources",
                "evidence_projection_score_decay",
            )
            missing_evidence_fields = [
                field for field in evidence_fields if field not in packing
            ]
            if missing_evidence_fields:
                if version < 10:
                    updates.update(
                        evidence_projection_top_k=0,
                        evidence_projection_max_sources=1,
                        evidence_projection_score_decay=1.0,
                    )
                else:
                    raise ValueError(
                        "Versions 10 through 12 require explicit evidence projection settings"
                    )
            augmentation_fields = (
                "evidence_augmentation_top_k",
                "evidence_augmentation_max_sources",
                "evidence_augmentation_score_decay",
            )
            missing_augmentation_fields = [
                field for field in augmentation_fields if field not in packing
            ]
            if missing_augmentation_fields:
                if version < 11:
                    updates.update(
                        evidence_augmentation_top_k=0,
                        evidence_augmentation_max_sources=1,
                        evidence_augmentation_score_decay=0.99,
                    )
                else:
                    raise ValueError(
                        "Versions 11 and 12 require explicit evidence augmentation settings"
                    )
            if "evidence_augmentation_anchor_policy" not in packing:
                if version < 12:
                    updates["evidence_augmentation_anchor_policy"] = "all"
                else:
                    raise ValueError(
                        "Version 12 requires an explicit evidence augmentation anchor policy"
                    )
            if updates:
                updates_to_value["packing"] = {**packing, **updates}
            if updates_to_value:
                return {**value, **updates_to_value}
        return value

    @model_serializer(mode="wrap")
    def serialize_version(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        # Each version's canonical JSON is its durable checksum input. Never
        # add later-generation defaults while reading an existing receipt.
        if self.schema_version == 1:
            data.pop("score_provenance", None)
            data.pop("ranking_policy", None)
        if self.schema_version < 3:
            data.pop("execution", None)
        if self.schema_version < 4 and isinstance(data.get("packing"), dict):
            data["packing"].pop("multipath_ordering", None)
        if self.schema_version < 6 and isinstance(data.get("packing"), dict):
            data["packing"].pop("context_guidance_mode", None)
        if self.schema_version < 7 and isinstance(data.get("packing"), dict):
            data["packing"].pop("context_format", None)
        if self.schema_version < 8 and isinstance(data.get("packing"), dict):
            data["packing"].pop("episode_context_top_k", None)
            data["packing"].pop("episode_context_local_k", None)
            data["packing"].pop("episode_context_score_decay", None)
        if self.schema_version < 9:
            if isinstance(data.get("scoring"), dict):
                data["scoring"].pop("current_update_multiplier", None)
            provenance = data.get("score_provenance")
            if isinstance(provenance, dict):
                for item in provenance.values():
                    if isinstance(item, dict) and isinstance(item.get("weights"), dict):
                        item["weights"].pop("current_update_multiplier", None)
        if self.schema_version < 10 and isinstance(data.get("packing"), dict):
            data["packing"].pop("evidence_projection_top_k", None)
            data["packing"].pop("evidence_projection_max_sources", None)
            data["packing"].pop("evidence_projection_score_decay", None)
        if self.schema_version < 11 and isinstance(data.get("packing"), dict):
            data["packing"].pop("evidence_augmentation_top_k", None)
            data["packing"].pop("evidence_augmentation_max_sources", None)
            data["packing"].pop("evidence_augmentation_score_decay", None)
        if self.schema_version < 12 and isinstance(data.get("packing"), dict):
            data["packing"].pop("evidence_augmentation_anchor_policy", None)
        return data

    @model_validator(mode="after")
    def unique_candidates(self):
        if (self.schema_version >= 3) != (self.execution is not None):
            raise ValueError("Versions 3 through 12 require an execution descriptor")
        if self.schema_version < 4 and self.packing.multipath_ordering != "density":
            raise ValueError("Legacy receipts support only density packing")
        if self.schema_version < 5 and self.packing.multipath_ordering == "balanced":
            raise ValueError("Balanced packing requires a version 5 receipt")
        if self.schema_version < 6 and self.packing.context_guidance_mode != "off":
            raise ValueError("Context guidance requires a version 6 receipt")
        if self.schema_version < 7 and self.packing.context_format != "auditable":
            raise ValueError("Compact context requires a version 7 receipt")
        if self.schema_version < 8 and self.packing.episode_context_top_k != 0:
            raise ValueError("Episode context requires a version 8 receipt")
        if self.schema_version < 10 and self.packing.evidence_projection_top_k != 0:
            raise ValueError("Evidence projection requires a version 10 receipt")
        if self.schema_version < 11 and self.packing.evidence_augmentation_top_k != 0:
            raise ValueError("Evidence augmentation requires a version 11 receipt")
        if (
            self.schema_version < 12
            and self.packing.evidence_augmentation_anchor_policy != "all"
        ):
            raise ValueError(
                "Selective evidence augmentation requires a version 12 receipt"
            )
        ids = [candidate.node_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Receipt candidate identities must be unique")
        if self.schema_version == 1:
            if self.score_provenance is not None or self.ranking_policy is not None:
                raise ValueError("Version 1 receipts cannot contain version 2 score provenance")
        else:
            if self.score_provenance is None or self.ranking_policy is None:
                raise ValueError("Replayable receipts require score provenance and ranking policy")
            if set(self.score_provenance) != set(ids):
                raise ValueError("Score provenance must cover exactly the returned candidates")
            for candidate in self.candidates:
                if self.score_provenance[candidate.node_id].replay_score() != candidate.score:
                    raise ValueError("Score provenance does not reproduce the returned score")
            if self.replay_ranking() != tuple(ids):
                raise ValueError("Score provenance does not reproduce the returned ranking")
            if self.schema_version < 9 and any(
                adjustment.kind == "current_update"
                for provenance in self.score_provenance.values()
                for adjustment in provenance.adjustments
            ):
                raise ValueError("Current-update scoring requires a version 9 receipt")
            if self.schema_version < 10 and any(
                adjustment.kind == "evidence_projection"
                for provenance in self.score_provenance.values()
                for adjustment in provenance.adjustments
            ):
                raise ValueError("Evidence projection requires a version 10 receipt")
            if self.schema_version < 11 and any(
                adjustment.kind == "evidence_augmentation"
                for provenance in self.score_provenance.values()
                for adjustment in provenance.adjustments
            ):
                raise ValueError("Evidence augmentation requires a version 11 receipt")
        return self

    def replay_ranking(self) -> tuple[UUID, ...]:
        """Replay the returned candidate ranking without a graph or models.

        This is a fixed-exposure replay: candidates omitted by generation,
        filtering or result selection are not present in the receipt.
        """
        if self.schema_version < 2 or self.score_provenance is None:
            raise ValueError("Version 1 receipts lack replayable score provenance")
        scores = {nid: provenance.replay_score() for nid, provenance in self.score_provenance.items()}

        def key(candidate: ReceiptCandidate):
            nid = candidate.node_id
            path = candidate.trace.path_score if candidate.trace else 0.0
            if self.ranking_policy == "score_id":
                return (0, -scores[nid], 0.0, str(nid))
            if self.ranking_policy == "reranked_prefix" and candidate.reranker_score is not None:
                return (0, -scores[nid], 0.0, str(nid))
            group = 1 if self.ranking_policy == "reranked_prefix" else 0
            return (group, -scores[nid], -path, str(nid))

        return tuple(c.node_id for c in sorted(self.candidates, key=key))

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class RelevanceSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    feedback_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    labels: dict[UUID, StrictBool] = Field(min_length=1)
    surface: Literal["results", "context"] = "results"
    method: Literal["explicit_user", "structured_evaluation"] = "explicit_user"


class RelevanceRecord(RelevanceSubmission):
    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1)
    recorded_at: AwareDatetime
    receipt_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    def matches(self, submission: RelevanceSubmission) -> bool:
        return all(getattr(self, field) == getattr(submission, field)
                   for field in RelevanceSubmission.model_fields)


class AnswerCitationSubmission(BaseModel):
    """Memory citations reported for one answer built from a saved context.

    An empty ``cited_node_ids`` tuple explicitly records that the answer cited
    no memory. It is distinct from missing answer telemetry and must not be
    interpreted as causal evidence that every exposed memory was unnecessary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    citation_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    answer_id: str = Field(min_length=1, max_length=512)
    cited_node_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    answer_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    method: Literal["model_reported", "application_verified", "human_verified"] = "model_reported"

    @model_validator(mode="after")
    def valid_answer_and_citations(self):
        if not self.answer_id.strip():
            raise ValueError("answer_id cannot be blank")
        if len(self.cited_node_ids) != len(set(self.cited_node_ids)):
            raise ValueError("cited_node_ids must be unique")
        return self


class AnswerCitationRecord(AnswerCitationSubmission):
    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1)
    recorded_at: AwareDatetime
    receipt_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def matches(self, submission: AnswerCitationSubmission) -> bool:
        return all(getattr(self, field) == getattr(submission, field)
                   for field in AnswerCitationSubmission.model_fields)


def make_receipt(*, request_id: UUID, user_id: str, query: str,
                 reference_time: datetime, scopes, scoring, packing, candidates, bundle,
                 min_score=None, result_limit=None, retrieval_mode=RetrievalMode.DEFAULT,
                 time_from=None, time_to=None, ranking_policy: RankingPolicy = "score_path_id",
                 execution: RetrievalExecution | None = None) -> RetrievalReceipt:
    included = {item.node.id: item for group in bundle.sections.values() for item in group}
    snapshots = []
    for item in candidates:
        if item.node.user_id != user_id:
            raise ValueError("Cannot record another owner's candidate")
        packed = included.get(item.node.id)
        snapshots.append(ReceiptCandidate(
            node_id=item.node.id, scope=item.node.scope,
            content_sha256=hashlib.sha256(item.node.content.encode()).hexdigest(),
            score=item.composite_score, trace=item.score_trace, reranker_score=item.reranker_score,
            representation=packed.representation if packed else None,
            token_cost=packed.token_cost if packed else 0, in_context=packed is not None,
            has_content=bool(packed and item.node.content.strip() and packed.representation in {
                RepresentationLevel.FULL, RepresentationLevel.PROSE, RepresentationLevel.STRUCTURED}),
        ))
    provenance = {}
    for candidate in candidates:
        if candidate.score_provenance is None:
            raise ValueError("Cannot record replayable receipt without candidate score provenance")
        provenance[candidate.node.id] = candidate.score_provenance
    if bundle.context_guidance is not None and execution is None:
        raise ValueError("Context-guided receipts require an execution descriptor")
    if packing.multipath_ordering == "balanced" and execution is None:
        raise ValueError("Balanced packing receipts require an execution descriptor")
    if packing.context_format == "compact" and execution is None:
        raise ValueError("Compact context receipts require an execution descriptor")
    if packing.episode_context_top_k > 0 and execution is None:
        raise ValueError("Episode context receipts require an execution descriptor")
    if packing.evidence_projection_top_k > 0 and execution is None:
        raise ValueError("Evidence projection receipts require an execution descriptor")
    if packing.evidence_augmentation_top_k > 0 and execution is None:
        raise ValueError("Evidence augmentation receipts require an execution descriptor")
    # Pre-guidance receipts mean guidance was off. Direct callers that omit an
    # execution descriptor retain that historical schema and exact semantics.
    receipt_packing = packing if execution is not None else packing.model_copy(
        update={"context_guidance_mode": "off", "context_format": "auditable"}
    )
    version: Literal[2, 12] = 12 if execution is not None else 2
    return RetrievalReceipt(schema_version=version, execution=execution,
                            request_id=request_id, user_id=user_id, query=query,
                            reference_time=reference_time, scopes=scopes,
                            scoring=scoring, packing=receipt_packing, candidates=tuple(snapshots),
                            min_score=min_score, result_limit=result_limit, retrieval_mode=retrieval_mode,
                            time_from=time_from, time_to=time_to,
                            score_provenance=provenance, ranking_policy=ranking_policy,
                            context_sha256=hashlib.sha256(bundle.render().encode()).hexdigest())
