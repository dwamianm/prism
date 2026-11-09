"""Keep knowledge profiles fresh after ingest (``enable_owner_profile_context``, issue #398).

A first-person question gets the owner's profile (:mod:`prme.retrieval.owner_profile`),
so a profile built before the owner's latest turns would answer from old
knowledge. ``consolidate_knowledge()`` builds a profile from the records that
mention the person by name and, for these rebuilds, from the turns the
person spoke (``include_speaker_turns``), so a profile is stale when an
active record newer than it mentions that name or was said by that person
and is not among its sources. A profile that already left sources out of its
token budget is not rebuilt: its oldest sources come first, so its text would
not change. A rebuild keeps the token budget the replaced profile recorded, so
a profile an application built with a larger budget is not silently cut back to
the default (issue #422).

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

from prme.config import DEFAULT_MAX_PROFILE_TOKENS
from prme.ingestion.owner_name import carried_owner_entity
from prme.models.speaker import metadata_speaker
from prme.organizer.profiles import mentions_entity, run_profile_work
from prme.retrieval.owner_profile import PROFILE_PREFIX, profile_entity_name, profiles_named
from prme.types import EdgeType, LifecycleState, NodeType, Scope

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


def _profile_budget(profile: MemoryNode | None) -> int:
    """The token budget to rebuild *profile* with.

    A profile records the ``max_profile_tokens`` it was built with, so a
    rebuild keeps a larger budget instead of falling back to the default and
    silently dropping sources the earlier build had included (issue #422).
    A profile written before that key existed falls back to its
    ``tokens_used``, which was within its budget, so a rebuild starts from the
    tokens it used rather than the default. A recorded zero is not a budget a
    build can publish, so it is treated as unknown.
    """
    metadata = (profile.metadata or {}) if profile is not None else {}
    budget = metadata.get("max_profile_tokens")
    if type(budget) is int and budget > 0:
        return budget
    used = metadata.get("tokens_used")
    if type(used) is int and used >= 0:
        return max(DEFAULT_MAX_PROFILE_TOKENS, used)
    return DEFAULT_MAX_PROFILE_TOKENS


async def _bound_claims(engine: MemoryEngine, *, user_id: str, scope: Scope, name: str) -> list[MemoryNode]:
    """The active claims the graph binds to *name*'s entity, profiles left out.

    ``consolidate_knowledge()`` takes the same claims (issue #448), so a
    profile built before extraction materialized them is rebuilt once they
    arrive. Only the entity's subject claims are read here: a claim whose
    first-person text was written with the name, and a claim from the
    person's turn, are among the newest records :func:`_mentioning` reads.
    It reads the plain graph store, so its callers must have refused a store
    with a privacy policy first, as both do.
    """
    from prme.storage.engine import PROFILE_CLAIM_TYPES

    graph = engine._graph_store
    wanted = name.casefold()
    # A content filter keeps this read small: the pass runs it once per
    # profile it considers, and an unscoped entity scan per profile is costly.
    entities = await graph.query_nodes(
        user_id=user_id, scope=scope, node_type=NodeType.ENTITY,
        lifecycle_states=list(PROFILE_SOURCE_STATES), content_contains_any=[name], limit=MENTION_SCAN,
    )
    entities = [node for node in entities if node.content.strip().casefold() == wanted]
    people = [
        str(node.id) for node in entities
        if str((node.metadata or {}).get("entity_type") or "").strip().casefold() == "person"
    ]
    entity_ids = people or [str(node.id) for node in entities]
    targets: set[str] = set()
    for entity_id in entity_ids:
        targets.update(
            str(edge.target_id)
            for edge in await graph.get_edges(source_id=entity_id, edge_type=EdgeType.HAS_FACT)
            if edge.user_id == user_id
        )
    if not targets:
        return []
    return [
        node for node in await graph.get_nodes(sorted(targets), user_id=user_id)
        if node.scope == scope
        and node.node_type in PROFILE_CLAIM_TYPES
        and node.lifecycle_state in PROFILE_SOURCE_STATES
        and not (node.metadata or {}).get("entity_profile")
    ]


async def _mentioning(engine: MemoryEngine, *, user_id: str, scope: Scope, name: str) -> list[MemoryNode]:
    """The newest active records in *scope* that mention *name* or that *name* said, profiles left out.

    These are the records ``consolidate_knowledge()`` builds the person's
    profile from with ``enable_owner_profile_context``: an owner's own turns
    rarely say their name. The claims bound to the person's entity are added
    from the graph, since neither a first-person claim nor a claim from their
    turn names them (issue #448).
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
        found = []
        for node in nodes.values():
            if (metadata_speaker(node.metadata) or "").casefold() == wanted or mentions_entity(node.content, name):
                found.append(node)
                continue
            reference = (node.metadata or {}).get("speaker_reference")
            speaker = reference.get("speaker") if isinstance(reference, dict) else None
            if isinstance(speaker, str) and speaker.casefold() == wanted:
                found.append(node)
        return found

    found = await run_profile_work(matching)
    # A claim from the person's turn can carry no speaker of its own when the
    # turn wrote the name into the claim, so the event it cites decides, as
    # consolidate_knowledge() reads it. Only the newest records are checked.
    from prme.storage.engine import PROFILE_CLAIM_TYPES

    known = {node.id for node in found}
    for node in nodes.values():
        if node.id in known or node.node_type not in PROFILE_CLAIM_TYPES:
            continue
        reference = (node.metadata or {}).get("speaker_reference")
        if isinstance(reference, dict) and isinstance(reference.get("speaker"), str):
            continue
        if not node.evidence_refs:
            continue
        event = await engine._event_store.get(str(node.evidence_refs[-1]))
        if (
            event is not None and event.user_id == user_id and event.scope == scope
            and (metadata_speaker(event.metadata) or "").casefold() == wanted
        ):
            found.append(node)
    bound = await _bound_claims(engine, user_id=user_id, scope=scope, name=name)
    # One record can qualify on more than one path; count it once.
    return list({node.id: node for node in (*found, *bound)}.values())


async def _is_stale(engine: MemoryEngine, profile: MemoryNode, mentions: list[MemoryNode]) -> bool:
    """Whether a rebuild could change *profile*.

    It could when a newer record names its person, was said by them, or is a
    claim their entity binds and is not among its sources, or when one of its
    sources is no longer a source a build reads (a correction retired it).
    ``build_profile`` keeps a profile's oldest sources within its token
    budget and breaks an event-time tie by content (issue #497), so a profile
    that already left sources out and still has all of its own is rebuilt
    with the same text and is left as it is. A profile built before that
    tie-break kept the same sources but may have quoted a different tied
    source; nothing here rebuilds it for the ordering change alone, so it
    improves only when an application replaces it.
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


async def _rebuild(
    engine: MemoryEngine, *, user_id: str, scope: Scope, name: str, profile: MemoryNode | None,
) -> dict | None:
    """Rebuild one profile at the budget it recorded; None when the rebuild failed.

    *profile* is the active profile the rebuild replaces, or None when there is
    none yet. A caller must pass it rather than rely on a default, so a future
    caller cannot silently rebuild at the default budget (issue #422).
    """
    try:
        published = await engine.consolidate_knowledge(
            user_id=user_id, scope=scope, entity_names=[name],
            max_profile_tokens=_profile_budget(profile), include_speaker_turns=True,
        )
    except Exception:
        logger.warning("Profile rebuild failed; the previous profile stays", exc_info=True)
        return None
    return {"entity_name": name, "scope": scope.value, "published": published > 0}


async def refresh_stale_profiles(engine: MemoryEngine, *, user_id: str, deadline: float) -> list[dict]:
    """Rebuild the owner's stale profiles until *deadline* (``time.monotonic()``).

    Reads the owner's ``PROFILE_REFRESH_SCAN`` newest active profiles and
    rebuilds each one that a newer active record mentions, that its person
    said, or that its person's entity binds, newest profile first, once per
    person and scope. A rebuild starts only before the deadline; one that has
    started runs to its end. Returns
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
        entry = await _rebuild(
            engine, user_id=user_id, scope=profile.scope, name=name, profile=profile,
        )
        if entry is not None:
            rebuilt.append(entry)
    return rebuilt


async def refresh_owner_profiles(engine: MemoryEngine, *, user_id: str, deadline: float) -> list[dict] | None:
    """Build or rebuild the owner's profile in each scope where a user turn named them, until *deadline*.

    The owner of a scope is the person entity a user turn most recently
    marked with the owner's name there. Their profile is built when they have
    none and at least two active records mention them, were said by them, or
    are claims their entity binds, and rebuilt when such a record is newer
    than it; otherwise it is left as it is. A rebuild is kept under the name
    the newest profile has, so it replaces that profile. A build starts only
    before the deadline. Returns
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
        profile = None
        if profiles:
            profile = _newest(profiles)
            if not await _is_stale(engine, profile, mentions):
                continue
            name = profile_entity_name(profile) or name
        elif len(mentions) < MIN_PROFILE_SOURCES:
            continue
        if time.monotonic() >= deadline:
            break
        entry = await _rebuild(
            engine, user_id=user_id, scope=scope, name=name, profile=profile,
        )
        if entry is not None:
            rebuilt.append(entry)
    return rebuilt
