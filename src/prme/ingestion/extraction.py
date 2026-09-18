"""ExtractionProvider Protocol and instructor-based implementations.

Defines the extraction interface (ExtractionProvider Protocol) and a
concrete implementation (InstructorExtractionProvider) that uses the
instructor library's from_provider() API to support OpenAI, Anthropic,
and Ollama backends through a single unified interface.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from decimal import Decimal
import os
import re
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import structlog
from pydantic import Field, SecretStr, ValidationError, ValidationInfo, model_validator

from prme.ingestion.schema import (
    ExtractedEntity,
    ExtractedFact,
    ExtractedQuantity,
    ExtractedRelationship,
    ExtractionResult,
)
from prme.ingestion.grounding import (
    _mentioned,
    _supporting_claim_passage,
    _supporting_passage,
    exact_decimal_string_from_quantity_text,
    recover_exact_quantity_from_object,
    validate_extracted_quantity,
)
from prme.ingestion.errors import ExtractionError, extraction_failure_code
from prme.ingestion.entity_references import reference_errors_by_claim

if TYPE_CHECKING:
    import instructor

    from prme.config import ExtractionConfig

logger = structlog.get_logger(__name__)

_VALIDATION_SOURCE: ContextVar[str | None] = ContextVar(
    "prme_extraction_validation_source", default=None
)
_VALIDATION_ROLE: ContextVar[str | None] = ContextVar(
    "prme_extraction_validation_role", default=None
)

_EXPLICIT_CONDITION_RE = re.compile(
    r"\b(?:if|unless|provided\s+that|as\s+long\s+as|only\s+if)\b",
    re.IGNORECASE,
)
_INDIRECT_QUESTION_IF_RE = re.compile(
    r"\b(?:ask(?:ed|ing)?|check(?:ed|ing)?|curious|determin(?:e|ed|ing)|"
    r"find(?:ing)?\s+out|know|see|wonder(?:ed|ing)?)\s+if\b",
    re.IGNORECASE,
)
_UNCERTAINTY_RE = re.compile(
    r"(?i:\b(?:might|could|possibly|perhaps|maybe)\b)|\bmay\b"
)
_POLITE_REQUEST_MODAL_RE = re.compile(
    r"\b(?:could\s+you|may\s+I|you\s+could\s+(?:please\s+)?(?:also\s+)?"
    r"(?:help|suggest|recommend|explain|show|tell|give|provide|brainstorm))\b",
    re.IGNORECASE,
)
_EXPLICIT_DECISION_RE = re.compile(
    r"\b(?:decid\w*|chos(?:e|en)|select(?:ed|s)?|opt(?:ed|s)?|agree(?:d|s)?|"
    r"commit(?:ted|s)?|reject(?:ed|s)?)\b",
    re.IGNORECASE,
)
_FIRST_PERSON_NONACTUAL_CLAUSE_RE = re.compile(
    r"(?<!\w)(?P<subject>I|we)(?!\w)(?:"
    r"(?:[’']m|[’']re|\s+am|\s+are)\s+"
    r"(?:trying|attempting|planning|hoping|aiming|looking|working)"
    r"|(?:[’']ve|\s+have)\s+(?:tried|attempted|planned|hoped|aimed)"
    r"|(?:[’']d|\s+would)\s+like"
    r"|\s+(?:try|tried|attempt|attempted|want|wanted|need|needed|plan|planned|"
    r"intend|intended|hope|hoped|aim|aimed|look|looked|work|worked)"
    r")\s+to\b(?P<body>.{0,400})",
    re.IGNORECASE,
)
_NONACTUAL_CLAUSE_BOUNDARY_RE = re.compile(
    r"[!?;]|\.(?=\s|$)|,(?=\s*(?:but|and|so|although|though|however)\b)",
    re.IGNORECASE,
)
_NONACTUAL_PREDICATE_RE = re.compile(
    r"(?:tr(?:y|ied|ies|ying)|attempt|plan|intend|want|need|hope|aim|look|work|seek|request|ask|"
    r"propos|consider|evaluat|explor)", re.IGNORECASE,
)


def _has_explicit_condition(evidence_quote: str) -> bool:
    """Recognize contingent clauses without treating indirect questions as conditions."""
    without_indirect_questions = _INDIRECT_QUESTION_IF_RE.sub("", evidence_quote)
    return _EXPLICIT_CONDITION_RE.search(without_indirect_questions) is not None


def _has_uncertainty(evidence_quote: str) -> bool:
    """Recognize claim modality without treating polite requests as claims."""
    without_polite_requests = _POLITE_REQUEST_MODAL_RE.sub("", evidence_quote)
    return _UNCERTAINTY_RE.search(without_polite_requests) is not None


def _validate_condition(epistemic_type: str, condition: str | None, evidence_quote: str) -> None:
    """Require explicit conditional syntax to remain typed and auditable."""
    has_explicit_condition = _has_explicit_condition(evidence_quote)
    if has_explicit_condition and epistemic_type != "conditional":
        raise ValueError("an explicit if/unless condition requires epistemic_type conditional")
    if epistemic_type == "conditional":
        if not condition or condition not in evidence_quote:
            raise ValueError("conditional claims require a verbatim condition from evidence_quote")
    elif condition is not None:
        raise ValueError("condition is only valid when epistemic_type is conditional")


def _validate_modality(fact_type: str, epistemic_type: str, evidence_quote: str,
                       *, subject: str, predicate: str, object_value: str) -> None:
    """Reject common uncertainty and contingent-action category collapses."""
    uncertain = _has_uncertainty(evidence_quote)
    if uncertain and epistemic_type not in {"hypothetical", "conditional"}:
        raise ValueError("might/may/could claims require hypothetical or conditional epistemic_type")
    if (
        fact_type == "decision"
        and (uncertain or _has_explicit_condition(evidence_quote))
        and _EXPLICIT_DECISION_RE.search(evidence_quote) is None
    ):
        raise ValueError("a contingent future action is not a decision without an explicit choice or commitment")
    if not _NONACTUAL_PREDICATE_RE.search(predicate):
        for match in _FIRST_PERSON_NONACTUAL_CLAUSE_RE.finditer(evidence_quote):
            body = match.group("body")
            boundary = _NONACTUAL_CLAUSE_BOUNDARY_RE.search(body)
            if boundary is not None:
                body = body[:boundary.start()]
            subject_in_clause = (
                match.group("subject").casefold() == subject.strip().casefold()
                or _mentioned(subject, body)
            )
            if subject_in_clause and _mentioned(object_value, body):
                raise ValueError(
                    "an attempt or intention requires a predicate that preserves "
                    "the non-completed speech act"
                )


def _validate_fact_source_support(fact: ExtractedFact, source: str) -> None:
    claim_passage = _supporting_claim_passage(fact.evidence_quote or "", source)
    evidence_passage = _supporting_passage(fact.evidence_quote or "", source)
    if claim_passage is None or evidence_passage is None:
        raise ValueError("evidence_quote must be copied verbatim from the source")
    if not _mentioned(fact.subject, claim_passage) or not _mentioned(fact.object, claim_passage):
        raise ValueError("subject and object must occur in evidence_quote")
    _validate_condition(fact.epistemic_type, fact.condition, claim_passage)
    _validate_modality(fact.fact_type, fact.epistemic_type, claim_passage,
                       subject=fact.subject, predicate=fact.predicate,
                       object_value=fact.object)
    quantity = validate_extracted_quantity(
        fact.quantity,
        object_value=fact.object,
        claim_passage=claim_passage,
    )
    if fact.quantity is not None and quantity is None:
        logger.warning(
            "extraction_quantity_discarded",
            subject=fact.subject,
            reason="Quantity is not an exact supported value and unit in the claim object",
        )
    fact.quantity = quantity
    fact.evidence_quote = evidence_passage


def _validate_relationship_source_support(
    relationship: ExtractedRelationship, source: str
) -> None:
    claim_passage = _supporting_claim_passage(
        relationship.evidence_quote or "", source
    )
    evidence_passage = _supporting_passage(
        relationship.evidence_quote or "", source
    )
    if claim_passage is None or evidence_passage is None:
        raise ValueError("relationship evidence_quote must be copied verbatim from the source")
    if (
        not _mentioned(relationship.source_entity, claim_passage)
        or not _mentioned(relationship.target_entity, claim_passage)
    ):
        raise ValueError("relationship endpoints must occur in evidence_quote")
    _validate_condition(
        relationship.epistemic_type, relationship.condition, claim_passage
    )
    _validate_modality("fact", relationship.epistemic_type, claim_passage,
                       subject=relationship.source_entity,
                       predicate=relationship.relationship_type,
                       object_value=relationship.target_entity)
    relationship.evidence_quote = evidence_passage


class _CitedFact(ExtractedFact):
    """Built-in providers must return source support or retry validation."""

    fact_type: Literal["fact", "decision", "preference"] = "fact"
    polarity: Literal["positive", "negative"] = Field(
        description=ExtractedFact.model_fields["polarity"].description
    )

    evidence_quote: str = Field(
        min_length=1,
        description=ExtractedFact.model_fields["evidence_quote"].description,
    )

class _CitedRelationship(ExtractedRelationship):
    polarity: Literal["positive", "negative"] = Field(
        description=ExtractedFact.model_fields["polarity"].description
    )
    evidence_quote: str = Field(min_length=1, description=ExtractedFact.model_fields["evidence_quote"].description)
    epistemic_type: str = Field(description=ExtractedFact.model_fields["epistemic_type"].description)


def _nonactual_cue_predicate(prefix: str) -> str | None:
    folded = prefix.casefold()
    cues = (
        (r"\bwould\s+like\b", "wants_to"),
        (r"\btrying\b", "trying_to"),
        (r"\btried\b", "tried_to"),
        (r"\btry\b", "tries_to"),
        (r"\battempting\b", "attempting_to"),
        (r"\battempted\b", "attempted_to"),
        (r"\battempt\b", "attempts_to"),
        (r"\bplanning\b|\bplanned\b|\bplan\b", "plans_to"),
        (r"\bintended\b|\bintend\b", "intends_to"),
        (r"\bwanted\b|\bwant\b", "wants_to"),
        (r"\bneeded\b|\bneed\b", "needs_to"),
        (r"\bhoping\b|\bhoped\b|\bhope\b", "hopes_to"),
        (r"\baiming\b|\baimed\b|\baim\b", "aims_to"),
        (r"\blooking\b|\blooked\b|\blook\b", "looking_to"),
        (r"\bworking\b|\bworked\b|\bwork\b", "working_to"),
    )
    return next((predicate for pattern, predicate in cues if re.search(pattern, folded)), None)


def _recover_omitted_nonactual_targets(
    extraction: ExtractionResult, source: str
) -> list[_CitedFact]:
    """Recover one exact target from a literal nonactual source clause.

    This is intentionally narrower than general semantic extraction. It uses
    only an entity name already returned by the model, requires that name after
    the clause's first action verb, and does nothing when a qualified target
    claim already exists.
    """
    recovered = []
    for match in _FIRST_PERSON_NONACTUAL_CLAUSE_RE.finditer(source):
        prefix = source[match.start():match.start("body")]
        cue = _nonactual_cue_predicate(prefix)
        if cue is None:
            continue
        body = match.group("body")
        boundary = _NONACTUAL_CLAUSE_BOUNDARY_RE.search(body)
        clause_body = body if boundary is None else body[:boundary.start()]
        candidates = []
        for entity in extraction.entities:
            position = clause_body.casefold().find(entity.name.casefold())
            if position >= 0:
                candidates.append((position, -len(entity.name), entity))
        if not candidates:
            continue
        position, _, target = min(candidates, key=lambda item: (item[0], item[1]))
        action_match = re.search(r"\b([A-Za-z][A-Za-z0-9_-]*)\b", clause_body[:position])
        if action_match is None:
            continue
        subject = match.group("subject")
        if any(
            fact.subject.casefold() == subject.casefold()
            and fact.object.casefold() == target.name.casefold()
            and _NONACTUAL_PREDICATE_RE.search(fact.predicate)
            for fact in extraction.facts
        ) or any(
            relationship.target_entity.casefold() == target.name.casefold()
            and _NONACTUAL_PREDICATE_RE.search(relationship.relationship_type)
            for relationship in extraction.relationships
        ):
            continue
        quote_end = (
            match.start("body") + boundary.start()
            if boundary is not None
            else match.end()
        )
        quote = source[match.start():quote_end].strip()
        explicit_condition = _EXPLICIT_CONDITION_RE.search(quote)
        condition = (
            quote[explicit_condition.start():].strip()
            if explicit_condition is not None
            else None
        )
        try:
            fact = _CitedFact(
                subject=subject,
                object_entity_type=target.entity_type,
                predicate=f"{cue}_{action_match.group(1).casefold()}",
                object=target.name,
                polarity="positive",
                evidence_quote=quote,
                confidence=1.0,
                fact_type="fact",
                scope=target.scope,
                epistemic_type="conditional" if condition is not None else "observed",
                condition=condition,
                temporal_intent="assertion",
            )
            _validate_fact_source_support(fact, source)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "extraction_attempt_target_not_recovered", reason=str(exc)
            )
            continue
        recovered.append(fact)
    return recovered


_EXACT_DIMENSIONLESS_ATTRIBUTE_RE = re.compile(
    r"(?<!\w)(?P<determiner>my|our|his|her|their|its|the)\s+"
    r"(?P<attribute>(?:[A-Za-z][A-Za-z'-]*\s+){0,3}"
    r"(?:score|count|rating|level))\s+"
    r"(?P<predicate>is|was|equals?|equaled|reached)\s+"
    r"(?P<value>[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+))"
    r"(?P<terminal>[.!?])?(?=\s|$)",
    re.IGNORECASE,
)


def _recover_exact_dimensionless_attributes(
    extraction: ExtractionResult,
    source: str,
    *,
    role: str | None,
) -> tuple[list[ExtractedEntity], list[_CitedFact]]:
    """Recover narrow source-literal numeric attributes omitted by the model.

    Recovery is limited to user-authored, sentence-level score/count/rating/level
    assertions with one terminal exact decimal. The ordinary fact, quantity and
    closed-reference validators still run before anything is admitted.
    """
    if role != "user":
        return [], []
    entities: list[ExtractedEntity] = []
    facts: list[_CitedFact] = []
    known_concepts = {
        entity.name.casefold()
        for entity in extraction.entities
        if entity.entity_type.casefold() == "concept"
    }
    for match in _EXACT_DIMENSIONLESS_ATTRIBUTE_RE.finditer(source):
        before = source[: match.start()].rstrip()
        after = source[match.end() :].lstrip()
        if before and before[-1] not in ".!?":
            continue
        if match.group("terminal") is None and after:
            continue
        attribute = match.group("attribute")
        source_text = match.group("value")
        exact_value = exact_decimal_string_from_quantity_text(source_text)
        if exact_value is None:
            continue
        if any(
            fact.subject.casefold() == attribute.casefold()
            and fact.quantity is not None
            and fact.quantity.source_text == source_text
            for fact in extraction.facts
        ):
            continue
        if attribute.casefold() not in known_concepts:
            entities.append(
                ExtractedEntity(name=attribute, entity_type="concept")
            )
            known_concepts.add(attribute.casefold())
        try:
            fact = _CitedFact(
                subject=attribute,
                subject_entity_type="concept",
                predicate=match.group("predicate").casefold(),
                object=source_text,
                quantity=ExtractedQuantity(
                    value=Decimal(exact_value),
                    unit="1",
                    source_text=source_text,
                ),
                polarity="positive",
                evidence_quote=match.group(0),
                confidence=1.0,
                fact_type="fact",
                epistemic_type="observed",
                temporal_intent="assertion",
            )
            _validate_fact_source_support(fact, source)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "extraction_dimensionless_attribute_not_recovered", reason=str(exc)
            )
            continue
        facts.append(fact)
    return entities, facts


def _enrich_missing_fact_quantities(
    extraction: ExtractionResult,
    source: str,
) -> int:
    """Attach one bounded source-derived measure to an existing grounded fact."""
    recovered = 0
    for fact in extraction.facts:
        if fact.quantity is not None:
            continue
        claim_passage = _supporting_claim_passage(fact.evidence_quote or "", source)
        if claim_passage is None:
            continue
        quantity = recover_exact_quantity_from_object(
            fact.object,
            claim_passage=claim_passage,
        )
        if quantity is not None:
            fact.quantity = quantity
            recovered += 1
    return recovered


_CONDITIONAL_QUANTIFIED_ACTION_RE = re.compile(
    r"(?<!\w)(?P<condition>(?:if|unless|provided\s+that|as\s+long\s+as|only\s+if)"
    r"\s+[^,!?;\n]{1,200}),\s*"
    r"(?P<subject>I|we)\s+(?P<modal>will|would)\s+"
    r"(?P<negative>not\s+)?(?P<verb>[A-Za-z][A-Za-z'-]*)\s+"
    r"(?P<object>[^.!?;\n]{1,300})(?P<terminal>[.!?])",
    re.IGNORECASE,
)


def _recover_conditional_quantified_actions(
    extraction: ExtractionResult,
    source: str,
    *,
    role: str | None,
) -> list[_CitedFact]:
    """Recover a complete first-person conditional action with one exact measure."""
    if role != "user":
        return []
    recovered: list[_CitedFact] = []
    for match in _CONDITIONAL_QUANTIFIED_ACTION_RE.finditer(source):
        before = source[: match.start()].rstrip()
        if before and before[-1] not in ".!?":
            continue
        subject = match.group("subject")
        object_value = match.group("object").strip()
        condition = match.group("condition")
        quantity = recover_exact_quantity_from_object(
            object_value,
            claim_passage=match.group(0),
        )
        if quantity is None:
            continue
        predicate = (
            f"{match.group('modal').casefold()}_"
            f"{match.group('verb').casefold()}"
        )
        polarity: Literal["positive", "negative"] = (
            "negative" if match.group("negative") else "positive"
        )
        if any(
            fact.subject.casefold() == subject.casefold()
            and fact.predicate.casefold() == predicate
            and fact.polarity == polarity
            and fact.epistemic_type == "conditional"
            and fact.quantity == quantity
            and (
                (passage := _supporting_claim_passage(
                    fact.evidence_quote or "", source
                ))
                is not None
                and match.group(0) in passage
            )
            for fact in extraction.facts
        ):
            continue
        try:
            fact = _CitedFact(
                subject=subject,
                predicate=predicate,
                object=object_value,
                quantity=quantity,
                polarity=polarity,
                evidence_quote=match.group(0),
                confidence=1.0,
                fact_type="fact",
                epistemic_type="conditional",
                condition=condition,
                temporal_intent="assertion",
            )
            _validate_fact_source_support(fact, source)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "extraction_conditional_quantity_not_recovered", reason=str(exc)
            )
            continue
        recovered.append(fact)
    return recovered

class _CitedExtractionResult(ExtractionResult):
    facts: list[_CitedFact] = Field(default_factory=list)  # type: ignore[assignment]
    relationships: list[_CitedRelationship] = Field(default_factory=list)  # type: ignore[assignment]

    @model_validator(mode="before")
    @classmethod
    def discard_malformed_claims(cls, value: Any) -> Any:
        """Keep one malformed claim from invalidating supported siblings."""
        if not isinstance(value, dict):
            return value
        cleaned = dict(value)
        for field_name, model in (
            ("facts", _CitedFact),
            ("relationships", _CitedRelationship),
        ):
            items = value.get(field_name)
            if not isinstance(items, list):
                continue
            admitted = []
            for index, item in enumerate(items):
                try:
                    model.model_validate(item)
                except (ValidationError, TypeError):
                    without_quantity = None
                    if field_name == "facts" and isinstance(item, dict) and "quantity" in item:
                        raw_quantity = item.get("quantity")
                        if (
                            isinstance(raw_quantity, dict)
                            and isinstance(raw_quantity.get("value"), float)
                            and isinstance(raw_quantity.get("source_text"), str)
                        ):
                            exact_value = exact_decimal_string_from_quantity_text(
                                raw_quantity["source_text"]
                            )
                            if exact_value is not None:
                                repaired = dict(item)
                                repaired["quantity"] = {
                                    **raw_quantity,
                                    "value": exact_value,
                                }
                                try:
                                    model.model_validate(repaired)
                                except (ValidationError, TypeError):
                                    pass
                                else:
                                    logger.info(
                                        "extraction_quantity_value_recovered",
                                        path=f"{field_name}[{index}].quantity.value",
                                        method="exact_source_text",
                                    )
                                    admitted.append(repaired)
                                    continue
                        candidate = dict(item)
                        candidate.pop("quantity")
                        try:
                            model.model_validate(candidate)
                        except (ValidationError, TypeError):
                            pass
                        else:
                            without_quantity = candidate
                    if without_quantity is not None:
                        logger.warning(
                            "extraction_quantity_discarded",
                            path=f"{field_name}[{index}].quantity",
                            reason="malformed quantity fields",
                        )
                        admitted.append(without_quantity)
                        continue
                    logger.warning(
                        "extraction_claim_discarded",
                        path=f"{field_name}[{index}]",
                        reason="malformed claim fields",
                    )
                else:
                    admitted.append(item)
            cleaned[field_name] = admitted
        return cleaned

    @model_validator(mode="after")
    def supported_closed_references(self, info: ValidationInfo):
        source = (info.context or {}).get("source_text")
        role = (info.context or {}).get("source_role")
        if source is None:
            source = _VALIDATION_SOURCE.get()
        if role is None:
            role = _VALIDATION_ROLE.get()
        if source is not None:
            supported_facts = []
            for index, fact in enumerate(self.facts):
                try:
                    _validate_fact_source_support(fact, source)
                except ValueError as exc:
                    logger.warning(
                        "extraction_claim_discarded",
                        path=f"facts[{index}]",
                        reason=str(exc),
                    )
                else:
                    supported_facts.append(fact)
            supported_relationships = []
            for index, relationship in enumerate(self.relationships):
                try:
                    _validate_relationship_source_support(relationship, source)
                except ValueError as exc:
                    logger.warning(
                        "extraction_claim_discarded",
                        path=f"relationships[{index}]",
                        reason=str(exc),
                    )
                else:
                    supported_relationships.append(relationship)
            self.facts = supported_facts
            self.relationships = supported_relationships
            enriched = _enrich_missing_fact_quantities(self, source)
            if enriched:
                logger.info(
                    "extraction_fact_quantities_recovered",
                    count=enriched,
                )
            recovered = _recover_omitted_nonactual_targets(self, source)
            if recovered:
                logger.info(
                    "extraction_nonactual_target_recovered", count=len(recovered)
                )
                self.facts.extend(recovered)
            recovered_entities, recovered_facts = (
                _recover_exact_dimensionless_attributes(self, source, role=role)
            )
            if recovered_facts:
                logger.info(
                    "extraction_dimensionless_attributes_recovered",
                    count=len(recovered_facts),
                )
                self.entities.extend(recovered_entities)
                self.facts.extend(recovered_facts)
            conditional_facts = _recover_conditional_quantified_actions(
                self, source, role=role
            )
            if conditional_facts:
                logger.info(
                    "extraction_conditional_quantities_recovered",
                    count=len(conditional_facts),
                )
                self.facts.extend(conditional_facts)
        fact_errors, relationship_errors = reference_errors_by_claim(self)
        closed_facts = []
        for index, (fact, errors) in enumerate(zip(self.facts, fact_errors, strict=True)):
            if errors:
                logger.warning(
                    "extraction_claim_discarded",
                    path=f"facts[{index}]",
                    reason="; ".join(errors),
                )
            else:
                closed_facts.append(fact)
        self.facts = closed_facts

        closed_relationships = []
        for index, (relationship, errors) in enumerate(
            zip(self.relationships, relationship_errors, strict=True)
        ):
            if errors:
                logger.warning(
                    "extraction_claim_discarded",
                    path=f"relationships[{index}]",
                    reason="; ".join(errors),
                )
            else:
                closed_relationships.append(relationship)
        self.relationships = closed_relationships
        return self

EXTRACTION_SYSTEM_PROMPT = """\
You are a knowledge extraction system. Your task is to extract structured \
information from conversation messages accurately and completely.

