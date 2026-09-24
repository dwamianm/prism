"""Evidence-bound temporal relation guidance for packed retrieval context.

The resolver may select and quote operands, but it cannot supply arithmetic.
PRME validates every citation against the already-packed bundle, computes a
small relation locally, asks an independent semantic gate to check the event to
time alignment, and only then adds token-counted guidance.  No result is stored
as memory or treated as ground truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
import time
from typing import Any, Literal, Protocol
from uuid import UUID

import dateparser  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from prme.retrieval.config import PackingConfig
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.temporal_relation_models import (
    GateAudit,
    Operation,
    ResolverAudit,
    TemporalRelationMetadata,
    TemporalRelationStatus,
)
from prme.types import RepresentationLevel


TEMPORAL_RELATION_PROTOCOL = "temporal_relation_v1"
CONFIRMED_RESOLVER_MODEL = "deepseek-v4.1-flash:cloud"
CONFIRMED_RESOLVER_MODEL_DIGEST = (
    "e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8"
)
CONFIRMED_RESOLVER_BASE_URL = "http://127.0.0.1:11434"
CONFIRMED_GATE_MODEL = "jev-1.13.0"
CONFIRMED_GATE_API_URL = "https://api.typesafe.ai/v1/systemone"
CONFIRMED_GATE_THRESHOLD = 0.85

TimeBasis = Literal[
    "event_time",
    "text_expression",
    "duration",
    "time_of_day",
    "none",
]

_EVENT_TIME_MARKER_RE = re.compile(
    r"\b(today|tonight|this morning|this afternoon|this evening)\b",
    re.IGNORECASE,
)
_DAY_PRECISION_RE = re.compile(
    r"\b(?:\d{1,2}(?:st|nd|rd|th)?|"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"\b(?P<value>\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve)\s+(?P<unit>seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)
_WORD_NUMBERS = {
    word: Decimal(index)
    for index, word in enumerate(
        (
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
        )
    )
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class TemporalRelationConfig(BaseModel):
    """Opt-in provider and safety settings for temporal relation guidance."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    enabled: bool = False
    resolver_provider: Literal["ollama"] = "ollama"
    resolver_model: str = Field(default=CONFIRMED_RESOLVER_MODEL, min_length=1)
    resolver_base_url: str = CONFIRMED_RESOLVER_BASE_URL
    resolver_model_digest: str | None = Field(
        default=CONFIRMED_RESOLVER_MODEL_DIGEST,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "Expected Ollama model digest. Set None to accept the observed digest "
            "for one request while still rejecting an in-flight identity change."
        ),
    )
    resolver_timeout_seconds: float = Field(default=90.0, gt=0, le=300)
    resolver_max_attempts: int = Field(default=3, ge=1, le=5)
    maximum_schema_repairs: Literal[1] = 1
    gate_provider: Literal["typesafe_jev"] = "typesafe_jev"
    gate_model: Literal["jev-1.13.0"] = "jev-1.13.0"
    gate_api_url: str = CONFIRMED_GATE_API_URL
    gate_threshold: float = Field(
        default=CONFIRMED_GATE_THRESHOLD,
        ge=0,
        le=1,
        description="Minimum probability for every cited operand.",
    )
    gate_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    gate_max_attempts: int = Field(default=3, ge=1, le=5)
    gate_api_key: SecretStr | None = Field(default=None, repr=False)
    failure_policy: Literal["fallback", "raise"] = "fallback"

    @model_validator(mode="after")
    def normalize_endpoints(self) -> TemporalRelationConfig:
        self.resolver_model = self.resolver_model.strip()
        self.resolver_base_url = self.resolver_base_url.strip().rstrip("/")
        self.gate_api_url = self.gate_api_url.strip().rstrip("/")
        if not self.resolver_model:
            raise ValueError("resolver_model must be nonempty")
        if not self.resolver_base_url.startswith(("http://", "https://")):
            raise ValueError("resolver_base_url must be an HTTP(S) URL")
        if not self.gate_api_url.startswith("https://"):
            raise ValueError("gate_api_url must be an HTTPS URL")
        return self

    @property
    def confirmation_protocol_aligned(self) -> bool:
        """Whether declared protocol values match the held-out confirmation.

        This describes request configuration, not universal quality or a promise
        that a hosted provider's internal weights remain reproducible.
        """
        return (
            self.resolver_provider == "ollama"
            and self.resolver_model == CONFIRMED_RESOLVER_MODEL
            and self.resolver_base_url == CONFIRMED_RESOLVER_BASE_URL
            and self.resolver_model_digest == CONFIRMED_RESOLVER_MODEL_DIGEST
            and self.gate_provider == "typesafe_jev"
            and self.gate_model == CONFIRMED_GATE_MODEL
            and self.gate_api_url == CONFIRMED_GATE_API_URL
            and self.gate_threshold == CONFIRMED_GATE_THRESHOLD
            and self.maximum_schema_repairs == 1
        )

    @property
    def configuration_sha256(self) -> str:
        return _sha256(self.model_dump(mode="json", exclude={"gate_api_key"}))


