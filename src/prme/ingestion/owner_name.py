"""Remember the name a memory's owner gave, so their later turns keep it (enable_owner_name_carry).

A user turn is the memory's owner by definition. The turn that gives their
name, by the caller's ``speaker`` or by saying who it is ("Hi, I am Alex
Rivera"), binds the turn's I, me and my to that person's entity. A later turn
that names nobody ("I prefer trains") has no name to bind to, so it kept an I
entity of its own, one per message.

The name is remembered on the person entity itself: the entity the turn
creates for the owner carries ``owner_name`` metadata, written with the node
when the derivation commits and never changed afterwards, so a derivation plan
that cites the entity does not go stale. A later user turn finds the owner's
newest such entity and binds its I to that name. Nothing is invented: the name
is one an earlier user turn gave.

Remembered names stay on the entities that were created with them. An earlier
claim is never rewritten when the owner gives another name; the newest named
entity wins from then on. An entity that already existed unmarked, for example
a person an earlier message named before the owner did, cannot be marked, so
that owner keeps their own I.
"""

from __future__ import annotations

from typing import Literal

import structlog

from prme.models.nodes import MemoryNode
from prme.storage.graph_store import GraphStore
from prme.types import NodeType, Scope

logger = structlog.get_logger(__name__)

OWNER_NAME_KEY = "owner_name"

# Entity nodes read per page while looking for the owner's name; the same page
# size entity matching uses.
_PAGE = 256


def owner_name_marker(event_id: str, basis: Literal["speaker", "self_introduction"]) -> dict:
    """The metadata a person entity carries when a user turn gave the owner's name."""
    return {"event_id": event_id, "basis": basis}


async def carried_owner_name(graph_store: GraphStore, *, user_id: str, scope: Scope) -> str | None:
    """The name of the owner's newest named entity, or None when no user turn gave one.

    Reads the owner's entities in this scope page by page, as entity matching
    does, and returns the content of the one marked most recently. Best effort:
    a failure is logged and returns None, which leaves the turn's I as it was.
    """
    newest: MemoryNode | None = None
    after_id: str | None = None
    try:
        while True:
            page = await graph_store.scan_nodes(
                node_type=NodeType.ENTITY, user_id=user_id, scope=scope, after_id=after_id, limit=_PAGE,
            )
            for node in page:
                marker = (node.metadata or {}).get(OWNER_NAME_KEY)
                if marker and node.content.strip() and (
                    newest is None or (node.created_at, str(node.id)) > (newest.created_at, str(newest.id))
                ):
                    newest = node
            if len(page) < _PAGE:
                break
            after_id = str(page[-1].id)
    except Exception as exc:
        logger.warning("owner_name.lookup_failed", error_type=type(exc).__name__)
        return None
    return newest.content.strip() if newest is not None else None