Extract the following from the provided text:

1. **Named Entities**: People, organizations, locations, products, concepts, \
and events mentioned in the text. Use the entity name exactly as it appears.

2. **Facts** (subject-predicate-object triples): Factual statements about \
entities. Each fact has:
   - A subject (an entity name from the text)
   - A predicate (the relationship or attribute, e.g., works_at, lives_in, \
role, likes, uses)
   - An object (the value or target entity)
   - A confidence score (0.0 to 1.0) reflecting how explicitly stated the \
fact is
   - A fact_type: use "fact" for general facts, "decision" for decisions \
made or communicated (e.g., "We decided to use PostgreSQL"), and \
"preference" for personal preferences expressed (e.g., "I prefer dark mode")
   - A polarity: "positive" when the proposition is affirmed or "negative" \
when it is denied, rejected, stopped, or stated with does not/never/no longer
   - An optional quantity for one unambiguous numeric amount in the object: copy \
the exact quantified phrase into source_text, its verbatim unit or symbol into \
unit, and its exact decimal value into value. Use unit "1" only when source_text \
is the bare number. Leave quantity null for ranges, approximations, locale decimal \
commas, scientific notation, or objects containing multiple numeric amounts. \
When the source states an amount about a target, keep the exact amount and target \
together in the fact object (for example, "$500 for the shelter"). Do not omit \
the amount to make the object entity-only, and do not detach the amount into an \
unrelated fact. Dates, times, versions, identifiers, addresses, phone numbers, \
model names, and ordinals are not quantities.

