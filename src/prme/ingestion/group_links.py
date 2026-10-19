"""Link a claim a user states about "we" to the people it speaks for (enable_owner_group_links).

A user turn that says "We chose Tokyo, Kyoto, and Osaka" is the memory's owner
speaking for a group they belong to. The claim is attached to the group the
turn names (a "We" entity local to the message), because a "we" claim is not
always true of one person alone, so a graph walk from the owner's entity never
reached it (#259, #260). This adds links, each a MENTIONS edge marked
``metadata.basis=group_member`` from the claim:

- to the owner, the entity that carries the name a user turn gave the owner
  (enable_owner_name_carry), because "we" always includes the speaker;
- to each person the session names with the owner, on evidence only: when the
  owner's turns of the session, up to and including the claim's turn, name
  exactly one group with the owner ("My partner Jordan and I", "Jordan and I",
  "me and Jordan") and each member is a person entity the extraction already
  created (prism-monorepo issue 275). Such a link also records the turn that
  named the group (``named_in``).

A session that names two or more different groups, or a member without a name
("my wife and I"), or a name no person entity has, links the owner only. Only
the turns at or before the claim's turn count, in the order they were
ingested, so a turn extracted alone, a batched request and a rebuild that
replays the same turns give the same links. The claim keeps its subject, its
group link and its literal "we", and the owner's own "I" claims are unchanged.

Like the text links of ``mention_links``, this runs after a derivation commits,
outside the derivation plan, and creates only the links that are missing. A
failure is logged and never fails or changes the extraction. A turn that is not
the owner's, a turn that names nobody before it, and an owner no user turn named
get no link, so no name is invented.
"""

from __future__ import annotations

import re
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
    from prme.models.events import Event
    from prme.models.nodes import MemoryNode
    from prme.storage.event_store import EventStore
    from prme.storage.graph_store import GraphStore

logger = structlog.get_logger(__name__)

# Marks a MENTIONS edge that records only that a claim's we includes its target.
# Claim merging ignores it, as it ignores a text link.
GROUP_LINK_METADATA = {"basis": "group_member"}

FIRST_PERSON_PLURAL = frozenset({"we", "us", "our", "ours", "ourselves"})
_CLAIM_TYPES = frozenset({NodeType.FACT, NodeType.DECISION, NodeType.PREFERENCE, NodeType.TASK})

# The owner's turns of a session read for the groups they name: the newest ones
# at or before the claim's turn. Far more than a chat session holds.
SESSION_TURNS = 500
# Entities read while looking up the names a group gives.
_LOOKUP_LIMIT = 1000


def is_group_link(edge: MemoryEdge) -> bool:
    """Whether the edge only records that its source claim's we includes its target."""
    return (edge.metadata or {}).get("basis") == GROUP_LINK_METADATA["basis"]


def group_fields(node: MemoryNode) -> frozenset[str]:
    """The claim's fields, subject or object, that are a literal we, us or our."""
    metadata = node.metadata or {}
    return frozenset(
        field for field in ("subject", "object")
        if isinstance(metadata.get(field), str)
        and metadata[field].strip().casefold() in FIRST_PERSON_PLURAL
        and unresolved_personal_reference(metadata[field], metadata.get(f"{field}_entity_type"))
    )


# --- The groups a turn names --------------------------------------------------------------------
#
# A conservative English reading, not coreference resolution. A group is the
# speaker plus a list of members joined to "I" or "me": "My partner Jordan and
# I", "Jordan, Sam and I", "Training update: Mel and I", "just me and Biscuit".
# A member is a name as written with capitals ("Jordan", "the Smiths"), or a
# relation without one ("my wife", "my best friend"). "and I" also joins two
# clauses ("I got the offer from Brightpath and I accepted it"), so the members
# before it must start a clause: the start of the text, a punctuation mark or a
# word such as "so" or "when". A list that runs into other words ends at the
# last member that does ("I called my mom, Jordan and I ate" names Jordan).

