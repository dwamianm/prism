"""Parallel candidate generation and merging (Stages 2-3).

Generates candidates from four backends (graph, vector, lexical, pinned)
in parallel via asyncio.gather, then merges them by node_id with
path_count tracking. Handles backend failures gracefully -- a failing
backend yields empty candidates, not a crash.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from prme.models.owner_reference import OWNER_REFERENCE_STATUS
from prme.privacy.policy import PrivacyDenied
from prme.retrieval.config import DEFAULT_PACKING_CONFIG, PackingConfig
from prme.retrieval.models import QueryAnalysis, RetrievalCandidate
from prme.types import EdgeType, LifecycleState, NodeType, Scope

if TYPE_CHECKING:
    from prme.models.edges import MemoryEdge
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore
    from prme.storage.lexical_index import LexicalIndex
    from prme.storage.vector_index import VectorIndex

logger = logging.getLogger(__name__)


@dataclass
class CandidateDiagnostics:
    """Per-call status, separate from candidate counts and provider messages."""

    embedding_mismatch: bool = False
    # With enable_multi_model_embeddings: how many of the owner's memories have no vector under the active
    # model, when some do not (``EmbeddingCoverage.as_metadata()``). None otherwise, so other responses keep
    # their bytes.
    embedding_incomplete: dict | None = None
    backend_failures: dict[str, str] = field(default_factory=dict)
    # Why the walk of ScoringWeights.rrf_graph_seed_count did not run, when it
    # did not: "neighborhood_limit" or "backend_error". The graph candidates
    # are kept either way; none of them gets a graph rank.
    graph_seed_reach: str | None = None


# The seed count walk reads the links of every node it can continue from. Past
# this many it is skipped, because its read grows with the neighborhood and the
# list is short-listed to the semantic top 10 anyway.
SEED_REACH_MAX_NODES = 5000


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


def merge_normalized_bm25_hits(result_sets: list[list[dict]]) -> list[dict]:
    """Normalize each lexical query and merge duplicate hits by best score.

    Supplementary lexical searches run as separate BM25 queries, whose raw
    scores are neither bounded nor comparable across result sets. Normalize
    each set first, then retain the strongest normalized score for each node.
    Input dictionaries are copied so backend results remain unchanged.
    """
    merged: dict[str, dict] = {}
    for result_set in result_sets:
        normalized = normalize_bm25_scores([dict(hit) for hit in result_set])
        for hit in normalized:
            node_id = hit["node_id"]
            saved = merged.get(node_id)
            if saved is None or hit["normalized_score"] > saved["normalized_score"]:
                merged[node_id] = hit
    return list(merged.values())


# Question words that never name an entity, so a lower-case "will" does not
# seed the graph from an entity called Will. An entity named by one of these
# is still found when the question capitalizes it (a proper noun).
_SEED_STOPWORDS = frozenset({
    "about", "after", "again", "all", "also", "and", "any", "are", "been", "before",
    "between", "both", "but", "can", "could", "did", "does", "during", "each", "every",
    "for", "from", "had", "has", "have", "her", "here", "him", "his", "how", "into",
    "its", "just", "many", "may", "might", "more", "most", "much", "must", "not", "now",
    "only", "other", "our", "over", "same", "shall", "should", "some", "still", "such",
    "than", "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "through", "was", "were", "what", "when", "where", "which", "who", "whom",
    "whose", "why", "will", "with", "would", "yes", "you", "your",
})
_SEED_WORD_RE = re.compile(r"\w+")
# A long question cannot multiply the entity lookup.
_MAX_SEED_TERMS = 32


def _seed_terms(query: str) -> list[str]:
    """Distinct lower-case question words that can name an entity, in order."""
    terms = dict.fromkeys(
        word for word in _SEED_WORD_RE.findall(query.lower())
        if len(word) > 2 and not word.isdigit() and word not in _SEED_STOPWORDS
    )
    return list(terms)[:_MAX_SEED_TERMS]


async def _find_seed_entities(
    analysis: QueryAnalysis,
    graph_store: GraphStore,
    user_id: str,
    scope: list[Scope] | None,
    seed_words: bool = False,
) -> list[MemoryNode]:
    """Entity nodes the question names.

    The capitalized names and quoted strings in ``analysis.entities`` match as
    substrings. With ``seed_words`` (``PackingConfig.graph_seed_words``, #259)
    every other word of the question also seeds the graph when it is a whole
    word of an entity's name, so a question typed in lower case ("which cities
    will alex and jordan visit") finds "Alex Rivera" and "Jordan". The SQL
    match only narrows the nodes that are hydrated.
    """
    scopes = scope if scope else None
    seeds: dict[str, MemoryNode] = {}
    if analysis.entities:
        for node in await graph_store.query_nodes(
            user_id=user_id,
            node_type=NodeType.ENTITY,
            scopes=scopes,
            content_contains_any=list(analysis.entities),
        ):
            seeds[str(node.id)] = node
    terms = _seed_terms(analysis.query) if seed_words else []
    if terms:
        wanted = set(terms)
        for node in await graph_store.query_nodes(
            user_id=user_id,
            node_type=NodeType.ENTITY,
            scopes=scopes,
            content_contains_any=terms,
            limit=200,
        ):
            if wanted.intersection(_SEED_WORD_RE.findall(node.content.lower())):
                seeds.setdefault(str(node.id), node)
    return list(seeds.values())


def _is_owner_entity(node: MemoryNode, owner_name_key: str) -> bool:
    """An entity a user turn named as the memory's owner, or the owner reference."""
    metadata = node.metadata or {}
    return node.node_type == NodeType.ENTITY and (
        bool(metadata.get(owner_name_key)) or metadata.get("identity_status") == OWNER_REFERENCE_STATUS
    )


def _unverified_alias(edge: MemoryEdge) -> bool:
    """An alias proposal no one accepted, which the neighborhood search does not traverse either."""
    metadata = edge.metadata or {}
    verified = metadata.get("identity_verified")
    return (edge.edge_type == EdgeType.RELATES_TO and metadata.get("relation") == "alias"
            and (verified is False or verified == "false"))


async def _seed_reach(
    graph_store: GraphStore,
    seeds: list[MemoryNode],
    nodes: dict[str, MemoryNode],
    max_hops: int,
    depths: dict[str, int] | None = None,
) -> dict[str, tuple[int, int, int]] | None:
    """How many seeds reach each node, their total hop count and the fewest hops (``rrf_graph_seed_count``).

    A seed reaches a node within ``max_hops`` along links that never pass
    through an owner entity: the owner's entity can be reached, and can be a
    seed, but a walk does not continue through it, because nearly every claim
    of the owner is one hop from it and would otherwise be near every other
    seed too. ``nodes`` are the records the graph search returned, which hold
    every node within ``max_hops`` of a seed that the search's filters admit,
    so the walk stays among them and the seeds. One query reads the links of
    the nodes a walk can continue from: with ``depths``, each node's fewest
    hops from any seed in that search, only those nearer than ``max_hops``,
    because a walk that avoids the owner is never shorter, and never the
    links of an owner entity that is not a seed, which no walk continues
    from. With fewer than two seeds no node can count two, so nothing is read.
    Returns None, having read nothing, when more than
    ``SEED_REACH_MAX_NODES`` nodes would be read.
    """
    if len(seeds) < 2:
        return {}
    from prme.ingestion.owner_name import OWNER_NAME_KEY

    known = {**nodes, **{str(seed.id): seed for seed in seeds}}
    owners = {node_id for node_id, node in known.items() if _is_owner_entity(node, OWNER_NAME_KEY)}
    seed_ids = {str(seed.id) for seed in seeds}
    expandable = (
        set(known) if depths is None
        else seed_ids | {node_id for node_id, depth in depths.items() if depth < max_hops}
    ) - (owners - seed_ids)
    if len(expandable) > SEED_REACH_MAX_NODES:
        return None
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in await graph_store.get_edges(node_ids=list(expandable)):
        source, target = str(edge.source_id), str(edge.target_id)
        if source != target and source in known and target in known and not _unverified_alias(edge):
            adjacency[source].add(target)
            adjacency[target].add(source)
    hops: dict[str, dict[str, int]] = defaultdict(dict)
    for seed in seeds:
        seed_id = str(seed.id)
        seen, frontier = {seed_id}, [seed_id]
        for depth in range(1, max_hops + 1):
            reached = []
            for node_id in frontier:
                if node_id in owners and node_id != seed_id:
                    continue
                for other in adjacency[node_id]:
                    if other not in seen:
                        seen.add(other)
                        reached.append(other)
                        hops[other][seed_id] = depth
            frontier = reached
    return {
        node_id: (len(by_seed), sum(by_seed.values()), min(by_seed.values()))
        for node_id, by_seed in hops.items()
    }


async def _generate_graph_candidates(
    analysis: QueryAnalysis,
    graph_store: GraphStore,
    user_id: str,
    config: PackingConfig,
    *,
    scope: list[Scope] | None = None,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    seed_reach: bool = False,
    diagnostics: CandidateDiagnostics | None = None,
) -> list[dict]:
    """Generate candidates from graph neighborhood traversal.

    Seeds are the entity nodes the question names (see
    ``_find_seed_entities``; matched in SQL via content LIKE filters, so only
    matching entity nodes are hydrated). For each seed, runs a single
    depth-aware neighborhood query to determine per-node hop distances
    for graph_proximity scoring.

    Scope and temporal filters are forwarded to query_nodes() for seed
    discovery and used for post-filter on neighborhood results.

    Returns list of dicts: {node: MemoryNode, graph_proximity: float}. With
    ``seed_reach`` (``ScoringWeights.rrf_graph_seed_count``) each dict also
    holds ``graph_seed_reach``, from ``_seed_reach``, or None for a node no
    seed reaches without passing through the owner's entity. When the walk is
    skipped or its read fails, every dict holds None and ``diagnostics``
    records why.
    """
    seed_nodes = await _find_seed_entities(
        analysis, graph_store, user_id, scope, config.graph_seed_words,
    )
    seed_ids = [str(node.id) for node in seed_nodes]

    if not seed_ids:
        return []

    # Determine valid_at for neighborhood queries if temporal filter is set.
    valid_at = time_from if time_from is not None else time_to

    # For each seed, determine hop distances via a single depth-aware
    # neighborhood query (one traversal instead of one per hop level).
    node_proximity: dict[str, float] = {}  # node_id -> best proximity
    node_depth: dict[str, int] = {}  # node_id -> fewest hops from a seed
    node_map: dict[str, MemoryNode] = {}  # node_id -> MemoryNode

    # Node types exempt from temporal filtering (persistent knowledge).
    _TEMPORAL_EXEMPT_TYPES = {NodeType.ENTITY, NodeType.PREFERENCE}

    max_hops = min(config.graph_max_hops, 3)
    _HOP_PROXIMITY = {1: 1.0, 2: 0.7, 3: 0.4}

    for seed_id in seed_ids:
        neighbors = await graph_store.get_neighborhood_with_depth(
            seed_id, max_hops=max_hops, valid_at=valid_at,
        )

        for neighbor, depth in neighbors:
            nid = str(neighbor.id)
            if nid == seed_id:
                continue
            proximity = _HOP_PROXIMITY.get(depth, 0.4)

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

            # Keep the best proximity across seeds.
            if nid not in node_proximity or proximity > node_proximity[nid]:
                node_proximity[nid] = proximity
            node_map[nid] = neighbor
            if depth < node_depth.get(nid, depth + 1):
                node_depth[nid] = depth

    # Post-filter to max_candidates: sort by confidence DESC, created_at DESC.
    candidates = [
        {"node": node_map[nid], "graph_proximity": node_proximity[nid]}
        for nid in node_proximity
        if nid in node_map
    ]
    candidates.sort(
        key=lambda c: (-c["node"].confidence, -c["node"].created_at.timestamp()),
    )
    candidates = candidates[: config.graph_max_candidates]
    if seed_reach:
        try:
            reach = await _seed_reach(graph_store, seed_nodes, node_map, max_hops, node_depth)
        except PrivacyDenied:
            raise
        except Exception:
            # Only the graph list's ordering depends on the walk, so a failed
            # read costs that term and keeps every graph candidate.
            logger.warning("Graph seed reach failed; no candidate gets a graph rank",
                           exc_info=not getattr(graph_store, "protected_reads", False))
            reach = None
            if diagnostics is not None:
                diagnostics.graph_seed_reach = "backend_error"
        else:
            if reach is None and diagnostics is not None:
                diagnostics.graph_seed_reach = "neighborhood_limit"
        for candidate in candidates:
            candidate["graph_seed_reach"] = (reach or {}).get(str(candidate["node"].id))
    return candidates


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

    Returns (candidates, embedding_mismatch_flag). A verified model mismatch
    returns empty candidates. Other errors propagate to the backend collector.

    Scope and temporal filters are forwarded to vector_index.search() which
    enforces them via DuckDB JOIN with the nodes table.

    Each candidate dict has: node_id, score (cosine similarity).
    """
    # Convert Scope enums to string values for the vector index.
    scope_values = [s.value for s in scope] if scope else None
    from prme.storage.embedding import EmbeddingVersionMismatchError

    try:
        results = await vector_index.search(
            analysis.query, user_id, k=config.vector_k,
            scope=scope_values,
            time_from=time_from,
            time_to=time_to,
        )
        return results, False
    except EmbeddingVersionMismatchError:
        logger.warning(
            "Vector search detected incompatible embedding metadata; "
            "falling back to empty vector candidates",
            exc_info=not bool(getattr(vector_index, "protected_reads", False)),
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

    Both predicates are pushed into SQL (one query each) instead of
    hydrating a window of recent nodes and filtering in Python.
    """
    scopes = scope if scope else None
    active_states = [LifecycleState.TENTATIVE, LifecycleState.STABLE]

    # Pinned nodes: salience == 1.0 (salience is capped at 1.0, so a
    # >= 1.0 SQL predicate is equivalent).
    pinned_nodes, task_nodes = await asyncio.gather(
        graph_store.query_nodes(
            user_id=user_id,
            scopes=scopes,
            min_confidence=None,
            min_salience=1.0,
            lifecycle_states=active_states,
            limit=500,
        ),
        graph_store.query_nodes(
            user_id=user_id,
            scopes=scopes,
            node_type=NodeType.TASK,
            min_confidence=None,
            lifecycle_states=active_states,
            limit=500,
        ),
        return_exceptions=True,
    )

    # The outer candidate gather can only settle this branch if both of its
    # queries have finished. Otherwise a denial can race the caller's database
    # close while the other query still uses the native connection.
    for result in (pinned_nodes, task_nodes):
        if isinstance(result, PrivacyDenied):
            raise result
    if isinstance(pinned_nodes, BaseException):
        raise pinned_nodes
    if isinstance(task_nodes, BaseException):
        raise task_nodes

    pinned: list[MemoryNode] = []
    seen_ids: set[str] = set()
    for node in [*pinned_nodes, *task_nodes]:
        nid = str(node.id)
        if nid not in seen_ids:
            seen_ids.add(nid)
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
                "graph_seed_reach": None,
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
        if cand.get("graph_seed_reach") is not None:
            entry["graph_seed_reach"] = cand["graph_seed_reach"]

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
                graph_seed_reach=data["graph_seed_reach"],
            )
        )

    return merged