3. **Relationships** between entities: How entities relate to each other. \
Use a source-supported predicate such as lives_in, works_at, or uses. Do not \
force residence into part_of, or infer causation from co-occurrence. Include \
an evidence_quote, epistemic_type, and polarity for every relationship. Prefer a fact \
triple for a statement; do not repeat it as a separate relationship. A claim \
with an explicit numeric amount must be a fact, not a relationship, so its exact \
quantity can be preserved.

4. **Summary**: A brief 1-2 sentence summary of the message content.

5. **Temporal references**: If a fact involves a time reference (e.g., \
"yesterday", "last week", "in March 2024", "3 days ago"), include the raw \
temporal text in the temporal_ref field.

6. **Scope Classification**: For each entity and fact, classify the scope:
   - "personal" — about a specific individual's preferences, habits, or personal context
   - "project" — about a specific project, its decisions, tools, or deliverables
   - "organisation" — about organization-wide policies, structures, or shared context
   - "agent" — about a specific AI agent's working memory or internal reasoning
   - "system" — system-generated content such as summaries or organizer output
   - "sandbox" — temporary or experimental context intended for isolated testing
   This is a descriptive suggestion. It never overrides the caller's write scope.
   If the scope is unclear, leave it as null (the system will use a safe default).

7. **Epistemic Type**: For each fact, classify its epistemic_type:
   - "observed" — directly stated or witnessed ("I work at Google")
   - "asserted" — claimed as fact without direct evidence
   - "inferred" — derived from context ("Based on their questions, they know Python")
   - "hypothetical" — speculative or possible without an explicit condition
   - "conditional" — applies only if an explicitly stated condition is true
   - "unverified" — from untrusted or unverified source
   Default to "asserted" if unclear. For "conditional", copy the exact source \
