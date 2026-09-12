"""Immutable retrieval snapshots and explicit, scoped relevance judgments."""
from datetime import datetime
import hashlib
import math
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.models import ScoreTrace
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


class RetrievalReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
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

    @model_validator(mode="after")
    def unique_candidates(self):
        ids = [candidate.node_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Receipt candidate identities must be unique")
        return self

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
                 time_from=None, time_to=None) -> RetrievalReceipt:
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
    return RetrievalReceipt(request_id=request_id, user_id=user_id, query=query,
                            reference_time=reference_time, scopes=scopes,
                            scoring=scoring, packing=packing, candidates=tuple(snapshots),
                            min_score=min_score, result_limit=result_limit, retrieval_mode=retrieval_mode,
                            time_from=time_from, time_to=time_to,
                            context_sha256=hashlib.sha256(bundle.render().encode()).hexdigest())
