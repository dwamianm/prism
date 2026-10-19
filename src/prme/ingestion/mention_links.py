"""Link an event's entities to the claims and source note whose text names them.

Extraction saves entities and claims, but a claim is linked only to its subject
(HAS_FACT) and a resolved object (MENTIONS). A claim whose object text contains
an entity name ("Japan in late March next year"), an entity that no claim is
about (the cities of "We chose Tokyo, Kyoto, and Osaka"), and the raw note all
stayed unlinked, so a graph walk from the entity found nothing (#259).

This runs after a derivation commits and after the raw note is created, in
whichever order those happen, and only creates the links that are missing. It
is not part of the derivation plan: the note may not exist when the plan is
made, an entity that no claim refers to is not a plan dependency, and a link
is derived from text the plan already holds, so a failure here never fails or
changes the plan.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

import structlog

from prme.models.edges import MemoryEdge
from prme.models.entity_identity import unresolved_personal_reference
from prme.types import ACTIVE_LIFECYCLE_STATES, EdgeType, NodeType

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.event_store import EventStore
    from prme.storage.graph_store import GraphStore

logger = structlog.get_logger(__name__)

# Marks a MENTIONS edge that records only that text names an entity. A claim's
# own subject and object links do not carry it, and claim merging ignores it.
TEXT_LINK_METADATA = {"basis": "text_mention"}


def is_text_link(edge: MemoryEdge) -> bool:
    """Whether the edge only records that its source's text names its target."""
    return (edge.metadata or {}).get("basis") == TEXT_LINK_METADATA["basis"]


async def link_event_mentions(
    event_id: str, sources: Sequence[MemoryNode], *, user_id: str, graph_store: GraphStore,
    event_store: EventStore, create_edge: Callable[[MemoryEdge], Awaitable[object]],
    with_note: bool = False,
) -> int:
    """MENTIONS-link an event's nodes to the entities their text names.

    ``sources`` are the nodes derived from the event that exist now, such as
    its claims; ``with_note`` adds its raw note when that exists. The entities
    are the ones the event's saved extraction lists. A name that two listed
    entities share, or that no single active entity of the owner carries, is
    skipped rather than guessed, as is a literal I or we. Best effort: a
    failure is logged and returns what was linked so far, because the
    derivation and the note are already durable. Returns the number of links
    created.
    """
    created = 0
    try:
        sources = list(sources)
        if with_note and (note := await graph_store.get_node(event_id)) is not None:
            sources.append(note)
        sources = [
            node for node in sources
            if node.node_type != NodeType.ENTITY and node.lifecycle_state in ACTIVE_LIFECYCLE_STATES
        ]
        if not sources:
            return 0
        extraction = await event_store.get_extraction(event_id, user_id=user_id)
        if extraction is None:
            return 0
        listed: dict[str, tuple[str, set[str | None]]] = {}
        for entity in extraction.result.get("entities") or []:
            name = (entity.get("name") or "").strip()
            kind = entity.get("entity_type")
            if name and not unresolved_personal_reference(name, kind):
                listed.setdefault(name.lower(), (name, set()))[1].add(kind)
        if not listed:
            return 0

        owned: dict[str, list] = {}
        for node in await graph_store.query_nodes(
            node_type=NodeType.ENTITY, user_id=user_id, scope=extraction.scope,
            content_contains_any=[name for name, _ in listed.values()], limit=500,
        ):
            owned.setdefault(node.content.strip().lower(), []).append(node)
        targets = []
        for key, (name, kinds) in listed.items():
            nodes = [n for n in owned.get(key, []) if (n.metadata or {}).get("entity_type") in kinds]
            if len(kinds) == 1 and len(nodes) == 1:
                targets.append((name, nodes[0]))
        if not targets:
            return 0

        from prme.organizer.profiles import mentions_entity

        linked = {
            (edge.source_id, edge.target_id)
            for edge in await graph_store.get_edges(node_ids=[str(node.id) for node in sources])
        }
        for source in sources:
            # A claim's object can name an entity its text no longer does.
            texts = [source.content]
            if source.node_type != NodeType.NOTE:
                texts.append((source.metadata or {}).get("object"))
            for name, entity in targets:
                if (source.id, entity.id) in linked or (entity.id, source.id) in linked:
                    continue
                if not any(isinstance(text, str) and mentions_entity(text, name) for text in texts):
                    continue
                edge = MemoryEdge(
                    id=uuid5(UUID(event_id), f"prme:text-link:{source.id}:{entity.id}"),
                    source_id=source.id, target_id=entity.id, edge_type=EdgeType.MENTIONS,
                    user_id=user_id, provenance_event_id=UUID(event_id),
                    metadata=dict(TEXT_LINK_METADATA),
                )
                try:
                    await create_edge(edge)
                except Exception:
                    # The note and the derivation can each find the link missing
                    # at once; the edge ID is fixed, so the second write fails.
                    if not any(e.id == edge.id for e in await graph_store.get_edges(source_id=str(source.id))):
                        raise
                    continue
                created += 1
    except Exception as exc:
        logger.warning("mention_links.failed", event_id=event_id, error_type=type(exc).__name__)
    return created
