"""Context formatting for LLM consumption.

Transforms retrieval results into formatted text optimized for LLM generation.
Applies context-type-specific formatting:

- **temporal**: Chronological sorting, days-ago annotations on each entry,
  pre-computed date offsets for relative time references in the query.
- **knowledge_update**: Chronological sorting with [LATEST] markers so the
  LLM prioritizes the most recent values.
- **default**: Relevance-ranked entries with date annotations.

Additionally, implements two enhancements from the PRIME dual-memory research
(Zhang et al., EMNLP 2025):

- **Profile preamble**: Extracts stable/tentative Facts, Preferences, and
  Instructions and prepends them as a compact user profile section.
  "Personalized thinking" (profile-aware reasoning) is the single biggest
  performance driver for LLM personalization.
- **Conflict annotations**: When CONTESTED nodes appear in results, explicit
  conflict mediation annotations are added so the LLM can handle
  contradictions between episodic and semantic memory.

Both enhancements are enabled by default (``include_profile=True``) and can
be disabled for callers that need raw formatted results.

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


# Compiled patterns for knowledge-update auto-detection
_AGGREGATION_GUARD_RE = re.compile(
    r"\b(?:how\s+many|how\s+much|total|count|list\s+all|sum)\b",
    re.IGNORECASE,
)

_CURRENT_STATE_RE = re.compile(
    r"(?:"
    r"\b(?:current|currently|now|presently|these\s+days)\b"
    r"|\bmost\s+recently\b"
    r"|\bright\s+now\b"
    r"|\bafter\s+(?:\w+\s+)?(?:recent|latest)\b"
    r"|\bdo\s+I\s+(?:have|keep|use|own|play|work)\b"
    r")",
    re.IGNORECASE,
)


def _detect_context_type(
    query: str,
    query_analysis: QueryAnalysis | None = None,
) -> str:
    """Auto-detect the best formatting strategy for this query.

    Returns one of: ``"aggregation"``, ``"temporal"``, ``"knowledge_update"``,
    ``"default"``.

    Detection order:

    0. **aggregation** — count/total/list-all queries. Needs all entries
       visible with dedup guidance. Checked first because aggregation
       keywords co-occur with temporal keywords.
    1. **knowledge_update** — current-state queries that are NOT aggregation.
    2. **temporal** — explicit TEMPORAL intent or time-oriented keywords.
    3. **default** — relevance-ranked with date annotations.
    """
    from prme.types import QueryIntent

    q = query.lower()

    # Aggregation: count/total/list-all queries need exhaustive display.
    if query_analysis and query_analysis.is_aggregation:
        return "aggregation"
    if _AGGREGATION_GUARD_RE.search(q):
        return "aggregation"

    # Knowledge-update: current-state queries that are NOT aggregation.
    if _CURRENT_STATE_RE.search(q):
        return "knowledge_update"

    if query_analysis and query_analysis.intent == QueryIntent.TEMPORAL:
        return "temporal"

    # Temporal heuristics
    temporal_re = re.compile(
        r"\b(when|before|after|ago|first|last|order|earliest|latest|how long"
        r"|how many (days|weeks|months|years))\b",
        re.IGNORECASE,
    )
    if temporal_re.search(q):
        return "temporal"

    return "default"


# ---------------------------------------------------------------------------
# PRIME enhancements: profile preamble & conflict annotations
# ---------------------------------------------------------------------------


def _build_profile_preamble(
    results: list[RetrievalCandidate],
) -> str:
    """Build a user profile preamble from semantic memory nodes.

    Extracts stable/tentative Facts, Preferences, and Instructions from
    the result set and formats them as a compact profile section. This
    implements "personalized thinking" from the PRIME dual-memory research
    (Zhang et al., EMNLP 2025): prepending a user profile summary helps
    downstream LLMs reason through the user's lens.

    Only includes nodes with lifecycle_state in (STABLE, TENTATIVE) to
    avoid surfacing superseded or contested information in the profile.

    Args:
        results: The full set of retrieval candidates.

    Returns:
        Formatted profile section string, or empty string if no
        semantic nodes are found.
    """
    from prme.types import LifecycleState, NodeType

    # Semantic memory node types that belong in a user profile
    PROFILE_TYPES = {NodeType.FACT, NodeType.PREFERENCE, NodeType.INSTRUCTION}
    PROFILE_STATES = {LifecycleState.STABLE, LifecycleState.TENTATIVE}

    profile_nodes = []
    for r in results:
        if (
            r.node.node_type in PROFILE_TYPES
            and r.node.lifecycle_state in PROFILE_STATES
        ):
            profile_nodes.append(r)

    if not profile_nodes:
        return ""

    # Group by type, sorted by confidence descending within each group
    from collections import defaultdict
    by_type: dict[str, list[RetrievalCandidate]] = defaultdict(list)
    for r in profile_nodes:
        by_type[r.node.node_type.value].append(r)

    lines: list[str] = ["## User Profile"]

    # Order: preferences first (most actionable), then facts, then instructions
    type_order = ["preference", "fact", "instruction"]
    type_labels = {
        "preference": "Preferences",
        "fact": "Known Facts",
        "instruction": "Learned Rules",
    }

    for ntype in type_order:
        nodes = by_type.get(ntype, [])
        if not nodes:
            continue
        # Sort by confidence descending
        nodes.sort(key=lambda r: r.node.confidence, reverse=True)
        lines.append(f"### {type_labels[ntype]}")
        for r in nodes:
            confidence_tag = ""
            if r.node.lifecycle_state == LifecycleState.TENTATIVE:
                confidence_tag = " (tentative)"
            lines.append(f"- {r.node.content}{confidence_tag}")

    return "\n".join(lines) + "\n"


def _build_conflict_annotations(
    results: list[RetrievalCandidate],
) -> str:
    """Build conflict annotations for CONTESTED nodes in results.

    When recent Events contradict stable Facts, the LLM needs explicit
    guidance to handle the conflict. This implements conflict mediation
    from the PRIME dual-memory research: naively combining episodic and
    semantic memory can underperform when conflicts exist.

    Args:
        results: The full set of retrieval candidates.

    Returns:
        Formatted conflict annotations, or empty string if no conflicts.
    """
    conflicts = [r for r in results if r.conflict_flag and r.contradicts_id]
    if not conflicts:
        return ""

    # Build a lookup of all result node IDs for cross-referencing
    node_lookup = {r.node.id: r for r in results}

    lines: list[str] = ["## Conflicting Information"]
    lines.append(
        "The following items have unresolved contradictions. "
        "Prefer the more recent or higher-confidence version."
    )

    seen: set = set()
    for r in conflicts:
        if r.node.id in seen:
            continue
        seen.add(r.node.id)

        counterpart = node_lookup.get(r.contradicts_id)
        if counterpart:
            seen.add(counterpart.node.id)
            # Determine which is newer
            r_time = r.node.event_time or r.node.created_at
            c_time = counterpart.node.event_time or counterpart.node.created_at
            if r_time >= c_time:
                newer, older = r, counterpart
            else:
                newer, older = counterpart, r
            lines.append(
                f"- NEWER: \"{newer.node.content}\" vs "
                f"OLDER: \"{older.node.content}\""
            )
        else:
            lines.append(f"- CONTESTED: \"{r.node.content}\" (contradicting memory not in results)")

    return "\n".join(lines) + "\n"


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
    include_profile: bool = True,
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
        include_profile: When ``True`` (default), prepend a user profile
            preamble and conflict annotations derived from the PRIME
            dual-memory research. Set to ``False`` for raw formatted output.

    Returns:
        Formatted context string ready for injection into an LLM prompt.
    """
    display = list(results[:max_results])
    if not display:
        return ""

    ctx_type = context_hint or _detect_context_type(query, query_analysis)

    if ctx_type == "aggregation":
        body = _format_aggregation(display, query, question_date)
    elif ctx_type == "temporal":
        body = _format_temporal(display, query, question_date)
    elif ctx_type == "knowledge_update":
        body = _format_knowledge_update(display, question_date)
    else:
        body = _format_default(display, question_date)

    if not include_profile:
        return body

    # Prepend profile preamble and conflict annotations (PRIME enhancements)
    parts: list[str] = []

    profile = _build_profile_preamble(results[:max_results])
    if profile:
        parts.append(profile)

    conflicts = _build_conflict_annotations(results[:max_results])
    if conflicts:
        parts.append(conflicts)

    if parts:
        parts.append("## Retrieved Memory\n" + body)
        return "\n".join(parts)

    return body


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
    """Knowledge-update formatting: chronological sort, strong recency markers."""
    results.sort(key=_get_event_dt)

    n = len(results)
    lines: list[str] = []
    for i, r in enumerate(results):
        event_dt = _get_event_dt(r)
        if i == n - 1:
            marker = " [MOST RECENT — USE THIS VALUE]"
        elif i >= n - 3:
            marker = " [RECENT]"
        elif i < n - 5:
            marker = " [OLDER]"
        else:
            marker = ""
        lines.append(
            f"[{i+1}] ({event_dt.strftime('%Y-%m-%d')}{marker}) {r.node.content}"
        )

    header = (
        "IMPORTANT: Entries are in chronological order (oldest first, newest last).\n"
        "When the same attribute appears multiple times with different values, "
        "the MOST RECENT entry supersedes all earlier ones. "
        "ALWAYS use the latest value — earlier values are outdated.\n\n"
    )
    return header + "\n".join(lines)


