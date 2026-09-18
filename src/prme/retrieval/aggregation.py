"""Exact aggregation over stored structured assertions."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import re
import unicodedata
from uuid import UUID

from prme.ingestion.grounding import normalize_quantity_unit, validate_extracted_quantity
from prme.ingestion.schema import ExtractedQuantity
from prme.models.aggregation import (
    AssertionAggregation,
    AssertionField,
    AssertionGroup,
    AssertionQuery,
    QuantityAggregation,
    QuantityAggregationQuery,
    QuantityGroup,
    QuantityGroupField,
    QuantitySample,
)
from prme.models.nodes import MemoryNode
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.models import RetrievalCandidate
from prme.types import LifecycleState, RetrievalMode


_PREDICATE_SEPARATOR_RE = re.compile(r"[\s\-]+")
_WHITESPACE_RE = re.compile(r"\s+")
_ASSERTION_FIELDS: tuple[AssertionField, ...] = (
    "subject",
    "predicate",
    "object",
    "polarity",
)


def normalize_assertion_value(field_name: AssertionField, value: str) -> str:
    """Normalize one exact selector without inferring semantic equivalence."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    if field_name == "predicate":
        normalized = _PREDICATE_SEPARATOR_RE.sub("_", normalized)
    return normalized


def _assertion_values(node: MemoryNode) -> dict[AssertionField, str] | None:
    metadata = node.metadata or {}
    values: dict[AssertionField, str] = {}
    for field_name in ("subject", "predicate", "object"):
        value = metadata.get(field_name)
        if not isinstance(value, str) or not value.strip():
            return None
        values[field_name] = value.strip()
    polarity = metadata.get("polarity", "positive")
    if not isinstance(polarity, str) or not polarity.strip():
        return None
    values["polarity"] = polarity.strip()
    return values


def _time_exclusion(
    node: MemoryNode, query: AssertionQuery | QuantityAggregationQuery
) -> str | None:
    if query.knowledge_at is not None and node.created_at > query.knowledge_at:
        return "after_knowledge_cutoff"
    if query.valid_at is not None and not (
        node.valid_from <= query.valid_at
        and (node.valid_to is None or node.valid_to > query.valid_at)
    ):
        return "outside_validity_window"
    if query.event_time_from is not None or query.event_time_to is not None:
        if node.event_time is None:
            return "missing_event_time"
        if (
            query.event_time_from is not None
            and node.event_time < query.event_time_from
        ):
            return "before_event_time_window"
        if query.event_time_to is not None and node.event_time > query.event_time_to:
            return "after_event_time_window"
    return None


@dataclass
class _Group:
    values: dict[AssertionField, str]
    normalized_values: dict[AssertionField, str]
    occurrence_count: int = 0
    evidence_refs: set[UUID] = field(default_factory=set)
    node_ids: list[UUID] = field(default_factory=list)
    earliest_event_time: datetime | None = None
    latest_event_time: datetime | None = None

    def add(self, node: MemoryNode, *, sample_limit: int) -> None:
        self.occurrence_count += 1
        self.evidence_refs.update(node.evidence_refs)
        if len(self.node_ids) < sample_limit:
            self.node_ids.append(node.id)
        if node.event_time is not None:
            if (
                self.earliest_event_time is None
                or node.event_time < self.earliest_event_time
            ):
                self.earliest_event_time = node.event_time
            if (
                self.latest_event_time is None
                or node.event_time > self.latest_event_time
            ):
                self.latest_event_time = node.event_time


def _selector_sets(
    query: AssertionQuery | QuantityAggregationQuery,
) -> dict[AssertionField, set[str]]:
    return {
        "subject": {
            normalize_assertion_value("subject", value) for value in query.subjects
        },
        "predicate": {
            normalize_assertion_value("predicate", value) for value in query.predicates
        },
        "object": {
            normalize_assertion_value("object", value) for value in query.objects
        },
        "polarity": {
            normalize_assertion_value("polarity", value) for value in query.polarities
        },
    }


@dataclass
class _ScanStats:
    scanned_nodes: int = 0
    exclusions: Counter[str] = field(default_factory=Counter)


