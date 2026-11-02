"""Current-state observations (``packing.current_state_observations``, issue #387).

Retrieval ranks by how well a record's text matches the question. A camera
session describes one scene many times in different words, so a question
about now ("How many fingers is the man holding up?") finds older frames that
use its words and misses the newest frames, which are the current state.

For a current-state question (:func:`prme.retrieval.query_analysis.is_current_state_question`)
in a store with observations, :func:`latest_session_observations` finds the
newest observations of the most recent observation session, and
:func:`add_current_state_observations` places them in the results right after
the top result, newest first. Their scores replay from the top result's
provenance through a ``current_observation`` adjustment, and packing reserves
them before the multi-path tier (``CURRENT_STATE`` path). A store without
observations is left as it is.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from prme.retrieval.models import (
    RetrievalCandidate,
    ScoreAdjustment,
    inherited_score,
    rank_fusion_relevance,
)
# The metadata.modality values that prme.vision.observe gives the records it
# stores, shared with the observation timeline (packing.observation_timeline).
from prme.retrieval.packing import OBSERVATION_MODALITIES
from prme.retrieval.time import as_utc
from prme.types import Scope

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore

# The path an added observation carries; packing reserves it (tier 2).
CURRENT_STATE_PATH = "CURRENT_STATE"

# How many of the newest observations a current-state question gets. The
# newest frame is the current state, but it may not show the thing asked
# about: in the owner's camera session of 2026-11-02 the newest frame showed
# the hands down, the one before it a fist and the one before that all five
# fingers spread. Three covers that and costs a bounded part of the context
# when the question was not about the camera.
CURRENT_STATE_OBSERVATION_COUNT = 3

# The owner's newest records read to find the most recent observation, and the
# newest records of its session read to find its newest observations. A store
# whose newest observation is older than the first window is treated as having
# none, so a camera left off while a long conversation goes on stops answering
# for it.
RECENT_RECORD_SCAN = 200
SESSION_RECORD_SCAN = 500

# Each added observation takes the top result's score times one less this
# fraction per place (newest first), so they follow the top result in time
# order and keep their order through selection and packing.
CURRENT_STATE_SCORE_STEP = 1e-6


def is_observation(node: MemoryNode) -> bool:
    """Whether *node* is a photo or video frame observation that ``observe()`` stored."""
    return (node.metadata or {}).get("modality") in OBSERVATION_MODALITIES


def _seen_at(node: MemoryNode) -> datetime:
    return as_utc(node.event_time or node.created_at)


def _newest_first(candidate: RetrievalCandidate) -> tuple[float, float, str]:
    node = candidate.node
    return (-_seen_at(node).timestamp(), -as_utc(node.created_at).timestamp(), str(node.id))


async def latest_session_observations(
    graph_store: GraphStore,
    *,
    user_id: str,
    scopes: Sequence[Scope] | None,
    created_before: datetime | None,
    eligible: Callable[[list[RetrievalCandidate]], list[RetrievalCandidate]],
    count: int = CURRENT_STATE_OBSERVATION_COUNT,
) -> list[RetrievalCandidate]:
    """The newest observations of the owner's most recent observation session, newest first.

    The most recent session is the session and scope of the observation with
    the latest event time among the owner's ``RECENT_RECORD_SCAN`` newest
    records in *scopes* (None for every scope). Its observations are read from
    its ``SESSION_RECORD_SCAN`` newest records and ordered by event time.
    *eligible* applies the request's filters (time window, knowledge cutoff,
    types, epistemic mode) to candidates built from the records, before the
    session is chosen, so a filtered-out observation never decides it. An
    observation without a session is skipped. Returns an empty list when the
    store has no eligible observation.
    """
    if count <= 0:
        return []
    scope_filter = list(scopes) if scopes else None

    def observations(nodes: list[MemoryNode]) -> list[RetrievalCandidate]:
        return eligible([
            RetrievalCandidate(node=node, paths=[CURRENT_STATE_PATH], path_count=1)
            for node in nodes
            if is_observation(node) and node.session_id is not None
        ])

    recent = observations(await graph_store.query_nodes(
        user_id=user_id, scopes=scope_filter, created_before=created_before,
        limit=RECENT_RECORD_SCAN,
    ))
    if not recent:
        return []
    newest = min(recent, key=_newest_first).node
    session = observations(await graph_store.query_nodes(
        user_id=user_id, scopes=[newest.scope], session_ids=[newest.session_id],
        created_before=created_before, limit=SESSION_RECORD_SCAN,
    ))
    session = [
        candidate for candidate in session
        if candidate.node.session_id == newest.session_id and candidate.node.scope == newest.scope
    ]
    session.sort(key=_newest_first)
    return session[:count]


def add_current_state_observations(
    scored: list[RetrievalCandidate],
    observations: Sequence[RetrievalCandidate],
) -> tuple[list[RetrievalCandidate], bool]:
    """Place *observations* (newest first) right after the top result.

    The i-th observation (0 for the newest) takes the top result's inherited
    score (:func:`prme.retrieval.models.inherited_score`) times
    ``1 - (i + 1) * CURRENT_STATE_SCORE_STEP``, with the top result's score
    provenance and a ``current_observation`` adjustment that names it, so the
    receipt replays the score. An observation already among *scored* with a
    higher score keeps it. Every observation gets the ``CURRENT_STATE`` path,
    which packing reserves before the multi-path tier. Each takes the top
    result's relevance for ``min_score``, as session context takes its
    trigger's.

    Returns the candidates, sorted by score and node ID when a score rose or a
    candidate was added (otherwise in their input order), and whether the
    ranking changed.
    """
    if not scored or not observations:
        return scored, False
    top = min(scored, key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    inherited, provenance = inherited_score(top)
    relevance = rank_fusion_relevance(top)
    by_id: dict[str, RetrievalCandidate] = {str(candidate.node.id): candidate for candidate in scored}
    ranking_changed = False
    changed = False
    for place, observation in enumerate(observations):
        coefficient = 1.0 - (place + 1) * CURRENT_STATE_SCORE_STEP
        adjusted = None
        score = inherited * coefficient
        if provenance is not None:
            adjusted = provenance.model_copy(update={
                "adjustments": provenance.adjustments + (ScoreAdjustment(
                    kind="current_observation", coefficient=coefficient, source_node_id=top.node.id,
                ),),
            })
            score = adjusted.replay_score()
        key = str(observation.node.id)
        current = by_id.get(key)
        if current is None:
            by_id[key] = RetrievalCandidate(
                node=observation.node,
                paths=[CURRENT_STATE_PATH],
                path_count=1,
                composite_score=score,
                score_provenance=adjusted,
                context_relevance=relevance,
            )
            changed = ranking_changed = True
            continue
        updates: dict[str, object] = {}
        if CURRENT_STATE_PATH not in current.paths:
            updates["paths"] = [*current.paths, CURRENT_STATE_PATH]
        if score > current.composite_score:
            updates.update(
                composite_score=score,
                reranker_score=None,
                score_trace=None,
                score_provenance=adjusted,
                context_relevance=max(current.context_relevance, relevance),
            )
            ranking_changed = True
        if updates:
            by_id[key] = current.model_copy(update=updates)
            changed = True
    if not changed:
        return scored, False
    results = list(by_id.values())
    if ranking_changed:
        results.sort(key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    return results, ranking_changed
