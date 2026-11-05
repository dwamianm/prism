"""Owner-scoped graph reads shared by both graph store backends.

A graph read given a ``user_id`` never steps onto, returns or names another
user's node (#438, #443, #444). Stores written before merges refused nodes of
different users, and direct graph writes, can hold links that cross users: an
edge whose two nodes belong to different users, or a ``superseded_by`` pointer
that names another user's node. Scoped reads skip those links; unscoped reads
(no ``user_id``) return the store as it is, and ``find_cross_user_links``
lists the links so an operator can repair an old store.

A node that no longer exists is not another user's node: a walk still passes
an edge whose node was deleted, and a pointer to a deleted node is kept, as
before, so a store without crossing links reads exactly as it did.

``superseded_by`` is cleared only on a retired node (superseded, deprecated or
archived), which is what every writer that sets the pointer leaves. An active
node that carries a pointer exists only after a direct graph write; its read is
left as stored, because ``ingest()`` planning and the profile and consolidation
publications compare the active nodes they read with the stored rows, and a
cleared copy would never match. ``find_cross_user_links`` lists such a node too.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from prme.models.nodes import MemoryNode
from prme.types import ACTIVE_LIFECYCLE_STATES


def _names_successor(node: MemoryNode) -> bool:
    return node.superseded_by is not None and node.lifecycle_state not in ACTIVE_LIFECYCLE_STATES


def named_successors(nodes: Iterable[MemoryNode]) -> list[str]:
    """The distinct node ids the given retired nodes name in ``superseded_by``."""
    return sorted({str(node.superseded_by) for node in nodes if _names_successor(node)})


def hide_foreign_successors(nodes: list[MemoryNode], foreign_ids: set[str]) -> list[MemoryNode]:
    """Return the nodes with ``superseded_by`` cleared where a retired node names a foreign node.

    ``foreign_ids`` holds the named nodes another user owns. The stored node is
    not changed; a node that names no foreign node is returned as it was read.
    """
    if not foreign_ids:
        return nodes
    return [
        node.model_copy(update={"superseded_by": None})
        if _names_successor(node) and str(node.superseded_by) in foreign_ids
        else node
        for node in nodes
    ]


@dataclass(frozen=True)
class CrossUserEdge:
    """An edge whose nodes, or whose own owner, are not all one user's."""

    edge_id: str
    edge_type: str
    owner: str
    source_id: str
    source_owner: str
    target_id: str
    target_owner: str


@dataclass(frozen=True)
class CrossUserPointer:
    """A node whose ``superseded_by`` names another user's node."""

    node_id: str
    owner: str
    superseded_by: str
    successor_owner: str


@dataclass(frozen=True)
class CrossUserLinks:
    """The links in a store that cross users, counted in full and listed up to a limit."""

    edge_count: int
    pointer_count: int
    edges: list[CrossUserEdge] = field(default_factory=list)
    pointers: list[CrossUserPointer] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.edge_count + self.pointer_count

    def to_dict(self) -> dict:
        return {
            "edge_count": self.edge_count,
            "pointer_count": self.pointer_count,
            "edges": [asdict(edge) for edge in self.edges],
            "pointers": [asdict(pointer) for pointer in self.pointers],
        }


# Both backends run these statements; the edge test reads "the edge's two
# nodes, and the edge itself, do not all belong to one user". An edge with a
# deleted node is left out: it crosses nobody.
CROSS_USER_EDGE_WHERE = (
    "s.user_id <> t.user_id OR e.user_id <> s.user_id OR e.user_id <> t.user_id"
)
CROSS_USER_POINTER_WHERE = "n.user_id <> s.user_id"
