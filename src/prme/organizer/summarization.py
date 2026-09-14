"""Hierarchical summarization pipeline (daily -> weekly -> monthly).

Implements LLM-free extractive summarization for the organize() job.
Groups events/nodes by time window, selects top-N most salient items,
and creates Summary nodes linked to their sources via evidence_refs
and DERIVED_FROM edges.

Summary hierarchy:
- Daily: Groups events by calendar day, picks top-N by salience
- Weekly: Rolls up daily summaries into weekly summaries
- Monthly: Rolls up weekly summaries into monthly summaries

Summaries are source excerpts, not newly observed truths. They retain source
qualifiers, epistemic labels, event times, and user/scope boundaries.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import timezone
from enum import Enum
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

from prme.config import OrganizerConfig
from prme.models.consolidation import (
    ConsolidationPublication,
    consolidation_request_hash,
)
from prme.models.derivation import PreparedEmbedding
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.consolidation import publish_consolidation_plan
from prme.organizer.models import JobResult
from prme.storage.embedding import encode_texts
from prme.types import (
    DecayProfile,
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    SourceType,
)

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)


class SummarizationLevel(str, Enum):
    """Hierarchical summarization level."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


def _group_nodes_by_day(nodes: list[MemoryNode]) -> dict[str, list[MemoryNode]]:
    """Group nodes by calendar day (YYYY-MM-DD)."""
    groups: dict[str, list[MemoryNode]] = defaultdict(list)
    for node in nodes:
        day_key = (node.event_time or node.created_at).astimezone(timezone.utc).strftime("%Y-%m-%d")
        groups[day_key] = groups.get(day_key, [])
        groups[day_key].append(node)
    return dict(groups)


def _group_nodes_by_week(nodes: list[MemoryNode]) -> dict[str, list[MemoryNode]]:
    """Group nodes by ISO week (YYYY-Www)."""
    groups: dict[str, list[MemoryNode]] = defaultdict(list)
    for node in nodes:
        iso = (node.event_time or node.created_at).astimezone(timezone.utc).isocalendar()
        week_key = f"{iso[0]}-W{iso[1]:02d}"
        groups[week_key] = groups.get(week_key, [])
        groups[week_key].append(node)
    return dict(groups)


def _group_nodes_by_month(nodes: list[MemoryNode]) -> dict[str, list[MemoryNode]]:
    """Group nodes by calendar month (YYYY-MM)."""
    groups: dict[str, list[MemoryNode]] = defaultdict(list)
    for node in nodes:
        month_key = (node.event_time or node.created_at).astimezone(timezone.utc).strftime("%Y-%m")
        groups[month_key] = groups.get(month_key, [])
        groups[month_key].append(node)
    return dict(groups)


def _group_scoped(nodes: list[MemoryNode], grouper) -> dict[tuple[str, Scope, str], list[MemoryNode]]:
    namespaces: dict[tuple[str, Scope], list[MemoryNode]] = defaultdict(list)
    for node in nodes:
        namespaces[(node.user_id, node.scope)].append(node)
    return {
        (user_id, scope, period): sources
        for (user_id, scope), group in namespaces.items()
        for period, sources in grouper(group).items()
    }


def _select_top_salient(
    nodes: list[MemoryNode], max_items: int
) -> list[MemoryNode]:
    """Select top-N nodes by salience_base descending.

    Ties broken by confidence_base descending, then created_at descending.
    """
    sorted_nodes = sorted(
        nodes,
        key=lambda n: (-n.salience_base, -n.confidence_base, -n.created_at.timestamp(), str(n.id)),
    )
    return sorted_nodes[:max_items]


def _build_summary_content(
    level: SummarizationLevel,
    period_key: str,
    source_nodes: list[MemoryNode],
) -> str:
    """Build extractive summary content from source nodes.

    Concatenates the content of the top-N most salient items, prefixed
    with the time period identifier.
    """
    lines = [f"[{level.value} summary: {period_key}; selected source excerpts, not exhaustive]"]
    for node in source_nodes:
        lines.append(json.dumps({
            "source_id": str(node.id), "epistemic_type": node.epistemic_type.value,
            "source_type": node.source_type.value,
            "event_time": (node.event_time or node.created_at).isoformat(),
            "valid_from": node.valid_from.isoformat(),
            "valid_to": node.valid_to.isoformat() if node.valid_to else None,
            "content": node.content,
        }, ensure_ascii=False))
    return "\n".join(lines)


