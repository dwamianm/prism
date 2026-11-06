"""Leave out a profile that quotes a memory retrieval has retired (issue #462).

``consolidate_knowledge()`` publishes a profile as a SUMMARY node whose metadata
names the records it quotes in ``source_node_ids``, and ``build_profile`` writes
each selected record's content into the profile text. A profile keeps its own
lifecycle state when one of those records is archived, superseded or deleted,
so candidate generation and packing returned the retired record's text through
the profile even though the record itself is no longer retrievable.

A profile is a derived view, not a record of its own. With this filter,
retrieval leaves out a profile whose quoted sources are no longer all
retrievable, until an explicit rebuild publishes a replacement. A profile with
no parsable source list cannot be verified, so it is left out too. Nothing is
written: the profile node and its generation stay as stored, so
``StaleProfileError`` and the organizer's stale-profile rebuild (``_is_stale``
in ``prme.organizer.owner_profiles``) keep working from the same source list.
The underlying record is still read by id and, when only retired rather than
deleted, by ``RetrievalMode.EXPLICIT``.

The filter fails closed: when the source read itself fails, the caller drops
the profile candidates through :func:`exclude_unverified_profiles` rather than
return a profile whose quoted text was never checked.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from prme.retrieval.filtering import DEFAULT_EXCLUDED_LIFECYCLE
from prme.retrieval.models import ExcludedCandidate, RetrievalCandidate
from prme.types import NodeType, RetrievalMode

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.graph_store import GraphStore

UNKNOWN_SOURCES_REASON = "profile_sources_unknown"
UNVERIFIED_SOURCES_REASON = "profile_sources_unverified"


def _is_profile(node: MemoryNode) -> bool:
    return node.node_type == NodeType.SUMMARY and (
        node.metadata or {}
    ).get("entity_profile") is True


def _quoted_sources(node: MemoryNode) -> list[str]:
    """The record ids a profile node quotes, or [] when it names none we can read."""
    values = (node.metadata or {}).get("source_node_ids")
    if not isinstance(values, list):
        return []
    sources: list[str] = []
    for value in values:
        try:
            sources.append(str(UUID(str(value))))
        except (TypeError, ValueError):
            # Metadata written by the engine holds node UUIDs. A malformed
            # value names no record, so it verifies nothing.
            continue
    return sources


def _split(
    candidates: Sequence[RetrievalCandidate],
    stale: dict[UUID, str],
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    kept: list[RetrievalCandidate] = []
    excluded: list[ExcludedCandidate] = []
    for candidate in candidates:
        reason = stale.get(candidate.node.id)
        if reason is None:
            kept.append(candidate)
        else:
            excluded.append(ExcludedCandidate(node_id=candidate.node.id, reason=reason))
    return kept, excluded


def exclude_unverified_profiles(
    candidates: Sequence[RetrievalCandidate],
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Drop every profile when its quoted records could not be read (fail closed)."""
    return _split(
        candidates,
        {
            candidate.node.id: UNVERIFIED_SOURCES_REASON
            for candidate in candidates
            if _is_profile(candidate.node)
        },
    )


async def suppress_retired_profiles(
    candidates: Sequence[RetrievalCandidate],
    *,
    graph_store: GraphStore,
    user_id: str,
    retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Drop the profiles whose quoted records are no longer retrievable.

    A profile goes when any of its ``source_node_ids`` is archived or
    superseded, or when one no longer exists at all (deleted). ``EXPLICIT``
    retrieval keeps retired material, so there it drops a profile only for a
    quoted record that is gone. A profile with no parsable source list is
    unverifiable and goes in every mode.

    One owner-scoped read fetches every quoted record at once, and none is
    made when no profile candidate has a readable source list.
    """
    profiles = [
        (candidate, _quoted_sources(candidate.node))
        for candidate in candidates
        if _is_profile(candidate.node)
    ]
    if not profiles:
        return list(candidates), []
    wanted = sorted({source for _, sources in profiles for source in sources})
    found = {
        str(node.id): node
        for node in await graph_store.get_nodes(
            wanted, include_superseded=True, user_id=user_id,
        )
    } if wanted else {}
    stale: dict[UUID, str] = {}
    for candidate, sources in profiles:
        if not sources:
            stale[candidate.node.id] = UNKNOWN_SOURCES_REASON
            continue
        missing = next((source for source in sources if source not in found), None)
        if missing is not None:
            stale[candidate.node.id] = f"profile_source_missing:{missing}"
            continue
        if retrieval_mode == RetrievalMode.EXPLICIT:
            continue
        retired = next(
            (
                source
                for source in sources
                if found[source].lifecycle_state in DEFAULT_EXCLUDED_LIFECYCLE
            ),
            None,
        )
        if retired is not None:
            stale[candidate.node.id] = f"profile_source_retired:{retired}"
    return _split(candidates, stale)
