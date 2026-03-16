"""Context formatting for LLM consumption.

Transforms retrieval results into formatted text optimized for LLM generation.
Applies context-type-specific formatting:

- **temporal**: Chronological sorting, days-ago annotations on each entry,
  pre-computed date offsets for relative time references in the query.
- **knowledge_update**: Chronological sorting with [LATEST] markers so the
  LLM prioritizes the most recent values.
- **default**: Relevance-ranked entries with date annotations.

Usage::

    from prme.retrieval.context_formatter import format_for_llm

    formatted = format_for_llm(
        results=response.results[:50],
        query="How many months ago did I book the Airbnb?",
        question_date=some_datetime,
    )
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.retrieval.models import QueryAnalysis, RetrievalCandidate

# ---------------------------------------------------------------------------
# Word-to-number mapping for time offset parsing
# ---------------------------------------------------------------------------

_WORD_NUMS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "a": 1, "an": 1,
}

_DAY_NAMES: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# ---------------------------------------------------------------------------
# Time offset parsing
# ---------------------------------------------------------------------------

_OFFSET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)"
        r"\s+weeks?\s+ago", re.IGNORECASE,
    ), "weeks"),
    (re.compile(
        r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)"
        r"\s+months?\s+ago", re.IGNORECASE,
    ), "months"),
    (re.compile(
        r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)"
        r"\s+days?\s+ago", re.IGNORECASE,
    ), "days"),
    (re.compile(
        r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)"
        r"\s+years?\s+ago", re.IGNORECASE,
    ), "years"),
]

_DOW_PATTERN = re.compile(
    r"last\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)",
    re.IGNORECASE,
)

_WEEKEND_PATTERN = re.compile(r"past\s+weekend|the\s+weekend", re.IGNORECASE)


def compute_time_offsets(query: str, question_dt: datetime) -> str:
    """Parse relative time references from *query* and compute target dates.

    Returns a multi-line string with ``COMPUTED:`` lines, or empty string
    if no time offsets are found in the query.
    """
    computations: list[str] = []

    for pattern, unit in _OFFSET_PATTERNS:
        match = pattern.search(query)
        if match:
            val_str = match.group(1).lower()
            val = _WORD_NUMS.get(val_str)
            if val is None and val_str.isdigit():
                val = int(val_str)
            if val is None:
                continue
            if unit == "weeks":
                delta = timedelta(weeks=val)
            elif unit == "months":
                delta = timedelta(days=val * 30)
            elif unit == "days":
                delta = timedelta(days=val)
            elif unit == "years":
                delta = timedelta(days=val * 365)
            else:
                continue
            target = question_dt - delta
            computations.append(
                f"COMPUTED: '{val} {unit} ago' from {question_dt.strftime('%Y-%m-%d')} "
                f"= approximately {target.strftime('%Y-%m-%d')} ({delta.days} days before)"
            )

    dow_match = _DOW_PATTERN.search(query)
    if dow_match:
        day_name = dow_match.group(1).lower()
        day_num = _DAY_NAMES[day_name]
        days_back = (question_dt.weekday() - day_num) % 7
        if days_back == 0:
            days_back = 7
        target = question_dt - timedelta(days=days_back)
        computations.append(
            f"COMPUTED: 'last {day_name.title()}' from {question_dt.strftime('%Y-%m-%d')} "
            f"= {target.strftime('%Y-%m-%d')} ({days_back} days before)"
        )

    if _WEEKEND_PATTERN.search(query):
        days_back = (question_dt.weekday() + 2) % 7
        if days_back == 0:
            days_back = 7
        target = question_dt - timedelta(days=days_back)
        computations.append(
            f"COMPUTED: 'past weekend' = Saturday {target.strftime('%Y-%m-%d')} "
            f"({days_back} days before {question_dt.strftime('%Y-%m-%d')})"
        )

    return "\n".join(computations)


# ---------------------------------------------------------------------------
# Days-ago annotation
# ---------------------------------------------------------------------------


def format_days_ago(event_dt: datetime, question_dt: datetime) -> str:
    """Format the time difference between *event_dt* and *question_dt*.

    Returns a human-readable string like ``~2 weeks ago (14 days)``.
    Uses date-level comparison to avoid same-day time-of-day artifacts.
    """
    # Compare dates, not datetimes, to avoid intra-day sign flips
    diff = (question_dt.date() - event_dt.date()).days
    if diff < 0:
        return f"in {-diff} days"
    if diff == 0:
        return "today"
    if diff == 1:
        return "yesterday"
    if diff < 7:
        return f"{diff} days ago"
    if diff < 14:
        weeks = diff // 7
        rem = diff % 7
        if rem == 0:
            return f"{weeks} week ago"
        return f"~{weeks} week ago ({diff} days)"
    if diff < 60:
        weeks = diff // 7
        return f"~{weeks} weeks ago ({diff} days)"
    if diff < 365:
        months = round(diff / 30.44, 1)
        return f"~{months} months ago ({diff} days)"
    years = round(diff / 365.25, 1)
    return f"~{years} years ago ({diff} days)"


# ---------------------------------------------------------------------------
# Context type detection
# ---------------------------------------------------------------------------


def _detect_context_type(
    query: str,
    query_analysis: QueryAnalysis | None = None,
) -> str:
    """Auto-detect the best formatting strategy for this query.

    Returns one of: ``"temporal"``, ``"knowledge_update"``, ``"default"``.
    """
    from prme.types import QueryIntent

    if query_analysis and query_analysis.intent == QueryIntent.TEMPORAL:
        return "temporal"

    q = query.lower()

    # Temporal heuristics
    temporal_re = re.compile(
        r"\b(when|before|after|ago|first|last|order|earliest|latest|how long"
        r"|how many (days|weeks|months|years))\b",
        re.IGNORECASE,
    )
    if temporal_re.search(q):
        return "temporal"

    # Knowledge-update heuristics are intentionally NOT auto-detected.
    # Chronological sort + [LATEST] markers can hurt when the model needs
    # to consider all entries (e.g., aggregation). Callers can still pass
    # context_hint="knowledge_update" explicitly when appropriate.

    return "default"


# ---------------------------------------------------------------------------
# Main formatter
# ---------------------------------------------------------------------------


def format_for_llm(
    results: list[RetrievalCandidate],
    query: str,
    *,
    query_analysis: QueryAnalysis | None = None,
    question_date: datetime | None = None,
    context_hint: str | None = None,
    max_results: int = 50,
) -> str:
    """Format retrieval results as text optimized for LLM consumption.

    Args:
        results: Scored retrieval candidates (typically the top-k).
        query: The original user query.
        query_analysis: Optional QueryAnalysis for intent-aware formatting.
        question_date: Reference date for temporal computations (e.g.
            "today" in the conversation). If ``None``, date-relative
            annotations are skipped.
        context_hint: Override auto-detection with an explicit context type.
            One of ``"temporal"``, ``"knowledge_update"``, ``"default"``.
        max_results: Maximum number of results to include.

    Returns:
        Formatted context string ready for injection into an LLM prompt.
    """
    display = list(results[:max_results])
    if not display:
        return ""

    ctx_type = context_hint or _detect_context_type(query, query_analysis)

    if ctx_type == "temporal":
        return _format_temporal(display, query, question_date)
    if ctx_type == "knowledge_update":
        return _format_knowledge_update(display, question_date)
    return _format_default(display, question_date)


# ---------------------------------------------------------------------------
# Format variants
# ---------------------------------------------------------------------------


def _get_event_dt(candidate) -> datetime:
    """Extract the best available datetime from a candidate."""
    return candidate.node.event_time or candidate.node.created_at


def _format_temporal(
    results: list[RetrievalCandidate],
    query: str,
    question_date: datetime | None,
) -> str:
    """Temporal formatting: chronological sort, days-ago, offset computation."""
    results.sort(key=_get_event_dt)

    lines: list[str] = []
    for i, r in enumerate(results):
        event_dt = _get_event_dt(r)
        if question_date:
            ago = format_days_ago(event_dt, question_date)
            lines.append(
                f"[{i+1}] ({event_dt.strftime('%Y-%m-%d')}, {ago}) {r.node.content}"
            )
        else:
            lines.append(
                f"[{i+1}] ({event_dt.strftime('%Y-%m-%d')}) {r.node.content}"
            )

    header = ""
    if question_date:
        header = f"Today's date: {question_date.strftime('%Y-%m-%d')}\n"
        computed = compute_time_offsets(query, question_date)
        if computed:
            header += computed + "\n"
        header += "\n"

    return header + "\n".join(lines)


def _format_knowledge_update(
    results: list[RetrievalCandidate],
    question_date: datetime | None,
) -> str:
    """Knowledge-update formatting: chronological sort, [LATEST] markers."""
    results.sort(key=_get_event_dt)

    n = len(results)
    lines: list[str] = []
    for i, r in enumerate(results):
        event_dt = _get_event_dt(r)
        marker = " [LATEST]" if i >= n - 5 else ""
        lines.append(
            f"[{i+1}] ({event_dt.strftime('%Y-%m-%d')}{marker}) {r.node.content}"
        )

    header = (
        "NOTE: Entries are in chronological order. "
        "When values change over time, the LATEST entry is correct.\n\n"
    )
    return header + "\n".join(lines)


def _format_default(
    results: list[RetrievalCandidate],
    question_date: datetime | None,
) -> str:
    """Default formatting: relevance-ranked with dates."""
    lines: list[str] = []
    for i, r in enumerate(results):
        event_dt = _get_event_dt(r)
        lines.append(
            f"[{i+1}] ({event_dt.strftime('%Y-%m-%d')}) {r.node.content}"
        )

    header = ""
    if question_date:
        header = f"Today's date: {question_date.strftime('%Y-%m-%d')}\n\n"

    return header + "\n".join(lines)
