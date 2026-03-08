"""Deduplication logic for the organizer (issue #11).

Detects duplicate memory nodes via vector similarity and exact content
matching, then merges them by archiving the lower-quality duplicate and
creating a SUPERSEDES edge from the canonical (kept) node to the
duplicate. Evidence refs and edges are transferred to the canonical node.

Conservative by design: only merges when vector similarity >= 0.92
(configurable). The canonical node is the one with higher confidence or
more evidence_refs; ties broken by creation time (older wins).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID

from prme.models.edges import MemoryEdge
from prme.types import EdgeType, LifecycleState

if TYPE_CHECKING:
    from prme.config import OrganizerConfig
    from prme.models.nodes import MemoryNode
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


class DuplicateCandidate:
    """A pair of nodes identified as potential duplicates."""

    __slots__ = ("node_a_id", "node_b_id", "similarity", "match_type")

    def __init__(
        self,
        node_a_id: str,
        node_b_id: str,
        similarity: float,
        match_type: str,
    ) -> None:
        self.node_a_id = node_a_id
        self.node_b_id = node_b_id
        self.similarity = similarity
        self.match_type = match_type  # "exact" or "semantic"

    def __repr__(self) -> str:
        return (
            f"DuplicateCandidate({self.node_a_id!r}, {self.node_b_id!r}, "
            f"sim={self.similarity:.4f}, type={self.match_type!r})"
        )


async def find_duplicates(
    engine: MemoryEngine,
    config: OrganizerConfig,
    batch_size: int = 100,
    budget_ms: float = 5000.0,
) -> list[DuplicateCandidate]:
    """Find duplicate nodes via vector similarity and exact content match.

    Iterates through active nodes, for each performing a vector search to
    find similar nodes. Pairs exceeding the similarity threshold are
    returned as duplicate candidates.

    Args:
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with dedup_similarity_threshold.
        batch_size: Max nodes to scan per call.
        budget_ms: Time budget in milliseconds.

    Returns:
        List of DuplicateCandidate pairs.
    """
    start = time.monotonic()
    threshold = config.dedup_similarity_threshold

    # Fetch active nodes
    nodes = await engine.query_nodes(
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=batch_size,
    )

    # Track already-paired IDs to avoid duplicates in output
    seen_pairs: set[tuple[str, str]] = set()
    candidates: list[DuplicateCandidate] = []

    # Index by normalized content for exact matching
    content_groups: dict[str, list[MemoryNode]] = {}
    for node in nodes:
        key = node.content.strip().lower()
        content_groups.setdefault(key, []).append(node)

    # Phase 1: Exact content matches
    for _content_key, group in content_groups.items():
        if len(group) < 2:
            continue
        # All nodes with identical content (case-insensitive) are duplicates
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                # Budget check
                elapsed_ms = (time.monotonic() - start) * 1000.0
                if elapsed_ms >= budget_ms:
                    return candidates

                a_id = str(group[i].id)
                b_id = str(group[j].id)
                pair_key = (min(a_id, b_id), max(a_id, b_id))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    candidates.append(
                        DuplicateCandidate(a_id, b_id, 1.0, "exact")
                    )

    # Phase 2: Semantic similarity via vector search
    for node in nodes:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        node_id = str(node.id)

        # Search for similar vectors
        try:
            results = await engine._vector_index.search(
                node.content,
                node.user_id,
                k=10,
            )
        except Exception:
            logger.debug("Vector search failed for node %s", node_id, exc_info=True)
            continue

        for result in results:
            other_id = result["node_id"]
            score = result["score"]

            # Skip self-matches
            if other_id == node_id:
                continue

            # Skip below threshold
            if score < threshold:
                continue

            pair_key = (min(node_id, other_id), max(node_id, other_id))
            if pair_key in seen_pairs:
                continue

            seen_pairs.add(pair_key)
            candidates.append(
                DuplicateCandidate(node_id, other_id, score, "semantic")
            )

    return candidates


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def _pick_canonical(node_a: MemoryNode, node_b: MemoryNode) -> tuple[MemoryNode, MemoryNode]:
    """Choose which node to keep (canonical) and which to archive (duplicate).

    Priority:
    1. Higher confidence_base
    2. More evidence_refs
    3. Older created_at (first to exist wins)

    Returns:
        (canonical, duplicate) tuple.
    """
    # Compare confidence
    if node_a.confidence_base > node_b.confidence_base:
        return (node_a, node_b)
    if node_b.confidence_base > node_a.confidence_base:
        return (node_b, node_a)

    # Equal confidence: compare evidence count
    if len(node_a.evidence_refs) > len(node_b.evidence_refs):
        return (node_a, node_b)
    if len(node_b.evidence_refs) > len(node_a.evidence_refs):
        return (node_b, node_a)

    # Equal evidence: older node wins
    if node_a.created_at <= node_b.created_at:
        return (node_a, node_b)
    return (node_b, node_a)


async def merge_duplicates(
    engine: MemoryEngine,
    duplicates: list[DuplicateCandidate],
) -> int:
    """Merge duplicate node pairs.

    For each pair:
    1. Determine canonical (kept) vs duplicate (archived) node.
    2. Transfer evidence_refs from duplicate to canonical.
    3. Transfer edges from duplicate to canonical.
    4. Create SUPERSEDES edge from canonical to duplicate.
    5. Archive the duplicate node.

    Args:
        engine: The MemoryEngine for storage operations.
        duplicates: List of DuplicateCandidate pairs from find_duplicates().

    Returns:
        Count of nodes merged (archived).
    """
    merged_count = 0
    # Track nodes already merged to avoid double-processing
    merged_ids: set[str] = set()

    for dup in duplicates:
        # Skip if either node was already merged in this pass
        if dup.node_a_id in merged_ids or dup.node_b_id in merged_ids:
            continue

        # Fetch both nodes
        node_a = await engine.get_node(dup.node_a_id)
        node_b = await engine.get_node(dup.node_b_id)

        if node_a is None or node_b is None:
            # One was already archived/superseded
            continue

        # Skip if either is not in a mergeable state
        if node_a.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
            continue
        if node_b.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
            continue

        canonical, duplicate = _pick_canonical(node_a, node_b)
        canonical_id = str(canonical.id)
        duplicate_id = str(duplicate.id)

        try:
            # Transfer evidence_refs from duplicate to canonical
            new_refs = list(canonical.evidence_refs)
            for ref in duplicate.evidence_refs:
                if ref not in new_refs:
                    new_refs.append(ref)

            if len(new_refs) > len(canonical.evidence_refs):
                await engine._graph_store.update_node(
                    canonical_id, evidence_refs=new_refs
                )

            # Transfer edges from duplicate to canonical
            await _transfer_edges(engine, duplicate_id, canonical_id)

            # Create SUPERSEDES edge from canonical to duplicate
            supersedes_edge = MemoryEdge(
                source_id=UUID(canonical_id),
                target_id=UUID(duplicate_id),
                edge_type=EdgeType.SUPERSEDES,
                user_id=canonical.user_id,
                confidence=1.0,
                metadata={"reason": "deduplication", "similarity": dup.similarity},
            )
            await engine._graph_store.create_edge(supersedes_edge)

            # Supersede the duplicate (sets lifecycle_state and superseded_by)
            await engine.supersede(duplicate_id, canonical_id)

            merged_ids.add(duplicate_id)
            merged_count += 1

            logger.info(
                "Merged duplicate: %s -> %s (sim=%.4f, type=%s)",
                duplicate_id,
                canonical_id,
                dup.similarity,
                dup.match_type,
            )

        except Exception:
            logger.warning(
                "Failed to merge duplicate pair (%s, %s)",
                dup.node_a_id,
                dup.node_b_id,
                exc_info=True,
            )

    return merged_count


async def _transfer_edges(
    engine: MemoryEngine,
    from_node_id: str,
    to_node_id: str,
) -> None:
    """Transfer edges from one node to another.

    For each edge where from_node is source or target, create a
    corresponding edge pointing to/from to_node. SUPERSEDES edges
    are not transferred (they are structural, not semantic).
    """
    graph = engine._graph_store

    # Get all edges where from_node is source
    outgoing = await graph.get_edges(source_id=from_node_id)
    for edge in outgoing:
        if edge.edge_type == EdgeType.SUPERSEDES:
            continue
        # Skip self-referential edges to the canonical node
        if str(edge.target_id) == to_node_id:
            continue
        new_edge = MemoryEdge(
            source_id=UUID(to_node_id),
            target_id=edge.target_id,
            edge_type=edge.edge_type,
            user_id=edge.user_id,
            confidence=edge.confidence,
            metadata=edge.metadata,
        )
        try:
            await graph.create_edge(new_edge)
        except Exception:
            logger.debug(
                "Failed to transfer outgoing edge %s", edge.id, exc_info=True
            )

    # Get all edges where from_node is target
    incoming = await graph.get_edges(target_id=from_node_id)
    for edge in incoming:
        if edge.edge_type == EdgeType.SUPERSEDES:
            continue
        # Skip self-referential edges from the canonical node
        if str(edge.source_id) == to_node_id:
            continue
        new_edge = MemoryEdge(
            source_id=edge.source_id,
            target_id=UUID(to_node_id),
            edge_type=edge.edge_type,
            user_id=edge.user_id,
            confidence=edge.confidence,
            metadata=edge.metadata,
        )
        try:
            await graph.create_edge(new_edge)
        except Exception:
            logger.debug(
                "Failed to transfer incoming edge %s", edge.id, exc_info=True
            )