_TOKEN_RE = re.compile(r"[^\W\d_][\w'’-]*|\d[\w'’.,-]*|[.,!?;:()\[\]{}\"“”—–]")
_DETERMINERS = frozenset({"my", "our", "the", "a", "an"})
_PRONOUNS = frozenset({
    "i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "ourselves", "you", "your", "yours",
    "he", "him", "his", "she", "her", "hers", "they", "them", "their", "theirs", "it", "its",
})
# Words that can open the clause a group starts, so they end the members before "and I".
_CLAUSE_WORDS = frozenset({
    "and", "or", "but", "so", "then", "because", "when", "while", "since", "after", "before", "until",
    "once", "if", "that", "where", "now", "today", "tonight", "yesterday", "tomorrow", "also", "both", "plus",
})
_BOUNDARY_MARKS = frozenset({".", "!", "?", ":", ";", "(", "[", "{", '"', "“", "”", "—", "–", ","})
# A member without a name is one of these after "my", "our", "the" or "a" ("my wife", "the kids").
_RELATIONS = frozenset({
    "partner", "partners", "wife", "husband", "spouse", "girlfriend", "boyfriend", "fiance", "fiancé",
    "fiancee", "fiancée", "friend", "friends", "buddy", "buddies", "roommate", "roommates", "flatmate",
    "flatmates", "housemate", "housemates", "colleague", "colleagues", "coworker", "coworkers", "co-worker",
    "co-workers", "teammate", "teammates", "team", "boss", "manager", "family", "parents", "mom", "mum",
    "mother", "dad", "father", "son", "sons", "daughter", "daughters", "kid", "kids", "child", "children",
    "baby", "brother", "brothers", "sister", "sisters", "sibling", "siblings", "cousin", "cousins", "aunt",
    "uncle", "grandma", "grandpa", "grandmother", "grandfather", "grandparents", "niece", "nephew",
    "neighbor", "neighbors", "neighbour", "neighbours", "classmate", "classmates", "coach", "mentor",
    "client", "clients", "cofounder", "co-founder", "dog", "cat", "crew", "band",
})
_MAX_NAME_WORDS = 4
_MAX_RELATION_WORDS = 3


def _fold(token: str) -> str:
    return token.casefold()


def _is_word(token: str) -> bool:
    return token[0].isalpha()


def _is_name(token: str) -> bool:
    """A capitalized word that can be part of a name: not I (or I'm), a pronoun, an article or a clause word."""
    return (_is_word(token) and token[0].isupper() and re.split(r"['’]", token)[0] != "I"
            and _fold(token) not in _PRONOUNS | _DETERMINERS | _CLAUSE_WORDS)


def _is_lower(token: str) -> bool:
    return _is_word(token) and not token[0].isupper()


def _is_boundary(tokens: list[str], index: int) -> bool:
    """Whether a clause can start right after ``tokens[index]`` (-1 is the start of the text)."""
    return index < 0 or tokens[index] in _BOUNDARY_MARKS or _fold(tokens[index]) in _CLAUSE_WORDS


def _item_before(tokens: list[str], end: int) -> tuple[str | None, int] | None:
    """The member that ends at ``tokens[end]``, read right to left, and the index where it starts.

    The member's name, or None for a relation without one ("my wife").
    """
    index = end
    names: list[str] = []
    while index >= 0 and len(names) < _MAX_NAME_WORDS and _is_name(tokens[index]):
        names.insert(0, tokens[index])
        index -= 1
    names_start = index + 1
    relation: list[str] = []
    while (index >= 0 and len(relation) < _MAX_RELATION_WORDS and _is_lower(tokens[index])
           and _fold(tokens[index]) not in _DETERMINERS):
        relation.insert(0, tokens[index])
        index -= 1
    determiner = index >= 0 and _fold(tokens[index]) in _DETERMINERS
    name = " ".join(names) or None
    if relation and determiner and _RELATIONS & {_fold(word) for word in relation}:
        return name, index
    if names and not relation and determiner:
        return name, index
    if names:
        return name, names_start
    return None


def _members_before(tokens: list[str], conjunction: int, *, clause_start: bool) -> tuple[str | None, ...] | None:
    """The members listed before the "and" at ``conjunction``, the longest list that can start there."""
    end = conjunction - 1
    if end >= 0 and tokens[end] == ",":
        end -= 1  # "Jordan, Sam, and I"
    members: list[str | None] = []
    found = None
    while end >= 0:
        item = _item_before(tokens, end)
        if item is None:
            break
        name, start = item
        members.insert(0, name)
        before = start - 1
        if not clause_start or _is_boundary(tokens, before):
            found = tuple(members)
        if before >= 0 and (tokens[before] == "," or _fold(tokens[before]) == "and"):
            end = before - 1
            if end >= 0 and tokens[end] == "," and _fold(tokens[before]) == "and":
                end -= 1
            continue
        break
    return found


