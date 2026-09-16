"""Local, auditable entailment checks over minimal memory evidence groups.

This module deliberately verifies explicit claims rather than deciding whether
an entire answer is good.  A claim can require several memories, while a long
concatenation can hide the decisive evidence beyond a model's input window.
The verifier therefore scores individual passages first and only explores a
small, deterministic set of minimal groups when no single passage decides the
claim.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
import hashlib
import itertools
import json
import math
import threading
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prme.retrieval.models import MemoryBundle
from prme.types import EpistemicType, LifecycleState, SourceType


DEFAULT_NLI_MODEL = "cross-encoder/nli-deberta-v3-base"
DEFAULT_NLI_REVISION = "6c749ce3425cd33b46d187e45b92bbf96ee12ec7"


class ClaimVerificationStatus(str, Enum):
    """Machine-derived relationship between a claim and supplied evidence."""

    SUPPORTED = "supported"
    REFUTED = "refuted"
    CONTESTED = "contested"
    INSUFFICIENT = "insufficient"
    INCOMPLETE = "incomplete"


class ClaimVerificationConfig(BaseModel):
    """Configuration for bounded local claim verification."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    model: str = Field(default=DEFAULT_NLI_MODEL, min_length=1)
    revision: str = Field(default=DEFAULT_NLI_REVISION, min_length=1)
    entailment_threshold: float = Field(default=0.80, ge=0.5, le=1.0)
    contradiction_threshold: float = Field(default=0.80, ge=0.5, le=1.0)
    batch_size: int = Field(default=32, ge=1, le=256)
    max_evidence: int = Field(default=64, ge=1, le=256)
    max_group_size: int = Field(default=2, ge=1, le=3)
    group_candidate_limit: int = Field(default=8, ge=2, le=16)

    @field_validator("model", "revision")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be nonempty")
        return normalized


class ClaimEvidence(BaseModel):
    """One exact, citable passage and its typed memory provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference: str = Field(min_length=1)
    memory_id: UUID
    text: str = Field(min_length=1)
    source_type: SourceType | None = None
    epistemic_type: EpistemicType | None = None
    lifecycle_state: LifecycleState | None = None
    event_time: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("reference", "text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be nonempty")
        return normalized


class EvidenceGroupScore(BaseModel):
    """NLI probabilities for one exact evidence group."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    references: tuple[str, ...]
    memory_ids: tuple[UUID, ...]
    entailment: float = Field(ge=0.0, le=1.0)
    contradiction: float = Field(ge=0.0, le=1.0)
    neutral: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def probabilities_sum_to_one(self) -> EvidenceGroupScore:
        if not math.isclose(
            self.entailment + self.contradiction + self.neutral,
            1.0,
            rel_tol=1e-5,
            abs_tol=1e-5,
        ):
            raise ValueError("NLI probabilities must sum to one")
        return self


class ClaimVerification(BaseModel):
    """Auditable claim decision over fixed evidence and verifier settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    claim: str
    status: ClaimVerificationStatus
    supporting_group: tuple[UUID, ...] = ()
    refuting_group: tuple[UUID, ...] = ()
    group_scores: tuple[EvidenceGroupScore, ...] = ()
    limitations: tuple[
        Literal["complete_set_requires_structured_aggregation"], ...
    ] = ()
    model: str
    requested_revision: str
    resolved_revision: str | None = None
    model_called: bool
    configuration_sha256: str
    claim_sha256: str
    evidence_sha256: str
    evaluation_id: str
    result_sha256: str


class ClaimVerificationError(RuntimeError):
    """A verifier dependency, model, or inference result was invalid."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _configuration_sha256(config: ClaimVerificationConfig) -> str:
    return _sha256(_canonical(config.model_dump(mode="json")))


def _evidence_sha256(evidence: Sequence[ClaimEvidence]) -> str:
    return _sha256(_canonical([item.model_dump(mode="json") for item in evidence]))