def _compute_summary_salience(source_nodes: list[MemoryNode]) -> float:
    """Compute summary salience as average of source salience_base values.

    Clamped to [0.0, 1.0].
    """
    if not source_nodes:
        return 0.5
    avg = sum(n.salience_base for n in source_nodes) / len(source_nodes)
    return max(0.0, min(1.0, avg))


def _compute_summary_confidence(source_nodes: list[MemoryNode]) -> float:
    """Compute summary confidence as average of source confidence_base values.

    Clamped to [0.0, 1.0].
    """
    if not source_nodes:
        return 0.5
    avg = sum(n.confidence_base for n in source_nodes) / len(source_nodes)
    return max(0.0, min(1.0, avg))


def _summary_publication_key(
    user_id: str,
    scope: Scope,
    level: SummarizationLevel,
    period_key: str,
) -> str:
    """Return the stable lineage identity for one hierarchical time bucket."""
    if not user_id or not period_key:
        raise ValueError("A summary publication key requires an owner and period")
    return str(
        uuid5(
            NAMESPACE_URL,
            json.dumps(
                [
                    "prme-hierarchical-summary-v2",
                    user_id,
                    scope.value,
                    level.value,
                    period_key,
                ],
                separators=(",", ":"),
            ),
        )
    )


async def _hierarchical_lineage_summaries(
    engine: MemoryEngine,
    *,
    user_id: str,
    scope: Scope,
    level: SummarizationLevel,
    period_key: str,
    key: str,
) -> tuple[list[MemoryNode], list[MemoryNode]]:
    """Load current and retired summaries, including the pre-journal format."""
    active_states = {LifecycleState.TENTATIVE, LifecycleState.STABLE}
    active: list[MemoryNode] = []
    retired: list[MemoryNode] = []
    after_id: str | None = None
    while True:
        page = await engine._graph_store.scan_nodes(
            user_id=user_id,
            scope=scope,
            node_type=NodeType.SUMMARY,
            lifecycle_states=list(LifecycleState),
            after_id=after_id,
            limit=500,
        )
        if not page:
            break
        for node in page:
            metadata = node.metadata or {}
            managed = (
                metadata.get("consolidation_summary") is True
                and metadata.get("consolidation_key") == key
            )
            legacy = (
                metadata.get("consolidation_key") is None
                and metadata.get("summarization_level") == level.value
                and metadata.get("period_key") == period_key
                and metadata.get("summary_format") == "source-excerpts-v1"
            )
            if managed or legacy:
                (active if node.lifecycle_state in active_states else retired).append(node)
        after_id = str(page[-1].id)
    return active, retired


async def _scan_summary_inputs(
    engine: MemoryEngine,
    *,
    user_id: str | None,
    node_type: NodeType | None = None,
    lifecycle_states: list[LifecycleState],
) -> list[MemoryNode]:
    """Read every organizer input through stable pages, including operator runs."""
    nodes: list[MemoryNode] = []
    after_id: str | None = None
    while True:
        page = await engine._graph_store.scan_nodes(
            user_id=user_id,
            node_type=node_type,
            lifecycle_states=lifecycle_states,
            after_id=after_id,
            limit=500,
            operator_unscoped=user_id is None,
        )
        if not page:
            break
        nodes.extend(page)
        after_id = str(page[-1].id)
    return nodes


