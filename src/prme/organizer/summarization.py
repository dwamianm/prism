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
from uuid import UUID

from prme.config import OrganizerConfig
from prme.ingestion.graph_writer import WriteQueueGraphWriter
from prme.storage.write_queue import WriteTracker
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.models import JobResult
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


async def _create_summary_node(
    engine: MemoryEngine,
    level: SummarizationLevel,
    period_key: str,
    source_nodes: list[MemoryNode],
    user_id: str,
) -> MemoryNode | None:
    """Create a summary node and DERIVED_FROM edges for its sources.

    Returns the created MemoryNode, or None if creation failed.
    """
    if not source_nodes:
        return None
    scope = source_nodes[0].scope
    if any(node.user_id != user_id or node.scope != scope for node in source_nodes):
        raise ValueError("Summary sources must share the requested user and scope")
    content = _build_summary_content(level, period_key, source_nodes)
    evidence_refs = []
    for node in source_nodes:
        evidence_refs.extend(node.evidence_refs)
        evidence_refs.append(node.id)
    # Deduplicate while preserving order
    seen: set[UUID] = set()
    unique_refs: list[UUID] = []
    for ref in evidence_refs:
        if ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)

    salience = _compute_summary_salience(source_nodes)
    confidence = _compute_summary_confidence(source_nodes)

    summary_node = MemoryNode(
        user_id=user_id,
        node_type=NodeType.SUMMARY,
        content=content,
        metadata={
            "summarization_level": level.value,
            "summary_format": "source-excerpts-v1",
            "period_key": period_key,
            "source_count": len(source_nodes),
            "source_node_ids": [str(n.id) for n in source_nodes],
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
        event_time=min(n.event_time or n.created_at for n in source_nodes),
        pinned=False,
    )

    tracker = WriteTracker()
    writer = WriteQueueGraphWriter(engine._graph_store, engine._write_queue, tracker)
    try:
        await writer.create_node(summary_node)
        for source_node in source_nodes:
            await writer.create_edge(MemoryEdge(
                source_id=summary_node.id, target_id=source_node.id,
                edge_type=EdgeType.DERIVED_FROM, user_id=user_id, confidence=1.0,
                metadata={"summarization_level": level.value, "period_key": period_key},
            ))
        await engine._write_queue.submit(
            lambda: engine._vector_index.index(str(summary_node.id), content, user_id),
            label=f"vector.summary:{summary_node.id}",
        )
        await engine._write_queue.submit(
            lambda: engine._lexical_index.index(
                str(summary_node.id), content, user_id, NodeType.SUMMARY.value, scope.value,
            ),
            label=f"lexical.summary:{summary_node.id}",
        )
    except Exception as exc:
        await tracker.rollback(
            engine._graph_store, engine._write_queue,
            vector_index=engine._vector_index, lexical_index=engine._lexical_index,
        )
        logger.warning("Failed to materialize %s summary for %s (%s)",
                       level.value, period_key, type(exc).__name__)
        return None

    return summary_node


async def _get_existing_summary_periods(
    engine: MemoryEngine,
    level: SummarizationLevel,
    user_id: str | None = None,
) -> set[tuple[str, Scope, str]]:
    """Get set of period_keys for which summaries already exist.

    Queries SUMMARY nodes with matching summarization_level in metadata.
    """
    existing_summaries = await engine._graph_store.query_nodes(
        node_type=NodeType.SUMMARY,
        user_id=user_id,
        lifecycle_states=[LifecycleState.STABLE, LifecycleState.TENTATIVE],
        limit=1000,
    )
    period_keys: set[tuple[str, Scope, str]] = set()
    for node in existing_summaries:
        if node.metadata and node.metadata.get("summarization_level") == level.value:
            pk = node.metadata.get("period_key")
            if pk:
                period_keys.add((node.user_id, node.scope, pk))
    return period_keys


async def generate_daily_summaries(
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> JobResult:
    """Generate daily summaries from events/nodes.

    Groups non-summary active nodes by calendar day and creates a summary
    for each day that has at least summarization_daily_min_events items.
    Skips days that already have a daily summary.

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
    all_nodes = await engine._graph_store.query_nodes(
        lifecycle_states=active_states,
        user_id=user_id,
        limit=5000,
    )

    # Filter out existing summary nodes
    source_nodes = [n for n in all_nodes if n.node_type != NodeType.SUMMARY]

    # Get existing daily summary period keys
    existing_periods = await _get_existing_summary_periods(
        engine, SummarizationLevel.DAILY, user_id
    )

    # Group by day
    day_groups = _group_scoped(source_nodes, _group_nodes_by_day)

    for namespace_period, nodes in sorted(day_groups.items()):
        summary_user_id, _scope, day_key = namespace_period
        # Check budget
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        # Skip if already summarized
        if namespace_period in existing_periods:
            continue

        # Skip if not enough events
        if len(nodes) < config.summarization_daily_min_events:
            continue

        processed += 1
        top_nodes = _select_top_salient(nodes, config.summarization_max_items_per_summary)

        # Use first node's user_id for the summary
        result = await _create_summary_node(
            engine,
            SummarizationLevel.DAILY,
            day_key,
            top_nodes,
            summary_user_id,
        )
        if result is not None:
            modified += 1
        else:
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
    daily_summaries = await engine._graph_store.query_nodes(
        node_type=NodeType.SUMMARY,
        lifecycle_states=[LifecycleState.STABLE, LifecycleState.TENTATIVE],
        user_id=user_id,
        limit=1000,
    )
    daily_summaries = [
        n for n in daily_summaries
        if n.metadata and n.metadata.get("summarization_level") == SummarizationLevel.DAILY.value
    ]

    # Get existing weekly summary period keys
    existing_periods = await _get_existing_summary_periods(
        engine, SummarizationLevel.WEEKLY, user_id
    )

    # Group daily summaries by week
    week_groups = _group_scoped(daily_summaries, _group_nodes_by_week)

    for namespace_period, nodes in sorted(week_groups.items()):
        summary_user_id, _scope, week_key = namespace_period
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        if namespace_period in existing_periods:
            continue

        if len(nodes) < config.summarization_weekly_min_summaries:
            continue

        processed += 1
        top_nodes = _select_top_salient(nodes, config.summarization_max_items_per_summary)

        result = await _create_summary_node(
            engine,
            SummarizationLevel.WEEKLY,
            week_key,
            top_nodes,
            summary_user_id,
        )
        if result is not None:
            modified += 1
        else:
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
    weekly_summaries = await engine._graph_store.query_nodes(
        node_type=NodeType.SUMMARY,
        lifecycle_states=[LifecycleState.STABLE, LifecycleState.TENTATIVE],
        user_id=user_id,
        limit=1000,
    )
    weekly_summaries = [
        n for n in weekly_summaries
        if n.metadata and n.metadata.get("summarization_level") == SummarizationLevel.WEEKLY.value
    ]

    # Get existing monthly summary period keys
    existing_periods = await _get_existing_summary_periods(
        engine, SummarizationLevel.MONTHLY, user_id
    )

    # Group weekly summaries by month
    month_groups = _group_scoped(weekly_summaries, _group_nodes_by_month)

    for namespace_period, nodes in sorted(month_groups.items()):
        summary_user_id, _scope, month_key = namespace_period
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        if namespace_period in existing_periods:
            continue

        if len(nodes) < config.summarization_monthly_min_summaries:
            continue

        processed += 1
        top_nodes = _select_top_salient(nodes, config.summarization_max_items_per_summary)

        result = await _create_summary_node(
            engine,
            SummarizationLevel.MONTHLY,
            month_key,
            top_nodes,
            summary_user_id,
        )
        if result is not None:
            modified += 1
        else:
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