async def _empty_graph_candidates() -> list[dict]:
    """No-op graph backend used when graph candidates are disabled."""
    return []


async def _empty_pinned_candidates() -> list[MemoryNode]:
    """No-op pinned backend used when pinned candidates are disabled."""
    return []


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
    include_graph: bool = True,
    include_pinned: bool = True,
    diagnostics: CandidateDiagnostics | None = None,
    graph_seed_reach: bool = False,
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
        include_graph: Run the graph neighborhood backend. Disabled for
            cheap secondary passes (e.g. cross-scope hints).
        include_pinned: Run the pinned/active-task backend. Disabled for
            cheap secondary passes (e.g. cross-scope hints).
        diagnostics: Optional per-call status output; never stores exception messages.
        graph_seed_reach: Measure how many seeds reach each graph candidate
            (``ScoringWeights.rrf_graph_seed_count``). One more graph query.

    Returns:
        Tuple of (merged_candidates, candidate_counts_per_backend).
        candidate_counts_per_backend maps backend name to pre-merge count.
    """
    # Run the enabled backends in parallel, forwarding scope and temporal
    # filters. Disabled backends are replaced with no-op coroutines so
    # result unpacking stays positional. The graph backend sets the walk's
    # status itself.
    if diagnostics is not None:
        diagnostics.graph_seed_reach = None
    results = await asyncio.gather(
        _generate_graph_candidates(
            analysis, graph_store, user_id, config,
            scope=scope, time_from=time_from, time_to=time_to, seed_reach=graph_seed_reach,
            diagnostics=diagnostics,
        ) if include_graph else _empty_graph_candidates(),
        _generate_vector_candidates(
            analysis, vector_index, user_id, config,
            scope=scope, time_from=time_from, time_to=time_to,
        ),
        _generate_lexical_candidates(
            analysis, lexical_index, user_id, config,
            scope=scope,
        ),
        _generate_pinned_candidates(graph_store, user_id, scope=scope)
        if include_pinned else _empty_pinned_candidates(),
        return_exceptions=True,
    )
    # Authorization and privacy failures are not degradable backend failures.
    # Settle every branch first, then propagate without formatting an exception.
    for result in results:
        if isinstance(result, PrivacyDenied):
            raise result
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result
    protected = bool(getattr(graph_store, "protected_reads", False))

    # Process results, treating exceptions as empty. Counts alone cannot
    # distinguish a healthy empty index from an unavailable backend.
    if diagnostics is not None:
        diagnostics.backend_failures.clear()
        diagnostics.embedding_mismatch = False
        diagnostics.embedding_incomplete = None
        for backend, result in zip(("GRAPH", "VECTOR", "LEXICAL", "PINNED"), results):
            if isinstance(result, BaseException):
                diagnostics.backend_failures[backend] = "backend_error"
    graph_cands: list[dict] = []
    vector_cands: list[dict] = []
    lexical_cands: list[dict] = []
    pinned_cands: list[MemoryNode] = []
    embedding_mismatch = False

    if isinstance(results[0], BaseException):
        logger.warning("Graph candidate generation failed", exc_info=False if protected else results[0])
    else:
        graph_cands = results[0]

    if isinstance(results[1], BaseException):
        logger.warning("Vector candidate generation failed", exc_info=False if protected else results[1])
    else:
        vector_cands, embedding_mismatch = results[1]

    if isinstance(results[2], BaseException):
        logger.warning("Lexical candidate generation failed", exc_info=False if protected else results[2])
    else:
        lexical_cands = results[2]

    if isinstance(results[3], BaseException):
        logger.warning("Pinned candidate generation failed", exc_info=False if protected else results[3])
    else:
        pinned_cands = results[3]

    if embedding_mismatch:
        if diagnostics is not None:
            diagnostics.embedding_mismatch = True
            diagnostics.backend_failures["VECTOR"] = "embedding_mismatch"
        logger.warning(
            "Embedding version mismatch detected; vector candidates empty. "
            "Falling back to lexical + graph + pinned only."
        )

    coverage = getattr(vector_index, "embedding_coverage", None)
    if diagnostics is not None and coverage is not None:
        # A store that is partly or wholly without vectors under the active model says so, with the counts,
        # instead of returning fewer vector candidates without a word.
        try:
            report = await coverage(user_id)
        except Exception:
            logger.warning("Embedding coverage could not be read", exc_info=True)
            report = None
        if report is not None:
            diagnostics.embedding_incomplete = report.as_metadata()

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

    # Batch resolution: one WHERE id IN (...) round trip instead of one
    # sequential get_node() call per candidate.
    if node_ids_to_resolve:
        for node in await graph_store.get_nodes(list(node_ids_to_resolve)):
            resolved_nodes[str(node.id)] = node

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
