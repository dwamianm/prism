"""Deduplication logic for the organizer (issue #11).

Proposes duplicate memory nodes via vector similarity and exact content
matching, then atomically publishes compatible copies, their evidence and
relationships, source retirement and one SUPERSEDES edge. A checksummed
operation records complete merge inputs and outputs.

Similarity is only a proposal signal. Merging requires equivalent text,
provenance/type metadata and effective validity (except named entity identity).
Canonical selection prefers confidence, evidence count, then age.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from prme.organizer.merge_policy import compatible_provenance, duplicate_merge_allowed
from prme.types import LifecycleState, Scope

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
    user_id: str | None = None,
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
        user_id: When given, only this user's nodes are scanned.

    Returns:
        List of DuplicateCandidate pairs within one user and scope.
    """
    start = time.monotonic()
    threshold = config.dedup_similarity_threshold

    # Fetch active nodes
    nodes = await engine.query_nodes(
        user_id=user_id,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=batch_size,
    )

    # Track already-paired IDs to avoid duplicates in output
    seen_pairs: set[tuple[str, str]] = set()
    candidates: list[DuplicateCandidate] = []

    # Matching text does not grant permission to combine namespaces.
    content_groups: dict[tuple[str, Scope, str], list[MemoryNode]] = {}
    for node in nodes:
        key = (node.user_id, node.scope, node.content.strip().lower())
        content_groups.setdefault(key, []).append(node)

    # Phase 1: Exact content matches
    for _content_key, group in content_groups.items():
        if len(group) < 2:
            continue
        # Normalized text proposes candidates; application checks equivalence.
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if not compatible_provenance(group[i], group[j]):
                    continue
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
                scope=[node.scope.value],
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

            # Recheck durable graph ownership/scope before proposing a merge;
            # an index may lag graph updates or be supplied by a custom backend.
            other_node = await engine.get_node(other_id, user_id=node.user_id)
            if other_node is None or not compatible_provenance(node, other_node):
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

    Each backend revalidates the pair and selects its canonical node inside a
    transaction covering evidence, relationship copies, source retirement,
    supersedence and the immutable operation record. Index eviction follows
    commit. A failed transaction leaves no partial merge visible.

    Args:
        engine: The MemoryEngine for storage operations.
        duplicates: List of DuplicateCandidate pairs from find_duplicates().

    Returns:
        Count of newly merged (superseded) nodes.
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

        # Revalidate at application time, including caller-constructed pairs.
        if (node_a.user_id, node_a.scope) != (node_b.user_id, node_b.scope):
            logger.warning(
                "Refusing to merge cross-namespace duplicate pair (%s, %s)",
                dup.node_a_id,
                dup.node_b_id,
            )
            continue

        # Recompute equivalence from durable values, not candidate labels or a
        # similarity threshold. A caller-constructed "exact" pair is not proof.
        if not duplicate_merge_allowed(node_a, node_b):
            logger.debug("Retaining non-equivalent duplicate candidates (%s, %s)", dup.node_a_id, dup.node_b_id)
            continue

        # Skip if either is not in a mergeable state
        if node_a.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
            continue
        if node_b.lifecycle_state not in (LifecycleState.TENTATIVE, LifecycleState.STABLE):
            continue

        try:
            result = await engine._graph_store.merge_nodes(
                dup.node_a_id, dup.node_b_id, user_id=node_a.user_id,
                kind="duplicate", score=dup.similarity,
            )
            if result is None or not result.applied:
                continue
            canonical_id, duplicate_id = result.canonical_id, result.retired_id
            # Graph publication is already durable. Index eviction is repairable
            # and candidate admission checks the retired lifecycle independently.
            await engine._evict_from_indexes(duplicate_id)

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
