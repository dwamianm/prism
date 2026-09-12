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
    schema_version: Literal[1, 2, 3, 4] = 1
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
        if isinstance(value, dict) and isinstance(value.get("packing"), dict):
            version = value.get("schema_version", 1)
            if "multipath_ordering" not in value["packing"]:
                if version in (1, 2, 3):
                    # Historical omission always means density, independently
                    # of any future application default. Do not mutate input.
                    return {**value, "packing": {**value["packing"], "multipath_ordering": "density"}}
                if version == 4:
                    raise ValueError("Version 4 receipts require an explicit packing ordering")
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
        return data

    @model_validator(mode="after")
    def unique_candidates(self):
        if (self.schema_version >= 3) != (self.execution is not None):
            raise ValueError("Versions 3 and 4 require an execution descriptor")
        if self.schema_version < 4 and self.packing.multipath_ordering != "density":
            raise ValueError("Legacy receipts support only density packing")
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
    return RetrievalReceipt(schema_version=4 if execution is not None else 2, execution=execution,
                            request_id=request_id, user_id=user_id, query=query,
                            reference_time=reference_time, scopes=scopes,
                            scoring=scoring, packing=packing, candidates=tuple(snapshots),
                            min_score=min_score, result_limit=result_limit, retrieval_mode=retrieval_mode,
                            time_from=time_from, time_to=time_to,
                            score_provenance=provenance, ranking_policy=ranking_policy,
                            context_sha256=hashlib.sha256(bundle.render().encode()).hexdigest())
