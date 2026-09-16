"""Validate source membership and preserve evidence passages.

Mention/citation checks reject unsupported text; they do not prove that a
model's predicate is entailed. Materialized content keeps the source passage
so the reader can see negations, conditions, and other qualifications.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata

import structlog

from prme.ingestion.schema import ExtractedQuantity, ExtractionResult

logger = structlog.get_logger(__name__)

_SOURCE_QUOTE_TRANSLATION = str.maketrans({
    "'": '"', "‘": '"', "’": '"', "“": '"', "”": '"',
})


def canonical_source_quote(quote: str, source: str) -> str | None:
    """Return the exact source span for text differing only in quote marks."""
    if not quote.strip():
        return None
    if quote in source:
        return quote
    normalized_quote = quote.translate(_SOURCE_QUOTE_TRANSLATION)
    normalized_source = source.translate(_SOURCE_QUOTE_TRANSLATION)
    start = normalized_source.find(normalized_quote)
    if start < 0:
        return None
    return source[start:start + len(quote)]


def _mentioned(value: str, text: str) -> bool:
    """Match a nonempty mention without matching Ann inside Marianne."""
    value = value.strip()
    if not value:
        return False
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text, re.IGNORECASE) is not None


_QUANTITY_NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)(?![\d.,])"
)
_INEXACT_QUANTITY_RE = re.compile(
    r"(?:[~≈<>−()]|\b(?:about|approximately|around|roughly|nearly|almost|between|"
    r"more\s+than|less\s+than|at\s+least|at\s+most|over|under)\b)",
    re.IGNORECASE,
)


def normalize_quantity_unit(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def _unit_mentioned(unit: str, text: str) -> bool:
    if unit == "1":
        return True
    if any(character.isalnum() for character in unit):
        # Unit letters may directly follow a number ("5kg") but must not be
        # embedded in another alphabetic word.
        return re.search(
            r"(?<![^\W\d_])" + re.escape(unit) + r"(?![^\W\d_])",
            text,
            re.IGNORECASE,
        ) is not None
    return unit in text


def validate_extracted_quantity(
    quantity: ExtractedQuantity | None,
    *,
    object_value: str,
    claim_passage: str,
) -> ExtractedQuantity | None:
    """Return a quantity only when its decimal, unit, and object are source-bound.

    Supported notation is one signed decimal token with optional comma thousands
    separators. Ranges, scientific notation, locale decimal commas, and multiple
    numbers are intentionally rejected instead of guessed.
    """
    if quantity is None:
        return None
    source_text = canonical_source_quote(quantity.source_text, claim_passage)
    if source_text is None or canonical_source_quote(source_text, object_value) is None:
        return None
    if _INEXACT_QUANTITY_RE.search(source_text):
        return None
    matches = list(_QUANTITY_NUMBER_RE.finditer(source_text))
    if len(matches) != 1:
        return None
    token = matches[0].group(0)
    outside_number = source_text[:matches[0].start()] + source_text[matches[0].end():]
    if re.search(r"\d", outside_number):
        return None
    try:
        parsed = Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None
    unit = quantity.unit.strip()
    if parsed != quantity.value or not _unit_mentioned(unit, source_text):
        return None
    if normalize_quantity_unit(unit) == "1" and source_text.strip() != token:
        return None
    return quantity.model_copy(update={"source_text": source_text, "unit": unit})


def _supporting_passage(quote: str, source: str) -> str | None:
    """Expand a real citation to paragraph boundaries to retain qualifiers.

    A model can quote a genuine substring while omitting a trailing condition.
    Keep its surrounding paragraph(s), without inventing a sentence boundary.
    Repeated quotations conservatively retain the complete source.
    """
    quote = canonical_source_quote(quote, source)
    if quote is None:
        return None
    start = source.find(quote)
    if source.find(quote, start + 1) != -1:
        return source
    end = start + len(quote)
    separators = list(re.finditer(r"\n\s*\n", source))
    left = max((m.end() for m in separators if m.end() <= start), default=0)
    right = min((m.start() for m in separators if m.start() >= end), default=len(source))
    return source[left:right]


_QUALIFYING_CONTINUATION_RE = re.compile(
    r"(?i)^(?:but\s+)?(?:only\s+if|unless|provided\s+that|as\s+long\s+as|"
    r"never|except(?:\s+if|\s+when|\s+for)?|however\b)"
)


def _supporting_claim_passage(quote: str, source: str) -> str | None:
    """Return the source sentence(s) that semantically qualify one claim.

    Stored evidence remains paragraph-complete via :func:`_supporting_passage`.
    Claim classification needs a narrower span so an unrelated question or
    hypothetical elsewhere in the paragraph cannot contaminate the claim. A
    following sentence that begins with a condition or exception remains part
    of the span so a shortened citation cannot erase a trailing qualifier.
    """
    quote = canonical_source_quote(quote, source)
    if quote is None:
        return None
    start = source.find(quote)
    if source.find(quote, start + 1) != -1:
        return source
    end = start + len(quote)

    paragraph_breaks = list(re.finditer(r"\n\s*\n", source))
    paragraph_start = max(
        (match.end() for match in paragraph_breaks if match.end() <= start),
        default=0,
    )
    paragraph_end = min(
        (match.start() for match in paragraph_breaks if match.start() >= end),
        default=len(source),
    )
    paragraph = source[paragraph_start:paragraph_end]
    relative_start = start - paragraph_start
    relative_end = end - paragraph_start
    boundaries = list(re.finditer(r"[.!?](?:[\"')\]]*)?(?=\s+|$)", paragraph))

    sentence_start = max(
        (match.end() for match in boundaries if match.end() <= relative_start),
        default=0,
    )
    while sentence_start < len(paragraph) and paragraph[sentence_start].isspace():
        sentence_start += 1
    sentence_end = next(
        (match.end() for match in boundaries if match.end() >= relative_end),
        len(paragraph),
    )

    while sentence_end < len(paragraph):
        continuation_start = sentence_end
        while (
            continuation_start < len(paragraph)
            and paragraph[continuation_start].isspace()
        ):
            continuation_start += 1
        if not _QUALIFYING_CONTINUATION_RE.match(paragraph[continuation_start:]):
            break
        sentence_end = next(
            (
                match.end()
                for match in boundaries
                if match.end() > continuation_start
            ),
            len(paragraph),
        )

    return paragraph[sentence_start:sentence_end].strip()


def validate_grounding(
    result: ExtractionResult, source_text: str
) -> ExtractionResult:
    """Filter out extracted items not grounded in source text.

    Verify exact citations and boundary-aware case-insensitive mentions.

    Filtering rules:
    - Entities: name must occur as a complete mention in the source.
    - Facts: subject and object must occur in a supporting source passage.
      Citations expand to paragraphs to retain omitted qualifiers. Custom
      providers without citations use the complete source as their support.
    - Relationships: the same citation expansion and complete-mention rules
      apply to both endpoints. Relationship entailment remains unverified.
    - Summary: preserved as model output, not treated as verified evidence.

    Args:
        result: The ExtractionResult from LLM extraction.
        source_text: The original message text to validate against.

    Returns:
        A new ExtractionResult with ungrounded items removed.
    """

    # Filter entities: name must appear in source
    grounded_entities = []
    for entity in result.entities:
        if _mentioned(entity.name, source_text):
            grounded_entities.append(entity)
        else:
            logger.warning(
                "grounding_entity_discarded",
                entity_name=entity.name,
                entity_type=entity.entity_type,
                reason="Entity name not found in source text",
            )

    # A citation proves source membership, not semantic entailment. Preserve
    # the source passage as the fact content instead of trusting a lossy triple.
    grounded_facts = []
    for fact in result.facts:
        passage = (
            _supporting_passage(fact.evidence_quote, source_text)
            if fact.evidence_quote is not None else source_text
        )
        if passage and _mentioned(fact.subject, passage) and _mentioned(fact.object, passage):
            replacement = fact.replaces_object
            if replacement is not None and not _mentioned(replacement, passage):
                replacement = None
            condition = fact.condition
            if condition is not None and condition not in passage:
                logger.warning(
                    "grounding_condition_discarded",
                    subject=fact.subject,
                    reason="Condition is not a verbatim span of the supporting passage",
                )
                condition = None
            epistemic_type = fact.epistemic_type
            if epistemic_type == "conditional" and condition is None:
                # An unevaluable conditional must not enter default retrieval as
                # an ordinary assertion. Hypothetical is the conservative legacy
                # fallback until a provider supplies the actual condition.
                epistemic_type = "hypothetical"
            quantity = validate_extracted_quantity(
                fact.quantity,
                object_value=fact.object,
                claim_passage=passage,
            )
            if fact.quantity is not None and quantity is None:
                logger.warning(
                    "grounding_quantity_discarded",
                    subject=fact.subject,
                    reason="Quantity is not an exact supported value and unit in the claim object",
                )
            grounded_facts.append(fact.model_copy(update={
                "evidence_quote": passage,
                "replaces_object": replacement,
                "condition": condition,
                "epistemic_type": epistemic_type,
                "quantity": quantity,
            }))
        else:
            logger.warning(
                "grounding_fact_discarded",
                subject=fact.subject,
                predicate=fact.predicate,
                object=fact.object,
                reason="Missing source support for citation, subject, or object",
            )

    # Filter relationships: both endpoints must appear in the cited passage
    grounded_relationships = []
    for rel in result.relationships:
        passage = _supporting_passage(rel.evidence_quote, source_text) if rel.evidence_quote is not None else source_text
        source_grounded = passage is not None and _mentioned(rel.source_entity, passage)
        target_grounded = passage is not None and _mentioned(rel.target_entity, passage)
        if passage is not None and source_grounded and target_grounded:
            condition = rel.condition
            if condition is not None and condition not in passage:
                logger.warning(
                    "grounding_relationship_condition_discarded",
                    source_entity=rel.source_entity,
                    target_entity=rel.target_entity,
                    reason="Condition is not a verbatim span of the supporting passage",
                )
                condition = None
            epistemic_type = rel.epistemic_type
            if epistemic_type == "conditional" and condition is None:
                epistemic_type = "hypothetical"
            grounded_relationships.append(rel.model_copy(update={
                "evidence_quote": passage,
                "condition": condition,
                "epistemic_type": epistemic_type,
            }))
        else:
            ungrounded_side = (
                "source_entity"
                if not source_grounded
                else "target_entity"
            )
            logger.warning(
                "grounding_relationship_discarded",
                source_entity=rel.source_entity,
                target_entity=rel.target_entity,
                relationship_type=rel.relationship_type,
                reason=f"{ungrounded_side} not found in source text",
            )

    return ExtractionResult(
        entities=grounded_entities,
        facts=grounded_facts,
        relationships=grounded_relationships,
        summary=result.summary,
    )