async def _matching_assertions(
    engine,
    query: AssertionQuery | QuantityAggregationQuery,
    *,
    user_id: str,
    batch_size: int,
    stats: _ScanStats,
) -> AsyncIterator[
    tuple[MemoryNode, dict[AssertionField, str], dict[AssertionField, str]]
]:
    """Yield every exact matching assertion and accumulate transparent exclusions."""
    selectors = _selector_sets(query)
    predicate_prefixes = (
        {
            normalize_assertion_value("predicate", value)
            for value in query.predicate_prefixes
        }
        if isinstance(query, QuantityAggregationQuery)
        else set()
    )
    if query.lifecycle_states is not None:
        lifecycle_states = list(query.lifecycle_states)
    elif query.retrieval_mode == RetrievalMode.EXPLICIT:
        lifecycle_states = list(LifecycleState)
    else:
        lifecycle_states = None

    scopes = query.scopes or (None,)
    for scope in scopes:
        for node_type in query.node_types:
            cursor = None
            while True:
                page = await engine.scan_nodes(
                    user_id=user_id,
                    scope=scope,
                    node_type=node_type,
                    lifecycle_states=lifecycle_states,
                    after_id=cursor,
                    limit=batch_size,
                )
                stats.scanned_nodes += len(page)
                if not page:
                    break

                candidates = [RetrievalCandidate(node=node) for node in page]
                kept, epistemic_exclusions = filter_epistemic(
                    candidates,
                    query.retrieval_mode,
                    unverified_threshold=engine._unverified_confidence_threshold,
                    filter_lifecycle=False,
                )
                kept_ids = {candidate.node.id for candidate in kept}
                for excluded in epistemic_exclusions:
                    stats.exclusions[excluded.reason.split(":", 1)[0]] += 1

                for node in page:
                    if node.id not in kept_ids:
                        continue
                    reason = _time_exclusion(node, query)
                    if reason is not None:
                        stats.exclusions[reason] += 1
                        continue
                    values = _assertion_values(node)
                    if values is None:
                        stats.exclusions["missing_structured_assertion"] += 1
                        continue
                    normalized = {
                        field_name: normalize_assertion_value(
                            field_name, values[field_name]
                        )
                        for field_name in _ASSERTION_FIELDS
                    }
                    predicate_matches = (
                        not selectors["predicate"] and not predicate_prefixes
                    ) or normalized["predicate"] in selectors["predicate"] or any(
                        normalized["predicate"] == prefix
                        or normalized["predicate"].startswith(prefix + "_")
                        for prefix in predicate_prefixes
                    )
                    if not predicate_matches or any(
                        field_name != "predicate"
                        and selectors[field_name]
                        and normalized[field_name] not in selectors[field_name]
                        for field_name in _ASSERTION_FIELDS
                    ):
                        stats.exclusions["selector_mismatch"] += 1
                        continue
                    yield node, values, normalized

                if len(page) < batch_size:
                    break
                cursor = str(page[-1].id)


