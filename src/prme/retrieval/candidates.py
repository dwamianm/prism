"""Parallel candidate generation and merging (Stages 2-3).

Generates candidates from four backends (graph, vector, lexical, pinned)
in parallel via asyncio.gather, then merges them by node_id with
path_count tracking. Handles backend failures gracefully -- a failing
backend yields empty candidates, not a crash.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from prme.retrieval.config import DEFAULT_PACKING_CONFIG, PackingConfig
from prme.retrieval.models import QueryAnalysis, RetrievalCandidate
from prme.types import LifecycleState, NodeType, Scope

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore
    from prme.storage.lexical_index import LexicalIndex
    from prme.storage.vector_index import VectorIndex

logger = logging.getLogger(__name__)


def normalize_bm25_scores(results: list[dict]) -> list[dict]:
    """Normalize BM25 scores to [0, 1] via min-max within the result set.

    Adds a ``normalized_score`` key to each result dict. If all scores
    are equal, all normalized scores are set to 1.0.

    Args:
        results: List of dicts, each with a ``score`` key.

    Returns:
        The same list with ``normalized_score`` added to each dict.
    """
    if not results:
        return results

    scores = [r["score"] for r in results]
    min_s = min(scores)
    max_s = max(scores)

    if max_s == min_s:
        for r in results:
            r["normalized_score"] = 1.0
    else:
        for r in results:
            r["normalized_score"] = (r["score"] - min_s) / (max_s - min_s)

    return results


async def _generate_graph_candidates(
    analysis: QueryAnalysis,
    graph_store: GraphStore,
    user_id: str,
    config: PackingConfig,
    *,
    scope: list[Scope] | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> list[dict]:
    """Generate candidates from graph neighborhood traversal.

    Seeds are entity nodes matching extracted entity names. For each seed,
    runs get_neighborhood at 1-hop, 2-hop, and 3-hop to determine per-node
    hop distances for graph_proximity scoring.

    Scope and temporal filters are forwarded to query_nodes() for seed
    discovery and used for post-filter on neighborhood results.

    Returns list of dicts: {node: MemoryNode, graph_proximity: float}.
    """
    if not analysis.entities:
        return []

    # Find seed nodes matching extracted entity names.
    # For multi-scope, iterate and union (query_nodes takes single Scope).
    if scope is not None and scope:
        all_entity_nodes = []
        seen_entity_ids: set[str] = set()
        for s in scope:
            nodes = await graph_store.query_nodes(
                user_id=user_id,
                node_type=NodeType.ENTITY,
                scope=s,
            )
            for n in nodes:
                nid = str(n.id)
                if nid not in seen_entity_ids:
                    seen_entity_ids.add(nid)
                    all_entity_nodes.append(n)
    else:
        all_entity_nodes = await graph_store.query_nodes(
            user_id=user_id,
            node_type=NodeType.ENTITY,
        )

    seed_ids: list[str] = []
    for node in all_entity_nodes:
        for entity_name in analysis.entities:
            if entity_name.lower() in node.content.lower():
                seed_ids.append(str(node.id))
                break

    if not seed_ids:
        return []

    # Determine valid_at for neighborhood queries if temporal filter is set.
    valid_at = time_from if time_from is not None else time_to

    # For each seed, determine hop distances via incremental neighborhood
    # queries: 1-hop, 2-hop (minus 1-hop), 3-hop (minus 1&2-hop).
    node_proximity: dict[str, float] = {}  # node_id -> best proximity
    node_map: dict[str, MemoryNode] = {}  # node_id -> MemoryNode

    # Node types exempt from temporal filtering (persistent knowledge).
    _TEMPORAL_EXEMPT_TYPES = {NodeType.ENTITY, NodeType.PREFERENCE}

    max_hops = min(config.graph_max_hops, 3)

    for seed_id in seed_ids:
        seen_at_previous_hops: set[str] = {seed_id}

        for hop in range(1, max_hops + 1):
            proximity = {1: 1.0, 2: 0.7, 3: 0.4}.get(hop, 0.4)

            neighbors = await graph_store.get_neighborhood(
                seed_id, max_hops=hop, valid_at=valid_at,
            )

            for neighbor in neighbors:
                nid = str(neighbor.id)
                if nid not in seen_at_previous_hops:
                    # Scope filter: skip neighbors outside requested scopes.
                    if scope is not None and scope:
                        if neighbor.scope not in scope:
                            continue

                    # Temporal filter: skip nodes outside temporal window,
                    # but exempt ENTITY and PREFERENCE types.
                    if neighbor.node_type not in _TEMPORAL_EXEMPT_TYPES:
                        if time_from is not None and neighbor.valid_to is not None:
                            if neighbor.valid_to <= time_from:
                                continue
                        if time_to is not None and neighbor.valid_from is not None:
                            if neighbor.valid_from > time_to:
                                continue

                    # First time seeing this node -- it's at this hop distance.
                    if nid not in node_proximity or proximity > node_proximity[nid]:
                        node_proximity[nid] = proximity
                    node_map[nid] = neighbor

            # Add all IDs from this hop level to seen set.
            seen_at_previous_hops.update(str(n.id) for n in neighbors)

    # Post-filter to max_candidates: sort by confidence DESC, created_at DESC.
    candidates = [
        {"node": node_map[nid], "graph_proximity": node_proximity[nid]}
        for nid in node_proximity
        if nid in node_map
    ]
    candidates.sort(
        key=lambda c: (-c["node"].confidence, -c["node"].created_at.timestamp()),
    )
    return candidates[: config.graph_max_candidates]


async def _generate_vector_candidates(
    analysis: QueryAnalysis,
    vector_index: VectorIndex,
    user_id: str,
    config: PackingConfig,
    *,
    scope: list[Scope] | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
) -> tuple[list[dict], bool]:
    """Generate candidates from vector similarity search.

    Returns (candidates, embedding_mismatch_flag). On embedding version
    mismatch or other error, returns empty candidates with mismatch=True.

    Scope and temporal filters are forwarded to vector_index.search() which
    enforces them via DuckDB JOIN with the nodes table.

    Each candidate dict has: node_id, score (cosine similarity).
    """
    # Convert Scope enums to string values for the vector index.
    scope_values = [s.value for s in scope] if scope else None
    try:
        results = await vector_index.search(
            analysis.query, user_id, k=config.vector_k,
            scope=scope_values,
            time_from=time_from,
            time_to=time_to,
        )
        return results, False
    except Exception:
        logger.warning(
            "Vector search failed (possible embedding version mismatch); "
            "falling back to empty vector candidates",
            exc_info=True,
        )
        return [], True


async def _generate_lexical_candidates(
    analysis: QueryAnalysis,
    lexical_index: LexicalIndex,
    user_id: str,
    config: PackingConfig,
    *,
    scope: list[Scope] | None = None,
) -> list[dict]:
    """Generate candidates from lexical (BM25) search.

    Scope filter is forwarded to lexical_index.search() which enforces
    it via tantivy query AND clause.

    Returns list of dicts with normalized_score added via min-max
    normalization.
    """
    # Convert Scope enums to string values for the lexical index.
    scope_values = [s.value for s in scope] if scope else None
    results = await lexical_index.search(
        analysis.query, user_id, limit=config.lexical_k,
        scope=scope_values,
    )
    return normalize_bm25_scores(results)


async def _generate_pinned_candidates(
    graph_store: GraphStore,
    user_id: str,
    *,
    scope: list[Scope] | None = None,
) -> list[MemoryNode]:
    """Generate always-include candidates: pinned nodes and active tasks.

    Pinned = salience == 1.0. Active tasks = TASK type with lifecycle
    in (TENTATIVE, STABLE). Scope filter limits to requested scopes.
    """
    # For multi-scope, iterate and union (query_nodes takes single Scope).
    if scope is not None and scope:
        all_nodes = []
        seen_ids: set[str] = set()
        for s in scope:
            nodes = await graph_store.query_nodes(
                user_id=user_id,
                scope=s,
                min_confidence=None,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
                limit=500,
            )
            for n in nodes:
                nid = str(n.id)
                if nid not in seen_ids:
                    seen_ids.add(nid)
                    all_nodes.append(n)
    else:
        # No scope filter -- get all active nodes.
        all_nodes = await graph_store.query_nodes(
            user_id=user_id,
            min_confidence=None,
            lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            limit=500,
        )

    pinned: list[MemoryNode] = []
    for node in all_nodes:
        is_pinned = node.salience == 1.0
        is_active_task = node.node_type == NodeType.TASK
        if is_pinned or is_active_task:
            pinned.append(node)

    return pinned


def merge_candidates(
    graph_cands: list[dict],
    vector_cands: list[dict],
    lexical_cands: list[dict],
    pinned_cands: list[MemoryNode],
    resolved_nodes: dict[str, MemoryNode],
) -> list[RetrievalCandidate]:
    """Merge candidates from all backends by node_id.

    If a node appears in multiple backends, scores and paths are combined:
    union of paths, increment path_count, take max of each score component.

    Args:
        graph_cands: Graph candidates (each has ``node`` and ``graph_proximity``).
        vector_cands: Vector candidates (each has ``node_id`` and ``score``).
        lexical_cands: Lexical candidates (each has ``node_id`` and ``normalized_score``).
        pinned_cands: Pinned/active MemoryNode objects.
        resolved_nodes: Pre-resolved node_id -> MemoryNode map for vector/lexical
            candidates that need full node objects.

    Returns:
        Merged list of RetrievalCandidate objects.
    """
    # Accumulate per-node data.
    candidates: dict[str, dict] = {}  # node_id -> accumulated data

    def _ensure_entry(node_id: str, node: MemoryNode | None) -> dict:
        if node_id not in candidates:
            candidates[node_id] = {
                "node": node,
                "paths": [],
                "semantic_score": 0.0,
                "lexical_score": 0.0,
                "graph_proximity": 0.0,
            }
        elif node is not None and candidates[node_id]["node"] is None:
            candidates[node_id]["node"] = node
        return candidates[node_id]

    # Graph candidates (already have full MemoryNode).
    for cand in graph_cands:
        node = cand["node"]
        entry = _ensure_entry(str(node.id), node)
        if "GRAPH" not in entry["paths"]:
            entry["paths"].append("GRAPH")
        entry["graph_proximity"] = max(
            entry["graph_proximity"], cand["graph_proximity"]
        )

    # Vector candidates (need resolution from resolved_nodes).
    for cand in vector_cands:
        node_id = cand["node_id"]
        node = resolved_nodes.get(node_id)
        if node is None:
            # Orphaned vector entry -- skip.
            logger.debug(
                "Skipping orphaned vector candidate (no graph node)",
                extra={"node_id": node_id},
            )
            continue
        entry = _ensure_entry(node_id, node)
        if "VECTOR" not in entry["paths"]:
            entry["paths"].append("VECTOR")
        entry["semantic_score"] = max(
            entry["semantic_score"], cand.get("score", 0.0)
        )

    # Lexical candidates (need resolution from resolved_nodes).
    for cand in lexical_cands:
        node_id = cand["node_id"]
        node = resolved_nodes.get(node_id)
        if node is None:
            logger.debug(
                "Skipping orphaned lexical candidate (no graph node)",
                extra={"node_id": node_id},
            )
            continue
        entry = _ensure_entry(node_id, node)
        if "LEXICAL" not in entry["paths"]:
            entry["paths"].append("LEXICAL")
        entry["lexical_score"] = max(
            entry["lexical_score"], cand.get("normalized_score", 0.0)
        )

    # Pinned candidates (already have full MemoryNode).
    for node in pinned_cands:
        entry = _ensure_entry(str(node.id), node)
        if "PINNED" not in entry["paths"]:
            entry["paths"].append("PINNED")

    # Build RetrievalCandidate objects, skipping any without a resolved node.
    merged: list[RetrievalCandidate] = []
    for node_id, data in candidates.items():
        if data["node"] is None:
            continue
        merged.append(
            RetrievalCandidate(
                node=data["node"],
                paths=data["paths"],
                path_count=len(data["paths"]),
                semantic_score=data["semantic_score"],
                lexical_score=data["lexical_score"],
                graph_proximity=data["graph_proximity"],
            )
        )

    return merged


async def generate_candidates(
    analysis: QueryAnalysis,
    *,
    graph_store: GraphStore,
    vector_index: VectorIndex,
    lexical_index: LexicalIndex,
    user_id: str,
    scope: list[Scope] | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    config: PackingConfig = DEFAULT_PACKING_CONFIG,
) -> tuple[list[RetrievalCandidate], dict[str, int]]:
    """Generate and merge candidates from all four backends in parallel.

    Runs graph, vector, lexical, and pinned candidate generation
    concurrently via asyncio.gather. Backend failures are handled
    gracefully -- a failing backend produces empty candidates, not a crash.

    Args:
        analysis: QueryAnalysis from Stage 1.
        graph_store: GraphStore for neighborhood traversal and entity lookup.
        vector_index: VectorIndex for semantic similarity search.
        lexical_index: LexicalIndex for BM25 search.
        user_id: User ID for scoping all queries.
        scope: Optional list of Scope filters. When provided, candidates from
            other scopes are excluded before merging. None means no filter.
        time_from: Optional temporal window start. Forwarded to graph and
            vector backends. ENTITY and PREFERENCE types are exempt.
        time_to: Optional temporal window end. Forwarded to graph and
            vector backends. ENTITY and PREFERENCE types are exempt.
        config: PackingConfig controlling candidate limits.

    Returns:
        Tuple of (merged_candidates, candidate_counts_per_backend).
        candidate_counts_per_backend maps backend name to pre-merge count.
    """
    # Run all four backends in parallel, forwarding scope and temporal filters.
    results = await asyncio.gather(
        _generate_graph_candidates(
            analysis, graph_store, user_id, config,
            scope=scope, time_from=time_from, time_to=time_to,
        ),
        _generate_vector_candidates(
            analysis, vector_index, user_id, config,
            scope=scope, time_from=time_from, time_to=time_to,
        ),
        _generate_lexical_candidates(
            analysis, lexical_index, user_id, config,
            scope=scope,
        ),
        _generate_pinned_candidates(graph_store, user_id, scope=scope),
        return_exceptions=True,
    )

    # Process results, treating exceptions as empty.
    graph_cands: list[dict] = []
    vector_cands: list[dict] = []
    lexical_cands: list[dict] = []
    pinned_cands: list[MemoryNode] = []
    embedding_mismatch = False

    if isinstance(results[0], BaseException):
        logger.warning("Graph candidate generation failed", exc_info=results[0])
    else:
        graph_cands = results[0]

    if isinstance(results[1], BaseException):
        logger.warning("Vector candidate generation failed", exc_info=results[1])
    else:
        vector_cands, embedding_mismatch = results[1]

    if isinstance(results[2], BaseException):
        logger.warning("Lexical candidate generation failed", exc_info=results[2])
    else:
        lexical_cands = results[2]

    if isinstance(results[3], BaseException):
        logger.warning("Pinned candidate generation failed", exc_info=results[3])
    else:
        pinned_cands = results[3]

    if embedding_mismatch:
        logger.warning(
            "Embedding version mismatch detected; vector candidates empty. "
            "Falling back to lexical + graph + pinned only."
        )

    # Batch-resolve node_ids for vector and lexical candidates.
    node_ids_to_resolve: set[str] = set()
    for cand in vector_cands:
        node_ids_to_resolve.add(cand["node_id"])
    for cand in lexical_cands:
        node_ids_to_resolve.add(cand["node_id"])

    # Subtract IDs we already have from graph/pinned candidates.
    known_ids: set[str] = set()
    for cand in graph_cands:
        known_ids.add(str(cand["node"].id))
    for node in pinned_cands:
        known_ids.add(str(node.id))
    node_ids_to_resolve -= known_ids

    # Resolve from graph store (local DuckDB -- no network latency).
    resolved_nodes: dict[str, MemoryNode] = {}
    # Pre-populate with known nodes.
    for cand in graph_cands:
        resolved_nodes[str(cand["node"].id)] = cand["node"]
    for node in pinned_cands:
        resolved_nodes[str(node.id)] = node

    for node_id in node_ids_to_resolve:
        node = await graph_store.get_node(node_id)
        if node is not None:
            resolved_nodes[node_id] = node

    # Merge all candidates.
    merged = merge_candidates(
        graph_cands, vector_cands, lexical_cands, pinned_cands, resolved_nodes
    )

    counts = {
        "GRAPH": len(graph_cands),
        "VECTOR": len(vector_cands),
        "LEXICAL": len(lexical_cands),
        "PINNED": len(pinned_cands),
    }

    return merged, counts
