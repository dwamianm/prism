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
    r"(?:[~≈<>−±()]|\b(?:about|approximately|around|roughly|nearly|almost|circa|"
    r"estimated?|between|from|up\s+to|more\s+than|less\s+than|no\s+more\s+than|"
    r"no\s+less\s+than|at\s+least|at\s+most|over|under)\b)",
    re.IGNORECASE,
)
_INEXACT_QUANTITY_PREFIX_RE = re.compile(
    r"(?:[~≈<>−±]\s*|\b(?:about|approximately|around|roughly|nearly|almost|circa|"
    r"estimated?|between|from|up\s+to|more\s+than|less\s+than|no\s+more\s+than|"
    r"no\s+less\s+than|at\s+least|at\s+most|over|under)\s*)$",
    re.IGNORECASE,
)
_RANGE_BEFORE_QUANTITY_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:[-–—]|\bto)\s*(?:[$€£¥]\s*)?$",
    re.IGNORECASE,
)
_BETWEEN_PREFIX_RE = re.compile(
    r"\bbetween\b[^.!?;,\n]{0,40}\band\s*(?:[$€£¥]\s*)?$",
    re.IGNORECASE,
)
_RANGE_AFTER_QUANTITY_RE = re.compile(
    r"^\s*(?:[-–—]|to\b)\s*(?:[$€£¥]\s*)?\d",
    re.IGNORECASE,
)
_RECOVERABLE_QUANTITY_UNIT_RE = re.compile(
    r"^(?P<space>\s*)(?P<unit>"
    r"USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|"
    r"%|percent(?:age)?s?|"
    r"mm|cm|km|millimet(?:er|re)s?|centimet(?:er|re)s?|met(?:er|re)s?|"
    r"kilomet(?:er|re)s?|"
    r"mg|kg|milligrams?|grams?|kilograms?|"
    r"ml|millilit(?:er|re)s?|lit(?:er|re)s?|gallons?|"
    r"volts?|amps?|amperes?|watts?|"
    r"bytes?|KB|MB|GB|TB|"
    r"packages?|items?|files?|records?|requests?|users?|tickets?|tasks?|units?|people"
    r")(?=$|[^A-Za-z/])",
    re.IGNORECASE,
)
_CURRENCY_SYMBOLS = frozenset("$€£¥")


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


def exact_decimal_string_from_quantity_text(source_text: str) -> str | None:
    """Recover an exact decimal token from a supported quantified phrase.

    This reads the source phrase rather than converting a provider-supplied
    float. Ordinary grounding still has to prove the phrase occurs in both the
    claim object and evidence before the value can survive.
    """
    if _INEXACT_QUANTITY_RE.search(source_text):
        return None
    matches = list(_QUANTITY_NUMBER_RE.finditer(source_text))
    if len(matches) != 1:
        return None
    match = matches[0]
    outside_number = source_text[: match.start()] + source_text[match.end() :]
    if re.search(r"\d", outside_number):
        return None
    token = match.group(0).replace(",", "")
    try:
        parsed = Decimal(token)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    return token


def recover_exact_quantity_from_object(
    object_value: str,
    *,
    claim_passage: str,
) -> ExtractedQuantity | None:
    """Derive one supported exact measure from an already grounded object.

    This is a bounded provider-independent repair, not general unit inference.
    It copies a currency symbol/code or a unit from a conservative lexicon and
    delegates approximation, range, value, object, and evidence checks to the
    ordinary quantity validator.
    """
    numbers = list(_QUANTITY_NUMBER_RE.finditer(object_value))
    if len(numbers) != 1:
        return None
    number = numbers[0]
    if (
        number.start() >= 2
        and object_value[number.start() - 1] in {"-", ","}
        and object_value[number.start() - 2].isalnum()
    ):
        return None
    start, end = number.span()
    unit: str | None = None
    prefix = object_value[:start]
    symbol_index = len(prefix.rstrip()) - 1
    if symbol_index >= 0 and prefix[symbol_index] in _CURRENCY_SYMBOLS:
        start = symbol_index
        unit = prefix[symbol_index]
    else:
        unit_match = _RECOVERABLE_QUANTITY_UNIT_RE.match(object_value[end:])
        if unit_match is not None:
            unit = unit_match.group("unit")
            end += unit_match.end()
    if unit is None:
        return None
    if (
        object_value[:start].rstrip().endswith("(")
        or object_value[end:].lstrip().startswith(")")
    ):
        return None
    source_text = object_value[start:end]
    exact_value = exact_decimal_string_from_quantity_text(source_text)
    if exact_value is None:
        return None
    try:
        quantity = ExtractedQuantity(
            value=Decimal(exact_value),
            unit=unit,
            source_text=source_text,
        )
    except (ValueError, TypeError):
        return None
    return validate_extracted_quantity(
        quantity,
        object_value=object_value,
        claim_passage=claim_passage,
    )


