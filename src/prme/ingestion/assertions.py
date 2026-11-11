"""Caller-stated facts: ``ingest(content, assertions=[...])`` writes typed claims with no model call.

A robot's perception stack, a form or any other structured source already knows the fact it saw: the subject,
the relation and the object. With ``assertions`` the caller states it, and ingestion turns each assertion into
the claim typed extraction (``enable_typed_extraction``) would make from a sentence saying it: the subject and
object entities, a fact node with its relation, role and status, the ``HAS_FACT`` and ``MENTIONS`` links, the
grounding record and the derivation plan. The relation walk and the belief ledger read it with no special case
(memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md, section 4.3).

How it fits the pipeline:

- The validated assertions are kept in the event's metadata (``ASSERTIONS_KEY``), so the event log alone
  rebuilds them, as it rebuilds every derived record.
- Where ingestion would ask the model, :func:`structured_result` builds the extraction from the event, and it is
  saved as an extraction record with the provider ``structured``. From there the event is planned and published
  as any extracted event is. It never joins a model batch, and pronoun repair, corrections and resolved claim
  text do not apply to it.
- Grounding holds by construction: :func:`prepare_assertions` refuses, before the event is admitted, an
  assertion whose subject or object the content does not mention, and renders the content from the assertions
  when the caller gives none.
- ``reingest()`` derives such an event again from its own metadata, never through a model, under settings of its
  own (``prme.ingestion.reingest.structured_settings``).

Nothing here calls a model or reads the network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from prme.ingestion.grounding import _mentioned
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult

# The event metadata key holding the validated assertions. Reserved: a caller states assertions with the
# ``assertions`` argument, never through metadata.
ASSERTIONS_KEY = "prme_assertions_v1"
# The version of the assertion form and of the rule that builds claims from it. Recorded in each event, in the
# extraction record's model name and in reingest()'s settings for structured sources; change it with any change
# to what an unchanged assertion derives.
ASSERTION_VERSION = 1
STRUCTURED_PROVIDER = "structured"
STRUCTURED_MODEL = f"assertions-v{ASSERTION_VERSION}"
# The confidence a caller-stated claim carries; extraction gives a clearly stated fact about the same.
ASSERTION_CONFIDENCE = 0.95
# The relation of a literal attribute: ``has`` with the attribute's name as its predicate.
ATTRIBUTE_RELATION = "has"
ENTITY_TYPES = frozenset({"person", "organization", "location", "product", "concept", "event"})

_LEADING_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_ROLE = re.compile(r"[a-z0-9][a-z0-9 _'-]*")
_PHRASES = {
    "located_in": "is located in", "part_of": "is part of", "member_of": "is a member of",
    "works_at": "works at", "lives_in": "lives in", "partner_of": "is the partner of",
    "kin_of": "is related to", "friend_of": "is a friend of", "manager_of": "is the manager of",
    "owns": "owns", "visits": "visits", "involved_in": "is involved in", "prefers": "prefers",
    "allergic_to": "is allergic to", "uses": "uses", "decided": "decided on",
}


def _name(value: str) -> str:
    """An entity name without a leading article: "the red mug" and "red mug" are one entity."""
    stripped = " ".join(value.split())
    without = _LEADING_ARTICLE.sub("", stripped)
    return without or stripped


class Assertion(BaseModel):
    """One fact a caller states: ``subject`` ``relation`` ``object``, typed as extraction types a claim.

    ``relation`` is a relation of the closed vocabulary (``prme.retrieval.relations.RELATIONS``). For a literal
    value the relation is ``has``, ``predicate`` names the attribute and ``object`` is the value, with no
    ``object_type``. ``role`` qualifies the relation's far end as a typed claim's role does (the family word for
    ``kin_of``, the title for ``works_at``), and ``status`` is one of the typed statuses (planned, current, done,
    past, superseded).
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

    @field_validator("object_type", "predicate", "status", "role", mode="before")
    @classmethod
    def optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be nonempty text or null")
        return " ".join(value.split())

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


def render_content(assertions: Sequence[Assertion]) -> str:
    """A plain sentence per assertion, for a caller that gives no content. It names every subject and object."""
    sentences = []
    for item in assertions:
        if item.relation == ATTRIBUTE_RELATION:
            core = f"{item.subject} {item.predicate.replace('_', ' ')}: {item.object}"
        else:
            core = f"{item.subject} {_PHRASES.get(item.relation, item.relation.replace('_', ' '))} {item.object}"
        if item.polarity == "negative":
            core = f"It is not the case that {core}"
        details = [detail for detail in (item.role, item.status if item.status != "current" else None) if detail]
        sentence = core + (f" ({', '.join(details)})" if details else "")
        sentences.append(sentence[0].upper() + sentence[1:] + ".")
    return " ".join(sentences)


def prepare_assertions(content: str | None, assertions: Sequence[Assertion | Mapping[str, Any]],
                       ) -> tuple[str, list[dict]]:
    """Validate a caller's assertions against ``content`` before anything is admitted.

    Returns the content to store (rendered from the assertions when ``content`` is None or blank) and the
    assertions as they are kept in the event's metadata. Raises ``ValueError`` naming the assertion when one is
    malformed, uses a relation outside the vocabulary, a bad status, polarity or role, or names a subject or
    object that the content does not mention (a leading article may be there or not).
    """
    if not assertions:
        raise ValueError("assertions must hold at least one assertion")
    checked: list[Assertion] = []
    for index, item in enumerate(assertions):
        try:
            assertion = item if isinstance(item, Assertion) else Assertion.model_validate(dict(item))
        except (ValidationError, TypeError, ValueError) as exc:
            raise ValueError(f"assertions[{index}]: {_reason(exc)}") from None
        checked.append(assertion.normalized())
    if content is None or not content.strip():
        content = render_content(checked)
    for index, assertion in enumerate(checked):
        for field in ("subject", "object"):
            value = getattr(assertion, field)
            if not _mentioned(value, content):
                raise ValueError(f"assertions[{index}]: the content does not mention its {field} {value!r}")
    return content, [assertion.model_dump(mode="json", exclude_none=True) for assertion in checked]


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
    """The assertions kept in a source's metadata, validated again against its content."""
    stored = metadata.get(ASSERTIONS_KEY)
    if not isinstance(stored, Mapping) or stored.get("version") != ASSERTION_VERSION:
        raise ValueError("the source's assertions have no supported version")
    items = stored.get("items")
    if not isinstance(items, list):
        raise ValueError("the source's assertions are not a list")
    checked_content, _ = prepare_assertions(content, items)
    if checked_content != content:
        raise ValueError("the source's assertions need content")
    return [Assertion.model_validate(item).normalized() for item in items]


def structured_result(content: str, metadata: Mapping[str, Any]) -> ExtractionResult:
    """The extraction a source's assertions state: the entities and one typed fact per assertion.

    The facts cite no passage (``evidence_quote`` is None), so grounding and the claim's sentences read the
    whole content, as for a provider without citations, and each claim's text is the sentence that names it.
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
            relation=item.relation, role=item.role, status=item.status,
        ))
    return ExtractionResult(entities=list(entities.values()), facts=facts)
