"""Relation types for questions and claims (``enable_relation_routing``, dwamianm/prism#399).

Stored claims carry the extractor's open predicate (``has_husband``,
``is_husband_of``, ``has_dependents``), and a question reaches the graph only
through the names it holds. This module is the bridge between the two until
extraction writes typed claims:

- :data:`RELATIONS`: the closed vocabulary of the graph oracle research
  (``benchmarks/graph_oracle/DESIGN.md``), sixteen relations plus ``has``, each
  with its definition.
- :func:`derive_cues`: cue stems derived mechanically from those definitions,
  with no question in view.
- :func:`normalize_predicate`: maps an open predicate to a relation by its
  words and a small synonym table. A predicate it cannot map stays unmapped and
  is counted by :func:`coverage`.
- :data:`CORE_CONCEPTS` and :func:`concept_map`: concept words ("dependents",
  "home") that stand for relations, with a per-call extension.
- :func:`route_question`: the relation steps a question asks for, or None, which
  leaves the question unrouted.

Everything here is deterministic and calls no model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re

# The version of the routing rule recorded in receipts. Change it with any
# change to the vocabulary, the cues, the normalizer, the concepts, the router
# or the walk that can change a route or its candidates.
ROUTING_VERSION = 2

# The closed vocabulary (benchmarks/graph_oracle/DESIGN.md, "The relation
# vocabulary"), with each relation's subject and object kinds and definition.
RELATIONS: dict[str, tuple[str, str]] = {
    "located_in": ("place or org or event, place", "spatial containment: Tokyo in Japan, a firm in Brooklyn"),
    "part_of": ("thing or org, thing or org",
                "non spatial component: a team of a company, a dashboard of a project"),
    "member_of": ("person, org", "belongs to a team or group"),
    "works_at": ("person, org", "employed by; `role` holds the title"),
    "lives_in": ("person, place", "resides in"),
    "partner_of": ("person, person", "spouse, partner or fiance (symmetric)"),
    "kin_of": ("person, person", "family; `role` says which (sister, mother, niece)"),
    "friend_of": ("person, person", "friend; `role` may say running partner"),
    "manager_of": ("person, person", "directs or guides; `role` says manager or mentor"),
    "owns": ("person, thing", "owns, keeps, adopted"),
    "visits": ("person, place", "travels to; `status` holds the intent"),
    "involved_in": ("person, event or thing",
                    "takes part; `role` says leads, presents, signed up, reviewer, on call"),
    "prefers": ("person, thing", "likes or wants; negative polarity is dislike or avoid"),
    "allergic_to": ("person, thing", "an allergy"),
    "uses": ("person or org or thing, thing", "uses, runs on, chose; `role` may say learning"),
    "decided": ("person, thing", "chose or rejected; polarity carries which"),
    "has": ("any, literal", "an attribute value with `attr` as its key"),
}

# Relations a route never asks for: ``has`` holds literal values, not a link
# between two entities.
UNROUTED_RELATIONS = frozenset({"has"})

_STOPWORDS = frozenset("""a an and are at be by can did do does for from has have how i in is it its many me much my of
on or our s she he the their them they this to was were what when where which who whom why will with you your""".split())
# The schema's own words in a relation's definition: they describe the fields, not the relation.
_SCHEMA_WORDS = frozenset("role status polarity attr says say holds hold may key symmetric intent negative positive "
                          "value non".split())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold().replace("’", "'"))


def stem(word: str) -> str:
    """A crude stem, enough to match "rejected" with "rejection" and "rows" with "row"."""
    word = word.removesuffix("'s")
    for suffix in ("ation", "ion", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _verb_forms(base: str) -> set[str]:
    """A stem and the forms a verb takes: restore a dropped e (liv, live), add s, ed and ing, drop a final e before ing."""
    forms = {base, base + "e", base + "s", base + "ed", base + "ing"}
    if base.endswith("e"):
        forms.add(base[:-1] + "ing")
    return forms


def derive_cues(relations: Mapping[str, tuple[str, str]] | None = None) -> dict[str, frozenset[str]]:
    """Cue stems for each relation, derived from its name and definition, with no question in view.

    The words of the relation's name and definition, less the stop words, the
    schema's own words (role, status, polarity), words in backticks,
    capitalized words (example names: "Tokyo" says nothing about a relation)
    and digits; each word contributes its stem and the stems of its verb forms
    (s, ed, ing), and a doubled final consonant also stands for the plain stem
    (runn from running). The same rule as ``benchmarks.graph_oracle.gold``.
    """
    cues: dict[str, frozenset[str]] = {}
    for name, (_kinds, definition) in (relations if relations is not None else RELATIONS).items():
        definition = re.sub(r"`[^`]*`", " ", definition)
        words = [word for word in re.findall(r"[A-Za-z]+", definition) if word[0].islower()]
        words += name.split("_")
        stems: set[str] = set()
        for word in words:
            word = word.lower()
            if word in _STOPWORDS or word in _SCHEMA_WORDS or len(word) < 3:
                continue
            base = stem(word)
            stems |= {stem(form) for form in _verb_forms(base)} | {base}
            stems |= {item[:-1] for item in set(stems) if len(item) > 3 and item[-1] == item[-2]
                      and item[-1] not in "aeiou"}
        cues[name] = frozenset(stems)
    return cues


DERIVED_CUES: dict[str, frozenset[str]] = derive_cues()

# --- Roles: the people words of the vocabulary's person relations ---------------------------------------

# Partner words carry no role: a question about a husband asks for the partner.
_PARTNER_WORDS = frozenset("""husband wife spouse partner fiance fiancee boyfriend girlfriend""".split())
# Family words, each its own role; a group word stands for its members.
_KIN_WORDS = frozenset("""child son daughter kid stepchild stepson stepdaughter mother mom father dad parent stepmother
stepfather sister brother sibling niece nephew aunt uncle cousin grandmother grandfather grandparent grandchild
grandson granddaughter grandma grandpa""".split())
ROLE_GROUPS: dict[str, frozenset[str]] = {
    "child": frozenset("child son daughter kid stepchild stepson stepdaughter".split()),
    "parent": frozenset("parent mother mom father dad stepmother stepfather".split()),
    "sibling": frozenset("sibling sister brother".split()),
    "grandparent": frozenset("grandparent grandmother grandfather grandma grandpa".split()),
    "grandchild": frozenset("grandchild grandson granddaughter".split()),
}
_MANAGER_WORDS = frozenset("""manager boss mentor supervisor""".split())
_FRIEND_WORDS = frozenset("""friend""".split())
_IRREGULAR_PLURALS = {"children": "child", "wives": "wife", "grandchildren": "grandchild", "stepchildren": "stepchild",
                      "people": "person"}


def singular(word: str) -> str:
    """A word's singular form: the irregular plurals of the role words, then ies to y, then a final s."""
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def role_relation(word: str) -> tuple[str, str | None] | None:
    """The relation and role a people word names ("husband" is partner_of, "sister" kin_of with role sister)."""
    word = singular(word)
    if word in _PARTNER_WORDS:
        return "partner_of", None
    if word in _KIN_WORDS:
        return "kin_of", word
    if word in _MANAGER_WORDS:
        return "manager_of", word
    if word in _FRIEND_WORDS:
        return "friend_of", None
    return None


