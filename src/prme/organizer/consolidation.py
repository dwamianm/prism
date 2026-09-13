"""Predictive forgetting / consolidation pipeline (issue #22).

Identifies clusters of semantically similar episodic memories and creates
source-labelled extractive excerpts. Only fully represented, unchanged
sources are eligible for retirement. Similarity alone never justifies loss.

All clustering uses vector similarity (no LLM required). Consolidation
is extractive: the summary picks the highest-confidence content from the
cluster rather than generating new text.

This is a Layer 3 (explicit organize) job -- too expensive for
opportunistic maintenance.
"""

from __future__ import annotations

import logging
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.models import ConsolidationResult
from prme.types import (
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
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


def _render_source(node: MemoryNode) -> str:
    return (
        f"[source={node.id}; event_time={node.event_time}; "
        f"valid_from={node.valid_from}; valid_to={node.valid_to}; "
        f"epistemic={node.epistemic_type.value}; source_type={node.source_type.value}]\n"
        f"{node.content}"
    )


def _source_fingerprint(node: MemoryNode) -> str:
    snapshot = {"source": _render_source(node), "user_id": node.user_id, "scope": node.scope.value}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


async def cluster_similar_memories(
    engine: MemoryEngine,
    *,
    user_id: str | None = None,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.80,
) -> list[MemoryCluster]:
    """Find clusters of semantically similar episodic memories.

    Uses greedy clustering: for each unassigned node, find all similar
    nodes via vector search. Form a cluster if size >= min_cluster_size.

    Only considers active episodic node types (FACT, EVENT, NOTE).

    Args:
        engine: The MemoryEngine for storage operations.
        user_id: When given, only this user's memories are clustered.
        min_cluster_size: Minimum members to form a valid cluster.
        similarity_threshold: Minimum cosine similarity to include in cluster.

    Returns:
        List of MemoryCluster objects, each with a single owner.
    """
    # Fetch active episodic nodes
    episodic_types = [NodeType.FACT, NodeType.EVENT, NodeType.NOTE]
    active_states = [LifecycleState.TENTATIVE, LifecycleState.STABLE]

    all_nodes: list[MemoryNode] = []
    for ntype in episodic_types:
        nodes = await engine.query_nodes(
            user_id=user_id,
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

    for node in sorted(all_nodes, key=lambda n: str(n.id)):
        nid = str(node.id)
        if nid in assigned:
            continue

        # Find similar nodes via vector search
        try:
            results = await engine._vector_index.search(
                node.content,
                node.user_id,
                k=50, scope=[node.scope.value],
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
            # A cluster becomes one summary node with one owner, so members
            # from another tenant would leak content into it (issue #66).
            if (node_map[rid].user_id, node_map[rid].scope) != (node.user_id, node.scope):
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
    """Create a source-labelled SUMMARY excerpt within one namespace.

    The summary is extractive: it combines the highest-confidence content
    from the cluster members. The summary node gets:
    - node_type = SUMMARY
    - epistemic_type = INFERRED (system-generated abstraction)
    - confidence = average confidence of selected members
    - salience = max salience of selected members
    - evidence_refs = selected member IDs and their original evidence

    Creates DERIVED_FROM edges only for the fully represented sources.

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
        if node is not None and node.lifecycle_state in {LifecycleState.TENTATIVE, LifecycleState.STABLE}:
            members.append(node)

    if not members:
        raise ValueError("No valid member nodes found for consolidation")

    # A summary belongs to the centroid's namespace. A stale/malformed
    # cluster cannot combine private/project contents or different owners.
    centroid = next((m for m in members if str(m.id) == cluster.centroid_id), None)
    if centroid is None:
        raise ValueError("Consolidation centroid no longer exists")
    owner = centroid.user_id
    members = [m for m in members if (m.user_id, m.scope) == (owner, centroid.scope)]

    # This is an extractive excerpt, not a lossless abstraction of every
    # cluster member. Preserve complete source text and its temporal meaning.
    selected = sorted(members, key=lambda n: (-n.confidence, str(n.id)))[:3]
    summary_content = f"[Consolidated excerpt: {len(selected)} of {len(members)} memories]\n" + "\n\n".join(
        _render_source(m) for m in selected
    )
    coverage = {str(m.id): _source_fingerprint(m) for m in selected}
    avg_confidence = sum(m.confidence for m in selected) / len(selected)
    max_salience = max(m.salience for m in selected)
    evidence_refs = list(dict.fromkeys(ref for m in selected for ref in [m.id, *m.evidence_refs]))

    user_id = owner

    # Store the summary node via engine.store()
    _event_id = await engine.store(
        summary_content,
        user_id=user_id,
        node_type=NodeType.SUMMARY,
        scope=centroid.scope,
        metadata={
            "consolidation_coverage": coverage, "cluster_size": len(members),
            "consolidation_content_sha256": hashlib.sha256(summary_content.encode()).hexdigest(),
        },
        confidence=avg_confidence,
        epistemic_type=EpistemicType.INFERRED,
        source_type=SourceType.SYSTEM_INFERRED,
    )

    # Retrieve the created summary node
    nodes = await engine.query_nodes(
        user_id=user_id, node_type=NodeType.SUMMARY,
        content_contains_any=[str(selected[0].id)], limit=500,
    )
    summary_node: MemoryNode | None = None
    for n in nodes:
        if any(str(ref) == _event_id for ref in n.evidence_refs):
            summary_node = n
            break

    if summary_node is None:
        raise RuntimeError("Failed to retrieve created summary node")

    # Update the summary node with proper evidence_refs and salience
    await engine._graph_store.update_node(
        str(summary_node.id),
        evidence_refs=list(dict.fromkeys([*summary_node.evidence_refs, *evidence_refs])),
        salience_base=max_salience,
        salience=max_salience,
        confidence_base=avg_confidence,
        confidence=avg_confidence,
    )

    # Create DERIVED_FROM edges from summary to each member
    for member in selected:
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
    user_id: str | None = None,
    preserve_recent_days: int = 7,
    min_confidence_preserve: float = 0.8,
) -> int:
    """Archive individual episodic nodes that were consolidated.

    Preserves:
    - High-confidence nodes (>= min_confidence_preserve)
    - Recent nodes (created < preserve_recent_days ago)
    - Pinned, unrepresented, changed, or other-namespace nodes
    - All nodes when the summary is retired or lacks verified coverage

    Marks archived nodes with superseded_by pointing to the summary.

    Args:
        engine: The MemoryEngine for storage operations.
        cluster: The cluster whose members to consider for archival.
        summary_node_id: ID of the summary node that supersedes members.
        user_id: Owner of the summary; members belonging to anyone else are
            left alone.
        preserve_recent_days: Don't archive memories newer than this.
        min_confidence_preserve: Don't archive memories with confidence >= this.

    Returns:
        Count of archived nodes.
    """
    import math
    if (type(preserve_recent_days) is not int or preserve_recent_days < 0
            or not math.isfinite(min_confidence_preserve)
            or not 0 <= min_confidence_preserve <= 1):
        raise ValueError("Invalid consolidation retirement policy")
    now = datetime.now(timezone.utc)
    retired = 0
    for source_id in dict.fromkeys(cluster.member_ids):
        changed = await engine._graph_store.retire_consolidated(
            source_id, summary_node_id, user_id=user_id, at=now,
            preserve_recent_days=preserve_recent_days,
            min_confidence_preserve=min_confidence_preserve,
        )
        if changed:
            # External index eviction follows the durable transaction. An
            # error must never compensate by archiving against stale evidence.
            retired += 1
            await engine._evict_from_indexes(source_id)
    return retired


async def run_consolidation_pipeline(
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> ConsolidationResult:
    """Run the full consolidation pipeline: cluster, consolidate, forget.

    Budget-aware: checks elapsed time between stages and stops early
    if the budget is exceeded.

    Args:
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with consolidation parameters.
        budget_ms: Time budget in milliseconds.
        user_id: When given, only this user's memories are consolidated.

    Returns:
        ConsolidationResult with pipeline statistics.
    """
    start = time.monotonic()
    result = ConsolidationResult()

    # Stage 1: Cluster similar memories
    clusters = await cluster_similar_memories(
        engine,
        user_id=user_id,
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
            total_consolidated += len((summary_node.metadata or {}).get("consolidation_coverage", {}))

            # Forget
            archived = await forget_consolidated(
                engine,
                cluster,
                str(summary_node.id),
                user_id=summary_node.user_id,
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
