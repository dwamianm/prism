"""Predictive forgetting / consolidation pipeline (issue #22).

Identifies clusters of semantically similar episodic memories, abstracts
them into summary nodes, and archives the individual episodes. This is
pattern-based consolidation -- not just time-based -- inspired by
predictive forgetting theory (arXiv 2603.04688).

All clustering uses vector similarity (no LLM required). Consolidation
is extractive: the summary picks the highest-confidence content from the
cluster rather than generating new text.

This is a Layer 3 (explicit organize) job -- too expensive for
opportunistic maintenance.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.models import ConsolidationResult
from prme.types import (
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    SourceType,
)

if TYPE_CHECKING:
    from prme.config import OrganizerConfig
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MemoryCluster:
    """A cluster of semantically similar memory nodes."""

    centroid_id: str
    member_ids: list[str]
    avg_similarity: float
    topic_summary: str


# ---------------------------------------------------------------------------
# Pipeline functions
# ---------------------------------------------------------------------------


async def cluster_similar_memories(
    engine: MemoryEngine,
    *,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.80,
) -> list[MemoryCluster]:
    """Find clusters of semantically similar episodic memories.

    Uses greedy clustering: for each unassigned node, find all similar
    nodes via vector search. Form a cluster if size >= min_cluster_size.

    Only considers active episodic node types (FACT, EVENT, NOTE).

    Args:
        engine: The MemoryEngine for storage operations.
        min_cluster_size: Minimum members to form a valid cluster.
        similarity_threshold: Minimum cosine similarity to include in cluster.

    Returns:
        List of MemoryCluster objects.
    """
    # Fetch active episodic nodes
    episodic_types = [NodeType.FACT, NodeType.EVENT, NodeType.NOTE]
    active_states = [LifecycleState.TENTATIVE, LifecycleState.STABLE]

    all_nodes: list[MemoryNode] = []
    for ntype in episodic_types:
        nodes = await engine.query_nodes(
            node_type=ntype,
            lifecycle_states=active_states,
            limit=500,
        )
        all_nodes.extend(nodes)

    if len(all_nodes) < min_cluster_size:
        return []

    # Build lookup by node ID
    node_map: dict[str, MemoryNode] = {str(n.id): n for n in all_nodes}
    assigned: set[str] = set()
    clusters: list[MemoryCluster] = []

    for node in all_nodes:
        nid = str(node.id)
        if nid in assigned:
            continue

        # Find similar nodes via vector search
        try:
            results = await engine._vector_index.search(
                node.content,
                node.user_id,
                k=50,
            )
        except Exception:
            logger.debug("Vector search failed for node %s, skipping", nid)
            continue

        # Filter to similar, active, unassigned episodic nodes
        member_ids: list[str] = [nid]
        similarities: list[float] = []

        for r in results:
            rid = r["node_id"]
            score = r["score"]

            if rid == nid:
                continue
            if rid in assigned:
                continue
            if rid not in node_map:
                continue
            if score < similarity_threshold:
                continue

            member_ids.append(rid)
            similarities.append(score)

        if len(member_ids) < min_cluster_size:
            continue

        # Form cluster
        avg_sim = sum(similarities) / len(similarities) if similarities else 0.0

        # Centroid = the member with the highest confidence
        centroid_id = max(
            member_ids,
            key=lambda mid: node_map[mid].confidence if mid in node_map else 0.0,
        )

        # Topic summary = content of the centroid node
        centroid_node = node_map.get(centroid_id)
        topic = centroid_node.content if centroid_node else ""

        cluster = MemoryCluster(
            centroid_id=centroid_id,
            member_ids=member_ids,
            avg_similarity=avg_sim,
            topic_summary=topic,
        )
        clusters.append(cluster)

        # Mark all members as assigned
        assigned.update(member_ids)

    return clusters


async def consolidate_cluster(
    engine: MemoryEngine,
    cluster: MemoryCluster,
) -> MemoryNode:
    """Create a SUMMARY node that abstracts a cluster's shared pattern.

    The summary is extractive: it combines the highest-confidence content
    from the cluster members. The summary node gets:
    - node_type = SUMMARY
    - epistemic_type = INFERRED (system-generated abstraction)
    - confidence = average confidence of cluster members
    - salience = max salience of cluster members
    - evidence_refs = all cluster member IDs

    Creates DERIVED_FROM edges from summary to each member.

    Args:
        engine: The MemoryEngine for storage operations.
        cluster: The cluster to consolidate.

    Returns:
        The created SUMMARY MemoryNode.
    """
    # Fetch all member nodes
    members: list[MemoryNode] = []
    for mid in cluster.member_ids:
        node = await engine.get_node(mid, include_superseded=False)
        if node is not None:
            members.append(node)

    if not members:
        raise ValueError("No valid member nodes found for consolidation")

    # Extractive summary: pick the highest-confidence content
    # and combine unique content from top members
    sorted_members = sorted(members, key=lambda n: n.confidence, reverse=True)
    best_content = sorted_members[0].content

    # Build summary content from top 3 unique contents
    seen_content: set[str] = set()
    summary_parts: list[str] = []
    for m in sorted_members:
        if m.content not in seen_content:
            seen_content.add(m.content)
            summary_parts.append(m.content)
            if len(summary_parts) >= 3:
                break

    summary_content = (
        f"[Consolidated from {len(members)} memories] {best_content}"
        if len(summary_parts) == 1
        else f"[Consolidated from {len(members)} memories] "
        + " | ".join(summary_parts)
    )

    # Compute aggregate scores
    avg_confidence = sum(m.confidence for m in members) / len(members)
    max_salience = max(m.salience for m in members)
    evidence_refs = [m.id for m in members]

    # Determine user_id from first member
    user_id = members[0].user_id

    # Store the summary node via engine.store()
    event_id = await engine.store(
        summary_content,
        user_id=user_id,
        node_type=NodeType.SUMMARY,
        scope=Scope.SYSTEM,
        confidence=avg_confidence,
        epistemic_type=EpistemicType.INFERRED,
        source_type=SourceType.SYSTEM_INFERRED,
    )

    # Retrieve the created summary node
    nodes = await engine.query_nodes(user_id=user_id, limit=500)
    summary_node: MemoryNode | None = None
    for n in nodes:
        if n.content == summary_content and n.node_type == NodeType.SUMMARY:
            summary_node = n
            break

    if summary_node is None:
        raise RuntimeError("Failed to retrieve created summary node")

    # Update the summary node with proper evidence_refs and salience
    await engine._graph_store.update_node(
        str(summary_node.id),
        evidence_refs=evidence_refs,
        salience_base=max_salience,
        salience=max_salience,
        confidence_base=avg_confidence,
        confidence=avg_confidence,
    )

    # Create DERIVED_FROM edges from summary to each member
    for member in members:
        edge = MemoryEdge(
            source_id=summary_node.id,
            target_id=member.id,
            edge_type=EdgeType.DERIVED_FROM,
            user_id=user_id,
            confidence=avg_confidence,
        )
        await engine._graph_store.create_edge(edge)

    # Re-fetch the updated node
    updated_node = await engine.get_node(str(summary_node.id))
    return updated_node if updated_node is not None else summary_node


async def forget_consolidated(
    engine: MemoryEngine,
    cluster: MemoryCluster,
    summary_node_id: str,
    *,
    preserve_recent_days: int = 7,
    min_confidence_preserve: float = 0.8,
) -> int:
    """Archive individual episodic nodes that were consolidated.

    Preserves:
    - High-confidence nodes (>= min_confidence_preserve)
    - Recent nodes (created < preserve_recent_days ago)

    Marks archived nodes with superseded_by pointing to the summary.

    Args:
        engine: The MemoryEngine for storage operations.
        cluster: The cluster whose members to consider for archival.
        summary_node_id: ID of the summary node that supersedes members.
        preserve_recent_days: Don't archive memories newer than this.
        min_confidence_preserve: Don't archive memories with confidence >= this.

    Returns:
        Count of archived nodes.
    """
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=preserve_recent_days)
    archived_count = 0

    for mid in cluster.member_ids:
        node = await engine.get_node(mid, include_superseded=False)
        if node is None:
            continue

        # Preserve high-confidence nodes
        if node.confidence >= min_confidence_preserve:
            logger.debug(
                "Preserving high-confidence node %s (confidence=%.2f)",
                mid, node.confidence,
            )
            continue

        # Preserve recent nodes
        if node.created_at > recent_cutoff:
            logger.debug(
                "Preserving recent node %s (created_at=%s)",
                mid, node.created_at,
            )
            continue

        # Archive via supersede (marks superseded_by and transitions state)
        try:
            await engine.supersede(mid, summary_node_id)
            archived_count += 1
        except ValueError:
            # Node may already be in a terminal state
            logger.debug(
                "Could not supersede node %s, attempting direct archive", mid
            )
            try:
                await engine.archive(mid)
                archived_count += 1
            except ValueError:
                logger.debug("Could not archive node %s, skipping", mid)

    return archived_count


async def run_consolidation_pipeline(
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> ConsolidationResult:
    """Run the full consolidation pipeline: cluster, consolidate, forget.

    Budget-aware: checks elapsed time between stages and stops early
    if the budget is exceeded.

    Args:
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with consolidation parameters.
        budget_ms: Time budget in milliseconds.

    Returns:
        ConsolidationResult with pipeline statistics.
    """
    start = time.monotonic()
    result = ConsolidationResult()

    # Stage 1: Cluster similar memories
    clusters = await cluster_similar_memories(
        engine,
        min_cluster_size=config.consolidation_min_cluster_size,
        similarity_threshold=config.consolidation_similarity_threshold,
    )
    result.clusters_found = len(clusters)

    if not clusters:
        result.duration_ms = (time.monotonic() - start) * 1000.0
        return result

    # Stage 2 & 3: Consolidate and forget each cluster
    total_consolidated = 0
    total_archived = 0
    summaries_created = 0

    for cluster in clusters:
        # Check budget
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            logger.info(
                "Consolidation budget exhausted after %d/%d clusters",
                summaries_created, len(clusters),
            )
            break

        try:
            # Consolidate
            summary_node = await consolidate_cluster(engine, cluster)
            summaries_created += 1
            total_consolidated += len(cluster.member_ids)

            # Forget
            archived = await forget_consolidated(
                engine,
                cluster,
                str(summary_node.id),
                preserve_recent_days=config.consolidation_preserve_recent_days,
                min_confidence_preserve=config.consolidation_min_confidence_preserve,
            )
            total_archived += archived

        except Exception:
            logger.warning(
                "Failed to consolidate cluster (centroid=%s)",
                cluster.centroid_id,
                exc_info=True,
            )

    result.nodes_consolidated = total_consolidated
    result.nodes_archived = total_archived
    result.summaries_created = summaries_created
    result.duration_ms = (time.monotonic() - start) * 1000.0

    return result
