"""Caller-stated facts: ``ingest(content, assertions=[...])`` writes typed claims with no model call.

A robot's perception stack, a form or any other structured source already knows the fact it saw: the subject,
the relation and the object. With ``assertions`` the caller states it, and ingestion turns each assertion into
the claim typed extraction (``enable_typed_extraction``) would make from a sentence saying it: the subject and
object entities, a fact node with its relation, role and status, the ``HAS_FACT`` and ``MENTIONS`` links and the
derivation plan. The relation walk and the belief ledger read it with no special case
(memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md, section 4.3).

How it fits the pipeline:

- The validated assertions are kept in the event's metadata (``ASSERTIONS_KEY``), so the event log alone
  rebuilds them, as it rebuilds every derived record.
- Where ingestion would ask the model, :func:`structured_result` builds the extraction from the event, and it is
  saved as an extraction record with the provider ``structured``. From there the event is planned and published
  as any extracted event is. It never joins a model batch, and pronoun repair, corrections and resolved claim
  text do not apply to it.
- Before the event is admitted, :func:`prepare_assertions` checks that each subject and object is something the
  source can be about: the content mentions it, it is the source's speaker and the content says I, me, my, mine,
  myself, we, us or our, or it names an entity the owner's memory already holds in the source's scope. A literal
  value (relation ``has``) must be in the content, unless it repeats the value of a live claim at the same
  subject and attribute (a closure: "I quit" stated as the old value with status past). The engine reads the
  memory and the live claims (:class:`Held`); how each end was found is kept with its assertion (``found``), so
  a rebuild from the log alone, which may see more in the graph, accepts exactly what admission accepted.
- An assertion's ``time`` (an ISO date or a datetime with its offset) is when the fact starts to hold or happened.
  It becomes the claim's stated time as a resolved date of an extracted fact does; without it the claim takes the
  source's time.
- A stated claim's text is a sentence rendered from its fields (:func:`claim_text`): the subject, the relation,
  the object, the role, the time and the status in plain words. The source's own text stays the stored message
  and the claim's evidence quote.
- ``reingest()`` derives such an event again from its own metadata, never through a model, under settings of its
  own (``prme.ingestion.reingest.structured_settings``).

Nothing here calls a model or reads the network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from prme.ingestion.claim_merge import _normalize as _fold
from prme.ingestion.grounding import _mentioned, mentioned_or_spoken, names_speaker
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult

# The event metadata key holding the validated assertions. Reserved: a caller states assertions with the
# ``assertions`` argument, never through metadata.
ASSERTIONS_KEY = "prme_assertions_v1"
# The version of the assertion form and of the rule that builds claims from it. Recorded in each event, in the
# extraction record's model name and in reingest()'s settings for structured sources; change it with any change
# to what an unchanged assertion derives. Version 2 (2026-11-11): the mention rule's speaker, memory and closure
# (recorded per end as ``found``), ``time``, and claim text rendered from the fields.
ASSERTION_VERSION = 2
STRUCTURED_PROVIDER = "structured"
STRUCTURED_MODEL = f"assertions-v{ASSERTION_VERSION}"
# The confidence a caller-stated claim carries; extraction gives a clearly stated fact about the same.
ASSERTION_CONFIDENCE = 0.95
# The relation of a literal attribute: ``has`` with the attribute's name as its predicate.
ATTRIBUTE_RELATION = "has"
ENTITY_TYPES = frozenset({"person", "organization", "location", "product", "concept", "event"})
# How a subject or object was found when its assertion was admitted. ``memory`` and ``closure`` are read from the
# graph at admission, so a rebuild takes them as recorded; ``text`` and ``speaker`` are checked again.
FOUND_IN_TEXT, FOUND_AS_SPEAKER, FOUND_IN_MEMORY, FOUND_AS_CLOSURE = "text", "speaker", "memory", "closure"
FOUND = (FOUND_IN_TEXT, FOUND_AS_SPEAKER, FOUND_IN_MEMORY, FOUND_AS_CLOSURE)

_LEADING_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_ROLE = re.compile(r"[a-z0-9][a-z0-9 _'-]*")
# The speaker speaking for a group they are in; mentioned_or_spoken reads I, me, my, mine and myself.
_SPEAKER_PLURAL = re.compile(r"(?<!\w)(?:we|us|our|ours|ourselves)(?!\w)", re.IGNORECASE)
# Each relation as a claim sentence says it: present (current), base (after "used to", "is going to", "does
# not") and past (done).
_FORMS = {
    "located_in": ("is located in", "be located in", "was located in"),
    "part_of": ("is part of", "be part of", "was part of"),
    "member_of": ("is a member of", "be a member of", "was a member of"),
    "works_at": ("works at", "work at", "worked at"),
    "lives_in": ("lives in", "live in", "lived in"),
    "partner_of": ("is the partner of", "be the partner of", "was the partner of"),
    "kin_of": ("is related to", "be related to", "was related to"),
    "friend_of": ("is a friend of", "be a friend of", "was a friend of"),
    "manager_of": ("is the manager of", "be the manager of", "was the manager of"),
    "owns": ("owns", "own", "owned"),
    "visits": ("visits", "visit", "visited"),
    "involved_in": ("is involved in", "be involved in", "was involved in"),
    "prefers": ("prefers", "prefer", "preferred"),
    "allergic_to": ("is allergic to", "be allergic to", "was allergic to"),
    "uses": ("uses", "use", "used"),
    "decided": ("decided on", "decide on", "decided on"),
}


def _name(value: str) -> str:
    """An entity name without a leading article: "the red mug" and "red mug" are one entity."""
    stripped = " ".join(value.split())
    without = _LEADING_ARTICLE.sub("", stripped)
    return without or stripped


def _time(value: str) -> str:
    """An ISO date, or an ISO datetime with its offset, in its canonical form."""
    text = value.strip()
    try:
        return date.fromisoformat(text).isoformat() if len(text) == 10 else _aware(datetime.fromisoformat(text))
    except ValueError:
        raise ValueError(f"time {value!r} must be an ISO date (2026-05-04) or an ISO datetime with its offset "
                         "(2026-05-04T09:30:00-06:00)") from None


def _aware(moment: datetime) -> str:
    if moment.utcoffset() is None:
        raise ValueError("no offset")
    return moment.isoformat()


class Assertion(BaseModel):
    """One fact a caller states: ``subject`` ``relation`` ``object``, typed as extraction types a claim.

    ``relation`` is a relation of the closed vocabulary (``prme.retrieval.relations.RELATIONS``). For a literal
    value the relation is ``has``, ``predicate`` names the attribute and ``object`` is the value, with no
    ``object_type``. ``role`` qualifies the relation's far end as a typed claim's role does (the family word for
    ``kin_of``, the title for ``works_at``), ``status`` is one of the typed statuses (planned, current, done,
    past, superseded), and ``time`` is when the fact starts to hold or happened.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    subject_type: str
    relation: str
    object: str
    object_type: str | None = None
    predicate: str | None = None
    status: str | None = None
    polarity: Literal["positive", "negative"] = "positive"
    role: str | None = None
    time: str | None = None

    @field_validator("subject", "object", "relation", "subject_type", mode="before")
    @classmethod
    def required_text(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be nonempty text")
        return " ".join(value.split())

    @field_validator("relation", "subject_type")
    @classmethod
    def lower(cls, value: str) -> str:
        return value.casefold()

    @field_validator("object_type", "predicate", "status", "role", "time", mode="before")
    @classmethod
    def optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be nonempty text or null")
        return " ".join(value.split())

    @field_validator("time")
    @classmethod
    def iso_time(cls, value: str | None) -> str | None:
        return None if value is None else _time(value)

    @model_validator(mode="after")
    def check(self) -> Assertion:
        from prme.ingestion.extraction import RELATION_STATUSES
        from prme.retrieval.relations import RELATIONS

        if self.relation not in RELATIONS:
            raise ValueError(f"relation {self.relation!r} is not in the vocabulary "
                             "(prme.retrieval.relations.RELATIONS)")
        if self.subject_type not in ENTITY_TYPES:
            raise ValueError(f"subject_type {self.subject_type!r} is not one of {sorted(ENTITY_TYPES)}")
        if self.relation == ATTRIBUTE_RELATION:
            if self.predicate is None:
                raise ValueError("relation 'has' needs a predicate, the attribute's name")
            if self.object_type is not None:
                raise ValueError("relation 'has' takes a literal object, with no object_type")
        else:
            if self.predicate is not None:
                raise ValueError("predicate is only for relation 'has'; the relation is the predicate")
            if self.object_type is None or self.object_type.casefold() not in ENTITY_TYPES:
                raise ValueError(f"object_type must be one of {sorted(ENTITY_TYPES)} for relation {self.relation!r}")
        if self.status is not None and self.status.casefold() not in RELATION_STATUSES:
            raise ValueError(f"status {self.status!r} is not one of {list(RELATION_STATUSES)}")
        if self.role is not None and (len(self.role) > 50 or _ROLE.fullmatch(self.role.casefold()) is None):
            raise ValueError(f"role {self.role!r} must be a short lower-case word or phrase")
        return self

    def normalized(self) -> Assertion:
        """Names without a leading article; lower-case types, status and role. A literal value keeps its words."""
        entity_object = self.relation != ATTRIBUTE_RELATION
        return self.model_copy(update={
            "subject": _name(self.subject),
            "object": _name(self.object) if entity_object else self.object,
            "object_type": self.object_type.casefold() if self.object_type else None,
            "status": self.status.casefold() if self.status else None,
            "role": self.role.casefold() if self.role else None,
        })

    def address(self) -> tuple[str, str, str]:
        """The claim's subject and attribute, as a closure is matched: subject, its type, relation or has:predicate."""
        attribute = f"{ATTRIBUTE_RELATION}:{_fold(self.predicate)}" if self.relation == ATTRIBUTE_RELATION else self.relation
        return _fold(self.subject), self.subject_type, attribute


@dataclass(frozen=True)
class Held:
    """What the owner's memory held in the source's scope when its assertions were checked.

    ``entities`` holds (name, entity type) of the entities an end may name; ``values`` holds (subject, subject
    type, relation or ``has:<predicate>``, object) of live claims a literal value may repeat. Names and values are
    compared as the claim merge compares them (NFKC, case folded, one space for ``_`` and runs). The engine reads them from the graph only for ends the content does not
    settle (``prme.storage.engine``).
    """

    entities: frozenset[tuple[str, str]] = frozenset()
    values: frozenset[tuple[str, str, str, str]] = frozenset()


def _found(assertion: Assertion, field: str, content: str, speaker: str | None, held: Held) -> str | None:
    """How ``field`` of ``assertion`` is found for this source, or None."""
    value = getattr(assertion, field)
    if _mentioned(value, content):
        return FOUND_IN_TEXT
    if field == "object" and assertion.relation == ATTRIBUTE_RELATION:
        return FOUND_AS_CLOSURE if (*assertion.address(), _fold(value)) in held.values else None
    if speaker is not None and names_speaker(value, speaker) and (
            mentioned_or_spoken(value, content, speaker) or _SPEAKER_PLURAL.search(content)):
        return FOUND_AS_SPEAKER
    kind = assertion.subject_type if field == "subject" else assertion.object_type
    return FOUND_IN_MEMORY if (_fold(value), kind) in held.entities else None


def needs_memory(assertion: Assertion, content: str, speaker: str | None) -> dict[str, str]:
    """The ends the content and the speaker do not settle, for the engine to look up: field to "entity" or "value"."""
    return {field: "value" if field == "object" and assertion.relation == ATTRIBUTE_RELATION else "entity"
            for field in ("subject", "object")
            if _found(assertion, field, content, speaker, Held()) is None}


def checked_assertions(assertions: Sequence[Assertion | Mapping[str, Any]]) -> list[Assertion]:
    """Each assertion validated and normalized; a malformed one raises ``ValueError`` with its index."""
    if not assertions:
        raise ValueError("assertions must hold at least one assertion")
    checked: list[Assertion] = []
    for index, item in enumerate(assertions):
        try:
            assertion = item if isinstance(item, Assertion) else Assertion.model_validate(dict(item))
        except (ValidationError, TypeError, ValueError) as exc:
            raise ValueError(f"assertions[{index}]: {_reason(exc)}") from None
        checked.append(assertion.normalized())
    return checked


def prepare_assertions(content: str | None, assertions: Sequence[Assertion | Mapping[str, Any]], *,
                       speaker: str | None = None, held: Held = Held()) -> tuple[str, list[dict]]:
    """Validate a caller's assertions against ``content`` before anything is admitted.

    Returns the content to store (rendered from the assertions when ``content`` is None or blank) and the
    assertions as they are kept in the event's metadata, each with ``found``: how its subject and its object were
    found (``text``, ``speaker``, ``memory`` or ``closure``; see the module docstring). Raises ``ValueError``
    naming the assertion when one is malformed, uses a relation outside the vocabulary, a bad status, polarity,
    role or time, or names a subject or object that none of the rules finds.
    """
    checked = checked_assertions(assertions)
    if content is None or not content.strip():
        content = render_content(checked)
    prepared = []
    for index, assertion in enumerate(checked):
        found = {}
        for field in ("subject", "object"):
            found[field] = _found(assertion, field, content, speaker, held)
            if found[field] is None:
                literal = field == "object" and assertion.relation == ATTRIBUTE_RELATION
                raise ValueError(
                    f"assertions[{index}]: the content does not mention its {field} {getattr(assertion, field)!r}"
                    + (", and it repeats no live claim's value at the same subject and attribute" if literal
                       else ", and it is neither the speaker nor an entity the owner's memory holds"))
        prepared.append({**assertion.model_dump(mode="json", exclude_none=True), "found": found})
    return content, prepared


def _reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        error = exc.errors()[0]
        where = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "invalid")).removeprefix("Value error, ")
        return f"{where}: {message}" if where else message
    return str(exc)


