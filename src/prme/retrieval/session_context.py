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
from prme.retrieval.models import RetrievalCandidate

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore

logger = logging.getLogger(__name__)


async def expand_session_context(
    scored: list[RetrievalCandidate],
    graph_store: GraphStore,
    user_id: str,
    config: PackingConfig,
) -> list[RetrievalCandidate]:
    """Expand top-scored candidates with adjacent session turns.

    For each of the top ``config.session_context_top_k`` scored candidates
    that have a ``session_id``, queries the graph store for all nodes in
    that session ordered by ``created_at``, then includes up to
    ``config.session_context_window`` turns before and after the
    retrieved node.

    New context nodes receive:
    - A composite_score of ``trigger.composite_score * config.session_context_score_decay``
    - A ``SESSION_CONTEXT`` entry in their paths list
    - De-duplication against already-present candidates

    Args:
        scored: Scored and ranked candidates (from score_and_rank).
        graph_store: GraphStore for querying session nodes.
        user_id: User ID for scoping graph queries.
        config: PackingConfig with session context settings.

    Returns:
        Expanded candidate list with context nodes interleaved after
        their trigger nodes, preserving deterministic ordering.
    """
    window = config.session_context_window
    top_k = config.session_context_top_k
    decay = config.session_context_score_decay

    if window <= 0 or not scored:
        return scored

    # Collect node IDs already in the result set for de-duplication.
    existing_ids: set[str] = {str(c.node.id) for c in scored}

    # Group the top-K candidates by session_id.
    top_candidates = scored[:top_k]
    session_triggers: dict[str, list[RetrievalCandidate]] = {}
    for candidate in top_candidates:
        sid = candidate.node.session_id
        if sid is not None:
            session_triggers.setdefault(sid, []).append(candidate)

    if not session_triggers:
        return scored

    # Fetch all nodes once and group by session_id (avoids repeated queries).
    session_nodes: dict[str, list[MemoryNode]] = {}
    try:
        all_nodes = await graph_store.query_nodes(
            user_id=user_id,
            limit=2000,
        )
        for n in all_nodes:
            if n.session_id in session_triggers:
                session_nodes.setdefault(n.session_id, []).append(n)
        # Sort each session's nodes by created_at.
        for sid in session_nodes:
            session_nodes[sid].sort(key=lambda n: n.created_at)
    except Exception:
        logger.warning(
            "Failed to fetch session nodes for user_id=%s; skipping expansion",
            user_id,
            exc_info=True,
        )

    # Build context expansion candidates.
    # We collect them keyed by trigger candidate to interleave properly.
    new_candidates: list[RetrievalCandidate] = []
    newly_added_ids: set[str] = set()

    for sid, triggers in session_triggers.items():
        nodes = session_nodes.get(sid, [])
        if not nodes:
            continue

        # Build an index from node_id to position for quick lookup.
        node_id_to_pos: dict[str, int] = {
            str(n.id): i for i, n in enumerate(nodes)
        }

        for trigger in triggers:
            trigger_id = str(trigger.node.id)
            pos = node_id_to_pos.get(trigger_id)
            if pos is None:
                # Trigger node not found in session query results; skip.
                continue

            # Determine the window of adjacent nodes.
            start = max(0, pos - window)
            end = min(len(nodes), pos + window + 1)

            context_score = trigger.composite_score * decay

            for i in range(start, end):
                ctx_node = nodes[i]
                ctx_id = str(ctx_node.id)

                # Skip the trigger node itself and already-included nodes.
                if ctx_id in existing_ids or ctx_id in newly_added_ids:
                    continue

                new_candidates.append(
                    RetrievalCandidate(
                        node=ctx_node,
                        paths=["SESSION_CONTEXT"],
                        path_count=1,
                        semantic_score=0.0,
                        lexical_score=0.0,
                        graph_proximity=0.0,
                        composite_score=context_score,
                    )
                )
                newly_added_ids.add(ctx_id)

    if not new_candidates:
        return scored

    # Merge: append context nodes to the scored list. They already have
    # composite_score set, so the existing deterministic sort in pipeline
    # will position them correctly (just below their triggers).
    expanded = list(scored) + new_candidates

    # Re-sort deterministically: score descending, then node ID ascending.
    expanded.sort(
        key=lambda c: (-c.composite_score, str(c.node.id)),
    )

    return expanded