# --- The predicate normalizer ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimRelation:
    """What a stored claim's predicate means in the vocabulary.

    ``role`` is the people word the predicate names (sister, child), and
    ``role_end`` the end of the claim that holds it: ``"subject"`` for
    ``is_child_of`` (the subject is the child) and ``"object"`` for
    ``has_child``. ``negative`` is true for a predicate that states the
    opposite of its relation ("dislikes" is prefers with negative polarity).
    """

    relation: str
    role: str | None = None
    role_end: str | None = None
    negative: bool = False


# Whole predicates, after their words are joined by "_" with the function words dropped.
_PREDICATE_SYNONYMS: dict[str, ClaimRelation] = {
    "married_to": ClaimRelation("partner_of"),
    "married": ClaimRelation("partner_of"),
    "engaged_to": ClaimRelation("partner_of"),
    "engaged": ClaimRelation("partner_of"),
    "partner_of": ClaimRelation("partner_of"),
    "kin_of": ClaimRelation("kin_of"),
    "related_to": ClaimRelation("kin_of"),
    "lives_in": ClaimRelation("lives_in"),
    "lives_at": ClaimRelation("lives_in"),
    "resides_in": ClaimRelation("lives_in"),
    "moved_to": ClaimRelation("lives_in"),
    "works_at": ClaimRelation("works_at"),
    "works_for": ClaimRelation("works_at"),
    "employed_by": ClaimRelation("works_at"),
    "allergic_to": ClaimRelation("allergic_to"),
    "member_of": ClaimRelation("member_of"),
    "located_in": ClaimRelation("located_in"),
    "part_of": ClaimRelation("part_of"),
}
# Single words of a predicate, the first one that maps decides.
_PREDICATE_WORDS: dict[str, ClaimRelation] = {
    **{word: ClaimRelation("lives_in") for word in "live lives lived living reside resides resided residing".split()},
    **{word: ClaimRelation("works_at") for word in "work works worked working employed employer employee job".split()},
    **{word: ClaimRelation("owns") for word in "owns owned adopt adopts adopted pet dog cat".split()},
    **{word: ClaimRelation("visits") for word in
       "visit visits visited visiting travel travels traveled travelled traveling travelling trip".split()},
    **{word: ClaimRelation("prefers") for word in
       "like likes liked love loves loved enjoy enjoys enjoyed prefer prefers preferred favorite want wants".split()},
    **{word: ClaimRelation("prefers", negative=True) for word in
       "dislike dislikes disliked hate hates hated avoid avoids avoided".split()},
    **{word: ClaimRelation("allergic_to") for word in "allergic allergy allergies".split()},
    **{word: ClaimRelation("uses") for word in "use uses used using learn learns learned learning".split()},
    **{word: ClaimRelation("decided") for word in "decide decides decided chose choose chooses picked".split()},
    **{word: ClaimRelation("decided", negative=True) for word in "reject rejects rejected".split()},
    **{word: ClaimRelation("member_of") for word in "member belongs joined".split()},
    **{word: ClaimRelation("manager_of", "manager", "subject") for word in "manages managed supervises".split()},
    **{word: ClaimRelation("manager_of", "mentor", "subject") for word in "mentors mentored".split()},
    **{word: ClaimRelation("involved_in") for word in "leads attends participates involved".split()},
}
# Function words a predicate is built from ("is_child_of", "has_husband"); "own" is the adjective of "their own".
_FUNCTION_WORDS = frozenset("is are was were be been am has have had a an the of to in at on for with by my his her their"
                            " our your its own".split())