span describing the condition into condition. Otherwise set condition to null.

8. **Temporal Intent**: For each fact, classify its temporal_intent:
   - "update" — this fact replaces a prior state (signals: "now", "changed to", \
"moved to", "switched to", "no longer", "left", "started", "recently")
   - "assertion" — this is a standalone claim with no indication it replaces \
prior knowledge
   If unclear, leave temporal_intent as null (the system will use a safe default).

IMPORTANT RULES:
- Every named fact subject and relationship endpoint must use a name listed in entities.
  Copy that entity name exactly; do not alternate between shortened and full names.
  Literal unresolved personal references such as "I", "we", or "they" may be used
  without listing them as named entities. Copy the literal reference; do not invent a speaker name.
  Relationship endpoints are entity names, not phrases combining predicates and objects.
  If the same name identifies different entity types, include subject_entity_type,
  object_entity_type, source_entity_type, or target_entity_type to identify the intended listed entity.
- Relationship claims must preserve negations, uncertainty, and conditions just as facts do.
  Use conditional or hypothetical for possible relationships; never convert them to current reality.
- Polarity is independent of fact_type and epistemic_type. A dislike is a \
  negative preference; a rejected option is a negative decision. Keep the \
  predicate about the underlying relation and express denial in polarity.
- An action that will occur only if a future condition becomes true is a \
  conditional fact, not a decision, unless the source separately states that \
  the choice or commitment has already been made.
