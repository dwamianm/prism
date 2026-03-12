"""Query analysis for the hybrid retrieval pipeline (Stage 1).

Transforms a raw query string into a QueryAnalysis with classified intent,
extracted entities, and resolved temporal signals. Uses dateparser + pattern
matching only -- no blocking LLM calls per RFC-0005 S3.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from uuid import uuid4

from prme.retrieval.models import QueryAnalysis
from prme.types import QueryIntent, RetrievalMode

logger = logging.getLogger(__name__)

# --- Intent classification patterns ---

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


def _classify_intent(query: str, has_temporal_signals: bool) -> QueryIntent:
    """Classify query intent using keyword/pattern matching.

    Returns the FIRST matching pattern, with SEMANTIC as fallback.
    """
    if _ENTITY_PREFIXES.search(query):
        return QueryIntent.ENTITY_LOOKUP

    # Check for proper nouns (capitalization heuristic)
    if _PROPER_NOUN_RE.search(query):
        return QueryIntent.ENTITY_LOOKUP

    if _TEMPORAL_KEYWORDS.search(query) or has_temporal_signals:
        return QueryIntent.TEMPORAL

    if _RELATIONAL_KEYWORDS.search(query):
        return QueryIntent.RELATIONAL

    if _FACTUAL_KEYWORDS.search(query):
        return QueryIntent.FACTUAL

    return QueryIntent.SEMANTIC


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


def _extract_temporal_signals(query: str) -> list[dict]:
    """Extract temporal expressions from the query via dateparser.

    Returns a list of dicts with keys: type, value, resolved.
    Falls back to empty list if dateparser is unavailable or fails.
    """
    try:
        from dateparser.search import search_dates

        results = search_dates(
            query,
            settings={
                "RETURN_AS_TIMEZONE_AWARE": True,
                "TIMEZONE": "UTC",
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
    retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
) -> QueryAnalysis:
    """Analyze a query into intent, entities, and temporal signals.

    This is Stage 1 of the retrieval pipeline (RFC-0005 S3).
    Uses pattern matching and dateparser only -- no blocking LLM calls.

    Args:
        query: Raw query text from the user.
        time_from: Explicit start of temporal window (overrides extraction).
        time_to: Explicit end of temporal window (overrides extraction).
        retrieval_mode: Retrieval mode controlling epistemic filtering.

    Returns:
        QueryAnalysis with classified intent, extracted entities,
        temporal signals, and a unique request_id.
    """
    # Extract temporal signals from query text.
    temporal_signals = _extract_temporal_signals(query)
    has_temporal_signals = len(temporal_signals) > 0

    # Classify intent (temporal detection feeds into intent classification).
    intent = _classify_intent(query, has_temporal_signals)

    # Extract entity names.
    entities = _extract_entities(query)

    # Resolve time_from / time_to: explicit overrides take priority.
    resolved_time_from = time_from
    resolved_time_to = time_to

    if resolved_time_from is None and resolved_time_to is None and temporal_signals:
        # Derive from extracted temporal signals.
        resolved_dates = [s["resolved"] for s in temporal_signals if s.get("resolved")]
        if resolved_dates:
            resolved_time_from = min(resolved_dates)
            resolved_time_to = max(resolved_dates)

    return QueryAnalysis(
        query=query,
        intent=intent,
        entities=entities,
        temporal_signals=temporal_signals,
        time_from=resolved_time_from,
        time_to=resolved_time_to,
        retrieval_mode=retrieval_mode,
        request_id=uuid4(),
    )
