"""Exact aggregation over stored structured assertions."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
import re
import unicodedata
from uuid import UUID

from prme.models.aggregation import (
    AssertionAggregation,
    AssertionField,
    AssertionGroup,
    AssertionQuery,
)
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


def _assertion_values(node) -> dict[AssertionField, str] | None:
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


def _time_exclusion(node, query: AssertionQuery) -> str | None:
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

    def add(self, node) -> None:
        self.occurrence_count += 1
        self.evidence_refs.update(node.evidence_refs)
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


def _selector_sets(query: AssertionQuery) -> dict[AssertionField, set[str]]:
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

    selectors = _selector_sets(query)
    exclusions: Counter[str] = Counter()
    groups: dict[tuple[str, ...], _Group] = {}
    scanned_nodes = 0
    matched_records = 0

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
                scanned_nodes += len(page)
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
                    exclusions[excluded.reason.split(":", 1)[0]] += 1

                for node in page:
                    if node.id not in kept_ids:
                        continue
                    reason = _time_exclusion(node, query)
                    if reason is not None:
                        exclusions[reason] += 1
                        continue
                    values = _assertion_values(node)
                    if values is None:
                        exclusions["missing_structured_assertion"] += 1
                        continue
                    normalized = {
                        field_name: normalize_assertion_value(
                            field_name, values[field_name]
                        )
                        for field_name in _ASSERTION_FIELDS
                    }
                    if any(
                        selectors[field_name]
                        and normalized[field_name] not in selectors[field_name]
                        for field_name in _ASSERTION_FIELDS
                    ):
                        exclusions["selector_mismatch"] += 1
                        continue

                    matched_records += 1
                    key = tuple(normalized[field_name] for field_name in query.group_by)
                    group = groups.get(key)
                    if group is None:
                        group = _Group(
                            values={
                                field_name: values[field_name]
                                for field_name in query.group_by
                            },
                            normalized_values={
                                field_name: normalized[field_name]
                                for field_name in query.group_by
                            },
                        )
                        groups[key] = group
                    group.add(node)

                if len(page) < batch_size:
                    break
                cursor = str(page[-1].id)

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
                len(group.node_ids) > query.sample_limit
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
        scanned_nodes=scanned_nodes,
        matched_records=matched_records,
        distinct_count=len(groups),
        groups=result_groups,
        groups_truncated=len(groups) > len(result_groups),
        exclusions=dict(sorted(exclusions.items())),
    )
