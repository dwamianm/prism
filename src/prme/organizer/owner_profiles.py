"""Keep knowledge profiles fresh after ingest (``enable_owner_profile_context``, issue #398).

A first-person question gets the owner's profile (:mod:`prme.retrieval.owner_profile`),
so a profile built before the owner's latest turns would answer from old
knowledge. ``consolidate_knowledge()`` builds a profile from the records that
mention the person by name and, for these rebuilds, from the turns the
person spoke (``include_speaker_turns``), so a profile is stale when an
active record newer than it mentions that name or was said by that person
and is not among its sources. A profile that already left sources out of its
token budget is not rebuilt: its oldest sources come first, so its text would
not change.

- :func:`refresh_stale_profiles` runs in the organizer's opportunistic pass:
  it rebuilds each of the owner's stale profiles, newest first, until the
  pass's deadline, at most once per person and scope.
- :func:`refresh_owner_profiles` runs in ``end_session()``: for each scope
  where a user turn marked the owner's name (:mod:`prme.ingestion.owner_name`),
  it builds the owner's profile when there is none and two records mention
  them, and rebuilds it when it is stale, within a deadline.

Both return what they rebuilt, for the organizer result. Rebuilding is name
matching with no model call; publishing a profile embeds its text once. A
failed rebuild is logged and leaves the previous profile in place. An owner
whose memories are under a privacy policy is skipped: their profiles are
rebuilt only by an explicit ``consolidate_knowledge()``.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from prme.ingestion.owner_name import carried_owner_entity
from prme.models.speaker import metadata_speaker
from prme.organizer.profiles import mentions_entity, run_profile_work
from prme.retrieval.owner_profile import PROFILE_PREFIX, profile_entity_name, profiles_named
from prme.types import LifecycleState, NodeType, Scope

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.engine import MemoryEngine

from prme.privacy.diagnostics import ScopedLogger

logger = ScopedLogger(logging.getLogger(__name__))

# The owner's newest profiles the opportunistic pass looks at.
PROFILE_REFRESH_SCAN = 100

# The newest records that mention a person read to decide whether their
# profile is stale. Records come newest first, so the first one newer than the
# profile decides; a burst of more new records than this still finds one.
MENTION_SCAN = 50

# The lifecycle states consolidate_knowledge() reads its sources in.
PROFILE_SOURCE_STATES = (LifecycleState.TENTATIVE, LifecycleState.STABLE)

# consolidate_knowledge() publishes a profile only with two mentioning records.
MIN_PROFILE_SOURCES = 2


def _newest(nodes: list[MemoryNode]) -> MemoryNode:
    return max(nodes, key=lambda node: (node.created_at, str(node.id)))


async def _mentioning(engine: MemoryEngine, *, user_id: str, scope: Scope, name: str) -> list[MemoryNode]:
    """The newest active records in *scope* that mention *name* or that *name* said, profiles left out.

    These are the records ``consolidate_knowledge()`` builds the person's
    profile from with ``enable_owner_profile_context``: an owner's own turns
    rarely say their name.
    """
    graph = engine._graph_store
    named = await graph.query_nodes(user_id=user_id, scope=scope, content_contains_any=[name], limit=MENTION_SCAN)
    newest = await graph.query_nodes(user_id=user_id, scope=scope, limit=MENTION_SCAN)
    nodes = {
        node.id: node for node in [*named, *newest]
        if not (node.metadata or {}).get("entity_profile")
    }
    wanted = name.casefold()

    def matching() -> list[MemoryNode]:
        return [
            node for node in nodes.values()
            if (metadata_speaker(node.metadata) or "").casefold() == wanted
            or mentions_entity(node.content, name)
        ]

    return await run_profile_work(matching)


async def _is_stale(engine: MemoryEngine, profile: MemoryNode, mentions: list[MemoryNode]) -> bool:
    """Whether a rebuild could change *profile*.

    It could when a newer record names or was said by its person and is not
    among its sources, or when one of its sources is no longer a source a
    build reads (a correction retired it). ``build_profile`` keeps a
    profile's oldest sources within its token budget, so a profile that
    already left sources out and still has all of its own would be rebuilt
    with the same text; it is left as it is.
    """
    metadata = profile.metadata or {}
    sources = [str(value) for value in metadata.get("source_node_ids") or ()]
    if sources:
        current = await engine._graph_store.get_nodes(sources)
        if sum(node.lifecycle_state in PROFILE_SOURCE_STATES for node in current) < len(set(sources)):
            return True
    included, available = metadata.get("source_count_included"), metadata.get("source_count_available")
    if isinstance(included, int) and isinstance(available, int) and included < available:
        return False
    known = set(sources)
    return any(
        node.created_at > profile.created_at and str(node.id) not in known
        for node in mentions
    )


async def _owner_has_policy(engine: MemoryEngine, user_id: str) -> bool:
    privacy = engine._privacy
    return bool(privacy.selected() or await privacy.owner_records(user_id))


async def _rebuild(engine: MemoryEngine, *, user_id: str, scope: Scope, name: str) -> dict | None:
    try:
        published = await engine.consolidate_knowledge(
            user_id=user_id, scope=scope, entity_names=[name], include_speaker_turns=True,
        )
    except Exception:
        logger.warning("Profile rebuild failed; the previous profile stays", exc_info=True)
        return None
    return {"entity_name": name, "scope": scope.value, "published": published > 0}


async def refresh_stale_profiles(engine: MemoryEngine, *, user_id: str, deadline: float) -> list[dict]:
    """Rebuild the owner's stale profiles until *deadline* (``time.monotonic()``).

    Reads the owner's ``PROFILE_REFRESH_SCAN`` newest active profiles and
    rebuilds each one that a newer active record mentions or that its person
    said, newest profile first, once per person and scope. A rebuild starts
    only before the deadline; one that has started runs to its end. Returns
    one entry per rebuild: the person, the scope and whether a profile was
    published (a person with fewer than two sources has their profile retired
    instead).
    """
    if await _owner_has_policy(engine, user_id):
        return []
    found = await engine._graph_store.query_nodes(
        node_type=NodeType.SUMMARY, user_id=user_id, content_contains_any=[PROFILE_PREFIX],
        limit=PROFILE_REFRESH_SCAN,
    )
    newest: dict[tuple[Scope, str], MemoryNode] = {}
    for node in found:
        name = profile_entity_name(node)
        if name is None:
            continue
        key = (node.scope, name.casefold())
        if key not in newest or (node.created_at, str(node.id)) > (newest[key].created_at, str(newest[key].id)):
            newest[key] = node
    rebuilt: list[dict] = []
    for profile in sorted(newest.values(), key=lambda node: (node.created_at, str(node.id)), reverse=True):
        if time.monotonic() >= deadline:
            break
        name = profile_entity_name(profile)
        assert name is not None
        mentions = await _mentioning(engine, user_id=user_id, scope=profile.scope, name=name)
        if not await _is_stale(engine, profile, mentions) or time.monotonic() >= deadline:
            continue
        entry = await _rebuild(engine, user_id=user_id, scope=profile.scope, name=name)
        if entry is not None:
            rebuilt.append(entry)
    return rebuilt


async def refresh_owner_profiles(engine: MemoryEngine, *, user_id: str, deadline: float) -> list[dict] | None:
    """Build or rebuild the owner's profile in each scope where a user turn named them, until *deadline*.

    The owner of a scope is the person entity a user turn most recently
    marked with the owner's name there. Their profile is built when they have
    none and at least two active records mention them or were said by them,
    and rebuilt when such a record is newer than it; otherwise it is left as
    it is. A rebuild is kept under the name the newest profile has, so it
    replaces that profile. A build starts only before the deadline. Returns
    one entry per build or rebuild, as :func:`refresh_stale_profiles` does,
    or None when the owner's memories are under a privacy policy.
    """
    if await _owner_has_policy(engine, user_id):
        return None
    rebuilt: list[dict] = []
    for scope in Scope:
        if time.monotonic() >= deadline:
            break
        owner = await carried_owner_entity(engine._graph_store, user_id=user_id, scope=scope)
        if owner is None:
            continue
        name = owner.content.strip()
        profiles = await profiles_named(engine._graph_store, user_id=user_id, name=name, scopes=[scope])
        mentions = await _mentioning(engine, user_id=user_id, scope=scope, name=name)
        if profiles:
            newest = _newest(profiles)
            if not await _is_stale(engine, newest, mentions):
                continue
            name = profile_entity_name(newest) or name
        elif len(mentions) < MIN_PROFILE_SOURCES:
            continue
        if time.monotonic() >= deadline:
            break
        entry = await _rebuild(engine, user_id=user_id, scope=scope, name=name)
        if entry is not None:
            rebuilt.append(entry)
    return rebuilt