async def aggregate_assertions(
    engine,
    query: AssertionQuery,
    *,
    user_id: str,
    batch_size: int = 500,
) -> AssertionAggregation:
    """Aggregate every matching stored assertion in bounded scan pages.

    The scan is exact over structured claim metadata and finishes every page
    before returning. As with ``iter_nodes``, concurrent mutations can change
    the set between pages; audited counts require an unchanged store.
    """
    if not user_id or batch_size < 1:
        raise ValueError(
            "aggregate_assertions requires user_id and a positive batch_size"
        )

    stats = _ScanStats()
    groups: dict[tuple[str, ...], _Group] = {}
    matched_records = 0

    async for node, values, normalized in _matching_assertions(
        engine,
        query,
        user_id=user_id,
        batch_size=batch_size,
        stats=stats,
    ):
        matched_records += 1
        key = tuple(normalized[field_name] for field_name in query.group_by)
        group = groups.get(key)
        if group is None:
            group = _Group(
                values={
                    field_name: values[field_name] for field_name in query.group_by
                },
                normalized_values={
                    field_name: normalized[field_name]
                    for field_name in query.group_by
                },
            )
            groups[key] = group
        group.add(node, sample_limit=query.sample_limit)

    ordered = sorted(groups.items(), key=lambda item: item[0])
    returned = ordered[: query.group_limit]
    result_groups = tuple(
        AssertionGroup(
            values=group.values,
            normalized_values=group.normalized_values,
            occurrence_count=group.occurrence_count,
            evidence_count=len(group.evidence_refs),
            sample_node_ids=tuple(
                sorted(group.node_ids, key=str)[: query.sample_limit]
            ),
            sample_evidence_refs=tuple(
                sorted(group.evidence_refs, key=str)[: query.sample_limit]
            ),
            samples_truncated=(
                group.occurrence_count > len(group.node_ids)
                or len(group.evidence_refs) > query.sample_limit
            ),
            earliest_event_time=group.earliest_event_time,
            latest_event_time=group.latest_event_time,
        )
        for _, group in returned
    )
    return AssertionAggregation(
        user_id=user_id,
        query=query,
        scanned_nodes=stats.scanned_nodes,
        matched_records=matched_records,
        distinct_count=len(groups),
        groups=result_groups,
        groups_truncated=len(groups) > len(result_groups),
        exclusions=dict(sorted(stats.exclusions.items())),
    )


@dataclass
class _ExactDecimalTotal:
    """Add finite decimals as scaled integers without context rounding."""

    coefficient: int = 0
    exponent: int = 0
    initialized: bool = False

    def add(self, value: Decimal) -> None:
        parts = value.as_tuple()
        coefficient = int("".join(str(digit) for digit in parts.digits))
        if parts.sign:
            coefficient = -coefficient
        exponent = int(parts.exponent)
        if not self.initialized:
            self.coefficient = coefficient
            self.exponent = exponent
            self.initialized = True
        elif exponent < self.exponent:
            self.coefficient = self.coefficient * 10 ** (self.exponent - exponent) + coefficient
            self.exponent = exponent
        else:
            self.coefficient += coefficient * 10 ** (exponent - self.exponent)

    def value(self) -> Decimal:
        if not self.initialized:
            raise ValueError("cannot read an empty decimal total")
        sign = int(self.coefficient < 0)
        digits = tuple(int(character) for character in str(abs(self.coefficient)))
        return Decimal((sign, digits, self.exponent))


@dataclass
class _QuantityGroupAccumulator:
    values: dict[QuantityGroupField, str]
    normalized_values: dict[QuantityGroupField, str]
    total: _ExactDecimalTotal = field(default_factory=_ExactDecimalTotal)
    value_count: int = 0
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    evidence_refs: set[UUID] = field(default_factory=set)
    samples: list[QuantitySample] = field(default_factory=list)
    earliest_event_time: datetime | None = None
    latest_event_time: datetime | None = None

    def add(
        self, node: MemoryNode, quantity: ExtractedQuantity, *, sample_limit: int
    ) -> None:
        self.value_count += 1
        self.total.add(quantity.value)
        if self.minimum is None or quantity.value < self.minimum:
            self.minimum = quantity.value
        if self.maximum is None or quantity.value > self.maximum:
            self.maximum = quantity.value
        self.evidence_refs.update(node.evidence_refs)
        if len(self.samples) < sample_limit:
            self.samples.append(QuantitySample(
                node_id=node.id,
                value=quantity.value,
                unit=quantity.unit,
                source_text=quantity.source_text,
                evidence_refs=tuple(sorted(node.evidence_refs, key=str)),
            ))
        if node.event_time is not None:
            if self.earliest_event_time is None or node.event_time < self.earliest_event_time:
                self.earliest_event_time = node.event_time
            if self.latest_event_time is None or node.event_time > self.latest_event_time:
                self.latest_event_time = node.event_time