class RawOperand(BaseModel):
    """One model-selected operand before local evidence validation."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    evidence_id: UUID
    quote: str = Field(min_length=1)
    time_expression: str = Field(min_length=1)
    time_basis: TimeBasis


class RawResolution(BaseModel):
    """Resolver output. Computed values are deliberately absent."""

    model_config = ConfigDict(extra="forbid")
    operation: Operation
    operands: list[RawOperand] = Field(default_factory=list, max_length=8)


class EvidenceRecord(BaseModel):
    """Exact fields exposed from one already-packed candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: UUID
    event_time: datetime | None
    text: str


class ValidatedOperand(BaseModel):
    """A bundle-local verbatim citation with a locally resolved value."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    evidence_id: UUID
    quote: str
    time_expression: str
    time_basis: TimeBasis
    resolved_time: datetime | None = None
    duration_value: Decimal | None = None
    duration_unit: str | None = None


class TemporalRelation(BaseModel):
    """A computed, cited relation that remains explicitly inferred."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    operation: Operation
    operands: tuple[ValidatedOperand, ...]
    value: str
    guidance: str


class ResolverResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    resolution: RawResolution
    audit: ResolverAudit


class TemporalResolver(Protocol):
    async def resolve(
        self,
        query: str,
        question_time: datetime,
        records: tuple[EvidenceRecord, ...],
    ) -> ResolverResult: ...


class TemporalRelationGate(Protocol):
    async def assess(
        self,
        query: str,
        relation: TemporalRelation,
    ) -> GateAudit: ...


def _parse_text_date(expression: str, relative_to: datetime) -> datetime | None:
    value = dateparser.parse(
        expression,
        languages=["en"],
        settings={
            "RELATIVE_BASE": relative_to,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "UTC",
            "PREFER_DATES_FROM": "past",
        },
    )
    if value is None or not _DAY_PRECISION_RE.search(expression):
        return None
    return value.astimezone(timezone.utc)


def _parse_duration(expression: str) -> tuple[Decimal, str] | None:
    match = _DURATION_RE.fullmatch(expression.strip())
    if match is None:
        return None
    raw_value = match.group("value").casefold()
    value = Decimal(raw_value) if raw_value[0].isdigit() else _WORD_NUMBERS[raw_value]
    unit = match.group("unit").casefold().removesuffix("s")
    return value, unit


def records_from_bundle(bundle: MemoryBundle) -> tuple[EvidenceRecord, ...]:
    """Project exact packed representations without parsing rendered context."""
    records: list[EvidenceRecord] = []
    seen: set[UUID] = set()
    for candidates in bundle.sections.values():
        for candidate in candidates:
            if candidate.node.id in seen:
                raise ValueError("packed bundle contains a duplicate node")
            seen.add(candidate.node.id)
            if candidate.rendered_text is None:
                raise ValueError("packed candidate has no rendered representation")
            records.append(
                EvidenceRecord(
                    id=candidate.node.id,
                    event_time=candidate.node.event_time,
                    text=candidate.rendered_text,
                )
            )
    return tuple(records)


