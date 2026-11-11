"""Which claim holds now (``enable_belief_state``, memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md).

A routed question (``enable_relation_routing``) reaches the claims stored at
one address: an entity and a relation, such as Sam and ``works_at``. Rank
fusion then treats those claims as one more vote, so an old claim whose words
match the question ("I work as a nurse at St. Mary's hospital") ranks above
the claim that replaced it ("I start at a pediatric clinic on November 2").

This module reads the claims at an address as a small ledger and says which
one holds at the question's time:

- :func:`read_ledger` gives each claim a state from what the claim itself
  records (its typed ``status``, its polarity, its stated time) and from the
  later claims at the same address: ``current``, ``planned``, ``earlier``
  (a newer value took its place at a single-valued address), ``ended`` (a
  later claim says it stopped) or ``negated``.
- :func:`place_beliefs` puts those claims at the head of the results in ledger
  order, ahead of the text ranking, with a ``belief`` score adjustment that
  replays, and the ``BELIEF`` path, which packing reserves.

Nothing is written: the ledger is computed from stored claims at read time, so
a rebuild, a re-ingest or a correction changes the next answer and no stored
memory is rewritten. It is deterministic and calls no model.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import re
from typing import TYPE_CHECKING

from prme.retrieval.models import RetrievalCandidate, ScoreAdjustment, rank_fusion_relevance
from prme.retrieval.time import as_utc

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode

# The path a claim placed by its ledger state carries; packing reserves it (tier 2).
BELIEF_PATH = "BELIEF"

# The version of the ledger rule recorded in receipts. Change it with any
# change to the states, the single-valued relations, the closing rules or the
# order that can change where a claim is placed.
BELIEF_VERSION = 1

# Relations where a newer value replaces the older one: a person has one
# employer, one home and one partner at a time unless the claims say
# otherwise. Every other relation holds a set (pets, allergies, relatives,
# preferences), where a new value adds to the old ones.
SINGLE_VALUED = frozenset({"works_at", "lives_in", "partner_of", "located_in"})



def is_single_valued(relation: str) -> bool:
    """Whether a newer value at this relation replaces the older one. An attribute (``has:diet``) has one value."""
    return relation in SINGLE_VALUED or relation.startswith("has:")


# Each placed claim takes the top result's score times one plus this fraction
# per place from the end, so the claims lead the results in ledger order and
# keep that order through selection and packing.
BELIEF_SCORE_STEP = 1e-3

# The order of states for a question about now. A question about history
# reads the ledger in time order instead.
_NOW_ORDER = {"current": 0, "planned": 1, "earlier": 2, "ended": 3, "negated": 4}

_ARTICLES = frozenset({"a", "an", "the", "my", "our", "his", "her", "their", "its", "this", "that"})


@dataclass(frozen=True)
class Belief:
    """One claim at an address and whether it holds at the question's time."""

    node_id: str
    state: str
    relation: str
    role: str | None
    pivot: str
    when: datetime


def _words(text: object) -> frozenset[str]:
    if not isinstance(text, str):
        return frozenset()
    return frozenset(word for word in re.findall(r"[a-z0-9']+", text.casefold()) if word not in _ARTICLES)


def _same_value(left: frozenset[str], right: frozenset[str]) -> bool:
    """Whether two object phrases name the same thing: one's words contain the other's ("the hospital",
    "St. Mary's hospital")."""
    return bool(left and right) and (left <= right or right <= left)


def _claim_time(node: MemoryNode) -> datetime:
    return as_utc(node.event_time or node.valid_from or node.created_at)


def _text_key(node: MemoryNode) -> str:
    """A claim's text, to order claims stated at one time. A node id is new on every build of the same log, so
    it only orders claims whose time and text are both equal, which say the same thing."""
    return " ".join((node.content or "").casefold().split())


def _own_state(node: MemoryNode, now: datetime) -> str:
    """The state a claim records for itself, before later claims are read."""
    metadata = node.metadata or {}
    if metadata.get("polarity") == "negative":
        return "negated"
    status = metadata.get("status")
    if status in ("past", "superseded"):
        return "ended"
    if node.valid_to is not None and as_utc(node.valid_to) <= now:
        return "ended"
    if status == "planned" and _claim_time(node) > now:
        return "planned"
    # current, done, a plan whose date has come, and an untyped claim: it holds unless a later claim says otherwise.
    return "current"


