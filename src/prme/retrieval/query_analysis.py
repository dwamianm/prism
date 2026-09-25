"""Query analysis for the hybrid retrieval pipeline (Stage 1).

Transforms a raw query string into a QueryAnalysis with classified intent,
extracted entities, and resolved temporal signals. Uses dateparser + pattern
matching only -- no blocking LLM calls per RFC-0005 S3.
"""

from __future__ import annotations

import logging
import asyncio
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Literal, get_args
from uuid import uuid4

from prme._temporal import DATEPARSER_LOCK as _DATEPARSER_LOCK
from prme.retrieval.models import QueryAnalysis
from prme.types import QueryIntent, RetrievalMode

logger = logging.getLogger(__name__)

# --- Intent classification patterns ---

# Whether the entity checks or the temporal check runs first (issue #85).
QueryIntentOrder = Literal["entity_first", "temporal_first"]
QUERY_INTENT_ORDERS = frozenset(get_args(QueryIntentOrder))

# Keywords triggering ENTITY_LOOKUP intent.
_ENTITY_PREFIXES = re.compile(
    r"^\s*(who|what\s+is|what\s+are)\b", re.IGNORECASE
)

# Keywords triggering TEMPORAL intent.
_TEMPORAL_KEYWORDS = re.compile(
    r"\b(when|last|before|after|recent|recently|ago|yesterday|today|tomorrow"
    r"|earlier|later|since|until|prior)\b",
    re.IGNORECASE,
)

# Keywords triggering RELATIONAL intent.
_RELATIONAL_KEYWORDS = re.compile(
    r"\b(related\s+to|connected\s+to|linked\s+to|between|associated\s+with)\b",
    re.IGNORECASE,
)

# Keywords triggering FACTUAL intent.
_FACTUAL_KEYWORDS = re.compile(
    r"\b(what|how|why|does|is\s+it\s+true|fact|facts)\b",
    re.IGNORECASE,
)

# Patterns indicating aggregation/count queries that need broader candidate pools.
_AGGREGATION_KEYWORDS = re.compile(
    r"\b(how\s+many|how\s+much|how\s+often|total|count"
    r"|all\s+the\s+times|every\s+time|list\s+all|all\s+of\s+the)\b",
    re.IGNORECASE,
)

# ``how many`` introduces both set cardinality and elapsed-time questions.
# Explicit interval language needs temporal ranking and arithmetic, not
# exhaustive-set coverage warnings or broader count scans. A time unit alone is
# insufficient: "hours across both jobs" is still a sum over multiple records.
_TEMPORAL_QUANTITY_RE = re.compile(
    r"\bhow\s+(?:many\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)"
    r"|much\s+time)\b[^?.!]{0,50}\b(?:ago|passed|elapsed|before|between|since|until)\b",
    re.IGNORECASE,
)

# Heuristic for proper nouns: 1+ consecutive capitalized words not at
# sentence start. We anchor on "not after sentence-start" by checking
# that the match is not preceded by nothing or a sentence-ending punctuation.
_PROPER_NOUN_RE = re.compile(r"(?<!\A)(?<![.!?]\s)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)")

# Quoted strings as entity references.
_QUOTED_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')

# dateparser false positives: single words that dateparser interprets
# as day/month/time abbreviations (e.g. "me" -> Monday, "may" -> May,
# "hour" -> datetime). We only trust single-word dateparser matches if
# they are known temporal keywords from our own regex pattern.
_KNOWN_TEMPORAL_WORDS = frozenset({
    "yesterday", "today", "tomorrow", "ago", "last", "before", "after",
    "recent", "recently", "earlier", "later", "since", "until", "prior",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct",
    "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
})

# --- dateparser cost control (issue #61) ---
#
# search_dates() is the single most expensive call in the retrieval hot path:
# 70ms per query with no language pin (dateparser tries its full locale set)
# versus 0.3ms pinned to English. Two guards keep it off the critical path:
# a language pin, and a cue pre-gate so queries with no date-like token skip
# the call entirely.

# Default language set for temporal parsing. English is already assumed
# elsewhere in the pipeline (tantivy English stemming, the English stopword
# list in the aggregation scan). Pass languages=None to restore dateparser's
# auto-detection at its original cost.
DEFAULT_TEMPORAL_LANGUAGES: tuple[str, ...] = ("en",)

# Words that can appear in a multi-word temporal expression without being a
# temporal match on their own ("two weeks ago", "the day after tomorrow").
# The pre-gate needs them so it stays a superset of what dateparser finds.
_RELATIVE_TIME_WORDS = frozenset({
    "now", "next", "past", "coming", "upcoming", "tonight", "weekend",
    "day", "days", "night", "nights", "week", "weeks", "month", "months",
    "year", "years", "hour", "hours", "minute", "minutes", "second",
    "seconds", "morning", "afternoon", "evening", "midnight", "noon",
    "decade", "decades", "fortnight", "quarter", "century",
})