def _stored_quantity(
    node: MemoryNode, assertion_values: dict[AssertionField, str]
) -> tuple[ExtractedQuantity | None, str | None]:
    raw = (node.metadata or {}).get("quantity")
    if raw is None:
        return None, "missing_quantity"
    if (
        not isinstance(raw, dict)
        or raw.get("grounding") != "object_decimal_v1"
        or not isinstance(raw.get("value"), str)
        or not isinstance(raw.get("unit"), str)
        or not isinstance(raw.get("source_text"), str)
    ):
        return None, "invalid_quantity"
    try:
        quantity = ExtractedQuantity.model_validate({
            "value": raw["value"],
            "unit": raw["unit"],
            "source_text": raw["source_text"],
        })
    except ValueError:
        return None, "invalid_quantity"
    evidence = (node.metadata or {}).get("evidence_quote")
    if not isinstance(evidence, str) or not evidence:
        evidence = node.content
    grounded = validate_extracted_quantity(
        quantity,
        object_value=assertion_values["object"],
        claim_passage=evidence,
    )
    if grounded is None:
        return None, "invalid_quantity"
    return grounded, None


async def aggregate_quantities(
    engine,
    query: QuantityAggregationQuery,
    *,
    user_id: str,
    batch_size: int = 500,
) -> QuantityAggregation:
    """Aggregate exact grounded decimals, keeping incompatible units separate."""
    if not user_id or batch_size < 1:
        raise ValueError(
            "aggregate_quantities requires user_id and a positive batch_size"
        )

    stats = _ScanStats()
    groups: dict[tuple[str, ...], _QuantityGroupAccumulator] = {}
    unit_selectors = {
        normalize_quantity_unit(value) for value in query.units
    }
    matched_quantity_records = 0

    async for node, assertion_values, normalized_assertions in _matching_assertions(
        engine,
        query,
        user_id=user_id,
        batch_size=batch_size,
        stats=stats,
    ):
        quantity, reason = _stored_quantity(node, assertion_values)
        if quantity is None:
            stats.exclusions[reason or "invalid_quantity"] += 1
            continue
        normalized_unit = normalize_quantity_unit(quantity.unit)
        if unit_selectors and normalized_unit not in unit_selectors:
            stats.exclusions["unit_mismatch"] += 1
            continue

        matched_quantity_records += 1
        values: dict[QuantityGroupField, str] = {}
        normalized_values: dict[QuantityGroupField, str] = {}
        for field_name in query.group_by:
            if field_name == "unit":
                values[field_name] = quantity.unit
                normalized_values[field_name] = normalized_unit
            else:
                values[field_name] = assertion_values[field_name]
                normalized_values[field_name] = normalized_assertions[field_name]
        key = tuple(normalized_values[field_name] for field_name in query.group_by)
        group = groups.get(key)
        if group is None:
            group = _QuantityGroupAccumulator(
                values=values,
                normalized_values=normalized_values,
            )
            groups[key] = group
        group.add(node, quantity, sample_limit=query.sample_limit)

    ordered = sorted(groups.items(), key=lambda item: item[0])
    returned = ordered[: query.group_limit]
    result_groups = []
    for _, group in returned:
        if group.minimum is None or group.maximum is None:
            raise RuntimeError("quantity group was created without a value")
        result_groups.append(QuantityGroup(
            values=group.values,
            normalized_values=group.normalized_values,
            value_count=group.value_count,
            total=group.total.value(),
            minimum=group.minimum,
            maximum=group.maximum,
            evidence_count=len(group.evidence_refs),
            samples=tuple(group.samples),
            samples_truncated=group.value_count > len(group.samples),
            earliest_event_time=group.earliest_event_time,
            latest_event_time=group.latest_event_time,
        ))
    return QuantityAggregation(
        user_id=user_id,
        query=query,
        scanned_nodes=stats.scanned_nodes,
        matched_quantity_records=matched_quantity_records,
        distinct_count=len(groups),
        groups=tuple(result_groups),
        groups_truncated=len(groups) > len(result_groups),
        semantic_equivalence=(
            "normalized_exact_and_predicate_prefix"
            if query.predicate_prefixes
            else "normalized_exact_only"
        ),
        exclusions=dict(sorted(stats.exclusions.items())),
    )