def read_ledger(
    nodes: Mapping[str, MemoryNode],
    claims: Sequence[Mapping[str, object]],
    pivots: Mapping[str, str],
    *,
    last_step: int,
    now: datetime,
    history: bool = False,
) -> list[Belief]:
    """The claims of a route's last step as a ledger, in the order a reader should see them.

    ``claims`` is the walk's record (node id, step, relation, role), ``pivots``
    the entity each claim was read on, and ``nodes`` the claim nodes. Claims
    are grouped by address (pivot entity, relation, and the role except at a
    single-valued relation). Inside an address:

    - a claim that states an end (``past``, negative polarity) closes every
      claim stated before it that names the same value, and at a single-valued
      relation every earlier claim, since "I quit" need not name the place;
    - at a single-valued relation the newest claim that holds is ``current``
      and the older ones are ``earlier``; claims stated at the newest time
      hold together.

    The result lists every address's claims: for a question about now,
    ``current`` first (newest first), then ``planned`` (soonest first),
    ``earlier``, ``ended`` and ``negated`` (newest first); with ``history``,
    in time order. Claims stated at one time are ordered by their text, so
    the order is the same on every build of the same log.
    """
    now = as_utc(now)
    groups: dict[tuple[str, str, str | None], list[tuple[datetime, str, str]]] = defaultdict(list)
    for claim in claims:
        node_id = str(claim["node_id"])
        node = nodes.get(node_id)
        if claim.get("step") != last_step or node is None or node_id not in pivots:
            continue
        relation = str(claim["relation"])
        role = claim.get("role")
        # A role names which relative or which part (kin_of sister, kin_of mother), so it is part of the address.
        # At a single-valued relation it is a detail of the one value (works_at with the title nurse).
        address = (pivots[node_id], relation,
                   role if isinstance(role, str) and not is_single_valued(relation) else None)
        groups[address].append((_claim_time(node), node_id, _own_state(node, now)))

    beliefs: list[Belief] = []
    for (pivot, relation, role), members in groups.items():
        members.sort()
        states = {node_id: state for _when, node_id, state in members}
        values = {node_id: _words((nodes[node_id].metadata or {}).get("object")) for _when, node_id, _state in members}
        single = is_single_valued(relation)
        # An end closes what was stated before it. A claim stated at the same time is not earlier: "I quit the
        # hospital. I start at a clinic." in one turn must not close the clinic, and their order in the log is
        # not recorded.
        for closed_at, closer, state in members:
            if state not in ("ended", "negated"):
                continue
            for earlier_when, earlier, _earlier_state in members:
                if earlier_when < closed_at and states[earlier] == "current" and (
                    _same_value(values[earlier], values[closer]) or (single and state == "ended")
                ):
                    states[earlier] = "ended"
        if single:
            # The newest stated time holds. Claims stated at that same time hold together: one turn can state
            # the place and its schedule, and nothing in the log says which of the two is the older.
            holding = [(when, node_id) for when, node_id, _state in members if states[node_id] == "current"]
            newest = holding[-1][0] if holding else None
            for when, node_id in holding:
                if when < newest:
                    states[node_id] = "earlier"
        beliefs.extend(
            Belief(node_id=node_id, state=states[node_id], relation=relation, role=role, pivot=pivot, when=when)
            for when, node_id, _state in members
        )

    if history:
        beliefs.sort(key=lambda belief: (belief.when, _text_key(nodes[belief.node_id]), belief.node_id))
    else:
        beliefs.sort(key=lambda belief: (
            _NOW_ORDER[belief.state],
            belief.when.timestamp() if belief.state == "planned" else -belief.when.timestamp(),
            _text_key(nodes[belief.node_id]),
            belief.node_id,
        ))
    return beliefs


def place_beliefs(
    scored: list[RetrievalCandidate],
    beliefs: Sequence[Belief],
    nodes: Mapping[str, MemoryNode],
    *,
    lead: bool = True,
) -> tuple[list[RetrievalCandidate], int]:
    """Put the ledger's claims together in the results, in ledger order.

    With ``lead`` (a question whose own words named the relation) they go to
    the head of the results. Without it (an address found in the catalog by
    the claim the question matches best) they go where the best ranked of
    them already stands, so a claim that holds now is never below one that
    ended, and an address the question was not about gains no rank.

    The i-th of n claims takes the top result's score times
    ``1 + (n - i) * BELIEF_SCORE_STEP``, with the top result's score
    provenance and a ``belief`` adjustment that names it, so the receipt
    replays the score. Each gets the ``BELIEF`` path and its ``belief_state``,
    which the reader format shows as a tag. A claim that is not among
    ``scored`` is added.

    Returns the candidates sorted by score and node id, and how many claims
    were placed (0 leaves ``scored`` as it was).
    """
    if not scored or not beliefs:
        return scored, 0
    wanted = {belief.node_id for belief in beliefs}
    anchors = scored if lead else [candidate for candidate in scored if str(candidate.node.id) in wanted]
    if not anchors:
        return scored, 0
    top = min(anchors, key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    # The top result's own score, with its route term: the claims must lead every result, and a score that
    # dropped the term (as context records inherit it) can fall below a result the route did not reach.
    inherited, provenance = top.composite_score, top.score_provenance
    relevance = rank_fusion_relevance(top)
    by_id: dict[str, RetrievalCandidate] = {str(candidate.node.id): candidate for candidate in scored}
    placed = 0
    total = len(beliefs)
    for place, belief in enumerate(beliefs):
        node = nodes.get(belief.node_id)
        current = by_id.get(belief.node_id)
        if node is None and current is None:
            continue
        coefficient = 1.0 + (total - place) * BELIEF_SCORE_STEP
        adjusted = None
        score = inherited * coefficient
        if provenance is not None:
            adjusted = provenance.model_copy(update={
                "adjustments": provenance.adjustments + (ScoreAdjustment(
                    kind="belief", coefficient=coefficient, source_node_id=top.node.id,
                ),),
            })
            score = adjusted.replay_score()
        if current is None:
            assert node is not None
            by_id[belief.node_id] = RetrievalCandidate(
                node=node, paths=[BELIEF_PATH], path_count=1, composite_score=score,
                score_provenance=adjusted, context_relevance=relevance, belief_state=belief.state,
            )
        else:
            by_id[belief.node_id] = current.model_copy(update={
                "paths": current.paths if BELIEF_PATH in current.paths else [*current.paths, BELIEF_PATH],
                "composite_score": score,
                "reranker_score": None,
                "score_trace": None,
                "score_provenance": adjusted,
                "context_relevance": max(current.context_relevance, relevance),
                "belief_state": belief.state,
            })
        placed += 1
    if not placed:
        return scored, 0
    results = list(by_id.values())
    results.sort(key=lambda candidate: (-candidate.composite_score, str(candidate.node.id)))
    return results, placed
