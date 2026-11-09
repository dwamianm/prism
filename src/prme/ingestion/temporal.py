"""Validation for caller-supplied historical source clocks, and month resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Literal

import dateparser  # type: ignore[import-untyped]

from prme._temporal import DATEPARSER_LOCK


@dataclass(frozen=True)
class TemporalResolution:
    """An ISO resolution and whether its year was guessed from the turn's tense."""

    value: str
    uncertain: bool = False


_MONTH_NAME = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
_NUMERIC_YEAR = re.compile(r"\b(?:1[6-9]|20)\d{2}\b")
# A two-digit year is written with an apostrophe so it is not a day of month.
_SHORT_YEAR = re.compile(r"(?:'|’)\d{2}\b")

# Words and phrases that place the reference after the turn: a plan, a promise,
# a booking or an explicit future tense. Inflections that are also past forms
# ("planned", "hoped", "expected") are left out, because they can describe the
# past act rather than the referenced month; those fall to the nearest
# occurrence and its uncertainty flag. Nouns that share a future word ("book",
# "reserve", "schedule") are left out for the same reason.
_FUTURE_CUE = re.compile(
    r"(?:"
    r"\b(?:"
    r"will|shall|won't|"
    r"going\s+to|gonna|"
    r"planning|plans?\s+to|intend(?:s)?\s+to|intending\s+to|"
    r"expect(?:s)?\s+to|expecting\s+to|"
    r"hope(?:s)?\s+to|hoping\s+to|"
    r"aim(?:s)?\s+to|aiming\s+to|"
    r"thinking\s+(?:about|of)|considering|"
    r"upcoming|next|soon|"
    r"scheduled\s+(?:for|to)|is\s+scheduled|"
    r"booking|booked\s+(?:a|for)|"
    r"reserved\s+(?:a|for)|reservation\s+(?:for|in)|"
    r"arrang(?:ed|ing)|organis(?:ed|ing)|organiz(?:ed|ing)|"
    r"start(?:s)?|begin(?:s)?|fly|flies|arrive(?:s)?|"
    r"looking\s+forward\s+to"
    r")\b"
    r"|(?:'|’)(?:ll)\b"
    r")",
    re.IGNORECASE,
)
# Words that place it before the turn: past tense, completion or an explicit
# backward marker.
_PAST_CUE = re.compile(
    r"\b(?:"
    r"was|were|had|did|didn't|"
    r"went|gone|visited|happened|occurred|"
    r"took|attended|celebrated|moved|graduated|married|divorced|"
    r"started|finished|completed|ended|left|returned|stayed|spent|"
    r"met|saw|bought|sold|received|gave|found|read|checked|watched|heard|"
    r"last|ago|previously|formerly|"
    r"back\s+in|used\s+to"
    r")\b",
    re.IGNORECASE,
)
_SENTENCE_BREAKS = ".!?\n"


def validate_source_time(event_time: datetime | None) -> None:
    """Reject ambiguous source times before an event is durably admitted.

    Historical rows and omitted clocks keep their existing semantics. Callers
    importing new sources must supply a datetime with an explicit timezone.
    """
    if event_time is not None and (
        not isinstance(event_time, datetime)
        or event_time.tzinfo is None
        or event_time.utcoffset() is None
    ):
        raise ValueError("event_time must be a timezone-aware datetime")


