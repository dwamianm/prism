"""Leave out source text that a correction has replaced (issue #270).

``ingest()`` keeps a message as a note and writes the claims extracted from it
as separate records that cite the message in ``evidence_refs``. When a later
message corrects a claim, only the claim is retired: the note that stated the
old value, and any sibling claim whose text is the same sentence, stay active
and keep reaching the context, sometimes ahead of the correction. A value
stored with ``store()`` and ``supersede()`` has no such leftovers, because the
retired record is the text.

With ``PRMEConfig.enable_superseded_source_suppression``, a candidate is left
out of default retrieval when a retired claim from one of its source messages
is replaced by a record from another message, and the candidate's text holds
that claim's text. Nothing is written: events, notes and claims keep their
states and stay readable by id, and ``RetrievalMode.EXPLICIT`` keeps them in
the results as it keeps superseded records.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from prme.models.nodes import MemoryNode
from prme.retrieval.models import ExcludedCandidate, RetrievalCandidate
from prme.types import LifecycleState, NodeType

if TYPE_CHECKING:
    from prme.storage.graph_store import GraphStore

# The extracted claims a correction retires, and the records that may replace one.
_CLAIM_TYPES = frozenset({NodeType.FACT, NodeType.DECISION, NodeType.PREFERENCE, NodeType.TASK})
_REPLACEMENT_TYPES = _CLAIM_TYPES | {NodeType.NOTE}
# The records whose text can repeat a retired claim's sentence.
_SOURCE_TYPES = _CLAIM_TYPES | {NodeType.NOTE}


def _text(node: MemoryNode) -> str:
    return " ".join((node.content or "").split()).casefold()


def _holds_name(text: str, name: str) -> bool:
    """Whether the (normalized) text has the (normalized) name as a whole word or phrase."""
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) is not None


def _statement(node: MemoryNode) -> tuple | None:
    """The subject, predicate, object and polarity an extracted claim states, if it records them."""
    meta = node.metadata or {}
    if not isinstance(meta.get("predicate"), str):
        return None
    return tuple(str(meta.get(key)).casefold() for key in ("subject", "predicate", "object", "polarity"))


def _replaces(retired: MemoryNode, replacement: MemoryNode | None) -> bool:
    """Whether ``replacement``, from another message, states something else than ``retired`` did.

    A claim that the organizer merged into a duplicate, or one that a later
    message restated, is not a correction: the same statement is still current.
    """
    if replacement is None or replacement.node_type not in _REPLACEMENT_TYPES:
        return False
    if set(retired.evidence_refs) & set(replacement.evidence_refs):
        return False
    if _text(retired) == _text(replacement):
        return False
    statement = _statement(retired)
    return statement is None or statement != _statement(replacement)


async def suppress_replaced_sources(
    candidates: Sequence[RetrievalCandidate],
    *,
    graph_store: GraphStore,
    user_id: str,
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Drop the candidates whose text a correction replaced.

    A candidate goes when a superseded claim that cites one of the candidate's
    own source messages was replaced by a record from another message, and the
    candidate's text holds that claim's text. The source note of the claim
    qualifies, and so does any claim written from the same sentence. A source
    message that states other things too is left out whole: its other claims
    stay active and can reach the context in their own lines.

    The lookups are one count of the owner's superseded records, and when
    there are some, one read of them and one read of their replacements.
    """
    sources = {
        ref
        for candidate in candidates
        if candidate.node.node_type in _SOURCE_TYPES
        for ref in candidate.node.evidence_refs
    }
    states = [LifecycleState.SUPERSEDED]
    # Most owners have nothing retired, and one count tells so.
    total = await graph_store.count_nodes(user_id=user_id, lifecycle_states=states) if sources else 0
    if not total:
        return list(candidates), []
    retired: dict[UUID, list[MemoryNode]] = {}
    for node in await graph_store.query_nodes(user_id=user_id, lifecycle_states=states, limit=total):
        if node.node_type in _CLAIM_TYPES and node.superseded_by is not None and _text(node):
            for ref in sources.intersection(node.evidence_refs):
                retired.setdefault(ref, []).append(node)
    if not retired:
        return list(candidates), []
    replacements = {
        node.id: node
        for node in await graph_store.get_nodes(
            sorted({str(claim.superseded_by) for claims in retired.values() for claim in claims}),
            include_superseded=True,
        )
    }
    # The candidates that go, each with the retired claim whose text it holds,
    # and the text that goes from each source message.
    gone: dict[UUID, MemoryNode] = {}
    lost: dict[UUID, list[tuple[str, MemoryNode]]] = {}
    for candidate in candidates:
        node = candidate.node
        if node.node_type not in _SOURCE_TYPES:
            continue
        text = _text(node)
        replaced = next(
            (
                claim
                for ref in node.evidence_refs
                for claim in retired.get(ref, ())
                if claim.id != node.id
                and _text(claim) in text
                and _replaces(claim, replacements.get(claim.superseded_by))
            ),
            None,
        )
        if replaced is not None:
            gone[node.id] = replaced
            for ref in node.evidence_refs:
                lost.setdefault(ref, []).append((text, replaced))
    kept: list[RetrievalCandidate] = []
    excluded: list[ExcludedCandidate] = []
    for candidate in candidates:
        node = candidate.node
        claim = gone.get(node.id) or _replaced_name(node, lost)
        if claim is None:
            kept.append(candidate)
        else:
            excluded.append(ExcludedCandidate(node_id=node.id, reason=f"replaced_source:{claim.id}"))
    return kept, excluded


def _replaced_name(node: MemoryNode, lost: dict[UUID, list[tuple[str, MemoryNode]]]) -> MemoryNode | None:
    """The retired claim behind an entity name that every source message of the entity lost.

    Packing folds a bare name into the line of the message it came from, so
    it stays out of the context. With that line gone the name would reach the
    context alone, and for a retired value ("Austin") it would be that value.
    """
    name = _text(node)
    if node.node_type != NodeType.ENTITY or not name or not node.evidence_refs:
        return None
    claims = [
        next((claim for text, claim in lost.get(ref, ()) if _holds_name(text, name)), None)
        for ref in node.evidence_refs
    ]
    return claims[0] if all(claims) else None