def recover_exact_quantity_prefix(
    text: str,
    *,
    claim_passage: str,
) -> ExtractedQuantity | None:
    """Derive one exact bounded measure from the beginning of source text.

    This helper supports deterministic recovery of an omitted measured action.
    It consumes only a leading currency phrase or decimal plus a unit from the
    same conservative lexicon as :func:`recover_exact_quantity_from_object`.
    Remaining clause text is ignored only after that exact phrase is isolated;
    the ordinary validator still checks the phrase and its local qualifiers in
    the complete claim passage.
    """
    stripped = text.lstrip()
    if not stripped:
        return None
    number_start = 0
    if stripped[0] in _CURRENCY_SYMBOLS:
        number_start = 1
        while number_start < len(stripped) and stripped[number_start].isspace():
            number_start += 1
    number = _QUANTITY_NUMBER_RE.match(stripped, number_start)
    if number is None:
        return None
    start, end = number.span()
    if number_start:
        start = 0
    else:
        unit_match = _RECOVERABLE_QUANTITY_UNIT_RE.match(stripped[end:])
        if unit_match is None:
            return None
        end += unit_match.end()
    phrase = stripped[start:end]
    return recover_exact_quantity_from_object(
        phrase,
        claim_passage=claim_passage,
    )


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
    start = claim_passage.find(source_text)
    if start < 0:
        return None
    prefix = claim_passage[max(0, start - 48) : start]
    # Approximation cues only govern the current clause. This still catches a
    # model clipping "about" from source_text without letting an unrelated
    # earlier clause poison an exact amount.
    local_prefix = re.split(r"[.!?;,\n]", prefix)[-1]
    suffix = claim_passage[start + len(source_text) : start + len(source_text) + 24]
    if (
        _INEXACT_QUANTITY_PREFIX_RE.search(local_prefix)
        or _RANGE_BEFORE_QUANTITY_RE.search(local_prefix)
        or _BETWEEN_PREFIX_RE.search(local_prefix)
        or _RANGE_AFTER_QUANTITY_RE.search(suffix)
    ):
        return None
    exact_value = exact_decimal_string_from_quantity_text(source_text)
    if exact_value is None:
        return None
    try:
        parsed = Decimal(exact_value)
    except InvalidOperation:
        return None
    unit = quantity.unit.strip()
    if parsed != quantity.value or not _unit_mentioned(unit, source_text):
        return None
    if (
        normalize_quantity_unit(unit) == "1"
        and source_text.strip().replace(",", "") != exact_value
    ):
        return None
    return quantity.model_copy(update={"source_text": source_text, "unit": unit})


def _supporting_passage(quote: str, source: str) -> str | None:
    """Expand a real citation to paragraph boundaries to retain qualifiers.

    A model can quote a genuine substring while omitting a trailing condition.
    Keep its surrounding paragraph(s), without inventing a sentence boundary.
    Repeated quotations conservatively retain the complete source.
    """
    canonical_quote = canonical_source_quote(quote, source)
    if canonical_quote is None:
        return None
    start = source.find(canonical_quote)
    if source.find(canonical_quote, start + 1) != -1:
        return source
    end = start + len(canonical_quote)
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
    canonical_quote = canonical_source_quote(quote, source)
    if canonical_quote is None:
        return None
    start = source.find(canonical_quote)
    if source.find(canonical_quote, start + 1) != -1:
        return source
    end = start + len(canonical_quote)

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