# A query is worth handing to dateparser only if it contains a digit or one of
# these words. Every signal the extractor keeps satisfies one of the two:
# matches with digits, single-word matches restricted to _KNOWN_TEMPORAL_WORDS,
# and multi-word matches, which need a unit or qualifier word to parse.
_TEMPORAL_GATE_WORDS = _KNOWN_TEMPORAL_WORDS | _RELATIVE_TIME_WORDS

_DIGIT_RE = re.compile(r"\d")
_WORD_RE = re.compile(r"[a-z]+")


def _has_temporal_cue(query: str) -> bool:
    """Cheap pre-check for whether a query can contain a date expression."""
    if _DIGIT_RE.search(query):
        return True
    return any(
        word in _TEMPORAL_GATE_WORDS for word in _WORD_RE.findall(query.lower())
    )


def _classify_intent(
    query: str,
    has_temporal_signals: bool,
    *,
    temporal_first: bool = False,
) -> QueryIntent:
    """Classify query intent using keyword/pattern matching.

    Returns the FIRST matching pattern, with SEMANTIC as fallback. By default
    the two entity checks come first, so a temporal question that names a
    person, place or organization is an ENTITY_LOOKUP. ``temporal_first``
    checks temporal wording and parsed dates before them (issue #85). Only
    TEMPORAL is read downstream; entity names are extracted separately.
    """
    temporal = bool(_TEMPORAL_KEYWORDS.search(query)) or has_temporal_signals
    if temporal_first and temporal:
        return QueryIntent.TEMPORAL

    if _ENTITY_PREFIXES.search(query):
        return QueryIntent.ENTITY_LOOKUP

    # Check for proper nouns (capitalization heuristic)
    if _PROPER_NOUN_RE.search(query):
        return QueryIntent.ENTITY_LOOKUP

    if temporal:
        return QueryIntent.TEMPORAL

    if _RELATIONAL_KEYWORDS.search(query):
        return QueryIntent.RELATIONAL

    if _FACTUAL_KEYWORDS.search(query):
        return QueryIntent.FACTUAL

    return QueryIntent.SEMANTIC


def _is_name_signal(signal: dict, entities: list[str]) -> bool:
    """Whether a date match is a lone month or weekday word used as a name.

    "Who is June dating?" and "What does Sun Microsystems make?" give
    dateparser a single capitalized word that is also part of an extracted
    name. A date after a preposition or with a number keeps them in the match
    ("in June", "on Sunday", "May 2023"), so it is not read as a name. A bare
    capitalized month or weekday word is read as a name, as the entity-first
    order reads it.
    """
    text = signal["value"].strip()
    return (
        " " not in text
        and not any(c.isdigit() for c in text)
        and text[:1].isupper()
        and any(text in entity.split() for entity in entities)
    )


def _extract_entities(query: str) -> list[str]:
    """Extract potential entity names from the query.

    Uses two heuristics:
    1. Sequences of 1+ consecutive capitalized words (proper nouns).
    2. Quoted strings as explicit entity references.
    """
    entities: list[str] = []

    # Proper noun sequences
    for match in _PROPER_NOUN_RE.finditer(query):
        name = match.group(1).strip()
        if name and name not in entities:
            entities.append(name)

    # Quoted strings
    for match in _QUOTED_RE.finditer(query):
        name = match.group(1) or match.group(2)
        if name and name not in entities:
            entities.append(name)

    return entities