- Include an evidence_quote for every fact: copy the complete supporting source \
sentences verbatim, including negation, conditions, exceptions, and time references.
- Subject and object must occur in the supporting text. Keep object values as \
written rather than normalizing or paraphrasing them.
- A quantity source_text must be contained in that fact's object and evidence. \
Do not convert units, infer a currency from a symbol, or attach a number from \
another part of the sentence.
- Preserve the semantic target with an explicit amount. For example, "raised \
$500 for the shelter" should have an object containing "$500 for the shelter" \
and quantity source_text "$500", rather than an entity-only object that loses \
the amount.
- Emit every claim with an explicit numeric amount as a fact. Relationships \
cannot carry quantity metadata and must not replace the quantified fact.
- Quantity metadata is only for a measured or counted claim value. Do not attach \
it to dates, times, versions, identifiers, addresses, phone numbers, model names, \
or ordinals even when they contain a decimal-looking token.
- Preserve explicit dimensionless counts as quantities. For a possessive numeric \
attribute such as "My final score was 3", list "final score" as a concept entity, \
use that exact entity as the subject, and use the exact number as the fact object \
instead of inventing "I" as a source mention.
- Using something does not imply preferring it. One occurrence does not imply \
a habit. Multiple values can coexist (e.g., liking tea and coffee).
- Set replaces_object only for an explicit replacement of a named previous value \
("switched from Slack to Signal"). "Now also uses Signal" does not replace Slack. \
Copy the previous value exactly from the supporting passage.
- Preserve conditions and uncertainty. Use conditional or hypothetical epistemic \
types when appropriate; do not turn a possible future into a current fact.
- Preserve attempts and intentions in the predicate. "I'm trying to set up ESLint" \
  can become trying_to_set_up, but it does not establish uses, adopted, configured, \
  or enforced. "I want/need/plan to use X" does not establish uses X. A pasted \
  example or a request for help does not establish adoption or completion.
