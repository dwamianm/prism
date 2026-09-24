"""Session context window expansion (Stage 5.5b).

After scoring and ranking, expands the top-K retrieved results by pulling
adjacent turns from the same session_id. This addresses the "orphaned
question" problem where a retrieved question node lacks its adjacent answer.

Expanded context nodes are marked with a SESSION_CONTEXT path and assigned
a slightly lower score (composite_score * decay) to sort just below the
triggering node while remaining higher than unrelated results.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate, ScoreAdjustment, rank_fusion_relevance

from prme.types import Scope

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore

logger = logging.getLogger(__name__)


async def expand_session_context(
    scored: list[RetrievalCandidate],
    graph_store: GraphStore,
    user_id: str,
    config: PackingConfig,
    scope: list[Scope] | None = None,
) -> list[RetrievalCandidate]:
    """Expand top-scored candidates with adjacent session turns.

    For each of the top ``config.session_context_top_k`` scored candidates
    that have a ``session_id``, queries the graph store for all nodes in
    that session ordered by ``created_at``, then includes up to
    ``config.session_context_window`` turns before and after the
    retrieved node.

    Adjacent nodes receive:
    - A composite_score of ``trigger.composite_score * config.session_context_score_decay``
      when that is stronger than their existing score
    - A ``SESSION_CONTEXT`` entry in their paths list
    - De-duplication by node ID

    Args:
        scored: Scored and ranked candidates (from score_and_rank).
        graph_store: GraphStore for querying session nodes.
        user_id: User ID for scoping graph queries.
        config: PackingConfig with session context settings.
        scope: Optional scope filter, forwarded to the session-node query so
            expansion cannot pull in adjacent turns from another scope
            (issue #60). None means no scope filter.

    Returns:
        Expanded candidate list with context nodes interleaved after
        their trigger nodes, preserving deterministic ordering.
    """
    window = config.session_context_window
    top_k = config.session_context_top_k
    decay = config.session_context_score_decay

    if window <= 0 or not scored:
        return scored

    # Keep one candidate per node. Adjacent nodes that primary generation
    # already found still need the session signal; broad vector/lexical pools
    # commonly contain every session node before packing.
    candidates_by_id: dict[str, RetrievalCandidate] = {
        str(candidate.node.id): candidate for candidate in scored
    }
    changed = False
    ranking_changed = False

    # Group the top-K candidates by session_id.
    top_candidates = scored[:top_k]
    session_triggers: dict[tuple[str, Scope], list[RetrievalCandidate]] = {}
    for candidate in top_candidates:
        sid = candidate.node.session_id
        if sid is not None:
            session_triggers.setdefault((sid, candidate.node.scope), []).append(candidate)

    if not session_triggers:
        return scored

    # Built-in stores return one exact bounded window per trigger. The
    # compatibility path reconstructs those windows from the legacy query.
    trigger_windows: dict[str, list[MemoryNode]] = {}
    try:
        native_neighbor_query = getattr(type(graph_store), "get_session_neighbors", None)
        if callable(native_neighbor_query):
            trigger_windows = await graph_store.get_session_neighbors(
                [str(candidate.node.id) for candidate in top_candidates],
                user_id=user_id,
                window=window,
                scopes=scope if scope else None,
            )
        else:
            # Compatibility path for third-party GraphStore implementations.
            all_nodes = await graph_store.query_nodes(
                user_id=user_id,
                session_ids=list({key[0] for key in session_triggers}),
                scopes=scope if scope else None,
                limit=2000,
            )
            session_nodes: dict[tuple[str, Scope], list[MemoryNode]] = {}
            for n in all_nodes:
                key = (n.session_id, n.scope) if n.session_id is not None else None
                if key in session_triggers:
                    session_nodes.setdefault(key, []).append(n)
            for session_key, nodes in session_nodes.items():
                nodes.sort(key=lambda n: (n.created_at, str(n.id)))
                node_id_to_pos = {str(node.id): i for i, node in enumerate(nodes)}
                for trigger in session_triggers[session_key]:
                    pos = node_id_to_pos.get(str(trigger.node.id))
                    if pos is not None:
                        trigger_windows[str(trigger.node.id)] = nodes[
                            max(0, pos - window) : pos + window + 1
                        ]
    except Exception:
        logger.warning(
            "Failed to fetch session nodes for user_id=%s; skipping expansion",
            user_id,
            exc_info=True,
        )

    # Apply each context signal while preserving the strongest inherited score.
    for trigger in top_candidates:
        nodes = trigger_windows.get(str(trigger.node.id), [])
        if not nodes:
            continue

        trigger_id = str(trigger.node.id)
        context_score = trigger.composite_score * decay
        # A neighbor keeps its trigger's relevance even when its own score
        # stays higher; min_score gates it under rank fusion.
        context_relevance = rank_fusion_relevance(trigger)
        provenance = trigger.score_provenance
        if provenance is not None:
            provenance = provenance.model_copy(update={
                "adjustments": provenance.adjustments + (ScoreAdjustment(
                    kind="session_decay", coefficient=decay, source_node_id=trigger.node.id,
                ),),
            })

        for ctx_node in nodes:
            ctx_id = str(ctx_node.id)

            # A trigger does not provide context evidence for itself.
            if ctx_id == trigger_id:
                continue

            current = candidates_by_id.get(ctx_id)
            if current is None:
                candidates_by_id[ctx_id] = RetrievalCandidate(
                    node=ctx_node,
                    paths=["SESSION_CONTEXT"],
                    path_count=1,
                    semantic_score=0.0,
                    lexical_score=0.0,
                    graph_proximity=0.0,
                    composite_score=context_score,
                    score_provenance=provenance,
                    context_relevance=context_relevance,
                )
                changed = True
                ranking_changed = True
                continue

            updates: dict[str, object] = {}
            if "SESSION_CONTEXT" not in current.paths:
                updates["paths"] = [*current.paths, "SESSION_CONTEXT"]
            if context_relevance > current.context_relevance:
                updates["context_relevance"] = context_relevance
            if context_score > current.composite_score:
                updates.update(
                    composite_score=context_score,
                    reranker_score=None,
                    score_trace=None,
                    score_provenance=provenance,
                )
                ranking_changed = True
            if updates:
                candidates_by_id[ctx_id] = current.model_copy(update=updates)
                changed = True

    if not changed:
        return scored

    expanded = list(candidates_by_id.values())

    if ranking_changed:
        # A score promotion or new candidate requires a deterministic merge.
        expanded.sort(key=lambda c: (-c.composite_score, str(c.node.id)))

    return expanded
