"""Closed, content-free receipt family. Legacy receipt bytes remain independent.

Replay reproduces ordering of saved numeric candidate scores, not candidate
generation, the original query, context text, or model execution. A checksum
binds this opaque-identity manifest; it is never a commitment to source text.
"""

from __future__ import annotations

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from prme.models.relevance import AnswerCitationSubmission, RankingPolicy
from prme.privacy.journals import JournalDependency, JournalManifest
from prme.types import Scope, TEXT_REPRESENTATIONS, has_memory_text


class ProtectedReceiptCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_id: UUID
    scope: Scope
    score: float = Field(allow_inf_nan=False)
    path_score: float = Field(allow_inf_nan=False)
    reranked: StrictBool
    in_context: StrictBool
    has_content: StrictBool
    token_cost: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def exposure(self):
        if self.has_content and not self.in_context:
            raise ValueError("Content requires context membership")
        return self


class ProtectedRetrievalReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["protected_retrieval_receipt"] = "protected_retrieval_receipt"
    schema_version: Literal[1] = 1
    request_id: UUID
    user_id: UUID
    reference_time: AwareDatetime
    scopes: tuple[Scope, ...] | None
    manifest: JournalManifest
    candidates: tuple[ProtectedReceiptCandidate, ...]
    ranking_policy: RankingPolicy
    query_availability: Literal["not_retained"] = "not_retained"
    execution_availability: Literal["not_retained"] = "not_retained"
    source_commitments: Literal["not_retained"] = "not_retained"
    ranking_replay: Literal["saved_numeric_scores"] = "saved_numeric_scores"
    score_recomputation: Literal["unavailable"] = "unavailable"

    @model_validator(mode="after")
    def identities(self):
        ids = tuple(item.node_id for item in self.candidates)
        if (
            len(set(ids)) != len(ids)
            or self.manifest.journal_id != self.request_id
            or set(self.manifest.admitted_node_ids) != set(ids)
            or any(
                item.snapshot.policy.owner_id != self.user_id
                for item in self.manifest.dependencies
            )
        ):
            raise ValueError("Protected receipt identity mismatch")
        return self

    @property
    def checksum(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def replay_ranking(self) -> tuple[UUID, ...]:
        def key(item):
            if self.ranking_policy == "score_id" or (
                self.ranking_policy == "reranked_prefix" and item.reranked
            ):
                return 0, -item.score, 0.0, str(item.node_id)
            return (
                int(self.ranking_policy == "reranked_prefix"),
                -item.score,
                -item.path_score,
                str(item.node_id),
            )

        return tuple(item.node_id for item in sorted(self.candidates, key=key))


class ProtectedCitationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["protected_answer_citations"] = "protected_answer_citations"
    schema_version: Literal[1] = 1
    citation_id: UUID
    request_id: UUID
    answer_id: UUID
    cited_node_ids: tuple[UUID, ...]
    method: Literal["model_reported", "application_verified", "human_verified"]
    user_id: UUID
    recorded_at: AwareDatetime
    receipt_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_availability: Literal["not_retained"] = "not_retained"
    context_availability: Literal["not_retained"] = "not_retained"

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.cited_node_ids)) != len(self.cited_node_ids):
            raise ValueError("Duplicate citation identity")
        return self

    def matches(self, submission: AnswerCitationSubmission) -> bool:
        return (
            submission.answer_sha256 is None
            and str(self.answer_id) == submission.answer_id
            and all(
                getattr(self, name) == getattr(submission, name)
                for name in ("citation_id", "request_id", "cited_node_ids", "method")
            )
        )


def make_protected_receipt(
    *,
    request_id,
    user_id,
    reference_time,
    scopes,
    candidates,
    bundle,
    ranking_policy,
    context,
):
    included = {
        item.node.id: item for group in bundle.sections.values() for item in group
    }
    rows = []
    for item in candidates:
        if item.node.user_id != user_id:
            raise ValueError("Receipt candidate owner mismatch")
        packed = included.get(item.node.id)
        rows.append(
            ProtectedReceiptCandidate(
                node_id=item.node.id,
                scope=item.node.scope,
                score=item.composite_score,
                path_score=item.score_trace.path_score if item.score_trace else 0.0,
                reranked=item.reranker_score is not None,
                in_context=packed is not None,
                has_content=bool(
                    packed
                    and has_memory_text(item.node.content)
                    and packed.representation in TEXT_REPRESENTATIONS
                ),
                token_cost=packed.token_cost if packed else 0,
            )
        )
    return ProtectedRetrievalReceipt(
        request_id=request_id,
        user_id=user_id,
        reference_time=reference_time,
        scopes=scopes,
        candidates=tuple(rows),
        ranking_policy=ranking_policy,
        manifest=JournalManifest(
            journal_id=request_id,
            created_at=reference_time,
            admitted_node_ids=tuple(row.node_id for row in rows),
            dependencies=tuple(
                JournalDependency.from_record(row)
                for row in sorted(
                    context.records.values(), key=lambda value: str(value.event_id)
                )
            ),
        ),
    )


def read_receipt(raw: str):
    import json
    from prme.models.relevance import RetrievalReceipt

    data = json.loads(raw)
    codec = (
        ProtectedRetrievalReceipt
        if data.get("kind") == "protected_retrieval_receipt"
        else RetrievalReceipt
    )
    return codec.model_validate_json(raw)
