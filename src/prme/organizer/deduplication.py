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

from prme.privacy.journals import compatible_admitted_pair
from prme.organizer.merge_policy import compatible_provenance, duplicate_merge_allowed
from prme.types import LifecycleState, Scope

if TYPE_CHECKING:
    from prme.config import OrganizerConfig
    from prme.models.nodes import MemoryNode
    from prme.storage.engine import MemoryEngine

from prme.privacy.diagnostics import ScopedLogger

logger = logging.getLogger(__name__)
logger = ScopedLogger(logger)


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
    from prme.privacy.journals import generation_search
    start = time.monotonic()
    index = await generation_search(engine, user_id=user_id)

    # Fetch active nodes
    nodes = await engine.query_nodes(
        user_id=user_id,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=batch_size,
    )
    candidates, _completed, _search_failed = await _find_duplicates_in_nodes(
        engine,
        config,
        nodes,
        index=index,
        budget_ms=budget_ms,
        started_at=start,
        stop_on_search_error=False,
    )
    return candidates


async def find_duplicates_in_page(
    engine: MemoryEngine,
    config: OrganizerConfig,
    nodes: list[MemoryNode],
    *,
    index=None,
    budget_ms: float = 5000.0,
    user_id: str | None = None,
) -> tuple[list[DuplicateCandidate], int, bool]:
    """Find duplicates among one page of changed nodes and their neighbors.

    Incremental counterpart of :func:`find_duplicates` (dwamianm/prism#517).
    The caller supplies the nodes created or changed since the last run; each
    is still searched against the vector index, so a new copy finds an older
    twin that is not on the page. The second value is how many leading nodes
    were fully searched, which is how far the caller may advance its cursor.
    The third value is whether a neighbor search failed, which the caller
    reports as an error so the run is not mistaken for a budget stop.
    """
    if index is None:
        from prme.privacy.journals import generation_search
        index = await generation_search(engine, user_id=user_id)
    return await _find_duplicates_in_nodes(
        engine,
        config,
        nodes,
        index=index,
        budget_ms=budget_ms,
        stop_on_search_error=True,
    )


async def _find_duplicates_in_nodes(
    engine: MemoryEngine,
    config: OrganizerConfig,
    nodes: list[MemoryNode],
    *,
    index,
    budget_ms: float,
    started_at: float | None = None,
    stop_on_search_error: bool = False,
) -> tuple[list[DuplicateCandidate], int, bool]:
    """Propose duplicate pairs among, and against the neighbors of, *nodes*.

    Returns the candidates, how many leading input nodes were fully searched,
    and whether a search failed. An incremental caller advances its keyset
    cursor only over the searched prefix and stops at the first failure, so a
    page whose budget ran out or whose search failed is searched again rather
    than skipped. The full-scope caller keeps its earlier behavior and skips a
    node whose search raised.
    """
    start = time.monotonic() if started_at is None else started_at
    search_failed = False
    threshold = config.dedup_similarity_threshold

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
                if not compatible_admitted_pair(compatible_provenance, group[i], group[j]):
                    continue
                # Budget check. The exact phase has not finished, so no input
                # node counts as fully searched.
                elapsed_ms = (time.monotonic() - start) * 1000.0
                if elapsed_ms >= budget_ms:
                    return candidates, 0, False

                a_id = str(group[i].id)
                b_id = str(group[j].id)
                pair_key = (min(a_id, b_id), max(a_id, b_id))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    candidates.append(
                        DuplicateCandidate(a_id, b_id, 1.0, "exact")
                    )

    # Phase 2: Semantic similarity via vector search
    completed = 0
    for node in nodes:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        node_id = str(node.id)

        # Search for similar vectors. A failed search stops the incremental
        # prefix rather than counting the node as examined, so this node is
        # retried on the next run instead of being skipped; the full-scope
        # path keeps its earlier behavior and skips it for this run.
        try:
            results = await index.search(
                node.content,
                node.user_id,
                k=10,
                scope=[node.scope.value],
            )
        except Exception:
            logger.debug("Vector search failed for node %s", node_id, exc_info=True)
            if stop_on_search_error:
                search_failed = True
                break
            continue
        completed += 1

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
            if other_node is None or not compatible_admitted_pair(compatible_provenance, node, other_node):
                continue

            seen_pairs.add(pair_key)
            candidates.append(
                DuplicateCandidate(node_id, other_id, score, "semantic")
            )

    return candidates, completed, search_failed


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
    *, user_id: str | None = None,
    failures: list[tuple[str, str]] | None = None,
) -> int:
    """Merge duplicate node pairs.

    Each backend revalidates the pair and selects its canonical node inside a
    transaction covering evidence, relationship copies, source retirement,
    supersedence and the immutable operation record. Index eviction follows
    commit. A failed transaction leaves no partial merge visible.

    Args:
        engine: The MemoryEngine for storage operations.
        duplicates: List of DuplicateCandidate pairs from find_duplicates().
        failures: When given, receives the two node ids of every pair whose
            merge raised, in the order they were tried. The incremental caller
            keeps the page resumable instead of advancing past a pair that was
            not retried (dwamianm/prism#545), and bounds its retry and skip on
            the failing pair's own node rather than the page head
            (dwamianm/prism#546).

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
        node_a = await engine.get_node(dup.node_a_id, **({"user_id": user_id} if user_id else {}))
        node_b = await engine.get_node(dup.node_b_id, **({"user_id": user_id} if user_id else {}))

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
        if not compatible_admitted_pair(duplicate_merge_allowed, node_a, node_b):
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
            if failures is not None:
                failures.append((dup.node_a_id, dup.node_b_id))
            logger.warning(
                "Failed to merge duplicate pair (%s, %s)",
                dup.node_a_id,
                dup.node_b_id,
                exc_info=True,
            )

    return merged_count
