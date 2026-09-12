"""Admission rules for automatic identity/duplicate merges, independent of scores."""

from prme.models.entity_identity import unresolved_personal_reference
from prme.models.nodes import MemoryNode
from prme.types import NodeType


def compatible_provenance(a: MemoryNode, b: MemoryNode) -> bool:
    """Do not combine different owners, source roles, episodes, types or claims."""
    fields = ("user_id", "scope", "node_type", "source_type", "epistemic_type", "session_id",
              "event_time", "valid_to", "ttl_days", "pinned")
    if any(getattr(a, field) != getattr(b, field) for field in fields):
        return False
    if (a.metadata or {}) != (b.metadata or {}):
        return False
    if a.node_type == NodeType.ENTITY:
        for node in (a, b):
            metadata = node.metadata or {}
            if metadata.get("identity_status") == "unresolved_reference" or unresolved_personal_reference(node.content, metadata.get("entity_type")):
                return False
    return True


def duplicate_merge_allowed(a: MemoryNode, b: MemoryNode) -> bool:
    """Similarity cannot establish equivalence; state copies must share validity."""
    if not compatible_provenance(a, b):
        return False
    if a.node_type == NodeType.ENTITY:
        # Preserve the existing conservative named-entity normalization. This
        # still does not solve equal-name homonyms without explicit identities.
        return a.content.strip().casefold() == b.content.strip().casefold()
    return a.content == b.content and a.valid_from == b.valid_from


def alias_pair_allowed(a: MemoryNode, b: MemoryNode) -> bool:
    return a.node_type == b.node_type == NodeType.ENTITY and compatible_provenance(a, b)