def _group_text(group: Sequence[ClaimEvidence]) -> str:
    if len(group) == 1:
        return group[0].text
    return "\n".join(
        f"[E{index}] {item.text}" for index, item in enumerate(group, start=1)
    )


def _best_group(
    scores: Sequence[EvidenceGroupScore],
    *,
    probability: Literal["entailment", "contradiction"],
) -> EvidenceGroupScore | None:
    if not scores:
        return None
    return min(
        scores,
        key=lambda item: (
            len(item.memory_ids),
            -getattr(item, probability),
            tuple(str(memory_id) for memory_id in item.memory_ids),
        ),
    )


class ClaimVerifier:
    """Verify explicit claims with a pinned local NLI cross-encoder.

    The model is loaded lazily. Inference and first load share one lock because
    transformer modules are mutable and concurrent first requests must not load
    duplicate copies. Derived exhaustive counts or lists must set
    ``requires_complete_set=True``; NLI cannot prove that a retrieved top-k set
    is complete, so those requests return ``incomplete`` without a model call.
    """

    def __init__(self, config: ClaimVerificationConfig | None = None) -> None:
        self.config = ClaimVerificationConfig.model_validate(
            (config or ClaimVerificationConfig()).model_dump()
        )
        self._model: Any | None = None
        self._resolved_revision: str | None = None
        self._label_indexes: dict[str, int] | None = None
        self._prediction_lock = threading.Lock()

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise ClaimVerificationError(
                "sentence-transformers is required for claim verification; "
                "install prme[verification]"
            ) from exc
        try:
            model = CrossEncoder(
                self.config.model,
                revision=self.config.revision,
                trust_remote_code=False,
            )
            raw_labels = getattr(model.model.config, "id2label", {})
            labels = {
                str(label).casefold(): int(index) for index, label in raw_labels.items()
            }
            required = {"contradiction", "entailment", "neutral"}
            if set(labels) != required or set(labels.values()) != {0, 1, 2}:
                raise ValueError(
                    "model labels must be exactly contradiction, entailment, and neutral"
                )
            self._resolved_revision = getattr(model.model.config, "_commit_hash", None)
            self._label_indexes = labels
            self._model = model
        except ClaimVerificationError:
            raise
        except Exception as exc:
            raise ClaimVerificationError(
                f"Could not load claim verification model {self.config.model!r}"
            ) from exc

    def _predict_sync(
        self,
        pairs: Sequence[tuple[str, str]],
    ) -> list[tuple[float, float, float]]:
        if not pairs:
            return []
        with self._prediction_lock:
            self._ensure_model()
            assert self._model is not None
            assert self._label_indexes is not None
            try:
                raw = self._model.predict(
                    list(pairs),
                    batch_size=self.config.batch_size,
                    apply_softmax=True,
                    show_progress_bar=False,
                )
            except Exception as exc:
                raise ClaimVerificationError(
                    "Claim verification inference failed"
                ) from exc

        import numpy as np

        values = np.asarray(raw, dtype=float)
        if values.shape != (len(pairs), 3) or not np.isfinite(values).all():
            raise ClaimVerificationError(
                "Claim verifier must return three finite probabilities per group"
            )
        if ((values < 0) | (values > 1)).any() or not np.allclose(
            values.sum(axis=1), 1.0, rtol=1e-5, atol=1e-5
        ):
            raise ClaimVerificationError(
                "Claim verifier probabilities must be normalized to [0, 1]"
            )
        indexes = self._label_indexes
        return [
            (
                float(row[indexes["entailment"]]),
                float(row[indexes["contradiction"]]),
                float(row[indexes["neutral"]]),
            )
            for row in values
        ]

    async def _score_groups(
        self,
        claim: str,
        groups: Sequence[tuple[ClaimEvidence, ...]],
    ) -> list[EvidenceGroupScore]:
        probabilities = await asyncio.to_thread(
            self._predict_sync,
            [(_group_text(group), claim) for group in groups],
        )
        return [
            EvidenceGroupScore(
                references=tuple(item.reference for item in group),
                memory_ids=tuple(item.memory_id for item in group),
                entailment=probability[0],
                contradiction=probability[1],
                neutral=probability[2],
            )
            for group, probability in zip(groups, probabilities, strict=True)
        ]

    async def verify(
        self,
        claim: str,
        evidence: Sequence[ClaimEvidence],
        *,
        requires_complete_set: bool = False,
    ) -> ClaimVerification:
        """Verify a declarative claim against exact, typed evidence passages."""
        normalized_claim = claim.strip()
        if not normalized_claim:
            raise ValueError("claim must be nonempty")
        if len(evidence) > self.config.max_evidence:
            raise ValueError(
                f"evidence count exceeds configured maximum {self.config.max_evidence}"
            )

        unique: list[ClaimEvidence] = []
        references: set[str] = set()
        memory_ids: set[UUID] = set()
        for item in evidence:
            if item.reference in references:
                raise ValueError(f"duplicate evidence reference: {item.reference}")
            if item.memory_id in memory_ids:
                raise ValueError(f"duplicate evidence memory: {item.memory_id}")
            references.add(item.reference)
            memory_ids.add(item.memory_id)
            unique.append(item)

        if requires_complete_set:
            return self._result(
                claim=normalized_claim,
                evidence=unique,
                status=ClaimVerificationStatus.INCOMPLETE,
                scores=(),
                model_called=False,
                limitations=("complete_set_requires_structured_aggregation",),
            )
        if not unique:
            return self._result(
                claim=normalized_claim,
                evidence=(),
                status=ClaimVerificationStatus.INSUFFICIENT,
                scores=(),
                model_called=False,
            )

        all_scores: list[EvidenceGroupScore] = []
        groups = [(item,) for item in unique]
        single_scores = await self._score_groups(normalized_claim, groups)
        all_scores.extend(single_scores)

        support = [
            item
            for item in single_scores
            if item.entailment >= self.config.entailment_threshold
        ]
        refute = [
            item
            for item in single_scores
            if item.contradiction >= self.config.contradiction_threshold
        ]

        if not support and not refute and self.config.max_group_size > 1:
            ranked = sorted(
                zip(unique, single_scores, strict=True),
                key=lambda item: (
                    -max(item[1].entailment, item[1].contradiction),
                    str(item[0].memory_id),
                ),
            )[: self.config.group_candidate_limit]
            candidates = [item[0] for item in ranked]
            for size in range(2, self.config.max_group_size + 1):
                grouped = list(itertools.combinations(candidates, size))
                if not grouped:
                    break
                group_scores = await self._score_groups(normalized_claim, grouped)
                all_scores.extend(group_scores)
                support = [
                    item
                    for item in group_scores
                    if item.entailment >= self.config.entailment_threshold
                ]
                refute = [
                    item
                    for item in group_scores
                    if item.contradiction >= self.config.contradiction_threshold
                ]
                if support or refute:
                    break

        if support and refute:
            status = ClaimVerificationStatus.CONTESTED
        elif support:
            status = ClaimVerificationStatus.SUPPORTED
        elif refute:
            status = ClaimVerificationStatus.REFUTED
        else:
            status = ClaimVerificationStatus.INSUFFICIENT
        return self._result(
            claim=normalized_claim,
            evidence=unique,
            status=status,
            scores=tuple(all_scores),
            model_called=True,
            supporting=_best_group(support, probability="entailment"),
            refuting=_best_group(refute, probability="contradiction"),
        )

    async def verify_bundle(
        self,
        claim: str,
        bundle: MemoryBundle,
        *,
        evidence_refs: Sequence[str] | None = None,
        requires_complete_set: bool = False,
    ) -> ClaimVerification:
        """Verify using only passages present in an exact packed bundle."""
        evidence = self.bundle_evidence(bundle)
        if evidence_refs is not None:
            by_reference = {item.reference: item for item in evidence}
            selected: list[ClaimEvidence] = []
            for reference in evidence_refs:
                if reference not in by_reference:
                    raise ValueError(f"Unknown context reference: {reference}")
                selected.append(by_reference[reference])
            evidence = tuple(selected)
        return await self.verify(
            claim,
            evidence,
            requires_complete_set=requires_complete_set,
        )

    @staticmethod
    def bundle_evidence(bundle: MemoryBundle) -> tuple[ClaimEvidence, ...]:
        """Extract exact rendered passages and typed provenance from a bundle."""
        context = bundle.render()
        reference_by_id = {
            memory_id: reference
            for reference, memory_id in bundle.context_references.items()
        }
        evidence: list[ClaimEvidence] = []
        seen: set[UUID] = set()
        for candidates in bundle.sections.values():
            for candidate in candidates:
                node = candidate.node
                if node.id in seen or candidate.rendered_text is None:
                    continue
                reference = reference_by_id.get(node.id, str(node.id))
                rendered_reference = (
                    f'"{reference}"' in context
                    or f"[{reference}]" in context
                    or f'"id":"{reference}"' in context
                )
                if not rendered_reference:
                    continue
                evidence.append(
                    ClaimEvidence(
                        reference=reference,
                        memory_id=node.id,
                        text=candidate.rendered_text,
                        source_type=node.source_type,
                        epistemic_type=node.epistemic_type,
                        lifecycle_state=node.lifecycle_state,
                        event_time=node.event_time,
                        valid_from=node.valid_from,
                        valid_to=node.valid_to,
                    )
                )
                seen.add(node.id)
        return tuple(evidence)

    def _result(
        self,
        *,
        claim: str,
        evidence: Sequence[ClaimEvidence],
        status: ClaimVerificationStatus,
        scores: tuple[EvidenceGroupScore, ...],
        model_called: bool,
        supporting: EvidenceGroupScore | None = None,
        refuting: EvidenceGroupScore | None = None,
        limitations: tuple[
            Literal["complete_set_requires_structured_aggregation"], ...
        ] = (),
    ) -> ClaimVerification:
        config_sha = _configuration_sha256(self.config)
        claim_sha = _sha256(claim)
        evidence_sha = _evidence_sha256(evidence)
        identity = _sha256(
            _canonical(
                {
                    "claim_sha256": claim_sha,
                    "configuration_sha256": config_sha,
                    "evidence_sha256": evidence_sha,
                }
            )
        )
        public = {
            "evaluation_id": identity,
            "group_scores": [item.model_dump(mode="json") for item in scores],
            "limitations": list(limitations),
            "refuting_group": (
                [str(item) for item in refuting.memory_ids] if refuting else []
            ),
            "status": status.value,
            "supporting_group": (
                [str(item) for item in supporting.memory_ids] if supporting else []
            ),
        }
        return ClaimVerification(
            claim=claim,
            status=status,
            supporting_group=supporting.memory_ids if supporting else (),
            refuting_group=refuting.memory_ids if refuting else (),
            group_scores=scores,
            limitations=limitations,
            model=self.config.model,
            requested_revision=self.config.revision,
            resolved_revision=self._resolved_revision if model_called else None,
            model_called=model_called,
            configuration_sha256=config_sha,
            claim_sha256=claim_sha,
            evidence_sha256=evidence_sha,
            evaluation_id=identity,
            result_sha256=_sha256(_canonical(public)),
        )


__all__ = [
    "DEFAULT_NLI_MODEL",
    "DEFAULT_NLI_REVISION",
    "ClaimEvidence",
    "ClaimVerification",
    "ClaimVerificationConfig",
    "ClaimVerificationError",
    "ClaimVerificationStatus",
    "ClaimVerifier",
    "EvidenceGroupScore",
]