async def _materialize_summary_node(
    engine: MemoryEngine,
    level: SummarizationLevel,
    period_key: str,
    source_nodes: list[MemoryNode],
    user_id: str,
) -> tuple[MemoryNode | None, bool]:
    """Prepare and atomically publish or reuse one hierarchical summary."""
    if not source_nodes:
        return None, False
    scope = source_nodes[0].scope
    if any(node.user_id != user_id or node.scope != scope for node in source_nodes):
        raise ValueError("Summary sources must share the requested user and scope")
    selected_nodes = list(source_nodes)
    source_nodes = sorted(selected_nodes, key=lambda node: str(node.id))
    content = _build_summary_content(level, period_key, selected_nodes)
    evidence_refs = []
    for node in selected_nodes:
        evidence_refs.extend(node.evidence_refs)
        evidence_refs.append(node.id)
    # Deduplicate while preserving order
    seen: set[UUID] = set()
    unique_refs: list[UUID] = []
    for ref in evidence_refs:
        if ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)

    salience = _compute_summary_salience(selected_nodes)
    confidence = _compute_summary_confidence(selected_nodes)
    key = _summary_publication_key(user_id, scope, level, period_key)
    provider = engine._vector_index._provider
    source_ids = [str(node.id) for node in source_nodes]
    selected_ids = [str(node.id) for node in selected_nodes]
    request_hash = consolidation_request_hash(
        source_nodes,
        content=content,
        confidence=confidence,
        salience=salience,
        selected_ids=selected_ids,
        embedding_identity=(
            provider.model_name,
            provider.model_version,
            provider.dimension,
        ),
        policy="hierarchical_source_excerpts_v2",
    )
    previous, retired = await _hierarchical_lineage_summaries(
        engine,
        user_id=user_id,
        scope=scope,
        level=level,
        period_key=period_key,
        key=key,
    )
    matching = [
        node
        for node in previous
        if (node.metadata or {}).get("consolidation_request_hash") == request_hash
    ]
    if len(previous) == 1 and len(matching) == 1:
        for stale in retired:
            await engine._evict_from_indexes(str(stale.id))
        return matching[0], False

    generation = await engine._graph_store.consolidation_generation(key)
    summary_id = uuid5(UUID(key), f"{request_hash}:{generation}")
    plan = await engine._graph_store.get_prepared_consolidation(
        str(summary_id), user_id=user_id
    )
    if plan is None:
        summary_node = MemoryNode(
            id=summary_id,
            user_id=user_id,
            node_type=NodeType.SUMMARY,
            content=content,
            metadata={
                "consolidation_summary": True,
                "summary_publication_kind": "hierarchical_source_excerpts_v2",
                "summary_publication_key": key,
                "consolidation_key": key,
                "consolidation_request_hash": request_hash,
                "consolidation_generation": generation,
                "summarization_level": level.value,
                "summary_format": "source-excerpts-v1",
                "period_key": period_key,
                "source_count": len(source_nodes),
                "source_node_ids": source_ids,
                "selected_source_node_ids": selected_ids,
            },
            confidence=confidence,
            confidence_base=confidence,
            salience=salience,
            salience_base=salience,
            epistemic_type=EpistemicType.INFERRED,
            source_type=SourceType.SYSTEM_INFERRED,
            lifecycle_state=LifecycleState.STABLE,
            evidence_refs=unique_refs,
            decay_profile=DecayProfile.SLOW,
            scope=scope,
            event_time=min(n.event_time or n.created_at for n in selected_nodes),
            pinned=False,
        )
        edges = tuple(
            MemoryEdge(
                id=uuid5(summary_id, f"derived-from:{source_node.id}"),
                source_id=summary_id,
                target_id=source_node.id,
                edge_type=EdgeType.DERIVED_FROM,
                user_id=user_id,
                confidence=1.0,
                metadata={
                    "summarization_level": level.value,
                    "period_key": period_key,
                },
                valid_from=summary_node.created_at,
                created_at=summary_node.created_at,
            )
            for source_node in selected_nodes
        )
        vectors = await encode_texts(provider, [content])
        if len(vectors) != 1:
            raise ValueError("Summary embedding provider must return exactly one vector")
        plan = ConsolidationPublication(
            node=summary_node,
            sources=tuple(source_nodes),
            previous=tuple(sorted(previous, key=lambda node: str(node.id))),
            edges=edges,
            embedding=PreparedEmbedding(
                node_id=summary_id,
                content=content,
                model=provider.model_name,
                version=provider.model_version,
                dimension=provider.dimension,
                values=tuple(vectors[0]),
            ),
            generation=generation,
            request_hash=request_hash,
        )
        plan = await engine._graph_store.prepare_consolidation(plan)

    return await publish_consolidation_plan(engine, plan, retired=retired), True


async def _create_summary_node(
    engine: MemoryEngine,
    level: SummarizationLevel,
    period_key: str,
    source_nodes: list[MemoryNode],
    user_id: str,
) -> MemoryNode | None:
    """Create or reuse a summary; retained as a testable compatibility helper."""
    node, _created = await _materialize_summary_node(
        engine, level, period_key, source_nodes, user_id
    )
    return node


