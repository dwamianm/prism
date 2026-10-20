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
from functools import lru_cache
from itertools import islice
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

import structlog

from prme.epistemic.inference import OWNER_ROLES
from prme.ingestion.mention_links import is_text_link
from prme.ingestion.owner_name import carried_owner_entity
from prme.models.edges import MemoryEdge
from prme.models.entity_identity import PERSONAL_REFERENCES, unresolved_personal_reference
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
# Claim merging ignores it, as it ignores a text link. A link to a named member
# also carries ``named_in``; the owner's link does not.
GROUP_LINK_METADATA = {"basis": "group_member"}
NAMED_IN_KEY = "named_in"

FIRST_PERSON_PLURAL = frozenset({"we", "us", "our", "ours", "ourselves"})
_CLAIM_TYPES = frozenset({NodeType.FACT, NodeType.DECISION, NodeType.PREFERENCE, NodeType.TASK})

# The owner's turns of a session read for the groups they name, and for the
# copies a repeated we claim may merge with: the newest ones at or before the
# claim's turn. Far more than a chat session holds.
SESSION_TURNS = 500
# Entities read while looking up the names a group gives. A name is matched as
# a substring before the exact check, so a short name ("Al") can match many
# entities; this reads twice mention_links' 500, which looks up the names one
# extraction listed.
_LOOKUP_LIMIT = 1000


def is_group_link(edge: MemoryEdge) -> bool:
    """Whether the edge only records that its source claim's we includes its target."""
    return (edge.metadata or {}).get("basis") == GROUP_LINK_METADATA["basis"]


def is_owner_group_link(edge: MemoryEdge) -> bool:
    """Whether the edge is a claim's group link to the owner who said it, not to a named member."""
    return is_group_link(edge) and NAMED_IN_KEY not in (edge.metadata or {})


def is_group_reference(value: object, entity_type: str | None) -> bool:
    """Whether a claim's subject or object is a literal we, us or our."""
    return (isinstance(value, str) and value.strip().casefold() in FIRST_PERSON_PLURAL
            and unresolved_personal_reference(value, entity_type))


def group_fields(node: MemoryNode) -> frozenset[str]:
    """The claim's fields, subject or object, that are a literal we, us or our."""
    metadata = node.metadata or {}
    return frozenset(
        field for field in ("subject", "object")
        if is_group_reference(metadata.get(field), metadata.get(f"{field}_entity_type"))
    )


# --- The groups a turn names --------------------------------------------------------------------
#
# A conservative English reading, not coreference resolution. A group is the
# speaker plus a list of members joined to "I" or "me": "My partner Jordan and
# I", "Jordan, Sam and I", "Training update: Mel and I", "just me and Biscuit".
# A member is a name as written with capitals ("Jordan", "the Smiths"), or a
# relation without one ("my wife", "my best friend"; right next to "and I",
# anything after "my" or "our", as in "my bandmates and I"). "and I" also joins two
# clauses ("I got the offer from Brightpath and I accepted it"), so the members
# before it must start a clause: the start of the text, a punctuation mark or a
# word such as "so" or "when". A list that runs into other words ends at the
# last member that does ("I called my mom, Jordan and I ate" names Jordan).

_TOKEN_RE = re.compile(r"[^\W\d_][\w'’-]*|\d[\w'’.,-]*|[.,!?;:()\[\]{}\"“”—–]")
_DETERMINERS = frozenset({"my", "our", "the", "a", "an"})
# The pronouns entity identity keeps local to a message, and "its".
_PRONOUNS = PERSONAL_REFERENCES | {"its"}
# Words that can open the clause a group starts, so they end the members before "and I". Greetings and
# interjections ("Hi, Jordan and I") are never a name either.
_CLAUSE_WORDS = frozenset({
    "and", "or", "but", "so", "then", "because", "when", "while", "since", "after", "before", "until",
    "once", "if", "that", "where", "now", "today", "tonight", "yesterday", "tomorrow", "also", "both", "plus",
    "hi", "hey", "hello", "yes", "yeah", "no", "ok", "okay", "well", "oh", "thanks", "sorry", "anyway",
    "actually", "btw",
})
# Words that open a sentence with a capital letter but never name a person ("On Saturday, Jordan and I").
_FUNCTION_WORDS = frozenset({
    "on", "in", "at", "for", "with", "from", "to", "by", "of", "about", "during", "over", "into", "around",
    "last", "next", "this", "every", "each", "some", "all", "just", "only", "even", "maybe", "later", "soon",
    "here", "there", "what", "who", "how", "why", "which", "please", "not",
})
_NOT_NAMES = _PRONOUNS | _DETERMINERS | _CLAUSE_WORDS | _FUNCTION_WORDS
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
# A capitalized word that can stand before a name without being part of it ("Sunday Jordan", "Coach Taylor"):
# the only words dropped from the front of a name when the whole name is no stored person. Months that are
# also first names (April, May, June, August) are left out, so "June Lee" is never read as "Lee".
_LEADING_NON_NAMES = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
    "march", "july", "september", "october", "november", "december", "mr", "mrs", "ms", "miss", "mx", "dr",
    "doctor", "prof", "professor", "coach", "captain", "sir", "madam", "aunt", "auntie", "uncle", "cousin",
    "grandma", "grandpa", "nana",
})
_MAX_NAME_WORDS = 4
_MAX_RELATION_WORDS = 3
# Bounds on one turn's reading, so a pasted roster costs a fixed amount: the members of one group, and the
# tokens read from one turn.
_MAX_MEMBERS = 16
_MAX_TURN_TOKENS = 4096
# The longest turn whose groups are kept between link passes.
_CACHED_TEXT = 20_000