def _item_after(tokens: list[str], start: int) -> tuple[str | None, int] | None:
    """The member that starts at ``tokens[start]``, and the index where it ends."""
    index = start
    determiner = index < len(tokens) and _fold(tokens[index]) in _DETERMINERS
    if determiner:
        index += 1
    relation: list[str] = []
    while (index < len(tokens) and len(relation) < _MAX_RELATION_WORDS and _is_lower(tokens[index])
           and _fold(tokens[index]) not in _DETERMINERS):
        relation.append(tokens[index])
        index += 1
    if relation and not (determiner and _RELATIONS & {_fold(word) for word in relation}):
        return None
    names: list[str] = []
    while index < len(tokens) and len(names) < _MAX_NAME_WORDS and _is_name(tokens[index]):
        names.append(tokens[index])
        index += 1
    if not names and not relation:
        return None
    return " ".join(names) or None, index - 1


def _members_after(tokens: list[str], conjunction: int) -> tuple[str | None, ...] | None:
    """The members listed after the "and" at ``conjunction`` ("me and Jordan", "me and my wife")."""
    members: list[str | None] = []
    start = conjunction + 1
    while start < len(tokens):
        item = _item_after(tokens, start)
        if item is None:
            break
        name, end = item
        members.append(name)
        following = end + 1
        if following + 1 < len(tokens) and (tokens[following] == "," or _fold(tokens[following]) == "and"):
            start = following + 1
            if _fold(tokens[start]) == "and" and tokens[following] == ",":
                start += 1
            continue
        break
    return tuple(members) or None


def named_groups(text: str) -> list[tuple[str | None, ...]]:
    """Each group the text names with its speaker, as its members in order of mention.

    A member is the name the text gives it as written, or None for a relation
    without a name ("my wife and I" gives ``(None,)``). "and I" counts only
    after members that start a clause; "and me", "me and" and "I and" count
    anywhere.
    """
    tokens = _TOKEN_RE.findall(text)
    groups: list[tuple[str | None, ...]] = []
    for index in range(len(tokens) - 1):
        word, following = tokens[index], tokens[index + 1]
        members = None
        if _fold(word) == "and" and following in {"I", "i"}:
            members = _members_before(tokens, index, clause_start=True)
        elif _fold(word) == "and" and _fold(following) in {"me", "myself"}:
            members = _members_before(tokens, index, clause_start=False)
        elif (_fold(word) in {"me", "myself"} or word in {"I", "i"}) and _fold(following) == "and":
            members = _members_after(tokens, index + 1)
        if members:
            groups.append(members)
    return groups


# --- Who the owner's group is in a session ------------------------------------------------------


def _speaker_key(event: Event) -> str | None:
    speaker = metadata_speaker(event.metadata)
    return speaker.casefold() if speaker else None


def _suffixes(name: str) -> list[str]:
    """The name and each shorter run of its last words, longest first ("Today Jordan" gives "Jordan" too)."""
    words = name.split()
    return [" ".join(words[start:]) for start in range(len(words))]


async def _people(graph_store: GraphStore, event: Event, names: set[str]) -> dict[str, list[MemoryNode]]:
    """The owner's person entities in the event's scope whose name is one of ``names``, by folded name."""
    people: dict[str, list[MemoryNode]] = {}
    if not names:
        return people
    wanted = {name.casefold() for name in names}
    for node in await graph_store.query_nodes(
        user_id=event.user_id, node_type=NodeType.ENTITY, scopes=[event.scope],
        content_contains_any=sorted(names), limit=_LOOKUP_LIMIT,
    ):
        metadata = node.metadata or {}
        folded = node.content.strip().casefold()
        if (folded in wanted
                and str(metadata.get("entity_type", "")).strip().casefold() == "person"
                and metadata.get("identity_status") is None):
            people.setdefault(folded, []).append(node)
    return people