def validate_validity_window(
    valid_from: datetime | None,
    valid_to: datetime | None,
) -> None:
    """Validate an explicit half-open validity interval before admission."""
    for name, value in (("valid_from", valid_from), ("valid_to", valid_to)):
        if value is not None and (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError(f"{name} must be a timezone-aware datetime")
    if valid_to is not None and valid_from is None:
        raise ValueError("valid_to requires an explicit valid_from")
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise ValueError("valid_to must be after valid_from")


def dateparser_settings(preference: str, base: datetime) -> dict:
    """The dateparser settings every ingestion resolution shares."""
    return {
        "PREFER_DATES_FROM": preference,
        "RELATIVE_BASE": base,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": "UTC",
        "TO_TIMEZONE": "UTC",
    }


def names_month_without_year(temporal_ref: str) -> bool:
    """Whether a reference names a month and no year, so dateparser must guess one."""
    if not _MONTH_NAME.search(temporal_ref):
        return False
    return _NUMERIC_YEAR.search(temporal_ref) is None and _SHORT_YEAR.search(temporal_ref) is None


def classify_tense(
    source_text: str | None, temporal_ref: str,
) -> Literal["past", "future"] | None:
    """The turn's tense at a month reference: future or plan, past, or no signal.

    Only the sentence that holds the reference decides, so another date's tense
    in the same passage does not leak in. A passage that mentions the reference
    more than once is ambiguous about which mention the claim means, so it is
    no signal. When both a future and a past cue appear, the one nearer the
    reference wins; a tie is no signal.
    """
    if not source_text:
        return None
    occurrences = list(re.finditer(re.escape(temporal_ref), source_text, re.IGNORECASE))
    if len(occurrences) != 1:
        # The reference is not verbatim in the passage, or it appears more than
        # once, so no cue can be tied to the claim's own mention.
        return None
    start = occurrences[0].start()
    sentence = _sentence_containing(source_text, start, len(temporal_ref))
    reference = re.search(re.escape(temporal_ref), sentence, re.IGNORECASE)
    if reference is None:
        return None
    position = reference.start()
    future = [match.start() for match in _FUTURE_CUE.finditer(sentence)]
    past = [match.start() for match in _PAST_CUE.finditer(sentence)]
    if not future:
        return "past" if past else None
    if not past:
        return "future"
    future_distance = min(abs(offset - position) for offset in future)
    past_distance = min(abs(offset - position) for offset in past)
    if future_distance == past_distance:
        return None
    return "future" if future_distance < past_distance else "past"


def resolve_month_without_year(
    temporal_ref: str, *, base: datetime, source_text: str | None,
) -> TemporalResolution | None:
    """Resolve a month named without a year against the turn's own time.

    A plan or future turn takes the next occurrence, something that already
    happened takes the most recent past occurrence, and a turn that gives no
    tense takes the occurrence nearest the event time and is marked uncertain.
    A plan that names the event's own month takes that month, not the next
    year's. Returns None when dateparser cannot read the reference.
    """
    def parse(preference: str) -> datetime | None:
        with DATEPARSER_LOCK:
            return dateparser.parse(temporal_ref, settings=dateparser_settings(preference, base))

    tense = classify_tense(source_text, temporal_ref)
    if tense == "future":
        parsed = parse("future")
        if parsed is not None and parsed.month == base.month and parsed.year != base.year:
            # dateparser rolls a same-month plan to next year because the
            # parsed day is earlier in the day than the turn. A plan made in
            # the month it names means that month.
            same_month = parse("current_period")
            if same_month is not None and same_month.year == base.year:
                parsed = same_month
        return TemporalResolution(parsed.isoformat()) if parsed is not None else None
    if tense == "past":
        parsed = parse("past")
        return TemporalResolution(parsed.isoformat()) if parsed is not None else None
    past = parse("past")
    future = parse("future")
    if past is None and future is None:
        return None
    if past is None or future is None:
        parsed = past or future
    else:
        parsed = min((past, future), key=lambda value: abs((value - base).total_seconds()))
    return TemporalResolution(parsed.isoformat(), uncertain=True)


def _sentence_containing(text: str, start: int, length: int) -> str:
    left = max(text.rfind(mark, 0, start) for mark in _SENTENCE_BREAKS) + 1
    right = min(
        (index for index in (text.find(mark, start + length) for mark in _SENTENCE_BREAKS)
         if index >= 0),
        default=len(text),
    )
    return text[left:right]