def _fold(token: str) -> str:
    return token.casefold()


def _is_word(token: str) -> bool:
    return token[0].isalpha()


def _is_name(token: str) -> bool:
    """A capitalized word that can be part of a name: not I (or I'm), a pronoun, an article or a function word."""
    return (_is_word(token) and token[0].isupper() and re.split(r"['’]", token)[0] != "I"
            and _fold(token) not in _NOT_NAMES)


def _is_lower(token: str) -> bool:
    return _is_word(token) and not token[0].isupper()


def _is_boundary(tokens: list[str], index: int) -> bool:
    """Whether a clause can start right after ``tokens[index]`` (-1 is the start of the text)."""
    return index < 0 or tokens[index] in _BOUNDARY_MARKS or _fold(tokens[index]) in _CLAUSE_WORDS


def _relation(words: list[str], determiner: str | None, *, joined: bool) -> bool:
    """Whether ``determiner`` and ``words`` name a member without a name ("my wife", "the kids").

    Right next to "and I" or "me and" (``joined``), "my" or "our" with any words is one ("my bandmates and
    I"); further along a list a known relation is needed, since "after my run, Jordan and I" lists no run.
    """
    if not words or determiner is None:
        return False
    return (joined and determiner in {"my", "our"}) or bool(_RELATIONS & {_fold(word) for word in words})