async def generate_daily_summaries(
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> JobResult:
    """Generate daily summaries from events/nodes.

    Groups non-summary active nodes by calendar day and creates or refreshes a
    summary for each day that has at least summarization_daily_min_events items.

    Args:
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with summarization thresholds.
        budget_ms: Time budget in milliseconds.
        user_id: Optional user scope.

    Returns:
        JobResult with execution details.
    """
    start = time.monotonic()
    processed = 0
    modified = 0
    errors = 0

    # Fetch active non-summary nodes
    active_states = [
        LifecycleState.TENTATIVE,
        LifecycleState.STABLE,
        LifecycleState.CONTESTED,
    ]
    all_nodes = await _scan_summary_inputs(
        engine,
        lifecycle_states=active_states,
        user_id=user_id,
    )

    # Filter out existing summary nodes
    source_nodes = [n for n in all_nodes if n.node_type != NodeType.SUMMARY]

    # Group by day
    day_groups = _group_scoped(source_nodes, _group_nodes_by_day)

    for namespace_period, nodes in sorted(day_groups.items()):
        summary_user_id, _scope, day_key = namespace_period
        # Check budget
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        # Skip if not enough events
        if len(nodes) < config.summarization_daily_min_events:
            continue

        processed += 1
        top_nodes = _select_top_salient(nodes, config.summarization_max_items_per_summary)

        # Use first node's user_id for the summary
        try:
            result, created = await _materialize_summary_node(
                engine,
                SummarizationLevel.DAILY,
                day_key,
                top_nodes,
                summary_user_id,
            )
            if result is not None and created:
                modified += 1
        except Exception as exc:
            logger.warning(
                "Failed to materialize daily summary for %s (%s)",
                day_key,
                type(exc).__name__,
            )
            errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job="summarize_daily",
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
        details={"level": "daily"},
    )


async def roll_up_weekly(
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> JobResult:
    """Roll up daily summaries into weekly summaries.

    Groups existing daily summary nodes by ISO week and creates a weekly
    summary for each week that has at least summarization_weekly_min_summaries
    daily summaries.

    Args:
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with summarization thresholds.
        budget_ms: Time budget in milliseconds.
        user_id: Optional user scope.

    Returns:
        JobResult with execution details.
    """
    start = time.monotonic()
    processed = 0
    modified = 0
    errors = 0

    # Fetch existing daily summary nodes
    daily_summaries = await _scan_summary_inputs(
        engine,
        node_type=NodeType.SUMMARY,
        lifecycle_states=[LifecycleState.STABLE, LifecycleState.TENTATIVE],
        user_id=user_id,
    )
    daily_summaries = [
        n for n in daily_summaries
        if n.metadata and n.metadata.get("summarization_level") == SummarizationLevel.DAILY.value
    ]

    # Group daily summaries by week
    week_groups = _group_scoped(daily_summaries, _group_nodes_by_week)

    for namespace_period, nodes in sorted(week_groups.items()):
        summary_user_id, _scope, week_key = namespace_period
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        if len(nodes) < config.summarization_weekly_min_summaries:
            continue

        processed += 1
        top_nodes = _select_top_salient(nodes, config.summarization_max_items_per_summary)

        try:
            result, created = await _materialize_summary_node(
                engine,
                SummarizationLevel.WEEKLY,
                week_key,
                top_nodes,
                summary_user_id,
            )
            if result is not None and created:
                modified += 1
        except Exception as exc:
            logger.warning(
                "Failed to materialize weekly summary for %s (%s)",
                week_key,
                type(exc).__name__,
            )
            errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job="summarize_weekly",
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
        details={"level": "weekly"},
    )


async def roll_up_monthly(
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> JobResult:
    """Roll up weekly summaries into monthly summaries.

    Groups existing weekly summary nodes by calendar month and creates a
    monthly summary for each month that has at least
    summarization_monthly_min_summaries weekly summaries.

    Args:
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with summarization thresholds.
        budget_ms: Time budget in milliseconds.
        user_id: Optional user scope.

    Returns:
        JobResult with execution details.
    """
    start = time.monotonic()
    processed = 0
    modified = 0
    errors = 0

    # Fetch existing weekly summary nodes
    weekly_summaries = await _scan_summary_inputs(
        engine,
        node_type=NodeType.SUMMARY,
        lifecycle_states=[LifecycleState.STABLE, LifecycleState.TENTATIVE],
        user_id=user_id,
    )
    weekly_summaries = [
        n for n in weekly_summaries
        if n.metadata and n.metadata.get("summarization_level") == SummarizationLevel.WEEKLY.value
    ]

    # Group weekly summaries by month
    month_groups = _group_scoped(weekly_summaries, _group_nodes_by_month)

    for namespace_period, nodes in sorted(month_groups.items()):
        summary_user_id, _scope, month_key = namespace_period
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        if len(nodes) < config.summarization_monthly_min_summaries:
            continue

        processed += 1
        top_nodes = _select_top_salient(nodes, config.summarization_max_items_per_summary)

        try:
            result, created = await _materialize_summary_node(
                engine,
                SummarizationLevel.MONTHLY,
                month_key,
                top_nodes,
                summary_user_id,
            )
            if result is not None and created:
                modified += 1
        except Exception as exc:
            logger.warning(
                "Failed to materialize monthly summary for %s (%s)",
                month_key,
                type(exc).__name__,
            )
            errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job="summarize_monthly",
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
        details={"level": "monthly"},
    )