- Attempts, failed attempts, intentions, wants, needs, and plans are durable state. \
  Do not omit them merely because the action is incomplete. "I've tried to install \
  CUDA" should produce tried_to_install; "I would like to use Redis if X" should \
  preserve wants_to_use and condition X. Relationships between tools inside an \
  attempted setup are also non-completed; do not emit used_with or configured_with.
  When an attempt has a count or failure detail, the attempted action target is \
  still required as its own claim; attempted_install_count=twice does not replace \
  tried_to_install=CUDA.
- Only extract information that is EXPLICITLY STATED or STRONGLY IMPLIED by \
the text.
- Do NOT infer facts that are not grounded in the source text.
- Do NOT fabricate entities or relationships not present in the text.
- Use entity names exactly as they appear in the text.
- Assign higher confidence (0.7-1.0) to explicitly stated facts and lower \
confidence (0.3-0.6) to implied ones.
"""

_ROLE_EXTRACTION_GUIDANCE = {
    "user": (
        "Prioritize explicit user state, preferences, decisions, tasks, relationships, "
        "and project facts. A request for a recommendation does not itself establish a preference."
    ),
    "assistant": (
        "Do not extract generic background knowledge, standalone recommendations, explanations, "
        "or illustrative examples as durable claims. Extract only durable conversation state: "
        "explicit assistant commitments or completed actions, and user/project facts the assistant "
        "explicitly attributes. A restatement is system-inferred evidence, not user corroboration. "
        "Return empty fact and relationship lists when the message contains no durable state."
    ),
    "system": (
        "Extract only durable policies, constraints, identities, and operating instructions. "
        "Do not extract examples or schema descriptions as claims."
    ),
    "tool": (
        "Extract source-supported tool results and task state. Do not promote logs, formatting, "
        "or examples into claims."
    ),
}

_ASSISTANT_EXTRACTION_SYSTEM_PROMPT = """\
You extract durable conversational memory from one historical assistant message.