def attach_assertions(metadata: Mapping[str, Any] | None, assertions: list[dict] | None) -> Any:
    """Add prepared assertions to source metadata, refusing the reserved key from the caller."""
    if isinstance(metadata, Mapping) and ASSERTIONS_KEY in metadata:
        raise ValueError(f"metadata.{ASSERTIONS_KEY} is reserved; pass assertions instead")
    if assertions is None:
        return metadata
    if metadata is not None and not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    return {**(metadata or {}), ASSERTIONS_KEY: {"version": ASSERTION_VERSION, "items": assertions}}


def is_structured(metadata: Mapping[str, Any] | None) -> bool:
    """Whether a source carries caller-stated assertions, so no model extracts it."""
    return isinstance(metadata, Mapping) and ASSERTIONS_KEY in metadata


def stored_assertions(content: str, metadata: Mapping[str, Any]) -> list[Assertion]:
    """The assertions kept in a source's metadata, checked again against its content as they were admitted.

    ``text`` and ``speaker`` are checked again from the content and the source's speaker; ``memory`` and
    ``closure`` were read from the graph at admission and are taken as recorded, so a rebuild from the log alone
    accepts exactly what admission accepted, whatever the graph holds by then.
    """
    from prme.models.speaker import metadata_speaker

    stored = metadata.get(ASSERTIONS_KEY)
    if not isinstance(stored, Mapping) or stored.get("version") != ASSERTION_VERSION:
        raise ValueError("the source's assertions have no supported version")
    items = stored.get("items")
    if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
        raise ValueError("the source's assertions are not a list")
    recorded = [item.get("found") for item in items]
    checked = checked_assertions([{key: value for key, value in item.items() if key != "found"} for item in items])
    entities, values = set(), set()
    for assertion, found in zip(checked, recorded):
        if not isinstance(found, Mapping) or set(found) != {"subject", "object"} or not set(found.values()) <= set(FOUND):
            raise ValueError("the source's assertions do not record how each end was found")
        for field, how in found.items():
            if how == FOUND_IN_MEMORY:
                kind = assertion.subject_type if field == "subject" else assertion.object_type
                entities.add((_fold(getattr(assertion, field)), kind))
            elif how == FOUND_AS_CLOSURE:
                values.add((*assertion.address(), _fold(assertion.object)))
    checked_content, prepared = prepare_assertions(content, checked, speaker=metadata_speaker(metadata),
                                                   held=Held(frozenset(entities), frozenset(values)))
    if checked_content != content:
        raise ValueError("the source's assertions need content")
    if [item["found"] for item in prepared] != [dict(found) for found in recorded]:
        raise ValueError("the source's assertions are not found as they were when admitted")
    return checked


