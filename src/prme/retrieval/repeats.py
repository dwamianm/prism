"""Records that say the same thing, shown once (``packing.collapse_identical_text``).

A sensor or a patrol stores the same sentence many times ("Hallway check:
nothing moved since the last pass."), and each copy is its own record with
the same similarity to a question, so a context can be the same line over and
over. :func:`collapse_identical_text` keeps the best ranked record of each
group of records with identical text and notes on it how many there are and
the time they span, which is also the count a "how many times" question
needs.

Only what is the same record said again is collapsed: the same text (case
and spacing aside), node type, scope, speaker and lifecycle state. A record
placed by another rule (a claim's ledger state, a current observation, the
owner's profile) is left alone. The others are listed as excluded with the
reason ``identical_text:`` and the id of the record that stands for them. Nothing stored
changes. Deterministic; no model call.
"""

from __future__ import annotations

from prme.models.speaker import metadata_speaker
from prme.retrieval.models import ExcludedCandidate, RetrievalCandidate
from prme.retrieval.time import as_utc
from prme.types import NodeType

# Version of the rule, recorded in receipts when it removed a record.
COLLAPSE_VERSION = 1

_KEPT_APART = frozenset({"BELIEF", "CURRENT_STATE", "OWNER_PROFILE"})
_NOT_COLLAPSED = frozenset({NodeType.ENTITY, NodeType.SUMMARY, NodeType.INSTRUCTION})


def _key(candidate: RetrievalCandidate) -> tuple | None:
    node = candidate.node
    text = " ".join((node.content or "").casefold().split())
    if not text or node.node_type in _NOT_COLLAPSED or _KEPT_APART.intersection(candidate.paths):
        return None
    return (text, node.node_type.value, node.scope.value, metadata_speaker(node.metadata),
            node.lifecycle_state.value)


def _minute(value) -> str:
    return as_utc(value).strftime("%Y-%m-%d %H:%M")


def collapse_identical_text(
    scored: list[RetrievalCandidate],
) -> tuple[list[RetrievalCandidate], list[ExcludedCandidate]]:
    """Keep the best ranked record of each group with identical text; return the rest as excluded.

    ``scored`` is in rank order and the result keeps that order. The kept
    record gets a ``repeat_note`` ("said 37 times, 2026-11-02 08:00 to
    2026-11-02 21:40"; "said 2 times" when the times are equal), which the
    reader format shows as a tag.
    """
    groups: dict[tuple, list[int]] = {}
    for index, candidate in enumerate(scored):
        key = _key(candidate)
        if key is not None:
            groups.setdefault(key, []).append(index)
    repeated = {members[0]: members for members in groups.values() if len(members) > 1}
    if not repeated:
        return scored, []
    dropped = {index: members[0] for members in repeated.values() for index in members[1:]}
    kept: list[RetrievalCandidate] = []
    excluded: list[ExcludedCandidate] = []
    for index, candidate in enumerate(scored):
        if index in dropped:
            excluded.append(ExcludedCandidate(
                node_id=candidate.node.id, reason=f"identical_text:{scored[dropped[index]].node.id}",
                composite_score=candidate.composite_score,
            ))
            continue
        members = repeated.get(index)
        if members is not None:
            times = [scored[member].node.event_time or scored[member].node.created_at for member in members]
            first, last = _minute(min(times, key=as_utc)), _minute(max(times, key=as_utc))
            note = f"said {len(members)} times" + ("" if first == last else f", {first} to {last}")
            candidate = candidate.model_copy(update={"repeat_note": note})
        kept.append(candidate)
    return kept, excluded