def _extract_temporal_signals(
    query: str,
    languages: Sequence[str] | None = DEFAULT_TEMPORAL_LANGUAGES,
    reference_time: datetime | None = None,
) -> list[dict]:
    """Extract temporal expressions from the query via dateparser.

    Returns a list of dicts with keys: type, value, resolved.
    Falls back to empty list if dateparser is unavailable or fails.

    Args:
        query: Raw query text.
        languages: Languages to parse against. None restores dateparser's
            own language detection, which costs ~70ms per query.
    """
    if not _has_temporal_cue(query):
        return []

    try:
        from dateparser.search import search_dates

        with _DATEPARSER_LOCK:
            results = search_dates(
                query,
                languages=list(languages) if languages else None,
                settings={
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "TIMEZONE": "UTC",
                    **({"RELATIVE_BASE": reference_time} if reference_time is not None else {}),
                },
            )

        if not results:
            return []

        signals: list[dict] = []
        for text_match, parsed_date in results:
            # Filter out dateparser false positives. dateparser often
            # matches common words like "me", "hour", "may" as dates.
            # For single-word matches, only trust known temporal words
            # or words containing digits.
            normalized = text_match.strip().lower()
            is_single_word = " " not in normalized
            has_digits = any(c.isdigit() for c in normalized)

            if is_single_word and not has_digits:
                if normalized not in _KNOWN_TEMPORAL_WORDS:
                    continue

            signal_type = (
                "ABSOLUTE" if any(c.isdigit() for c in text_match) else "RELATIVE"
            )
            signals.append(
                {
                    "type": signal_type,
                    "value": text_match,
                    "resolved": parsed_date,
                }
            )
        return signals

    except ImportError:
        logger.warning(
            "dateparser not available; temporal signal extraction disabled"
        )
        return []
    except Exception:
        logger.warning(
            "dateparser failed during temporal extraction; returning empty signals",
            exc_info=True,
        )
        return []


async def analyze_query(
    query: str,
    *,
    time_from: datetime | None = None,
    time_to: datetime | None = None,
    reference_time: datetime | None = None,
    retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
    languages: Sequence[str] | None = DEFAULT_TEMPORAL_LANGUAGES,
    intent_order: QueryIntentOrder = "entity_first",
) -> QueryAnalysis:
    """Analyze a query into intent, entities, and temporal signals.

    This is Stage 1 of the retrieval pipeline (RFC-0005 S3).
    Uses pattern matching and dateparser only -- no blocking LLM calls.

    Args:
        query: Raw query text from the user.
        time_from: Explicit start of temporal window (overrides extraction).
        time_to: Explicit end of temporal window (overrides extraction).
        reference_time: Timezone-aware base for relative dates. Defaults to
            the parser's current UTC time when called independently.
        retrieval_mode: Retrieval mode controlling epistemic filtering.
        languages: Languages for temporal parsing. None restores dateparser's
            own language detection at its original cost.
        intent_order: ``"temporal_first"`` classifies a question with
            temporal wording or a parsed date as TEMPORAL even when it names
            an entity (``PRMEConfig.query_intent_order``, issue #85). It also
            drops date matches that are a name, such as "June" in "Who is
            June dating?", from the signals and the resolved window.

    Returns:
        QueryAnalysis with classified intent, extracted entities,
        temporal signals, and a unique request_id.

    Raises:
        ValueError: An unknown ``intent_order``, or a ``reference_time``
            without a timezone.
    """
    if intent_order not in QUERY_INTENT_ORDERS:
        raise ValueError(f"Unknown query intent order: {intent_order!r}")
    # Extract temporal signals from query text. dateparser is CPU-bound and
    # can take milliseconds on a long query, so it runs off the event loop
    # (issue #61) -- otherwise concurrent retrievals serialize behind it.
    if reference_time is not None:
        if reference_time.utcoffset() is None:
            raise ValueError("reference_time must include a timezone")
        reference_time = reference_time.astimezone(timezone.utc)
    temporal_signals = await asyncio.to_thread(
        _extract_temporal_signals, query, languages, reference_time
    )

    # Extract entity names.
    entities = _extract_entities(query)

    temporal_first = intent_order == "temporal_first"
    if temporal_first:
        # The entity-first order reads these as names before it looks at dates.
        temporal_signals = [s for s in temporal_signals if not _is_name_signal(s, entities)]
    has_temporal_signals = len(temporal_signals) > 0

    # Classify intent (temporal detection feeds into intent classification).
    intent = _classify_intent(query, has_temporal_signals, temporal_first=temporal_first)

    # Resolve time_from / time_to: explicit overrides take priority.
    resolved_time_from = time_from
    resolved_time_to = time_to

    if resolved_time_from is None and resolved_time_to is None and temporal_signals:
        # Derive from extracted temporal signals.
        resolved_dates = [s["resolved"] for s in temporal_signals if s.get("resolved")]
        if resolved_dates:
            resolved_time_from = min(resolved_dates)
            resolved_time_to = max(resolved_dates)

    # Detect aggregation intent (count/total/list-all queries) without treating
    # elapsed-time quantities as set cardinality.
    is_aggregation = bool(
        _AGGREGATION_KEYWORDS.search(query)
        and not _TEMPORAL_QUANTITY_RE.search(query)
    )

    return QueryAnalysis(
        query=query,
        intent=intent,
        entities=entities,
        temporal_signals=temporal_signals,
        time_from=resolved_time_from,
        time_to=resolved_time_to,
        retrieval_mode=retrieval_mode,
        request_id=uuid4(),
        is_aggregation=is_aggregation,
    )
