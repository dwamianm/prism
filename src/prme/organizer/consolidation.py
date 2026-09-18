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
from uuid import UUID, uuid5

from prme.models.consolidation import (
    ConsolidationPublication,
    consolidation_key,
    consolidation_request_hash,
)
from prme.models.derivation import PreparedEmbedding
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.models import ConsolidationResult
from prme.types import (
    EdgeType,
    DecayProfile,
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


def _stable_source_key(node: MemoryNode) -> tuple:
    """Order equivalent source histories independently of generated UUIDs."""
    source_time = node.event_time or node.created_at
    return (
        node.user_id,
        node.scope.value,
        source_time,
        node.content.casefold(),
        node.content,
        str(node.id),
    )


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

    for node in sorted(all_nodes, key=_stable_source_key):
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
        centroid_id = min(
            member_ids,
            key=lambda mid: (
                -node_map[mid].confidence,
                _stable_source_key(node_map[mid]),
            ),
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
    summary, _created = await _consolidate_cluster(engine, cluster)
    return summary


async def _lineage_summaries(
    engine: MemoryEngine, *, user_id: str, scope, key: str
) -> tuple[list[MemoryNode], list[MemoryNode]]:
    """Return active and retired summaries for one exact owner-scoped lineage."""
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
            if (
                metadata.get("consolidation_summary") is True
                and metadata.get("consolidation_key") == key
            ):
                (active if node.lifecycle_state in active_states else retired).append(node)
        after_id = str(page[-1].id)
    return active, retired


async def publish_consolidation_plan(
    engine: MemoryEngine,
    plan: ConsolidationPublication,
    *,
    retired: tuple[MemoryNode, ...] | list[MemoryNode] = (),
) -> MemoryNode:
    """Stage and atomically publish a prepared extractive summary plan."""
    owner = plan.node.user_id
    if engine._pool is None:
        import asyncio
        import duckdb

        from prme.models.consolidation import StaleConsolidationError
        from prme.storage.consolidation_publication import ConsolidationStageFence

        fence = ConsolidationStageFence(
            engine._conn, engine._event_store._conn_lock, plan
        )
        for attempt in range(10):
            try:
                await engine._vector_index.stage(
                    plan.embedding, user_id=owner, fence=fence
                )
                await engine._lexical_index.stage_consolidation(plan, fence=fence)
                break
            except (duckdb.ConstraintException, duckdb.TransactionException) as exc:
                if attempt == 9:
                    raise StaleConsolidationError(
                        "Concurrent consolidation staging did not settle; retry"
                    ) from exc
                await asyncio.sleep(0.01 * (attempt + 1))
            except ValueError as exc:
                if "LockBusy" not in str(exc) or attempt == 9:
                    raise
                await asyncio.sleep(0.01 * (attempt + 1))
    node_id = await engine._write_queue.submit(
        lambda: engine._graph_store.publish_consolidation(plan),
        label=f"consolidation.publish:{plan.node.id}",
    )
    for stale in (*plan.previous, *retired):
        await engine._evict_from_indexes(str(stale.id))
    published = await engine.get_node(node_id)
    if published is None:
        raise RuntimeError("Consolidation publication did not produce an active summary")
    return published


async def _consolidate_cluster(
    engine: MemoryEngine,
    cluster: MemoryCluster,
) -> tuple[MemoryNode, bool]:
    """Prepare, stage, and atomically publish one extractive summary."""
    # Fetch all member nodes from a de-duplicated input identity list.
    members: list[MemoryNode] = []
    for mid in dict.fromkeys(cluster.member_ids):
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
    selected = sorted(
        members,
        key=lambda node: (-node.confidence, _stable_source_key(node)),
    )[:3]
    summary_content = f"[Consolidated excerpt: {len(selected)} of {len(members)} memories]\n" + "\n\n".join(
        _render_source(m) for m in selected
    )
    coverage = {str(m.id): _source_fingerprint(m) for m in selected}
    avg_confidence = sum(m.confidence for m in selected) / len(selected)
    max_salience = max(m.salience for m in selected)
    evidence_refs = list(dict.fromkeys(ref for m in selected for ref in [m.id, *m.evidence_refs]))
    members = sorted(members, key=lambda node: str(node.id))
    selected_ids = [str(node.id) for node in selected]
    key = consolidation_key(owner, centroid.scope, (node.id for node in members))
    provider = engine._vector_index._provider
    request_hash = consolidation_request_hash(
        members,
        content=summary_content,
        confidence=avg_confidence,
        salience=max_salience,
        selected_ids=selected_ids,
        embedding_identity=(
            provider.model_name,
            provider.model_version,
            provider.dimension,
        ),
    )
    previous, retired = await _lineage_summaries(
        engine, user_id=owner, scope=centroid.scope, key=key
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
        str(summary_id), user_id=owner
    )
    if plan is None:
        summary_node = MemoryNode(
            id=summary_id,
            user_id=owner,
            node_type=NodeType.SUMMARY,
            scope=centroid.scope,
            content=summary_content,
            metadata={
                "consolidation_summary": True,
                "consolidation_key": key,
                "consolidation_request_hash": request_hash,
                "consolidation_generation": generation,
                "consolidation_coverage": coverage,
                "source_node_ids": [str(node.id) for node in members],
                "selected_source_node_ids": selected_ids,
                "cluster_size": len(members),
                "consolidation_content_sha256": hashlib.sha256(summary_content.encode()).hexdigest(),
                "consolidation_format_version": 2,
            },
            evidence_refs=evidence_refs,
            confidence=avg_confidence,
            confidence_base=avg_confidence,
            salience=max_salience,
            salience_base=max_salience,
            epistemic_type=EpistemicType.INFERRED,
            source_type=SourceType.SYSTEM_INFERRED,
            decay_profile=DecayProfile.FAST,
        )
        edges = tuple(
            MemoryEdge(
                id=uuid5(summary_id, f"derived-from:{member.id}"),
                source_id=summary_id,
                target_id=member.id,
                edge_type=EdgeType.DERIVED_FROM,
                user_id=owner,
                confidence=avg_confidence,
                valid_from=summary_node.created_at,
                created_at=summary_node.created_at,
            )
            for member in selected
        )
        from prme.storage.embedding import encode_texts

        vectors = await encode_texts(provider, [summary_content])
        if len(vectors) != 1:
            raise ValueError("Consolidation embedding provider must return exactly one vector")
        plan = ConsolidationPublication(
            node=summary_node,
            sources=tuple(members),
            previous=tuple(sorted(previous, key=lambda node: str(node.id))),
            edges=edges,
            embedding=PreparedEmbedding(
                node_id=summary_id,
                content=summary_content,
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
            summary_node, created = await _consolidate_cluster(engine, cluster)
            summaries_created += int(created)
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