def structured_result(content: str, metadata: Mapping[str, Any]) -> ExtractionResult:
    """The extraction a source's assertions state: the entities and one typed fact per assertion.

    Each fact cites the whole content as its evidence and carries its ``time`` as a resolved reference
    (``temporal_ref``), which materialization turns into the claim's stated time. The claim's text is rendered
    from the fact's fields at materialization (:func:`claim_text`).
    """
    assertions = stored_assertions(content, metadata)
    entities: dict[tuple[str, str], ExtractedEntity] = {}

    def entity(name: str, entity_type: str) -> None:
        entities.setdefault((name.casefold(), entity_type), ExtractedEntity(name=name, entity_type=entity_type))

    facts = []
    for item in assertions:
        entity(item.subject, item.subject_type)
        if item.object_type is not None:
            entity(item.object, item.object_type)
        facts.append(ExtractedFact(
            subject=item.subject, subject_entity_type=item.subject_type,
            predicate=item.predicate if item.relation == ATTRIBUTE_RELATION else item.relation,
            object=item.object, object_entity_type=item.object_type,
            polarity=item.polarity, confidence=ASSERTION_CONFIDENCE, epistemic_type="asserted",
            evidence_quote=content, temporal_ref=item.time,
            relation=item.relation, role=item.role, status=item.status,
        ))
    return ExtractionResult(entities=list(entities.values()), facts=facts)