def _item_before(tokens: list[str], end: int, *, joined: bool = False) -> tuple[str | None, int] | None:
    """The member that ends at ``tokens[end]``, read right to left, and the index where it starts.

    The member's name, or None for a relation without one ("my wife"). ``joined`` says the member is
    the one right before "and I".
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
    determiner = _fold(tokens[index]) if index >= 0 and _fold(tokens[index]) in _DETERMINERS else None
    name = " ".join(names) or None
    if _relation(relation, determiner, joined=joined and not names):
        return name, index
    if names and not relation and determiner:
        return name, index
    if names:
        return name, names_start
    return None


def _members_before(tokens: list[str], conjunction: int, *, clause_start: bool) -> tuple[str | None, ...] | None:
    """The members listed before the "and" at ``conjunction``, the longest list that starts a clause.

    Without ``clause_start`` ("Jordan and me"), the one member right before "and" counts wherever it is.
    """
    end = conjunction - 1
    if end >= 0 and tokens[end] == ",":
        end -= 1  # "Jordan, Sam, and I"
    members: list[str | None] = []  # read right to left
    found = 0  # how many of them form the longest list that starts a clause
    while end >= 0:
        if len(members) == _MAX_MEMBERS:
            return None  # a list this long is a roster, not a group the speaker belongs to
        item = _item_before(tokens, end, joined=not members)
        if item is None:
            break
        name, start = item
        members.append(name)
        before = start - 1
        if _is_boundary(tokens, before) or (not clause_start and len(members) == 1):
            found = len(members)
        if before >= 0 and (tokens[before] == "," or _fold(tokens[before]) == "and"):
            end = before - 1
            if end >= 0 and tokens[end] == "," and _fold(tokens[before]) == "and":
                end -= 1
            continue
        break
    return tuple(reversed(members[:found])) or None


def _item_after(tokens: list[str], start: int, *, joined: bool = False) -> tuple[str | None, int] | None:
    """The member that starts at ``tokens[start]``, and the index where it ends.

    ``joined`` says the member is the one right after "me and".
    """
    index = start
    determiner = _fold(tokens[index]) if index < len(tokens) and _fold(tokens[index]) in _DETERMINERS else None
    if determiner:
        index += 1
    relation: list[str] = []
    while (index < len(tokens) and len(relation) < _MAX_RELATION_WORDS and _is_lower(tokens[index])
           and _fold(tokens[index]) not in _DETERMINERS):
        relation.append(tokens[index])
        index += 1
    names: list[str] = []
    while index < len(tokens) and len(names) < _MAX_NAME_WORDS and _is_name(tokens[index]):
        names.append(tokens[index])
        index += 1
    if (relation and not _relation(relation, determiner, joined=joined and not names)) or not (names or relation):
        return None
    return " ".join(names) or None, index - 1


def _members_after(tokens: list[str], conjunction: int) -> tuple[str | None, ...] | None:
    """The members listed after the "and" at ``conjunction`` ("me and Jordan", "me and my wife").

    A list ends with "and" before its last member ("me and Jordan, Sam and Dana"), so a member after a
    comma alone starts another clause ("me and Biscuit, Sam can't come") and does not count.
    """
    members: list[str | None] = []
    found = 0  # how many of them end a list
    start = conjunction + 1
    joined = True  # the first member follows "me and"
    while start < len(tokens):
        if len(members) == _MAX_MEMBERS:
            return None  # a list this long is a roster, not a group the speaker belongs to
        item = _item_after(tokens, start, joined=not members)
        if item is None:
            break
        name, end = item
        members.append(name)
        if joined:
            found = len(members)
        following = end + 1
        if following + 1 < len(tokens) and (tokens[following] == "," or _fold(tokens[following]) == "and"):
            start = following + 1
            joined = _fold(tokens[following]) == "and"
            if _fold(tokens[start]) == "and" and tokens[following] == ",":
                start += 1
                joined = True
            continue
        break
    return tuple(members[:found]) or None


def named_groups(text: str) -> list[tuple[str | None, ...]]:
    """Each group the text names with its speaker, as its members in order of mention.

    A member is the name the text gives it as written, or None for a relation
    without a name ("my wife and I" gives ``(None,)``). "and I" counts only
    after members that start a clause; "and me", "me and" and "I and" count
    anywhere. Only the first ``_MAX_TURN_TOKENS`` words and marks are read,
    and a list of more than ``_MAX_MEMBERS`` members names no group.
    """
    tokens = [match.group() for match in islice(_TOKEN_RE.finditer(text), _MAX_TURN_TOKENS)]
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


@lru_cache(maxsize=1024)
def _cached_groups(text: str) -> tuple[tuple[str | None, ...], ...]:
    return tuple(named_groups(text))


def _turn_groups(text: str) -> tuple[tuple[str | None, ...], ...]:
    """``named_groups`` of a turn. Every later "we" turn of a session reads its earlier turns again, so a
    turn of chat length is read once and kept; a longer one is read each time rather than held in memory."""
    return _cached_groups(text) if len(text) <= _CACHED_TEXT else tuple(named_groups(text))


# --- Who the owner's group is in a session ------------------------------------------------------


def _speaker_key(event: Event) -> str | None:
    speaker = metadata_speaker(event.metadata)
    return speaker.casefold() if speaker else None


def _candidate_names(name: str) -> list[str]:
    """The name, then the name without the words before it that never name anyone, longest first.

    "Sunday Jordan" and "Coach Taylor" also give "Jordan" and "Taylor". "Jordan Lee" gives only "Jordan Lee":
    its first word is part of the name, so a stored "Lee" is someone else.
    """
    words = name.split()
    candidates = [name]
    while len(words) > 1 and words[0].casefold().rstrip(".") in _LEADING_NON_NAMES:
        words = words[1:]
        candidates.append(" ".join(words))
    return candidates


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
    """The one person entity a name gives, by its longest candidate (``_candidate_names``), or None."""
    for candidate in _candidate_names(name):
        found = people.get(candidate.casefold(), [])
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
        for group in _turn_groups(turn.content)
    ]
    if not named:
        return [], None
    people = await _people(graph_store, event, {
        candidate for _, group in named for name in group if name for candidate in _candidate_names(name)
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
        if (event is None or event.user_id != user_id or event.role.casefold() not in OWNER_ROLES
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
            # The owner is still linked. Nothing links this turn's members later: only publishing the
            # same plan again runs this step.
            logger.warning("group_links.members_failed", event_id=event_id, error_type=type(exc).__name__)
            members, named_in = [], None
        # A text link (the claim's sentence names the person) does not stand in for a group link, which
        # the merge of a repeated we claim looks for; any other link between the two does.
        linked = {
            (edge.source_id, edge.target_id)
            for edge in await graph_store.get_edges(node_ids=[str(node.id) for node in claims])
            if not is_text_link(edge)
        }
        # Every claim's owner link first, so a failed member link costs no claim its owner link.
        links = [(claim, owner, dict(GROUP_LINK_METADATA)) for claim in claims]
        links += [(claim, member, {**GROUP_LINK_METADATA, NAMED_IN_KEY: named_in})
                  for member in members for claim in claims]
        for claim, target, metadata in links:
            if (claim.id, target.id) in linked or (target.id, claim.id) in linked:
                continue
            edge = MemoryEdge(
                id=uuid5(UUID(event_id), f"prme:group-link:{claim.id}:{target.id}"),
                source_id=claim.id, target_id=target.id, edge_type=EdgeType.MENTIONS,
                user_id=user_id, provenance_event_id=UUID(event_id), metadata=metadata,
            )
            try:
                await create_edge(edge)
            except Exception as exc:
                # A retry of the same publication finds the link made; the edge ID is fixed.
                try:
                    made = any(e.id == edge.id for e in await graph_store.get_edges(source_id=str(claim.id)))
                except Exception:
                    made = False
                if not made:
                    logger.warning("group_links.link_failed", event_id=event_id, error_type=type(exc).__name__)
                continue
            created += 1
    except Exception as exc:
        logger.warning("group_links.failed", event_id=event_id, error_type=type(exc).__name__)
    return created
