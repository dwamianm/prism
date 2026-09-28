"""Merge a repeated source-cited claim into one current record (#209).

Ingestion creates one node per extracted claim. When an owner states the same
claim again, for example in a later conversation, the earlier copies stay
active and retrieval returns every one of them. With ``enable_claim_merge``
the derivation plan still creates the new node, carries the earlier copies'
evidence, confidence and lifecycle state onto it, and retires those copies
through the ordinary replacement path (RFC-0016). Nothing is deleted, the old
copies keep their SUPERSEDES history, and the plan stays deterministic.

Matching is deliberately strict: exact claim identity after Unicode and case
normalization, never similarity. Claims that can describe separate occurrences
or carry per-node state stay separate: quantities (their sums across
statements are meaningful), conditions (confirmation is tracked per node),
relative dates that did not resolve, pinned or expiring nodes, and anything
that is not observed or asserted (publication only lets those two retire prior
knowledge). A copy whose effective time is later than the new statement is
never merged, so a late-arriving older statement gains no authority over it.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from prme.types import EdgeType, EpistemicType, LifecycleState

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode

MERGEABLE_EPISTEMIC_TYPES = frozenset({EpistemicType.OBSERVED, EpistemicType.ASSERTED})
_ACTIVE = (LifecycleState.TENTATIVE, LifecycleState.STABLE)


def _normalize(value: str) -> str:
    """Compare text by NFKC, case folding, and one space for `_` and whitespace runs."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("_", " ").split())


def claim_key(node: MemoryNode, object_entity_id: str | None) -> tuple | None:
    """Return a claim node's merge identity, or None when it must stay separate.

    The subject is not part of the key: callers only compare claims attached
    to the same resolved subject entity through HAS_FACT.
    """
    metadata = node.metadata or {}
    predicate, obj = metadata.get("predicate"), metadata.get("object")
    if not isinstance(predicate, str) or not isinstance(obj, str):
        return None
    if node.epistemic_type not in MERGEABLE_EPISTEMIC_TYPES or node.pinned or node.ttl_days is not None:
        return None
    if metadata.get("quantity") is not None or metadata.get("condition") is not None:
        return None
    if metadata.get("temporal_ref") and not metadata.get("resolved_date"):
        return None
    return (
        node.node_type.value, node.epistemic_type.value, node.source_type.value,
        _normalize(predicate), _normalize(obj), object_entity_id,
        metadata.get("polarity", "unknown"), metadata.get("resolved_date"),
    )


def _effective(node: MemoryNode):
    return node.event_time or node.valid_from


async def find_repeated_claims(
    graph, subject_entity_id: str, new: MemoryNode, key: tuple,
    object_entity_id: str | None, *, exclude: set[str],
) -> list[MemoryNode]:
    """Return active copies of ``key`` on the subject, oldest first.

    ``exclude`` holds node IDs already planned for this source, which are
    handled before this lookup and must not replace each other.
    """
    edges = await graph.get_edges(source_id=subject_entity_id)
    targets = sorted({str(edge.target_id) for edge in edges if edge.edge_type == EdgeType.HAS_FACT} - exclude)
    if not targets:
        return []
    new_effective = _effective(new)
    copies = []
    for node in await graph.get_nodes(targets):
        if (
            node.lifecycle_state not in _ACTIVE
            or node.valid_to is not None
            or _effective(node) > new_effective
            or claim_key(node, object_entity_id) != key
        ):
            continue
        # The object link is read last: only exact candidates pay for it.
        links = {
            str(edge.target_id) for edge in await graph.get_edges(source_id=str(node.id))
            if edge.edge_type == EdgeType.MENTIONS
        }
        if links == ({object_entity_id} if object_entity_id else set()):
            copies.append(node)
    return sorted(copies, key=lambda node: (_effective(node), str(node.id)))


def merged_claim(new: MemoryNode, copies: list[MemoryNode]) -> MemoryNode:
    """Build the one current record for a repeated claim from its active copies.

    Evidence keeps source order without repeats. Scores keep their highest
    value and a stable copy keeps the claim stable. The new statement's text,
    times and remaining metadata are kept unchanged.
    """
    evidence = []
    for ref in [ref for copy in copies for ref in copy.evidence_refs] + list(new.evidence_refs):
        if ref not in evidence:
            evidence.append(ref)
    nodes = [new, *copies]
    stable = any(copy.lifecycle_state == LifecycleState.STABLE for copy in copies)
    return new.model_copy(update={
        "evidence_refs": evidence,
        "confidence": max(node.confidence for node in nodes),
        "confidence_base": max(node.confidence_base for node in nodes),
        "salience": max(node.salience for node in nodes),
        "salience_base": max(node.salience_base for node in nodes),
        "reinforcement_boost": max(node.reinforcement_boost for node in nodes),
        "lifecycle_state": LifecycleState.STABLE if stable else new.lifecycle_state,
        "metadata": {**(new.metadata or {}), "merged_claim_ids": [str(copy.id) for copy in copies]},
    })
