"""Exact, auditable temporal state for structured memory assertions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from prme.models.nodes import MemoryNode
from prme.models.temporal import (
    AssertionState,
    AssertionStateConflict,
    AssertionStateEntry,
    AssertionStateHistoricalCoverage,
    AssertionStateQuery,
    AssertionStateValue,
)
from prme.retrieval.aggregation import _assertion_values, normalize_assertion_value
from prme.retrieval.filtering import filter_epistemic
from prme.retrieval.models import RetrievalCandidate
from prme.types import EdgeType, LifecycleState, RetrievalMode


_ACTIVE_STATES = {
    LifecycleState.TENTATIVE,
    LifecycleState.STABLE,
    LifecycleState.CONTESTED,
}


@dataclass
class _Record:
    node: MemoryNode
    subject: str
    predicate: str
    object: str
    polarity: str
    normalized_object: str
    normalized_polarity: str
    exclusion_reasons: list[str] = field(default_factory=list)
    contradiction_ids: set[UUID] = field(default_factory=set)

    @property
    def current_candidate(self) -> bool:
        return not self.exclusion_reasons


def _eligibility_reasons(engine, node: MemoryNode, query: AssertionStateQuery) -> list[str]:
    reasons: list[str] = []
    if not (
        node.valid_from <= query.valid_at
        and (node.valid_to is None or node.valid_to > query.valid_at)
    ):
        reasons.append("outside_validity_window")
    if node.lifecycle_state not in _ACTIVE_STATES:
        reasons.append(f"lifecycle_not_active:{node.lifecycle_state.value}")
    if node.superseded_by is not None:
        reasons.append("superseded_pointer")

    _, epistemic_exclusions = filter_epistemic(
        [RetrievalCandidate(node=node)],
        RetrievalMode.DEFAULT,
        unverified_threshold=engine._unverified_confidence_threshold,
        filter_lifecycle=False,
    )
    reasons.extend(exclusion.reason for exclusion in epistemic_exclusions)
    return reasons


async def _contradiction_edges(engine, records: list[_Record], *, graph=None):
    """Load touching contradiction edges in bounded ID batches.

    ``graph`` is a facade the caller already resolved. Passing it saves the privacy range
    check that resolving the facade a second time would pay; omitting it keeps the exact
    behavior of every existing caller.
    """
    edges = {}
    if graph is None:
        graph = engine._graph_store
        if records and hasattr(engine, "_ordinary_graph"):
            graph = await engine._ordinary_graph(records[0].node.user_id)
    node_ids = [str(record.node.id) for record in records]
    for offset in range(0, len(node_ids), 200):
        batch = node_ids[offset : offset + 200]
        for edge in await graph.get_edges(
            node_ids=batch,
            edge_type=EdgeType.CONTRADICTS,
            **({"include_superseded": True} if getattr(graph, "protected_reads", False) else {}),
        ):
            edges[edge.id] = edge
    return list(edges.values())


def _entry(record: _Record) -> AssertionStateEntry:
    node = record.node
    return AssertionStateEntry(
        node_id=node.id,
        node_type=node.node_type,
        content=node.content,
        subject=record.subject,
        predicate=record.predicate,
        object=record.object,
        polarity=record.polarity,
        epistemic_type=node.epistemic_type,
        source_type=node.source_type,
        lifecycle_state=node.lifecycle_state,
        confidence=node.confidence,
        event_time=node.event_time,
        created_at=node.created_at,
        updated_at=node.updated_at,
        valid_from=node.valid_from,
        valid_to=node.valid_to,
        superseded_by=node.superseded_by,
        evidence_refs=tuple(node.evidence_refs),
        contradiction_node_ids=tuple(sorted(record.contradiction_ids, key=str)),
        current_candidate=record.current_candidate,
        exclusion_reasons=tuple(record.exclusion_reasons),
    )


def classify_page(
    page: list[MemoryNode],
    *,
    engine,
    query: AssertionStateQuery,
    normalized_subject: str,
    normalized_predicate: str,
    exclusions: Counter[str],
) -> tuple[list[_Record], int]:
    """Turn one page of read nodes into claim records, counting every rejection.

    Returns the records this page contributed and how many of them sat at the asked address.
    The counts are the ones the state reports: a node with no usable assertion, a node at
    another address, a node after the knowledge cutoff, and one reason for each eligibility
    exclusion. Both page sources call this, so the counting rules live in one place.
    """
    records: list[_Record] = []
    matched = 0
    for node in page:
        values = _assertion_values(node)
        if values is None:
            exclusions["missing_structured_assertion"] += 1
            continue
        if (
            normalize_assertion_value("subject", values["subject"])
            != normalized_subject
            or normalize_assertion_value("predicate", values["predicate"])
            != normalized_predicate
        ):
            exclusions["selector_mismatch"] += 1
            continue
        matched += 1
        if query.knowledge_at is not None and node.created_at > query.knowledge_at:
            exclusions["after_knowledge_cutoff"] += 1
            continue
        reasons = _eligibility_reasons(engine, node, query)
        exclusions.update(reason.split(":", 1)[0] for reason in reasons)
        records.append(_Record(
            node=node,
            subject=values["subject"],
            predicate=values["predicate"],
            object=values["object"],
            polarity=values["polarity"],
            normalized_object=normalize_assertion_value("object", values["object"]),
            normalized_polarity=normalize_assertion_value("polarity", values["polarity"]),
            exclusion_reasons=reasons,
        ))
    return records, matched


async def get_assertion_state(
    engine,
    query: AssertionStateQuery,
    *,
    user_id: str,
    batch_size: int = 500,
) -> AssertionState:
    """Resolve eligible claims without inferring truth from recency.

    This is the full-scan path: it reads every claim of one owner and compares each address
    in Python. ``get_assertion_state_by_address`` asks the store for one address instead and
    returns the same state; this path stays as the fallback for a store that cannot answer
    an address read, and as the thing that path is checked against.
    """
    if not user_id or batch_size < 1:
        raise ValueError("get_assertion_state requires user_id and a positive batch_size")

    normalized_subject = normalize_assertion_value("subject", query.subject)
    normalized_predicate = normalize_assertion_value("predicate", query.predicate)
    exclusions: Counter[str] = Counter()
    records: list[_Record] = []
    scanned_nodes = 0
    matched_records = 0

    for node_type in query.node_types:
        cursor = None
        while True:
            page = await engine.scan_nodes(
                user_id=user_id,
                scope=query.scope,
                node_type=node_type,
                lifecycle_states=list(LifecycleState),
                after_id=cursor,
                limit=batch_size,
            )
            scanned_nodes += len(page)
            if not page:
                break
            page_records, page_matched = classify_page(
                page, engine=engine, query=query,
                normalized_subject=normalized_subject,
                normalized_predicate=normalized_predicate,
                exclusions=exclusions,
            )
            records.extend(page_records)
            matched_records += page_matched
            if len(page) < batch_size:
                break
            cursor = str(page[-1].id)

    return await build_state(
        engine, query, user_id=user_id, records=records,
        normalized_subject=normalized_subject, normalized_predicate=normalized_predicate,
        exclusions=exclusions, scanned_nodes=scanned_nodes, matched_records=matched_records,
    )


async def get_assertion_state_by_address(
    engine,
    query: AssertionStateQuery,
    *,
    user_id: str,
    batch_size: int = 500,
) -> AssertionState:
    """Resolve the same state as ``get_assertion_state`` by reading one address.

    The full scan reads every claim of one owner and compares each address in Python. This
    asks the store for the claims that sit at the asked address, which needs the two stored
    address columns. Both paths return the same object; the equality is checked against the
    full scan, not assumed.

    The two scan counts are the only numbers the address read cannot see, because it reads
    one address and they describe every address of the node type. They come from one count
    query instead of a walk: ``missing_structured_assertion`` is how many rows have no
    address at all, and ``selector_mismatch`` is the total minus those minus what this read
    returned.

    Falls back to the full scan on a store that resolves to a protected facade, which
    deliberately exposes no connection: the address read narrows which rows are read, and
    that facade exists to decide which rows may be read. Falls back too on a store without
    the columns.
    """
    if not user_id or batch_size < 1:
        raise ValueError("get_assertion_state_by_address requires user_id and a positive batch_size")

    normalized_subject = normalize_assertion_value("subject", query.subject)
    normalized_predicate = normalize_assertion_value("predicate", query.predicate)
    graph = await engine._ordinary_graph(user_id, scopes=[query.scope])
    if not hasattr(graph, "scan_assertions"):
        return await get_assertion_state(engine, query, user_id=user_id, batch_size=batch_size)
    found = await graph.scan_assertions(
        user_id=user_id, scope=query.scope, node_types=tuple(query.node_types),
        subject_key=normalized_subject, predicate_key=normalized_predicate,
    )
    if found is None:
        return await get_assertion_state(engine, query, user_id=user_id, batch_size=batch_size)
    page, total, unkeyed = found

    exclusions: Counter[str] = Counter()
    records, matched = classify_page(
        page, engine=engine, query=query,
        normalized_subject=normalized_subject, normalized_predicate=normalized_predicate,
        exclusions=exclusions,
    )
    # Both counts are assigned and not added, because they describe the whole queried node
    # type and this page can only see one address. A key is set only when the count is
    # non-zero: the full scan builds a Counter and only ever increments it, so a zero is
    # absent from its map rather than present as 0, and the state reports the map as stored.
    for name, count in (("missing_structured_assertion", unkeyed),
                        ("selector_mismatch", total - unkeyed - matched)):
        if count:
            exclusions[name] = count
        else:
            exclusions.pop(name, None)
    return await build_state(
        engine, query, user_id=user_id, records=records,
        normalized_subject=normalized_subject, normalized_predicate=normalized_predicate,
        exclusions=exclusions, scanned_nodes=total, matched_records=matched,
        graph=graph,
    )


async def build_state(
    engine,
    query: AssertionStateQuery,
    *,
    user_id: str,
    records: list[_Record],
    normalized_subject: str,
    normalized_predicate: str,
    exclusions: Counter[str] | None = None,
    scanned_nodes: int = 0,
    matched_records: int = 0,
    graph=None,
) -> AssertionState:
    """Build the state from the matched records, for either page source.

    Every step after the page lives here: contradiction edges, status, value groups,
    ordering, truncation. A caller that found its records another way passes the same two
    scan counts it would have made, so the state it returns carries the same numbers.

    ``graph`` is the fenced facade the caller already resolved, if it resolved one. The
    contradiction edge read is the one step that needs a graph, and re-resolving it would
    pay the privacy range check a second time for one call.
    """
    if exclusions is None:
        exclusions = Counter()
    visible_ids = {record.node.id for record in records}
    records_by_id = {record.node.id: record for record in records}
    current_ids = {
        record.node.id for record in records if record.current_candidate
    }
    raw_edges = await _contradiction_edges(engine, records, graph=graph) if records else []
    conflicts: list[AssertionStateConflict] = []
    active_conflict = False
    for edge in raw_edges:
        if (
            edge.user_id != user_id
            or edge.source_id not in visible_ids
            or edge.target_id not in visible_ids
            or (
                query.knowledge_at is not None
                and edge.created_at > query.knowledge_at
            )
        ):
            continue
        source = records_by_id[edge.source_id]
        target = records_by_id[edge.target_id]
        source.contradiction_ids.add(edge.target_id)
        target.contradiction_ids.add(edge.source_id)
        edge_active = (
            edge.valid_from <= query.valid_at
            and (edge.valid_to is None or edge.valid_to > query.valid_at)
            and edge.source_id in current_ids
            and edge.target_id in current_ids
        )
        active_conflict = active_conflict or edge_active
        conflicts.append(AssertionStateConflict(
            edge_id=edge.id,
            source_node_id=edge.source_id,
            target_node_id=edge.target_id,
            confidence=edge.confidence,
            evidence_ref=edge.provenance_event_id,
            created_at=edge.created_at,
            valid_from=edge.valid_from,
            valid_to=edge.valid_to,
            active_between_current_candidates=edge_active,
        ))

    current = [record for record in records if record.current_candidate]
    distinct_values = {
        (record.normalized_object, record.normalized_polarity) for record in current
    }
    if not current:
        status = "unknown"
    elif active_conflict or any(
        record.node.lifecycle_state == LifecycleState.CONTESTED for record in current
    ):
        status = "contested"
    elif len(current) == 1:
        status = "single"
    elif len(distinct_values) == 1:
        status = "consistent"
    else:
        status = "multiple"

    grouped: dict[tuple[str, str], list[_Record]] = defaultdict(list)
    for record in current:
        grouped[(record.normalized_object, record.normalized_polarity)].append(record)
    ordered_groups = sorted(grouped.items())
    value_groups = []
    for _, group in ordered_groups[: query.limit]:
        evidence = {ref for record in group for ref in record.node.evidence_refs}
        ordered_node_ids = sorted((record.node.id for record in group), key=str)
        value_groups.append(AssertionStateValue(
            object=group[0].object,
            polarity=group[0].polarity,
            normalized_object=group[0].normalized_object,
            normalized_polarity=group[0].normalized_polarity,
            candidate_count=len(group),
            evidence_count=len(evidence),
            sample_node_ids=tuple(ordered_node_ids[: query.limit]),
            sample_evidence_refs=tuple(sorted(evidence, key=str)[: query.limit]),
            samples_truncated=(
                len(ordered_node_ids) > query.limit or len(evidence) > query.limit
            ),
        ))

    current.sort(key=lambda record: (
        -record.node.confidence,
        -record.node.created_at.timestamp(),
        str(record.node.id),
    ))
    records.sort(key=lambda record: (
        record.node.valid_from,
        record.node.created_at,
        str(record.node.id),
    ))
    conflicts.sort(key=lambda conflict: (conflict.created_at, str(conflict.edge_id)))

    return AssertionState(
        user_id=user_id,
        query=query,
        normalized_subject=normalized_subject,
        normalized_predicate=normalized_predicate,
        status=status,
        scanned_nodes=scanned_nodes,
        matched_records=matched_records,
        current_candidate_count=len(current),
        current_values=tuple(value_groups),
        current_values_truncated=len(ordered_groups) > query.limit,
        current_candidates=tuple(_entry(record) for record in current[: query.limit]),
        current_candidates_truncated=len(current) > query.limit,
        timeline=tuple(_entry(record) for record in records[-query.limit :]),
        timeline_truncated=len(records) > query.limit,
        conflicts=tuple(conflicts[-query.limit :]),
        conflict_count=len(conflicts),
        conflicts_truncated=len(conflicts) > query.limit,
        exclusions=dict(sorted(exclusions.items())),
        historical_coverage=(
            AssertionStateHistoricalCoverage(knowledge_at=query.knowledge_at)
            if query.knowledge_at is not None
            else None
        ),
    )