Admit only:
- an explicit commitment or promise made by the assistant;
- an action the assistant explicitly says it completed;
- concrete user, project, task, or conversation state the assistant explicitly attributes.

You MUST extract admitted claims. For example, "I scheduled the meeting" is a
completed action and "I will send the agenda tomorrow" is a commitment. Preserve
the literal assistant subject `I`; do not replace it with a guessed name.

Do not catalog generic knowledge, explanations, examples, advice, book or product
descriptions, or standalone recommendations. A message that only answers a question,
explains a topic, or lists suggestions must return empty entities, facts, and
relationships. Include entities only when an admitted claim references them.

For every admitted fact or relationship, copy a complete verbatim evidence_quote
containing its subject, object or endpoint names, negation, conditions, and time
qualifiers. Preserve semantic polarity and use conditional or hypothetical for
uncertain claims. Literal references such as I, we, or they may remain unlisted;
never invent a speaker identity. Do not infer claims from prior conversation.
For one unambiguous numeric amount in a fact object, preserve the exact decimal,
verbatim quantified source_text, and verbatim unit or symbol. Do not convert units.
An optional one-sentence summary may describe the message without becoming a claim.
"""


def _extraction_prompt_for_role(role: str) -> str:
    """Add source-role admission policy without trusting role as prompt text."""
    normalized = role.strip().casefold()
    if normalized == "assistant":
        return _ASSISTANT_EXTRACTION_SYSTEM_PROMPT
    policy = _ROLE_EXTRACTION_GUIDANCE.get(
        normalized,
        "Extract only durable, source-supported state; omit examples and presentation text.",
    )
    return f"{EXTRACTION_SYSTEM_PROMPT}\nSOURCE MESSAGE ROLE: {normalized or 'unknown'}\n{policy}"


@runtime_checkable
class ExtractionProvider(Protocol):
    """Protocol for LLM-powered structured extraction.

    Implementations accept message content and return a structured
    ExtractionResult containing entities, facts, relationships, and
    an optional summary.
    """

    @property
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'openai', 'anthropic')."""
        ...

    @property
    def model_name(self) -> str:
        """Return the full model identifier (e.g., 'openai/gpt-4o-mini')."""
        ...

    async def extract(
        self, content: str, *, role: str = "user"
    ) -> ExtractionResult:
        """Extract structured information from message content.

        Args:
            content: The message text to extract from.
            role: The role of the message sender (e.g., 'user', 'assistant').

        Returns:
            ExtractionResult with entities, facts, relationships, and summary.
        """
        ...