# --- Claim text -------------------------------------------------------------------------------------------------


def claim_text(subject: str, relation: str, obj: str, *, predicate: str | None = None, role: str | None = None,
               status: str | None = None, polarity: str = "positive", time: str | None = None) -> str:
    """One sentence that says a stated claim: subject, relation, object, role, time and status in plain words.

    current (or none): "Dana works at Brightpath as analytics manager (since 2026-05-04)."
    past or superseded: "Dana used to work at Northwind." It reads as history, never as a denial.
    planned: "Dana is going to work at Brightpath (from 2026-05-04)."
    done: "Dana visited Japan (on 2026-03-20)."
    negative polarity: "Dana does not own car.", "Dana is not allergic to peanuts." ("did not" or "was not" for
    done or past, "is not going to" for planned).
    ``has``: "Dana's diet is pescatarian.", "Dana's diet used to be vegetarian.", "Dana's race time was 1:58:30."
    """
    state = "past" if status == "superseded" else status or "current"
    negative = polarity == "negative"
    if relation == ATTRIBUTE_RELATION:
        attribute = (predicate or "value").replace("_", " ").strip()
        verb = {"current": ("is not" if negative else "is"),
                "past": ("did not use to be" if negative else "used to be"),
                "done": ("was not" if negative else "was"),
                "planned": ("is not going to be" if negative else "is going to be")}[state]
        core = f"{subject}'s {attribute} {verb} {obj}"
        qualifier = f" ({role})" if role else ""
    else:
        present, base, past = _FORMS.get(relation, (relation.replace("_", " "),) * 3)
        # "be" phrases deny with "is not" and "was not"; other verbs with "does not" and "did not".
        copula = base.startswith("be ")
        verb = {"current": ((f"is not {base[3:]}" if copula else f"does not {base}") if negative else present),
                "past": ((f"was not {base[3:]}" if copula else f"did not {base}") if negative else f"used to {base}"),
                "done": ((f"was not {base[3:]}" if copula else f"did not {base}") if negative else past),
                "planned": (f"is not going to {base}" if negative else f"is going to {base}")}[state]
        core = f"{subject} {verb} {obj}"
        qualifier = (f" as {role}" if relation == "works_at" else f" ({role})") if role else ""
    when = ""
    if time:
        word = {"current": "since", "done": "on"}.get(state, "from")
        when = f" ({word} {time})"
    sentence = f"{core}{qualifier}{when}."
    return sentence[0].upper() + sentence[1:]


