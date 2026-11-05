"""The owner's profile for a first-person question (``enable_owner_profile_context``, issue #398).

"Do you remember the names of my dependents?" names no entity, so the graph
seed channel has no seed, and "dependents" occurs in no claim. The owner's
knowledge profile (``consolidate_knowledge``, a SUMMARY node with
``metadata.entity_profile``) holds the people and facts the engine connected to
them, and the model that answers can match "dependents" to the husband and the
child it lists.

For a first-person question (:func:`prme.retrieval.query_analysis.is_first_person_question`)
from a named owner, :func:`find_owner_profile` finds the owner's newest
profile: the owner is the caller's ``retrieve(speaker=...)`` or, without one,
the person entity a user turn marked with the owner's name
(:mod:`prme.ingestion.owner_name`) in the profile's scope, when user turns
gave the owner only one name there. :func:`add_owner_profile` places it right after the top result. Its score
replays from the top result's provenance through an ``owner_profile``
adjustment, and packing reserves it before the multi-path tier
(``OWNER_PROFILE`` path). Without a profile nothing changes.
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
from prme.retrieval.time import as_utc
from prme.types import NodeType, Scope

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore

# The path the owner's profile carries; packing reserves it (tier 2).
OWNER_PROFILE_PATH = "OWNER_PROFILE"

# The header consolidate_knowledge() starts every profile with.
PROFILE_PREFIX = "[Knowledge Profile: "
PROFILE_HEADER = PROFILE_PREFIX + "{name}]"

# The owner's newest profiles read to find the scopes that hold one, when the
# caller names no speaker.
PROFILE_LOOKUP_SCAN = 100

# Profiles read for one owner name. consolidate_knowledge() keeps one active
# profile per name and scope and retires the one it replaces, so a few cover
# every scope with room for a replacement not yet retired.
PROFILE_SCAN = 20

# The profile takes the top result's score times one less this fraction, so
# it follows the top result. It is half the current-state observations' step
# (prme.retrieval.current_state), so it also comes before them.
OWNER_PROFILE_SCORE_STEP = 5e-7


def profile_entity_name(node: MemoryNode) -> str | None:
    """The person a knowledge profile is about, or None when *node* is not a profile."""
    metadata = node.metadata or {}
    name = metadata.get("entity_name")
    if (
        node.node_type != NodeType.SUMMARY
        or metadata.get("entity_profile") is not True
        or not isinstance(name, str)
        or not name.strip()
    ):
        return None
    return name.strip()


def _newest_first(candidate: RetrievalCandidate) -> tuple[float, str]:
    return (-as_utc(candidate.node.created_at).timestamp(), str(candidate.node.id))


async def profiles_named(
    graph_store: GraphStore,
    *,
    user_id: str,
    name: str,
    scopes: list[Scope] | None,
    created_before: datetime | None = None,
) -> list[MemoryNode]:
    """The owner's active knowledge profiles of the person *name*, ignoring case, newest first."""
    nodes = await graph_store.query_nodes(
        node_type=NodeType.SUMMARY, user_id=user_id, scopes=scopes,
        content_contains_any=[PROFILE_HEADER.format(name=name)],
        created_before=created_before, limit=PROFILE_SCAN,
    )
    wanted = name.casefold()
    return [
        node for node in nodes
        if (found := profile_entity_name(node)) is not None and found.casefold() == wanted
    ]


async def _marked_owner_name(graph_store: GraphStore, *, user_id: str, scope: Scope) -> str | None:
    """The one name user turns gave the owner in *scope*, or None when they gave none or several.

    Several names in one scope means several people write under this owner
    (a shared account), and the newest of them need not be the one asking.
    """
    # Imported here: prme.ingestion imports storage, which imports retrieval.
    from prme.ingestion.owner_name import marked_owner_entities

    names = {
        node.content.strip().casefold(): node.content.strip()
        for node in await marked_owner_entities(graph_store, user_id=user_id, scope=scope) or ()
    }
    return next(iter(names.values())) if len(names) == 1 else None