def _format_aggregation(
    results: list[RetrievalCandidate],
    query: str,
    question_date: datetime | None,
) -> str:
    """Aggregation formatting: chronological, deduplicated, with counting guidance."""
    results.sort(key=_get_event_dt)

    lines: list[str] = []
    seen_content: set[str] = set()
    unique_count = 0
    for r in results:
        # Basic dedup: skip near-identical content
        content_key = r.node.content.strip().lower()[:100]
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        unique_count += 1

        event_dt = _get_event_dt(r)
        if question_date:
            ago = format_days_ago(event_dt, question_date)
            lines.append(
                f"[{unique_count}] ({event_dt.strftime('%Y-%m-%d')}, {ago}) {r.node.content}"
            )
        else:
            lines.append(
                f"[{unique_count}] ({event_dt.strftime('%Y-%m-%d')}) {r.node.content}"
            )

    header = (
        "AGGREGATION TASK: The question asks for a count, total, or list.\n"
        f"Below are {unique_count} unique entries (duplicates removed) "
        "in chronological order.\n"
        "IMPORTANT COUNTING RULES:\n"
        "- Count ONLY items that EXACTLY match the question's criteria.\n"
        "- Two mentions of the same item = 1 count (not 2).\n"
        "- If an entry is about a related but different topic, do NOT count it.\n"
        "- List each qualifying item before giving the final count.\n"
    )
    if question_date:
        header += f"Today's date: {question_date.strftime('%Y-%m-%d')}\n"
        computed = compute_time_offsets(query, question_date)
        if computed:
            header += computed + "\n"
    header += "\n"

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
