"""Entity snapshot generation for context packing.

Generates point-in-time EntitySnapshot views that bundle an entity's
current facts, preferences, decisions, tasks, and relationships into
a single read-only structure. Used by the retrieval pipeline to give
LLMs a complete picture of an entity during context packing.

Snapshots are NOT stored as new nodes -- they are ephemeral views
assembled from the graph at query time (or during organize()).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import LifecycleState, NodeType

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)


@dataclass
class EntitySnapshot:
    """Point-in-time view of an entity and its related memory objects.

    Bundles the entity node with its 1-hop neighborhood grouped by type.
    The summary_text field provides a concise structured summary suitable
    for LLM context injection.
    """

    entity_node: MemoryNode
    facts: list[MemoryNode] = field(default_factory=list)
    preferences: list[MemoryNode] = field(default_factory=list)
    decisions: list[MemoryNode] = field(default_factory=list)
    tasks: list[MemoryNode] = field(default_factory=list)
    relationships: list[MemoryEdge] = field(default_factory=list)
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    summary_text: str = ""


def _build_summary_text(entity_node: MemoryNode, grouped: dict[NodeType, list[MemoryNode]]) -> str:
    """Build a concise structured summary from an entity and its neighbors.

    Format:
        Entity: {content}. Known facts: {fact1}, {fact2}. Preferences: {pref1}.
        Active tasks: {task1}. Recent decisions: {dec1}.

    Args:
        entity_node: The entity node.
        grouped: Neighbor nodes grouped by NodeType.

    Returns:
        Structured summary string.
    """
    parts: list[str] = [f"Entity: {entity_node.content}"]

    facts = grouped.get(NodeType.FACT, [])
    if facts:
        fact_texts = ", ".join(f.content for f in facts)
        parts.append(f"Known facts: {fact_texts}")

    preferences = grouped.get(NodeType.PREFERENCE, [])
    if preferences:
        pref_texts = ", ".join(p.content for p in preferences)
        parts.append(f"Preferences: {pref_texts}")

    tasks = grouped.get(NodeType.TASK, [])
    if tasks:
        task_texts = ", ".join(t.content for t in tasks)
        parts.append(f"Active tasks: {task_texts}")

    decisions = grouped.get(NodeType.DECISION, [])
    if decisions:
        dec_texts = ", ".join(d.content for d in decisions)
        parts.append(f"Recent decisions: {dec_texts}")

    return ". ".join(parts) + "."


async def generate_entity_snapshot(
    engine: MemoryEngine,
    entity_node_id: str,
    *,
    at_time: datetime | None = None,
) -> EntitySnapshot:
    """Generate a point-in-time snapshot for a single entity.

    Retrieves the entity node, traverses its 1-hop neighborhood in the
    graph, groups neighbors by node type, collects edges, and assembles
    a structured summary.

    Args:
        engine: The MemoryEngine providing storage access.
        entity_node_id: String UUID of the entity node.
        at_time: Optional temporal filter -- only include neighbors and
            edges valid at this time. None means current time (no filter).

    Returns:
        EntitySnapshot with grouped neighbors and summary text.

    Raises:
        ValueError: If the entity node does not exist or is not an ENTITY type.
    """
    # Fetch the entity node
    entity_node = await engine._graph_store.get_node(
        entity_node_id, include_superseded=False
    )
    if entity_node is None:
        raise ValueError(f"Entity node {entity_node_id!r} not found")
    if entity_node.node_type != NodeType.ENTITY:
        raise ValueError(
            f"Node {entity_node_id!r} is {entity_node.node_type.value}, not entity"
        )

    # Get 1-hop neighborhood
    neighbors = await engine._graph_store.get_neighborhood(
        entity_node_id,
        max_hops=1,
        valid_at=at_time,
    )

    # Group neighbors by type, filtering to active lifecycle states
    active_states = {LifecycleState.TENTATIVE, LifecycleState.STABLE}
    grouped: dict[NodeType, list[MemoryNode]] = {}
    for node in neighbors:
        if node.lifecycle_state not in active_states:
            continue
        grouped.setdefault(node.node_type, []).append(node)

    # Collect edges involving this entity (both directions)
    edges_out = await engine._graph_store.get_edges(
        source_id=entity_node_id,
        valid_at=at_time,
    )
    edges_in = await engine._graph_store.get_edges(
        target_id=entity_node_id,
        valid_at=at_time,
    )
    all_edges = edges_out + edges_in

    # Build summary
    summary_text = _build_summary_text(entity_node, grouped)

    return EntitySnapshot(
        entity_node=entity_node,
        facts=grouped.get(NodeType.FACT, []),
        preferences=grouped.get(NodeType.PREFERENCE, []),
        decisions=grouped.get(NodeType.DECISION, []),
        tasks=grouped.get(NodeType.TASK, []),
        relationships=all_edges,
        generated_at=datetime.now(timezone.utc),
        summary_text=summary_text,
    )


async def generate_all_entity_snapshots(
    engine: MemoryEngine,
    *,
    user_id: str | None = None,
    at_time: datetime | None = None,
    limit: int = 50,
) -> list[EntitySnapshot]:
    """Generate snapshots for all active entity nodes.

    Queries all ENTITY nodes in active lifecycle states, then generates
    a snapshot for each.

    Args:
        engine: The MemoryEngine providing storage access.
        user_id: Optional user scope filter.
        at_time: Optional temporal filter for snapshot generation.
        limit: Maximum number of entities to snapshot.

    Returns:
        List of EntitySnapshot instances.
    """
    entities = await engine._graph_store.query_nodes(
        node_type=NodeType.ENTITY,
        user_id=user_id,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=limit,
    )

    snapshots: list[EntitySnapshot] = []
    for entity in entities:
        try:
            snap = await generate_entity_snapshot(
                engine, str(entity.id), at_time=at_time
            )
            snapshots.append(snap)
        except ValueError:
            logger.warning(
                "Skipping entity %s during bulk snapshot generation",
                entity.id,
                exc_info=True,
            )

    return snapshots


def render_snapshot_text(snapshot: EntitySnapshot) -> str:
    """Render an EntitySnapshot as text suitable for context packing.

    Produces a structured text block that can be included in a MemoryBundle
    for LLM consumption.

    Args:
        snapshot: The snapshot to render.

    Returns:
        Formatted text representation.
    """
    lines: list[str] = [
        f"[Entity Snapshot: {snapshot.entity_node.content}]",
        f"  ID: {snapshot.entity_node.id}",
        f"  Generated: {snapshot.generated_at.isoformat()}",
    ]

    if snapshot.facts:
        lines.append("  Facts:")
        for f in snapshot.facts:
            lines.append(f"    - {f.content}")

    if snapshot.preferences:
        lines.append("  Preferences:")
        for p in snapshot.preferences:
            lines.append(f"    - {p.content}")

    if snapshot.decisions:
        lines.append("  Decisions:")
        for d in snapshot.decisions:
            lines.append(f"    - {d.content}")

    if snapshot.tasks:
        lines.append("  Tasks:")
        for t in snapshot.tasks:
            lines.append(f"    - {t.content}")

    if snapshot.relationships:
        lines.append(f"  Relationships: {len(snapshot.relationships)} edges")

    return "\n".join(lines)