def validate_operands(
    resolution: RawResolution,
    records: dict[UUID, EvidenceRecord],
) -> tuple[tuple[ValidatedOperand, ...], tuple[str, ...]]:
    """Resolve only exact, bundle-local values and reject invented citations."""
    values: list[ValidatedOperand] = []
    errors: list[str] = []
    seen: set[UUID] = set()
    for index, operand in enumerate(resolution.operands):
        record = records.get(operand.evidence_id)
        if record is None:
            errors.append(f"operand[{index}] cites an unknown record")
            continue
        if operand.evidence_id in seen:
            errors.append(f"operand[{index}] repeats a record")
            continue
        seen.add(operand.evidence_id)
        if operand.quote not in record.text:
            errors.append(f"operand[{index}] quote is not verbatim")
            continue

        resolved_time: datetime | None = None
        duration_value: Decimal | None = None
        duration_unit: str | None = None
        if operand.time_basis == "event_time":
            if (
                operand.time_expression != "event_time"
                or record.event_time is None
                or not _EVENT_TIME_MARKER_RE.search(operand.quote)
            ):
                errors.append(f"operand[{index}] event_time is not licensed")
                continue
            resolved_time = record.event_time.astimezone(timezone.utc)
        elif operand.time_basis == "text_expression":
            if operand.time_expression not in operand.quote or record.event_time is None:
                errors.append(f"operand[{index}] text date is not verbatim")
                continue
            resolved_time = _parse_text_date(
                operand.time_expression, record.event_time.astimezone(timezone.utc)
            )
            if resolved_time is None:
                errors.append(f"operand[{index}] text date lacks day precision")
                continue
        elif operand.time_basis == "duration":
            if operand.time_expression not in operand.quote:
                errors.append(f"operand[{index}] duration is not verbatim")
                continue
            duration = _parse_duration(operand.time_expression)
            if duration is None:
                errors.append(f"operand[{index}] duration is unsupported")
                continue
            duration_value, duration_unit = duration
        elif operand.time_basis == "time_of_day":
            if operand.time_expression not in operand.quote:
                errors.append(f"operand[{index}] time of day is not verbatim")
                continue
        elif operand.time_expression.casefold() not in {"none", "unknown"}:
            errors.append(f"operand[{index}] none basis has a value")
            continue

        values.append(
            ValidatedOperand(
                name=operand.name.strip(),
                evidence_id=operand.evidence_id,
                quote=operand.quote,
                time_expression=operand.time_expression,
                time_basis=operand.time_basis,
                resolved_time=resolved_time,
                duration_value=duration_value,
                duration_unit=duration_unit,
            )
        )
    return tuple(values), tuple(errors)