def fact_text(fact: ExtractedFact) -> str:
    """The text of a stated claim from its extracted fact (:func:`structured_result`)."""
    return claim_text(fact.subject, fact.relation or fact.predicate, fact.object, predicate=fact.predicate,
                      role=fact.role, status=fact.status, polarity=fact.polarity, time=fact.temporal_ref)


def render_content(assertions: Sequence[Assertion]) -> str:
    """A claim sentence per assertion, for a caller that gives no content. It names every subject and object."""
    return " ".join(claim_text(item.subject, item.relation, item.object, predicate=item.predicate, role=item.role,
                               status=item.status, polarity=item.polarity, time=item.time) for item in assertions)


async def read_held(assertions: Sequence[Assertion], content: str, *, graph_store, entity_merger, user_id: str,
                    scope, speaker: str | None) -> Held:
    """What the owner's memory holds for the ends of ``assertions`` that ``content`` and ``speaker`` do not settle.

    An entity end is looked up as ingestion reuses an entity (``EntityMerger.find_entity``: this owner, this scope,
    the name and the type). A literal value is looked up among the live claims (tentative or stable) of its subject's
    entity, at the same relation, or for ``has`` the same attribute, with the same value. Nothing is written.
    """
    from prme.types import EdgeType, LifecycleState

    ids: dict[tuple[str, str], str | None] = {}

    async def entity_id(name: str, kind: str) -> str | None:
        if (name, kind) not in ids:
            ids[(name, kind)] = await entity_merger.find_entity(name, kind, user_id, scope=scope)
        return ids[(name, kind)]

    entities, values = set(), set()
    for assertion in assertions:
        for field, need in needs_memory(assertion, content, speaker).items():
            if need == "entity":
                kind = assertion.subject_type if field == "subject" else assertion.object_type
                if await entity_id(getattr(assertion, field), kind) is not None:
                    entities.add((_fold(getattr(assertion, field)), kind))
                continue
            subject_id = await entity_id(assertion.subject, assertion.subject_type)
            if subject_id is None:
                continue
            edges = await graph_store.get_edges(source_id=subject_id)
            claims = await graph_store.get_nodes(sorted({str(edge.target_id) for edge in edges
                                                         if edge.edge_type == EdgeType.HAS_FACT}))
            wanted = assertion.address()[2]
            for claim in claims:
                metadata = claim.metadata or {}
                relation, predicate, value = metadata.get("relation"), metadata.get("predicate"), metadata.get("object")
                if (claim.lifecycle_state in (LifecycleState.TENTATIVE, LifecycleState.STABLE)
                        and relation in (None, ATTRIBUTE_RELATION) and isinstance(predicate, str)
                        and isinstance(value, str) and f"{ATTRIBUTE_RELATION}:{_fold(predicate)}" == wanted
                        and _fold(value) == _fold(assertion.object)):
                    values.add((*assertion.address(), _fold(assertion.object)))
                    break
    return Held(frozenset(entities), frozenset(values))