async def find_owner_profile(
    graph_store: GraphStore,
    *,
    user_id: str,
    scopes: Sequence[Scope] | None,
    speaker: str | None,
    created_before: datetime | None,
    eligible: Callable[[list[RetrievalCandidate]], list[RetrievalCandidate]],
) -> RetrievalCandidate | None:
    """The owner's newest active profile that this request may see, or None.

    With *speaker*, the owner is the profile whose entity name is the speaker,
    ignoring case, in any of *scopes* (None for every scope). Without one, the
    owner of a scope is the person entity a user turn marked with the owner's
    name there (:mod:`prme.ingestion.owner_name`), when user turns gave the
    owner exactly one name in it, and only a profile of that name in that
    scope counts. Only scopes with one of the owner's ``PROFILE_LOOKUP_SCAN``
    newest profiles are read. *eligible* applies the request's filters (time
    window, knowledge cutoff, types, epistemic mode). Returns None when the
    owner is not named or has no eligible profile.
    """
    scope_filter = list(scopes) if scopes else None
    profiles: list[MemoryNode] = []
    if speaker is not None and speaker.strip():
        profiles = await profiles_named(
            graph_store, user_id=user_id, name=speaker.strip(),
            scopes=scope_filter, created_before=created_before,
        )
    else:
        # Reading a scope's entities costs more than a profile query, so only
        # scopes that hold a profile are read, and a store without one stops
        # here.
        found = await graph_store.query_nodes(
            node_type=NodeType.SUMMARY, user_id=user_id, scopes=scope_filter,
            content_contains_any=[PROFILE_PREFIX],
            created_before=created_before, limit=PROFILE_LOOKUP_SCAN,
        )
        profile_scopes = {node.scope for node in found if profile_entity_name(node) is not None}
        for scope in sorted(profile_scopes, key=lambda value: value.value):
            name = await _marked_owner_name(graph_store, user_id=user_id, scope=scope)
            if name is None:
                continue
            profiles.extend(
                node for node in await profiles_named(
                    graph_store, user_id=user_id, name=name,
                    scopes=[scope], created_before=created_before,
                )
                if node.scope == scope
            )
    if not profiles:
        return None
    candidates = eligible([
        RetrievalCandidate(node=node, paths=[OWNER_PROFILE_PATH], path_count=1)
        for node in profiles
    ])
    if not candidates:
        return None
    return min(candidates, key=_newest_first)


def add_owner_profile(
    scored: list[RetrievalCandidate],
    profile: RetrievalCandidate | None,
) -> tuple[list[RetrievalCandidate], bool, bool]:
    """Place the owner's *profile* right after the top result.

    The profile takes the top result's inherited score
    (:func:`prme.retrieval.models.inherited_score`) times
    ``1 - OWNER_PROFILE_SCORE_STEP``, with the top result's score provenance
    and an ``owner_profile`` adjustment that names it, so the receipt replays
    the score, and the ``OWNER_PROFILE`` path, which packing reserves before
    the multi-path tier. It takes the top result's relevance for
    ``min_score``, as session context takes its trigger's. A profile that is
    the top result is left as it is.

    Returns the candidates (sorted by score and node ID when the profile's
    score rose or it was added, otherwise in their input order), whether
    anything changed, and whether the ranking changed.
    """
    if not scored or profile is None:
        return scored, False, False
    top = min(scored, key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    if top.node.id == profile.node.id:
        return scored, False, False
    inherited, provenance = inherited_score(top)
    coefficient = 1.0 - OWNER_PROFILE_SCORE_STEP
    adjusted = None
    score = inherited * coefficient
    if provenance is not None:
        adjusted = provenance.model_copy(update={
            "adjustments": provenance.adjustments + (ScoreAdjustment(
                kind="owner_profile", coefficient=coefficient, source_node_id=top.node.id,
            ),),
        })
        score = adjusted.replay_score()
    relevance = rank_fusion_relevance(top)
    position = next(
        (index for index, candidate in enumerate(scored) if candidate.node.id == profile.node.id), None,
    )
    if position is None:
        results = [*scored, RetrievalCandidate(
            node=profile.node,
            paths=[OWNER_PROFILE_PATH],
            path_count=1,
            composite_score=score,
            score_provenance=adjusted,
            context_relevance=relevance,
        )]
        results.sort(key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
        return results, True, True
    current = scored[position]
    updates: dict[str, object] = {}
    if OWNER_PROFILE_PATH not in current.paths:
        updates["paths"] = [*current.paths, OWNER_PROFILE_PATH]
    ranking_changed = score > current.composite_score
    if ranking_changed:
        updates.update(
            composite_score=score,
            reranker_score=None,
            score_trace=None,
            score_provenance=adjusted,
            context_relevance=max(current.context_relevance, relevance),
        )
    if not updates:
        return scored, False, False
    results = list(scored)
    results[position] = current.model_copy(update=updates)
    if ranking_changed:
        results.sort(key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    return results, True, ranking_changed