def _format_decimal(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else format(value, "f")


def _evidence_lines(operands: tuple[ValidatedOperand, ...]) -> list[str]:
    lines = []
    for operand in operands:
        if operand.resolved_time is not None:
            value = operand.resolved_time.date().isoformat()
        elif operand.duration_value is not None:
            value = f"{_format_decimal(operand.duration_value)} {operand.duration_unit}"
        else:
            value = operand.time_expression
        lines.append(f"- {operand.name}: {value} [evidence_id={operand.evidence_id}]")
    return lines


def compute_relation(
    resolution: RawResolution,
    records: dict[UUID, EvidenceRecord],
    question_time: datetime,
) -> tuple[TemporalRelation | None, tuple[str, ...]]:
    """Compute a relation only when every operand passes local validation."""
    if question_time.utcoffset() is None:
        raise ValueError("question_time must include a timezone")
    operands, errors = validate_operands(resolution, records)
    if resolution.operation == "unsupported":
        if resolution.operands:
            return None, (*errors, "unsupported resolution included operands")
        return None, errors
    if errors or len(operands) != len(resolution.operands):
        return None, errors

    operation = resolution.operation
    value: str | None = None
    relation_line: str | None = None
    if operation == "elapsed_between":
        if len(operands) != 2 or any(item.resolved_time is None for item in operands):
            return None, ("elapsed_between requires two exact dates",)
        dates = [item.resolved_time.date() for item in operands if item.resolved_time]
        days = abs((dates[1] - dates[0]).days)
        value = f"{days} days"
        relation_line = f"- Deterministic calendar-date difference: {value}."
    elif operation == "elapsed_since_question":
        if len(operands) != 1 or operands[0].resolved_time is None:
            return None, ("elapsed_since_question requires one exact date",)
        days = (question_time.date() - operands[0].resolved_time.date()).days
        if days < 0:
            return None, ("event occurs after question time",)
        value = f"{days} days"
        relation_line = f"- Deterministic difference from question time: {value}."
    elif operation == "order":
        if len(operands) < 2 or any(item.resolved_time is None for item in operands):
            return None, ("order requires at least two exact dates",)
        resolved_dates = [item.resolved_time.date() for item in operands if item.resolved_time]
        if len(resolved_dates) != len(set(resolved_dates)):
            return None, ("order is unresolved for events on the same date",)
        ordered = sorted(operands, key=lambda item: (item.resolved_time, item.name))
        value = " -> ".join(item.name for item in ordered)
        relation_line = f"- Deterministic chronological order: {value}."
    elif operation in {"absolute_date", "event_at_query_date"}:
        if len(operands) != 1 or operands[0].resolved_time is None:
            return None, (f"{operation} requires one exact date",)
        value = operands[0].resolved_time.date().isoformat()
        relation_line = f"- Resolved event date: {value}."
    elif operation == "duration_from_text":
        if not operands or any(item.duration_value is None for item in operands):
            return None, ("duration_from_text requires explicit durations",)
        units = {item.duration_unit for item in operands}
        if len(units) != 1:
            return None, ("duration units differ",)
        total = sum(
            (item.duration_value for item in operands if item.duration_value is not None),
            Decimal(0),
        )
        unit = next(iter(units))
        formatted = _format_decimal(total)
        value = f"{formatted} {unit}{'' if total == 1 else 's'}"
        relation_line = f"- Deterministic sum of explicit durations: {value}."
    elif operation == "schedule":
        if not operands or any(item.time_basis != "time_of_day" for item in operands):
            return None, ("schedule requires explicit times of day",)
        value = ", ".join(item.time_expression for item in operands)
        relation_line = f"- Explicit schedule values: {value}."

    if value is None or relation_line is None:
        return None, (f"operation {operation} is not safely computable",)
    guidance = "\n".join(
        [
            "TEMPORAL RELATION (deterministically computed from model-aligned citations; inferred, not stored truth):",
            *_evidence_lines(operands),
            relation_line,
            "Verify event interpretation against the cited records.",
        ]
    )
    return TemporalRelation(
        operation=operation,
        operands=operands,
        value=value,
        guidance=guidance,
    ), errors


def _packed_candidates(bundle: MemoryBundle) -> list[RetrievalCandidate]:
    return [candidate for values in bundle.sections.values() for candidate in values]


def _combine_guidance(current: str | None, relation: str) -> str:
    return f"{current}\n{relation}" if current else relation


def repack_with_relation(
    bundle: MemoryBundle,
    relation: TemporalRelation,
    config: PackingConfig,
) -> MemoryBundle | None:
    """Add guidance while preserving all cited records and the same budget."""
    candidates = _packed_candidates(bundle)
    levels = {
        candidate.node.id: candidate.representation for candidate in candidates
    }
    required: list[tuple[UUID, RepresentationLevel]] = []
    for operand in relation.operands:
        representation = levels.get(operand.evidence_id)
        if representation is None:
            return None
        required.append((operand.evidence_id, representation))
    guidance = _combine_guidance(bundle.context_guidance, relation.guidance)
    candidate = pack_context(
        candidates,
        config,
        coverage_notice=bundle.coverage_notice,
        context_guidance=guidance,
        _required=tuple(required),
        _require_guidance=True,
    )
    cited = {operand.evidence_id for operand in relation.operands}
    candidate_ids = {
        item.node.id for values in candidate.sections.values() for item in values
    }
    control_ids = {item.node.id for item in candidates}
    if (
        candidate.context_guidance != guidance
        or not cited <= candidate_ids
        or not candidate_ids <= control_ids
        or candidate.token_budget != bundle.token_budget
        or candidate.tokens_used > candidate.token_budget
    ):
        return None
    # Only the already packed candidates were reconsidered. Keep earlier
    # exclusions as well as any records displaced by the relation guidance.
    candidate.excluded_ids = list(
        dict.fromkeys([*bundle.excluded_ids, *candidate.excluded_ids])
    )
    return candidate


class TemporalRelationEnricher:
    """Orchestrate resolution, local computation, gating, and safe repacking."""

    def __init__(
        self,
        config: TemporalRelationConfig,
        resolver: TemporalResolver,
        gate: TemporalRelationGate,
    ) -> None:
        self.config = TemporalRelationConfig.model_validate(
            config.model_dump(mode="python")
        )
        self._resolver = resolver
        self._gate = gate

    def _metadata(
        self,
        *,
        status: TemporalRelationStatus,
        control: MemoryBundle,
        started: float,
        result: MemoryBundle | None = None,
        relation: TemporalRelation | None = None,
        errors: tuple[str, ...] = (),
        resolver: ResolverAudit | None = None,
        gate: GateAudit | None = None,
        error_stage: Literal["resolver", "gate"] | None = None,
        error_type: str | None = None,
    ) -> TemporalRelationMetadata:
        result = result or control
        control_ids = {item.node.id for item in _packed_candidates(control)}
        result_ids = {item.node.id for item in _packed_candidates(result)}
        return TemporalRelationMetadata(
            status=status,
            confirmation_protocol_aligned=(
                self.config.confirmation_protocol_aligned
            ),
            configuration_sha256=self.config.configuration_sha256,
            operation=relation.operation if relation else None,
            value=relation.value if relation else None,
            evidence_ids=(
                tuple(operand.evidence_id for operand in relation.operands)
                if relation
                else ()
            ),
            validation_errors=errors,
            resolver=resolver,
            gate=gate,
            gate_threshold=self.config.gate_threshold,
            control_context_sha256=hashlib.sha256(control.render().encode()).hexdigest(),
            result_context_sha256=hashlib.sha256(result.render().encode()).hexdigest(),
            guidance_sha256=(
                hashlib.sha256(relation.guidance.encode()).hexdigest()
                if relation
                else None
            ),
            dropped_record_ids=tuple(sorted(control_ids - result_ids, key=str)),
            error_stage=error_stage,
            error_type=error_type,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    async def enrich(
        self,
        query: str,
        bundle: MemoryBundle,
        *,
        question_time: datetime,
        packing_config: PackingConfig,
    ) -> tuple[MemoryBundle, TemporalRelationMetadata]:
        started = time.perf_counter()
        records = records_from_bundle(bundle)
        if not records:
            return bundle, self._metadata(
                status="empty_context", control=bundle, started=started
            )
        try:
            resolved = await self._resolver.resolve(query, question_time, records)
        except Exception as exc:
            if self.config.failure_policy == "raise":
                raise
            return bundle, self._metadata(
                status="provider_error",
                control=bundle,
                started=started,
                error_stage="resolver",
                error_type=type(exc).__name__,
            )

        record_map = {record.id: record for record in records}
        relation, errors = compute_relation(
            resolved.resolution, record_map, question_time
        )
        if relation is None:
            status: TemporalRelationStatus = (
                "unsupported"
                if resolved.resolution.operation == "unsupported" and not errors
                else "validation_rejected"
            )
            return bundle, self._metadata(
                status=status,
                control=bundle,
                started=started,
                errors=errors,
                resolver=resolved.audit,
            )
        try:
            gate = await self._gate.assess(query, relation)
        except Exception as exc:
            if self.config.failure_policy == "raise":
                raise
            return bundle, self._metadata(
                status="provider_error",
                control=bundle,
                started=started,
                relation=relation,
                resolver=resolved.audit,
                error_stage="gate",
                error_type=type(exc).__name__,
            )
        if gate.minimum_probability < self.config.gate_threshold:
            return bundle, self._metadata(
                status="gate_rejected",
                control=bundle,
                started=started,
                relation=relation,
                resolver=resolved.audit,
                gate=gate,
            )
        enriched = repack_with_relation(bundle, relation, packing_config)
        if enriched is None:
            return bundle, self._metadata(
                status="packing_rejected",
                control=bundle,
                started=started,
                relation=relation,
                resolver=resolved.audit,
                gate=gate,
            )
        return enriched, self._metadata(
            status="accepted",
            control=bundle,
            result=enriched,
            started=started,
            relation=relation,
            resolver=resolved.audit,
            gate=gate,
        )
