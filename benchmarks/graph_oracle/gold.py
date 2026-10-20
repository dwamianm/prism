"""Gold graphs: the hand-written ideal graph of a corpus, its questions and their query specs.

A gold file (``benchmarks/fixtures/graph-oracle/<name>.json``) lists the entities
a good extractor would find, the claims between them with the memories that
state them, and for each question a query spec (seeds, relation path, target)
and the memories that answer it. ``DESIGN.md`` explains the shape. This module
loads and checks a file and answers the two text questions every strategy
needs: which entities does a text name, and which words of a question are cues.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re

from benchmarks.diagnostics.chat_probe import chat_turns, load_conversation
from benchmarks.fast_cycle import store as fast_store
from prme.types import NodeType, Scope

KIND = "prme-graph-oracle-gold"
SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "benchmarks" / "fixtures" / "graph-oracle"
GOLD_NAMES = ("defects-v1", "store-v1", "chat-v1")

# The closed vocabulary (DESIGN.md). `cues` are the question words that point at a relation.
RELATIONS: dict[str, dict] = {
    "located_in": {"cues": (), "definition": "spatial containment: Tokyo in Japan, a firm in Brooklyn"},
    "part_of": {"cues": ("part",), "definition": "non spatial component of a thing or org"},
    "member_of": {"cues": ("team", "member", "group"), "definition": "a person belongs to a team or group"},
    "works_at": {"cues": ("work", "job", "employ", "company", "title"), "definition": "employed by; role is the title"},
    "lives_in": {"cues": ("live", "home", "resid"), "definition": "resides in"},
    "partner_of": {"cues": ("partner", "spouse", "fiance", "engag", "wife", "husband"), "definition": "partner or fiance, symmetric"},
    "kin_of": {"cues": ("sister", "brother", "mother", "father", "niece", "nephew", "aunt", "uncle", "child", "family"),
               "definition": "family; role says which"},
    "friend_of": {"cues": ("friend", "run"), "definition": "friend; role may say running partner"},
    "manager_of": {"cues": ("manager", "mentor", "boss"), "definition": "directs or guides; role says manager or mentor"},
    "owns": {"cues": ("own", "dog", "cat", "pet"), "definition": "owns, keeps, adopted"},
    "visits": {"cues": ("visit", "trip", "travel", "stop", "drive"), "definition": "travels to; status holds the intent"},
    "involved_in": {"cues": ("lead", "present", "sign", "race", "review", "call", "join"),
                    "definition": "takes part; role says how"},
    "prefers": {"cues": ("prefer", "like", "dislike", "avoid", "want", "favorite"), "definition": "likes or wants; negative is dislike"},
    "allergic_to": {"cues": ("allerg",), "definition": "an allergy"},
    "uses": {"cues": ("use", "learn", "language", "tool", "editor", "read", "book", "backend"),
             "definition": "uses, runs on, chose; role may say learning"},
    "decided": {"cues": ("decid", "choos", "chose", "reject", "pick"), "definition": "chose or rejected; polarity says which"},
    "has": {"cues": (), "definition": "an attribute value, with attr as its key"},
}
STATUSES = ("planned", "current", "done", "past", "superseded")
KINDS = ("lookup", "list", "correction", "two_hop", "three_hop")
STOPWORDS = frozenset("""a an and are at be by can did do does for from has have how i in is it its many me much my of on or
our s she he the their them they this to was were what when where which who whom why will with you your""".split())


@dataclass(frozen=True)
class Entity:
    id: str
    type: str
    subtype: str
    names: tuple[str, ...]
    exact_case: bool = False


@dataclass(frozen=True)
class Claim:
    id: str
    s: str
    r: str
    o: str | None = None
    attr: str = ""
    value: str = ""
    ev: tuple[str, ...] = ()
    polarity: str = "positive"
    status: str = "current"
    role: str = ""
    time: str = ""
    inferred: bool = False
    supersedes: str | None = None
    via: str = ""


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    variants: tuple[str, ...]
    kind: str
    spec: dict
    answers: tuple[str, ...]
    terms: dict
    stale: tuple[str, ...] = ()
    origin: str = "source"


@dataclass(frozen=True)
class Memory:
    id: str
    text: str
    role: str
    speaker: str | None
    node_type: NodeType
    scope: Scope
    session: str
    event_time: datetime | None = None


@dataclass
class Gold:
    name: str
    path: Path
    owner: str
    owner_entity: str
    entities: dict[str, Entity]
    claims: list[Claim]
    questions: list[Question]
    memories: list[Memory]
    description: str = ""
    reference_time: datetime | None = None
    patterns: dict[str, list[tuple[re.Pattern, str]]] = field(default_factory=dict)

    def memory(self, memory_id: str) -> Memory:
        return next(memory for memory in self.memories if memory.id == memory_id)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.casefold().replace("’", "'"))


def stem(word: str) -> str:
    """A crude stem, enough to match "rejected" with "rejection" and "rows" with "row"."""
    word = word.removesuffix("'s")
    for suffix in ("ation", "ion", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def stems(text: str) -> set[str]:
    """The content stems of a text: no stop words, no digits only words."""
    return {stem(word) for word in _words(text) if word not in STOPWORDS and not word.isdigit()}


def singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def _corpus_memories(corpus: dict) -> tuple[str, datetime | None, list[Memory]]:
    path = ROOT / corpus["path"]
    if corpus["kind"] == "conversation":
        conversation = load_conversation(path)
        return conversation["owner"], datetime.fromisoformat(conversation["reference_time"]), [
            Memory(turn["key"], turn["content"], turn["role"], None, NodeType.NOTE, Scope.PERSONAL, turn["session_id"],
                   turn["event_time"]) for turn in chat_turns(conversation)]
    fixture = fast_store.load_fixture(path)
    return fixture["owner"], None, [
        Memory(f"m{number:02d}", memory["content"], memory["role"], memory.get("speaker"),
               NodeType(memory["node_type"]), Scope(memory["scope"]), memory["session"])
        for number, memory in enumerate(fixture["memories"])]


def _alias_patterns(entities: dict[str, Entity]) -> dict[str, list[tuple[re.Pattern, str]]]:
    patterns: dict[str, list[tuple[re.Pattern, str]]] = {}
    for entity in entities.values():
        flags = 0 if entity.exact_case else re.IGNORECASE
        patterns[entity.id] = [(re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", flags), name) for name in entity.names]
    return patterns


def mentions(gold: Gold, text: str) -> list[str]:
    """The entities a text names, in order of first mention.

    Every name is a whole word match (case blind unless the entity is exact
    case). A longer name wins over a shorter one that sits inside it, so
    "Denver Health" does not also name Denver.
    """
    found = []
    for entity_id, patterns in gold.patterns.items():
        for pattern, name in patterns:
            found.extend((match.start(), match.end(), entity_id) for match in pattern.finditer(text))
    found.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    taken: list[tuple[int, int]] = []
    chosen: dict[str, int] = {}
    for start, end, entity_id in found:
        if any(start < other_end and other_start < end for other_start, other_end in taken):
            continue
        taken.append((start, end))
        chosen[entity_id] = min(chosen.get(entity_id, start), start)
    return sorted(chosen, key=chosen.get)


def target_cues(gold: Gold, question: str) -> dict[str, set[str]]:
    """The types and subtypes a question word asks for: "which cities" asks for subtype city, "who" for person."""
    words = [singular(word) for word in _words(question)]
    joined = " ".join(words)
    subtypes = {entity.subtype for entity in gold.entities.values()
                if re.search(rf"(?<!\w){re.escape(singular(entity.subtype))}(?!\w)", joined)}
    types = {name for word, name in (("who", "person"), ("where", "place")) if word in words}
    return {"subtype": subtypes, "type": types}


# The schema's own words in a relation's definition: they describe the fields, not the relation.
SCHEMA_WORDS = frozenset("role status polarity attr says say holds hold may key symmetric intent negative positive "
                         "value non".split())
DESIGN = Path(__file__).with_name("DESIGN.md")


def _verb_forms(base: str) -> set[str]:
    """A stem and the forms a verb takes: restore a dropped e (liv, live), add s, ed and ing, drop a final e before ing."""
    forms = {base, base + "e", base + "s", base + "ed", base + "ing"}
    if base.endswith("e"):
        forms.add(base[:-1] + "ing")
    return forms


def derive_cues(design: str | None = None) -> dict[str, frozenset[str]]:
    """Cue stems for each relation, derived from the definitions in DESIGN.md's vocabulary table, with no question in view.

    For each row: the words of the relation's name and of its definition, less the stop words, the schema's own
    words, capitalized words (example names, kept out because "Tokyo" says nothing about a relation) and digits;
    each word contributes its stem and the stems of its verb forms (s, ed, ing).
    """
    text = (design if design is not None else DESIGN.read_text()).split("## The relation vocabulary", 1)[1]
    cues: dict[str, frozenset[str]] = {}
    for row in re.finditer(r"^\| `(\w+)` \|[^|]*\| (.+?) \|$", text, re.MULTILINE):
        name, definition = row.groups()
        if name not in RELATIONS:
            continue
        definition = re.sub(r"`[^`]*`", " ", definition)
        words = [word for word in re.findall(r"[A-Za-z]+", definition) if word[0].islower()]
        words += name.split("_")
        stems_: set[str] = set()
        for word in words:
            word = word.lower()
            if word in STOPWORDS or word in SCHEMA_WORDS or len(word) < 3:
                continue
            base = stem(word)
            stems_ |= {stem(form) for form in _verb_forms(base)} | {base}
            # A doubled final consonant (runn from running) also stands for the plain stem.
            stems_ |= {item[:-1] for item in set(stems_) if len(item) > 3 and item[-1] == item[-2] and item[-1] not in "aeiou"}
        cues[name] = frozenset(stems_)
    return cues


def relation_cues(question: str, source: str = "hand") -> set[str]:
    """The relations whose cue words the question holds: the hand written lexicon ("hand", a prefix match) or the
    one derived from DESIGN.md ("derived", an exact match of stems)."""
    question_stems = stems(question)
    if source == "derived":
        derived = _derived()
        return {name for name, words in derived.items() if words & question_stems}
    return {name for name, relation in RELATIONS.items()
            if any(any(stem_ == cue or stem_.startswith(cue) for stem_ in question_stems) for cue in relation["cues"])}


_DERIVED: dict[str, frozenset[str]] = {}


def _derived() -> dict[str, frozenset[str]]:
    if not _DERIVED:
        _DERIVED.update(derive_cues())
    return _DERIVED


def load_gold(name_or_path: str | Path) -> Gold:
    path = Path(name_or_path)
    if not path.suffix:
        path = FIXTURES / f"{name_or_path}.json"
    raw = json.loads(path.read_text())
    if raw.get("kind") != KIND or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a graph oracle gold file")
    owner, reference_time, memories = _corpus_memories(raw["corpus"])
    entities = {}
    for item in raw["entities"]:
        if item["id"] in entities:
            raise ValueError(f"{path}: entity {item['id']} is listed twice")
        entities[item["id"]] = Entity(item["id"], item["type"], item["subtype"], tuple(item["names"]),
                                      item.get("exact_case", False))
    claims = []
    for number, item in enumerate(raw["relations"], start=1):
        claims.append(Claim(
            item.get("id", f"c{number}"), item["s"], item["r"], item.get("o"), item.get("attr", ""),
            item.get("value", ""), tuple(item.get("ev", ())), item.get("polarity", "positive"),
            item.get("status", "current"), item.get("role", ""), item.get("time", ""), item.get("inferred", False),
            item.get("supersedes"), item.get("via", "")))
    questions = [Question(
        item["id"], item["question"], tuple(item.get("variants", ())), item["kind"], item["spec"],
        tuple(item["answers"]), item["terms"], tuple(item.get("stale", ())), item.get("origin", "source"))
        for item in raw["questions"]]
    gold = Gold(raw["name"], path, owner, raw["owner_entity"], entities, claims, questions, memories,
                raw.get("description", ""), reference_time)
    gold.patterns = _alias_patterns(entities)
    validate(gold)
    return gold


def validate(gold: Gold) -> None:
    """Reject a gold file that names an unknown entity, relation, memory or claim, or breaks the evidence rules."""
    memory_ids = {memory.id for memory in gold.memories}
    claim_ids = {claim.id for claim in gold.claims}
    problems = []
    if len(claim_ids) != len(gold.claims):
        problems.append("claim ids repeat")
    if gold.owner_entity not in gold.entities:
        problems.append("the owner entity is not listed")
    for claim in gold.claims:
        where = f"claim {claim.id} ({claim.s} {claim.r} {claim.o or claim.attr})"
        if claim.r not in RELATIONS:
            problems.append(f"{where}: relation is not in the vocabulary")
        if claim.status not in STATUSES or claim.polarity not in ("positive", "negative"):
            problems.append(f"{where}: bad status or polarity")
        if claim.s not in gold.entities or (claim.o is not None and claim.o not in gold.entities):
            problems.append(f"{where}: unknown entity")
        if (claim.o is None) != (claim.r == "has") or (claim.r == "has" and not claim.attr):
            problems.append(f"{where}: only has takes a literal, and it needs attr")
        if claim.inferred and (claim.ev or claim.r != "located_in"):
            problems.append(f"{where}: only located_in may be inferred, and it has no evidence")
        if not claim.inferred and not claim.ev:
            problems.append(f"{where}: a stated claim needs evidence")
        if set(claim.ev) - memory_ids:
            problems.append(f"{where}: unknown memory {sorted(set(claim.ev) - memory_ids)}")
        if claim.supersedes is not None and claim.supersedes not in claim_ids:
            problems.append(f"{where}: supersedes an unknown claim")
    superseded = {claim.supersedes for claim in gold.claims if claim.supersedes}
    for claim in gold.claims:
        if claim.id in superseded and claim.status == "current":
            problems.append(f"claim {claim.id} is superseded but still current")
    ids = [question.id for question in gold.questions]
    if len(set(ids)) != len(ids):
        problems.append("question ids repeat")
    for question in gold.questions:
        where = f"question {question.id}"
        if question.kind not in KINDS:
            problems.append(f"{where}: unknown kind")
        if not question.answers or set(question.answers) - memory_ids:
            problems.append(f"{where}: answers must be known memories")
        if bool(question.terms.get("all_of")) == bool(question.terms.get("any_of")):
            problems.append(f"{where}: terms need all_of or any_of")
        spec = question.spec
        if set(spec["seeds"]) - set(gold.entities):
            problems.append(f"{where}: unknown seed")
        for step in spec["path"]:
            if "r" in step and step["r"] not in RELATIONS:
                problems.append(f"{where}: relation {step['r']} is not in the vocabulary")
            if ("r" in step) == ("attr" in step):
                problems.append(f"{where}: a step needs r or attr")
        target = spec.get("target", {})
        if target.get("id") is not None and target["id"] not in gold.entities:
            problems.append(f"{where}: unknown target")
    if problems:
        raise ValueError(f"{gold.path}: " + "; ".join(problems))


def term_hits(terms: dict, text: str) -> bool:
    """The fast gate's answer rule: every ``all_of`` term, or any ``any_of`` term, is in the text."""
    lowered = text.casefold()
    if terms.get("all_of"):
        return all(term.casefold() in lowered for term in terms["all_of"])
    return any(term.casefold() in lowered for term in terms["any_of"])