class InstructorExtractionProvider:
    """ExtractionProvider using instructor for any supported LLM.

    Supports OpenAI, Anthropic, and Ollama backends through instructor's
    unified from_provider() API. Uses lazy client initialization to avoid
    API key validation at construction time.

    Args:
        provider_string: Provider/model string (e.g., 'openai/gpt-4o-mini',
            'anthropic/claude-3-5-sonnet-20241022', 'ollama/llama3.2',
            'bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0').
        model: Optional model id passed to client.create(). Required for the
            'bedrock' provider, whose instructor client is NOT pre-bound to a
            model by from_provider(). When None, the model is derived from the
            provider_string.
        max_retries: Number of instructor retries for schema validation failures.
        timeout: Timeout in seconds per extraction call.
        temperature: Sampling temperature passed to the provider.
    """

    def __init__(
        self,
        provider_string: str,
        *,
        model: str | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
        temperature: float = 0.0,
        reasoning_effort: Literal["none", "low", "medium", "high"] | None = None,
        api_key: SecretStr | None = None,
        base_url: str | None = None,
    ) -> None:
        self._provider_string = provider_string
        self._model = model
        self._max_retries = max_retries
        self._timeout = timeout
        self._temperature = temperature
        self._reasoning_effort = (
            "none" if reasoning_effort is None and self.provider_name == "ollama"
            else reasoning_effort
        )
        self._api_key = api_key
        self._base_url = base_url
        self._client: instructor.AsyncInstructor | None = None

    def _ensure_client(self) -> instructor.AsyncInstructor:
        """Lazily create the instructor async client on first use.

        This avoids API key validation at construction time, allowing
        the provider to be created without environment variables set.
        """
        if self._client is None:
            import instructor
            from dotenv import dotenv_values

            # SDK defaults read process variables but do not load .env. Resolve
            # only the selected provider's settings, without mutating os.environ.
            kwargs: dict = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key.get_secret_value()
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self.provider_name == "ollama":
                # Ollama's constrained structured-output path returns JSON in
                # message content. Tool mode can return the same valid JSON
                # without a tool envelope, which Instructor rejects.
                kwargs["mode"] = instructor.Mode.JSON
            provider_prefix = {"openai": "OPENAI", "anthropic": "ANTHROPIC"}.get(self.provider_name)
            if provider_prefix:
                local = dotenv_values(".env")
                key_name, url_name = f"{provider_prefix}_API_KEY", f"{provider_prefix}_BASE_URL"
                key = (
                    self._api_key.get_secret_value()
                    if self._api_key
                    else os.environ.get(key_name) or local.get(key_name)
                )
                url = self._base_url or os.environ.get(url_name) or local.get(url_name)
                if key:
                    kwargs["api_key"] = key
                if url:
                    kwargs["base_url"] = url

            self._client = instructor.from_provider(
                self._provider_string, async_client=True, **kwargs
            )
        return self._client

    @property
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'openai')."""
        return self._provider_string.split("/")[0]

    @property
    def model_name(self) -> str:
        """Return the full provider/model string."""
        return self._provider_string

    def _resolve_model_id(self) -> str | None:
        """Model id to pass to client.create().

        instructor.from_provider() pre-binds the model into the client for most
        providers (openai, anthropic, ...), but NOT for 'bedrock' -- there the
        model string is only used to pick a mode, so client.create() raises
        "Missing required parameter: modelId" unless we pass the model
        explicitly. Passing it for every provider is safe: create()'s model
        overrides the client default when both are set.
        """
        if self._model:
            return self._model
        if "/" in self._provider_string:
            return self._provider_string.split("/", 1)[1]
        return None

    async def extract(
        self, content: str, *, role: str = "user"
    ) -> ExtractionResult:
        """Extract structured information from message content.

        Args:
            content: The message text to extract from.
            role: The role of the message sender.

        Returns:
            ExtractionResult with extracted entities, facts, relationships,
            and summary. Raises ExtractionError on provider failure or timeout
            so the pipeline can distinguish an error from valid empty output.
        """
        try:
            client = self._ensure_client()
            create_kwargs: dict = {
                "response_model": _CitedExtractionResult,
                "messages": [
                    {"role": "system", "content": _extraction_prompt_for_role(role)},
                    # This is historical text to inspect, regardless of who
                    # authored the event. Sending an assistant event as an
                    # assistant chat turn asks the model to continue it and
                    # can yield an empty response. Event.role remains the
                    # authoritative source classification downstream.
                    {"role": "user", "content": content},
                ],
                "max_retries": self._max_retries,
                "temperature": self._temperature,
            }
            if self._reasoning_effort is not None:
                create_kwargs["reasoning_effort"] = self._reasoning_effort
            model_id = self._resolve_model_id()
            if model_id:
                create_kwargs["model"] = model_id
            # Instructor treats validation context as Jinja template context for
            # every prompt message. Keep grounding input task-local so literal
            # user code such as ``{{ variable }}`` reaches the model unchanged.
            source_token = _VALIDATION_SOURCE.set(content)
            role_token = _VALIDATION_ROLE.set(role.strip().casefold())
            try:
                result = await asyncio.wait_for(
                    client.create(**create_kwargs), timeout=self._timeout
                )
            finally:
                _VALIDATION_ROLE.reset(role_token)
                _VALIDATION_SOURCE.reset(source_token)
            return result
        except Exception as exc:
            logger.error(
                "extraction_failed",
                provider=self._provider_string,
                content_length=len(content),
                error_type=type(exc).__name__,
            )
            reason = extraction_failure_code(exc)
            raise ExtractionError(f"Extraction failed ({reason})", reason_code=reason) from exc


def create_extraction_provider(
    config: ExtractionConfig,
) -> ExtractionProvider:
    """Factory function to create an ExtractionProvider from config.

    Builds the provider string from config.provider and config.model,
    then creates an InstructorExtractionProvider instance.

    Args:
        config: ExtractionConfig with provider, model, max_retries, timeout.

    Returns:
        An ExtractionProvider instance (InstructorExtractionProvider).
    """
    provider_string = f"{config.provider}/{config.model}"
    return InstructorExtractionProvider(
        provider_string,
        model=config.model,
        max_retries=config.max_retries,
        timeout=config.timeout,
        temperature=config.temperature,
        reasoning_effort=config.reasoning_effort,
        api_key=config.api_key,
        base_url=config.base_url,
    )
