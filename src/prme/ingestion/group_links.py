"""Link a claim a user states about "we" to the owner's entity (enable_owner_group_links).

A user turn that says "We chose Tokyo, Kyoto, and Osaka" is the memory's owner
speaking for a group they belong to. The claim is attached to the group the
turn names (a "We" entity local to the message), because a "we" claim is not
always true of one person alone, so a graph walk from the owner's entity never
reached it (#259, #260). This adds one more link: a MENTIONS edge marked
``metadata.basis=group_member`` from the claim to the entity that carries the
name a user turn gave the owner (enable_owner_name_carry). The claim keeps its
subject and its group link, and the owner's own "I" claims are unchanged.

Like the text links of ``mention_links``, this runs after a derivation commits,
outside the derivation plan, and creates only the links that are missing. A
failure is logged and never fails or changes the extraction. A turn that is not
the owner's, a turn that names nobody before it, and an owner no user turn named
get no link, so no name is invented.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

import structlog

from prme.epistemic.inference import OWNER_ROLES
from prme.ingestion.owner_name import carried_owner_entity
from prme.models.edges import MemoryEdge
from prme.models.entity_identity import unresolved_personal_reference
from prme.models.owner_reference import OWNER_REFERENCE_KEY
from prme.models.speaker import metadata_speaker
from prme.types import ACTIVE_LIFECYCLE_STATES, EdgeType, NodeType

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.storage.event_store import EventStore
    from prme.storage.graph_store import GraphStore

logger = structlog.get_logger(__name__)

# Marks a MENTIONS edge that records only that a claim's we includes the owner.
# Claim merging ignores it, as it ignores a text link.
GROUP_LINK_METADATA = {"basis": "group_member"}

FIRST_PERSON_PLURAL = frozenset({"we", "us", "our", "ours", "ourselves"})
_CLAIM_TYPES = frozenset({NodeType.FACT, NodeType.DECISION, NodeType.PREFERENCE, NodeType.TASK})


def is_group_link(edge: MemoryEdge) -> bool:
    """Whether the edge only records that its source claim's we includes its target."""
    return (edge.metadata or {}).get("basis") == GROUP_LINK_METADATA["basis"]


def _speaks_for_a_group(node: MemoryNode) -> bool:
    """Whether the claim's subject or object is a literal we, us or our."""
    metadata = node.metadata or {}
    return any(
        isinstance(metadata.get(field), str)
        and metadata[field].strip().casefold() in FIRST_PERSON_PLURAL
        and unresolved_personal_reference(metadata[field], metadata.get(f"{field}_entity_type"))
        for field in ("subject", "object")
    )


async def link_group_claims(
    event_id: str, sources: Sequence[MemoryNode], *, user_id: str, graph_store: GraphStore,
    event_store: EventStore, create_edge: Callable[[MemoryEdge], Awaitable[object]],
) -> int:
    """MENTIONS-link the claims of an owner's turn that speak for a group to the owner's entity.

    ``sources`` are the nodes derived from the event. Only claims whose subject
    or object is a literal we, us or our are linked, and only when the event is
    a user turn that is not the owner's first-person reference. The owner is
    the entity a user turn marked with the name it gave the owner, under the
    turn's own ``speaker`` when the caller named one. Returns the number of
    links created.
    """
    created = 0
    try:
        claims = [
            node for node in sources
            if node.node_type in _CLAIM_TYPES and node.lifecycle_state in ACTIVE_LIFECYCLE_STATES
            and _speaks_for_a_group(node)
        ]
        if not claims:
            return 0
        event = await event_store.get(event_id)
        if (event is None or event.role.casefold() not in OWNER_ROLES
                or (event.metadata or {}).get(OWNER_REFERENCE_KEY) is True):
            return 0
        owner = await carried_owner_entity(
            graph_store, user_id=user_id, scope=event.scope, name=metadata_speaker(event.metadata),
        )
        if owner is None:
            return 0
        linked = {
            (edge.source_id, edge.target_id)
            for edge in await graph_store.get_edges(node_ids=[str(node.id) for node in claims])
        }
        for claim in claims:
            if (claim.id, owner.id) in linked or (owner.id, claim.id) in linked:
                continue
            edge = MemoryEdge(
                id=uuid5(UUID(event_id), f"prme:group-link:{claim.id}:{owner.id}"),
                source_id=claim.id, target_id=owner.id, edge_type=EdgeType.MENTIONS,
                user_id=user_id, provenance_event_id=UUID(event_id), metadata=dict(GROUP_LINK_METADATA),
            )
            try:
                await create_edge(edge)
            except Exception:
                # A retry of the same publication finds the link made; the edge ID is fixed.
                if not any(e.id == edge.id for e in await graph_store.get_edges(source_id=str(claim.id))):
                    raise
                continue
            created += 1
    except Exception as exc:
        logger.warning("group_links.failed", event_id=event_id, error_type=type(exc).__name__)
    return created
