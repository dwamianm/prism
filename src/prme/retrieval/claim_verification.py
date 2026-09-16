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
import re
import threading
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prme.retrieval.models import MemoryBundle
from prme.types import EpistemicType, LifecycleState, SourceType


DEFAULT_NLI_MODEL = "cross-encoder/nli-deberta-v3-base"
DEFAULT_NLI_REVISION = "6c749ce3425cd33b46d187e45b92bbf96ee12ec7"

_REFUTATION_CUE_RE = re.compile(
    r"\b(?:no|not|never|neither|nor|without|cannot|can't|isn't|aren't|"
    r"wasn't|weren't|didn't|doesn't|don't|won't|instead|rather than|"
    r"switched from|replaced|incorrect|false)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<!\w)-?\d+(?:\.\d+)?(?!\w)")
_WEEKDAY_RE = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_MONTH_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b",
    re.IGNORECASE,
)
_NEGATED_CLAUSE_RE = re.compile(
    r"[^.;!?]*(?:\b(?:not|never|no\s+longer|cannot|can't|isn't|aren't|"
    r"wasn't|weren't|didn't|doesn't|don't|won't)\b)[^.;!?]*",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_NONACTUAL_MODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "desire",
        re.compile(
            r"\b(?:want(?:s|ed)?|would\s+like|wish(?:es|ed)?|hope(?:s|d)?|"
            r"need(?:s|ed)?|aim\w*|seek\w*)\b|\b\w+[’']d\s+like\b",
            re.IGNORECASE,
        ),
    ),
    (
        "attempt",
        re.compile(
            r"\b(?:tr(?:y|ies|ied|ying)|attempt\w*|work(?:s|ed|ing)?\s+(?:on|to))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "plan",
        re.compile(
            r"\b(?:plan(?:s|ned|ning)?|intend\w*|schedul\w*|consider\w*|"
            r"explor\w*|evaluat\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "advice",
        re.compile(
            r"\b(?:should|ought|recommend\w*|suggest\w*|advis\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "uncertainty",
        re.compile(
            r"\b(?:might|maybe|perhaps|possible|possibly|potentially|uncertain|whether)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "conditional",
        re.compile(r"\b(?:if|unless|provided\s+that|in\s+case)\b", re.IGNORECASE),
    ),
    (
        "unverified",
        re.compile(
            r"\b(?:unverified|unconfirmed|reportedly|allegedly)\b",
            re.IGNORECASE,
        ),
    ),
)
_NEGATION_STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "application",
    "are",
    "at",
    "be",
    "by",
    "currently",
    "did",
    "do",
    "does",
    "for",
    "from",
    "in",
    "is",
    "it",
    "longer",
    "no",
    "not",
    "of",
    "on",
    "pipeline",
    "project",
    "service",
    "system",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "will",
    "with",
}

ClaimVerificationLimitation = Literal[
    "complete_set_requires_structured_aggregation",
    "uncorroborated_model_entailment",
    "uncorroborated_model_contradiction",
]
ClaimVerificationDecisionBasis = Literal[
    "model_entailment",
    "model_contradiction",
    "explicit_negation_overlap",
]


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
    entailment_policy: Literal["speech_act_guarded", "model_only"] = (
        "speech_act_guarded"
    )
    refutation_policy: Literal["explicit_corroboration", "model_only"] = (
        "explicit_corroboration"
    )

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

    schema_version: Literal[1, 2] = 2
    claim: str
    status: ClaimVerificationStatus
    supporting_group: tuple[UUID, ...] = ()
    refuting_group: tuple[UUID, ...] = ()
    supporting_basis: ClaimVerificationDecisionBasis | None = None
    refuting_basis: ClaimVerificationDecisionBasis | None = None
    group_scores: tuple[EvidenceGroupScore, ...] = ()
    limitations: tuple[ClaimVerificationLimitation, ...] = ()
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


def _explicit_values(text: str) -> tuple[set[str], set[str], set[str]]:
    return (
        set(_NUMBER_RE.findall(text)),
        {match.casefold() for match in _WEEKDAY_RE.findall(text)},
        {match.casefold() for match in _MONTH_RE.findall(text)},
    )


def _corroborates_refutation(
    claim: str,
    group: Sequence[ClaimEvidence],
) -> bool:
    claim_modes = _nonactual_modes(claim)
    if any(not _typed_evidence_compatible(item, claim_modes) for item in group):
        return False
    if not _corroborates_entailment(claim, group):
        return False
    evidence_text = "\n".join(item.text for item in group)
    if _REFUTATION_CUE_RE.search(claim) or _REFUTATION_CUE_RE.search(evidence_text):
        return True
    claim_values = _explicit_values(claim)
    evidence_values = _explicit_values(evidence_text)
    return any(
        claim_group and evidence_group and claim_group != evidence_group
        for claim_group, evidence_group in zip(
            claim_values,
            evidence_values,
            strict=True,
        )
    )


def _nonactual_modes(text: str) -> set[str]:
    modes = {
        mode
        for mode, pattern in _NONACTUAL_MODE_PATTERNS
        if pattern.search(text) is not None
    }
    if "?" in text:
        modes.add("question")
    return modes


def _typed_evidence_compatible(
    item: ClaimEvidence,
    claim_modes: set[str],
) -> bool:
    if item.lifecycle_state in {
        LifecycleState.SUPERSEDED,
        LifecycleState.DEPRECATED,
        LifecycleState.ARCHIVED,
    }:
        return False
    if item.epistemic_type == EpistemicType.HYPOTHETICAL:
        return bool(claim_modes.intersection({"uncertainty", "conditional"}))
    if item.epistemic_type == EpistemicType.CONDITIONAL:
        return "conditional" in claim_modes
    if item.epistemic_type == EpistemicType.UNVERIFIED:
        return bool(claim_modes.intersection({"unverified", "uncertainty"}))
    if item.epistemic_type == EpistemicType.DEPRECATED:
        return False
    return True


def _corroborates_entailment(
    claim: str,
    group: Sequence[ClaimEvidence],
) -> bool:
    claim_modes = _nonactual_modes(claim)
    evidence_modes: set[str] = set()
    for item in group:
        if not _typed_evidence_compatible(item, claim_modes):
            return False
        evidence_modes.update(_nonactual_modes(item.text))
    return not evidence_modes.difference(claim_modes)


def _negation_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _WORD_RE.findall(text):
        token = match.casefold()
        if token in _NEGATION_STOPWORDS:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = f"{token[:-3]}y"
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _explicit_negation_refutes(
    claim: str,
    group: Sequence[ClaimEvidence],
) -> bool:
    if _NEGATED_CLAUSE_RE.search(claim) is not None:
        return False
    if not _corroborates_entailment(claim, group):
        return False
    claim_modes = _nonactual_modes(claim)
    claim_tokens = _negation_tokens(claim)
    if len(claim_tokens) < 2:
        return False
    for item in group:
        if not _typed_evidence_compatible(item, claim_modes):
            continue
        for match in _NEGATED_CLAUSE_RE.finditer(item.text):
            clause_tokens = _negation_tokens(match.group())
            shared = claim_tokens.intersection(clause_tokens)
            if len(shared) >= 3 or (len(claim_tokens) == 2 and shared == claim_tokens):
                return True
    return False


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
        evidence_by_id = {item.memory_id: item for item in unique}

        def score_group(score: EvidenceGroupScore) -> tuple[ClaimEvidence, ...]:
            return tuple(evidence_by_id[memory_id] for memory_id in score.memory_ids)

        def corroborates_refutation(score: EvidenceGroupScore) -> bool:
            if self.config.refutation_policy == "model_only":
                return True
            return _corroborates_refutation(normalized_claim, score_group(score))

        def corroborates_entailment(score: EvidenceGroupScore) -> bool:
            if self.config.entailment_policy == "model_only":
                return True
            return _corroborates_entailment(normalized_claim, score_group(score))

        def classify_refutations(
            scores: Sequence[EvidenceGroupScore],
        ) -> tuple[
            list[EvidenceGroupScore],
            list[EvidenceGroupScore],
            list[EvidenceGroupScore],
        ]:
            raw_model = [
                item
                for item in scores
                if item.contradiction >= self.config.contradiction_threshold
            ]
            model = [item for item in raw_model if corroborates_refutation(item)]
            model_keys = {item.memory_ids for item in model}
            deterministic = [
                item
                for item in scores
                if item.memory_ids not in model_keys
                and _explicit_negation_refutes(
                    normalized_claim,
                    score_group(item),
                )
            ]
            return raw_model, model, deterministic

        groups = [(item,) for item in unique]
        single_scores = await self._score_groups(normalized_claim, groups)
        all_scores.extend(single_scores)

        raw_support = [
            item
            for item in single_scores
            if item.entailment >= self.config.entailment_threshold
        ]
        support = [item for item in raw_support if corroborates_entailment(item)]
        blocked_entailment = len(raw_support) != len(support)
        raw_refute, model_refute, deterministic_refute = classify_refutations(
            single_scores
        )
        refute = [*model_refute, *deterministic_refute]
        blocked_refutation = len(raw_refute) != len(model_refute)

        if not support and not refute and self.config.max_group_size > 1:
            ranked = sorted(
                zip(unique, single_scores, strict=True),
                key=lambda item: (
                    -max(item[1].entailment, item[1].contradiction),
                    str(item[0].memory_id),
                ),
            )[: self.config.group_candidate_limit]
            selected_ids = {item[0].memory_id for item in ranked}
            candidates = [item for item in unique if item.memory_id in selected_ids]
            for size in range(2, self.config.max_group_size + 1):
                grouped = list(itertools.combinations(candidates, size))
                if not grouped:
                    break
                group_scores = await self._score_groups(normalized_claim, grouped)
                all_scores.extend(group_scores)
                raw_support = [
                    item
                    for item in group_scores
                    if item.entailment >= self.config.entailment_threshold
                ]
                support = [
                    item for item in raw_support if corroborates_entailment(item)
                ]
                blocked_entailment = blocked_entailment or len(raw_support) != len(
                    support
                )
                raw_refute, model_refute, deterministic_refute = classify_refutations(
                    group_scores
                )
                refute = [*model_refute, *deterministic_refute]
                blocked_refutation = blocked_refutation or len(raw_refute) != len(
                    model_refute
                )
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
        best_support = _best_group(support, probability="entailment")
        best_refute = _best_group(refute, probability="contradiction")
        limitations: list[ClaimVerificationLimitation] = []
        if blocked_entailment:
            limitations.append("uncorroborated_model_entailment")
        if blocked_refutation:
            limitations.append("uncorroborated_model_contradiction")
        return self._result(
            claim=normalized_claim,
            evidence=unique,
            status=status,
            scores=tuple(all_scores),
            model_called=True,
            supporting=best_support,
            refuting=best_refute,
            supporting_basis="model_entailment" if best_support else None,
            refuting_basis=(
                "model_contradiction"
                if best_refute in model_refute
                else "explicit_negation_overlap"
                if best_refute
                else None
            ),
            limitations=tuple(limitations),
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
        supporting_basis: ClaimVerificationDecisionBasis | None = None,
        refuting_basis: ClaimVerificationDecisionBasis | None = None,
        limitations: tuple[ClaimVerificationLimitation, ...] = (),
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
            "refuting_basis": refuting_basis,
            "refuting_group": (
                [str(item) for item in refuting.memory_ids] if refuting else []
            ),
            "status": status.value,
            "supporting_basis": supporting_basis,
            "supporting_group": (
                [str(item) for item in supporting.memory_ids] if supporting else []
            ),
        }
        return ClaimVerification(
            claim=claim,
            status=status,
            supporting_group=supporting.memory_ids if supporting else (),
            refuting_group=refuting.memory_ids if refuting else (),
            supporting_basis=supporting_basis,
            refuting_basis=refuting_basis,
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
    "ClaimVerificationDecisionBasis",
    "ClaimVerificationError",
    "ClaimVerificationLimitation",
    "ClaimVerificationStatus",
    "ClaimVerifier",
    "EvidenceGroupScore",
]