def _predicate_words(predicate: str) -> list[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", predicate)
    return re.findall(r"[a-z]+", spaced.casefold())


def normalize_predicate(predicate: str | None) -> ClaimRelation | None:
    """The relation an open predicate states, or None when its words map to none.

    In order: a relation name of the vocabulary; a whole predicate in the
    synonym table (``is_married_to``, ``resides_in``), function words dropped;
    a people word (``has_husband``, ``is_child_of``, ``kin_of`` with a role),
    whose role is on the subject when the predicate ends in "of"
    (``is_sister_of``) and on the object otherwise (``has_sister``); a verb of
    the word table (``lives``, ``adopted``, ``dislikes``); then a word whose
    stem is a derived cue of exactly one relation. ``has`` is never returned:
    a literal attribute links no two entities.
    """
    if not predicate or not predicate.strip():
        return None
    words = _predicate_words(predicate)
    if not words:
        return None
    joined = "_".join(words)
    if joined in RELATIONS and joined not in UNROUTED_RELATIONS:
        return ClaimRelation(joined)
    content = [word for word in words if word not in _FUNCTION_WORDS]
    key = "_".join(content)
    if key in _PREDICATE_SYNONYMS:
        return _PREDICATE_SYNONYMS[key]
    if "_".join([*content, words[-1]]) in _PREDICATE_SYNONYMS and words[-1] in _FUNCTION_WORDS:
        return _PREDICATE_SYNONYMS["_".join([*content, words[-1]])]
    role_end = "subject" if words[-1] == "of" else "object"
    for word in content:
        found = role_relation(word)
        if found is not None:
            relation, role = found
            return ClaimRelation(relation, role, role_end if role is not None else None)
    for word in content:
        if word in _PREDICATE_WORDS:
            return _PREDICATE_WORDS[word]
    for word in content:
        matches = [name for name, cues in DERIVED_CUES.items()
                   if name not in UNROUTED_RELATIONS and stem(word) in cues]
        if len(matches) == 1:
            return ClaimRelation(matches[0])
    return None


def coverage(predicates: Iterable[str | None]) -> dict:
    """How many claims the normalizer maps, by relation, and the unmapped predicates by count.

    ``predicates`` holds one predicate per claim. The result has ``claims``,
    ``mapped``, ``by_relation`` and ``unmapped`` (predicate to count, most
    frequent first, then by name).
    """
    by_relation: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    claims = 0
    for predicate in predicates:
        claims += 1
        found = normalize_predicate(predicate)
        if found is None:
            unmapped[predicate or ""] += 1
        else:
            by_relation[found.relation] += 1
    return {"claims": claims, "mapped": sum(by_relation.values()),
            "by_relation": dict(sorted(by_relation.items())),
            "unmapped": dict(sorted(unmapped.items(), key=lambda item: (-item[1], item[0])))}


# --- Routes ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class RelationStep:
    """One relation a route step accepts.

    ``roles`` holds the people words the far end of the claim must hold (a
    child for "children"); empty accepts any. ``polarity`` is the polarity the
    claim must state: "negative" for a question about dislikes.
    """

    relation: str
    roles: tuple[str, ...] = ()
    polarity: str = "positive"

    def as_dict(self) -> dict:
        return {"relation": self.relation, **({"roles": list(self.roles)} if self.roles else {}),
                **({"polarity": self.polarity} if self.polarity != "positive" else {})}


# A route is one or two steps; each step holds the relations it accepts.
Route = tuple[tuple[RelationStep, ...], ...]


def _step(relation: str, role: str | None = None, polarity: str = "positive") -> RelationStep:
    if relation not in RELATIONS or relation in UNROUTED_RELATIONS:
        raise ValueError(f"Unknown relation {relation!r}")
    roles = tuple(sorted(ROLE_GROUPS.get(role, {role}))) if role else ()
    return RelationStep(relation, roles, polarity)


# The built-in concepts: a concept word stands for the relations of one step.
CORE_CONCEPTS: dict[str, tuple[str, ...]] = {
    "dependents": ("partner_of", "kin_of:child"),
    "family": ("partner_of", "kin_of"),
    "spouse": ("partner_of",),
    "children": ("kin_of:child",),
    "home": ("lives_in",),
    "job": ("works_at",),
    "allergies": ("allergic_to",),
    "pet": ("owns",),
}


def _concept_key(word: str) -> str:
    return singular(word.strip().casefold())


def parse_concept(relations: Sequence[str]) -> tuple[RelationStep, ...]:
    """A concept's relations, each ``"relation"`` or ``"relation:role"`` ("kin_of:child")."""
    if isinstance(relations, str) or not relations:
        raise ValueError("A concept needs a nonempty list of relations")
    steps = []
    for item in relations:
        if not isinstance(item, str):
            raise ValueError(f"A concept relation must be a string, not {item!r}")
        relation, _, role = item.strip().partition(":")
        steps.append(_step(relation.strip(), role.strip().casefold() or None))
    return tuple(sorted(set(steps)))


def concept_map(concepts: Mapping[str, Sequence[str]] | None = None) -> dict[str, tuple[RelationStep, ...]]:
    """The built-in concepts with a per-call extension, keyed by the concept word's singular.

    An extension word that is also built in replaces the built-in relations.
    Raises ValueError for an empty word, an unknown relation, or two extension
    words with one singular ("home" and "Home", "child" and "children").
    """
    merged = dict(CORE_CONCEPTS)
    seen: set[str] = set()
    for word, relations in (concepts or {}).items():
        if not isinstance(word, str) or not word.strip() or len(word.split()) != 1:
            raise ValueError(f"A concept is one word, not {word!r}")
        if _concept_key(word) in seen:
            raise ValueError(f"Two concepts stand for the word {_concept_key(word)!r}")
        seen.add(_concept_key(word))
        merged = {key: value for key, value in merged.items() if _concept_key(key) != _concept_key(word)}
        merged[word] = relations
    return {_concept_key(word): parse_concept(relations) for word, relations in merged.items()}


def normalize_concepts(concepts: Mapping[str, Sequence[str]] | None) -> dict[str, list[str]] | None:
    """A per-call concept extension validated and in a canonical form, for receipts; None when empty."""
    if not concepts:
        return None
    if not isinstance(concepts, Mapping):
        raise ValueError("concepts must map a word to a list of relations")
    concept_map(concepts)
    return {word.strip().casefold(): sorted(dict.fromkeys(item.strip() for item in relations))
            for word, relations in sorted(concepts.items())}


# Words of a question that say it asks about dislikes, not likes.
_NEGATIVE_WORDS = frozenset("dislike dislikes disliked avoid avoids avoided hate hates hated".split())
_POSSESSIVE_WORDS = frozenset({"my", "our", "your", "his", "her", "their"})
# A question about when asks for a time, which the text ranking answers better
# than a relation (the graph oracle's blind sets lost temporal questions to the
# walk), so it is never routed.
_TEMPORAL_RE = re.compile(r"^\W*when\b|\bhow\s+long\b|\bwhat\s+(?:time|date|day|year|month)\b", re.IGNORECASE)
MAX_ROUTE_STEPS = 2


def route_question(question: str, concepts: Mapping[str, Sequence[str]] | None = None) -> Route | None:
    """The relation steps a question asks for, or None when no word names a relation.

    A people word ("husband", "children") routes to its relation and role, a
    concept word to the concept's relations, and any other word whose stem is
    a derived cue (``DERIVED_CUES``) to the cued relations, with negative
    polarity for prefers on "dislike" or "avoid". When a people or concept word
    belongs to someone ("Dana's sister", "my sister") and the question asks for
    more ("Where does Dana's sister live?", "Who is my sister's husband?"), the
    route is two steps: the possessed words first, then the rest. Otherwise it is one step that accepts
    every relation found. A question about when ("When did Dana start the new
    job?", "how long", "what year") is never routed.
    """
    words = _words(question)
    if not words or _TEMPORAL_RE.search(question):
        return None
    known = concept_map(concepts)
    possessed: set[RelationStep] = set()
    rest: set[RelationStep] = set()
    negative = any(word in _NEGATIVE_WORDS for word in words)
    for index, raw in enumerate(words):
        word = raw.removesuffix("'s")
        before = words[index - 1] if index > 0 else ""
        # Belongs to someone: "my sister", "Dana's sister". A people word after another one's possessive
        # ("my sister's husband") is the next step of a chain instead.
        owned = before in _POSSESSIVE_WORDS or (
            before.endswith("'s") and role_relation(before.removesuffix("'s")) is None
            and singular(before.removesuffix("'s")) not in known)
        found: set[RelationStep] = set()
        people = role_relation(word)
        if people is not None:
            found.add(_step(*people))
        elif singular(word) in known:
            found.update(known[singular(word)])
        elif word not in _STOPWORDS:
            for name, cues in DERIVED_CUES.items():
                if name not in UNROUTED_RELATIONS and stem(word) in cues:
                    found.add(_step(name, polarity="negative" if name == "prefers" and negative else "positive"))
            owned = False
        (possessed if owned else rest).update(found)
    if not possessed and not rest:
        return None
    if possessed and rest - possessed:
        return (tuple(sorted(possessed)), tuple(sorted(rest - possessed)))
    return (tuple(sorted(possessed | rest)),)


def route_as_list(route: Route) -> list[list[dict]]:
    """A route as JSON, for receipts."""
    return [[step.as_dict() for step in steps] for steps in route]