def _person(name: str, people: dict[str, list[MemoryNode]]) -> MemoryNode | None:
    """The one person entity a name gives, by its longest run of last words that is a name, or None."""
    for suffix in _suffixes(name):
        found = people.get(suffix.casefold(), [])
        if len(found) == 1:
            return found[0]
        if found:
            return None  # two people share the name
    return None


async def session_group_members(
    event: Event, owner: MemoryNode, *, graph_store: GraphStore, event_store: EventStore,
) -> tuple[list[MemoryNode], str | None]:
    """The people the owner's group is in the event's session, and the turn that first named them.

    Reads the session's user turns with the event's speaker (none, or the
    caller's ``speaker``) at or before the event, in the order they were
    ingested, the newest ``SESSION_TURNS`` events at most. Returns
    the members when those turns name exactly one group with the owner and
    every member resolves to one person entity; otherwise no member, so the
    caller links the owner only.
    """
    if not event.session_id:
        return [], None
    speaker = _speaker_key(event)
    recent = await event_store.get_by_user(
        event.user_id, session_id=event.session_id, scopes=[event.scope], until=event.timestamp,
        limit=SESSION_TURNS,
    )
    named = [
        (str(turn.id), group)
        for turn in reversed(recent)
        if turn.role.casefold() in OWNER_ROLES and _speaker_key(turn) == speaker
        for group in named_groups(turn.content)
    ]
    if not named:
        return [], None
    people = await _people(graph_store, event, {
        suffix for _, group in named for name in group if name for suffix in _suffixes(name)
    })
    nodes: dict[str, MemoryNode] = {}
    groups: list[tuple[str, frozenset[str]]] = []
    for turn_id, group in named:
        members = set()
        for name in group:
            person = _person(name, people) if name else None
            if person is None:
                return [], None  # a member without a name, or one no single person entity has
            if person.id != owner.id:
                nodes[str(person.id)] = person
                members.add(str(person.id))
        if members:
            groups.append((turn_id, frozenset(members)))
    if len({members for _, members in groups}) != 1:
        return [], None
    first, members = groups[0]
    return [nodes[node_id] for node_id in sorted(members)], first


async def link_group_claims(
    event_id: str, sources: Sequence[MemoryNode], *, user_id: str, graph_store: GraphStore,
    event_store: EventStore, create_edge: Callable[[MemoryEdge], Awaitable[object]],
) -> int:
    """MENTIONS-link the claims of an owner's turn that speak for a group to the people in it.

    ``sources`` are the nodes derived from the event. Only claims whose subject
    or object is a literal we, us or our are linked, and only when the event is
    a user turn that is not the owner's first-person reference. The owner is
    the entity a user turn marked with the name it gave the owner, under the
    turn's own ``speaker`` when the caller named one; the other members are the
    people :func:`session_group_members` finds. Returns the number of links
    created.
    """
    created = 0
    try:
        claims = [
            node for node in sources
            if node.node_type in _CLAIM_TYPES and node.lifecycle_state in ACTIVE_LIFECYCLE_STATES
            and group_fields(node)
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
        try:
            members, named_in = await session_group_members(
                event, owner, graph_store=graph_store, event_store=event_store,
            )
        except Exception as exc:
            # The owner is still linked; only the members wait for a later repair.
            logger.warning("group_links.members_failed", event_id=event_id, error_type=type(exc).__name__)
            members, named_in = [], None
        targets = [(owner, dict(GROUP_LINK_METADATA))]
        targets += [(member, {**GROUP_LINK_METADATA, "named_in": named_in}) for member in members]
        linked = {
            (edge.source_id, edge.target_id)
            for edge in await graph_store.get_edges(node_ids=[str(node.id) for node in claims])
        }
        for claim in claims:
            for target, metadata in targets:
                if (claim.id, target.id) in linked or (target.id, claim.id) in linked:
                    continue
                edge = MemoryEdge(
                    id=uuid5(UUID(event_id), f"prme:group-link:{claim.id}:{target.id}"),
                    source_id=claim.id, target_id=target.id, edge_type=EdgeType.MENTIONS,
                    user_id=user_id, provenance_event_id=UUID(event_id), metadata=metadata,
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
