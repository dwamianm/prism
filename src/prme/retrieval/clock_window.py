"""A clock window stated in a question (``packing.clock_window_filter``).

"What did the garage camera see between 15:00 and 16:00 on November 3?" names
an exact window, and text ranking does not read it: the records it returns
are the ones whose words match, from any hour. :func:`parse_clock_window`
reads a window of clock times on one day, and retrieval then applies it as
the request's ``event_time_from`` and ``event_time_to``.

Only a clock window is read: two times of day, at least one written as a
clock time (``15:00``, ``3 pm``), with an optional day (a month and a day,
an ISO date, today or yesterday). A day alone is not, because a conversation
states what happened on a day at some later time, and a filter on the time a
record was stated would drop it. Deterministic; no model call.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re

_MONTHS = {name: number for number, names in enumerate((
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
    ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
    ("december", "dec"),
), start=1) for name in names}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?"
_WINDOW_RE = re.compile(rf"\b(?:between|from)\s+{_TIME}\s*(?:and|to|until|-)\s*{_TIME}(?!\w)", re.IGNORECASE)
_MONTH_DAY_RE = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE)
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH})\.?(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE)
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RELATIVE_RE = re.compile(r"\b(today|yesterday)\b", re.IGNORECASE)


def _clock(hour: str, minute: str | None, meridiem: str | None) -> tuple[int, int] | None:
    hours, minutes = int(hour), int(minute or 0)
    if meridiem:
        if not 1 <= hours <= 12:
            return None
        hours = hours % 12 + (12 if meridiem.lower().startswith("p") else 0)
    if hours > 23 or minutes > 59:
        return None
    return hours, minutes


def _day(query: str, reference: datetime) -> datetime | None:
    """The day the question names, at midnight in the reference time's zone; the reference's day when it names none."""
    midnight = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    found = _ISO_RE.search(query)
    try:
        if found:
            return midnight.replace(year=int(found[1]), month=int(found[2]), day=int(found[3]))
        month = day = year = None
        if found := _MONTH_DAY_RE.search(query):
            month, day, year = _MONTHS[found[1].lower()], int(found[2]), found[3]
        elif found := _DAY_MONTH_RE.search(query):
            day, month, year = int(found[1]), _MONTHS[found[2].lower()], found[3]
        if month is not None and day is not None:
            if year:
                return midnight.replace(year=int(year), month=month, day=day)
            # Without a year: the latest such day that is not after the reference day.
            candidate = midnight.replace(month=month, day=day)
            return candidate if candidate <= midnight else candidate.replace(year=candidate.year - 1)
    except ValueError:
        return None
    if found := _RELATIVE_RE.search(query):
        return midnight - timedelta(days=1) if found[1].lower() == "yesterday" else midnight
    return midnight


def parse_clock_window(query: str, reference_time: datetime) -> tuple[datetime, datetime] | None:
    """The window of clock times a question states, as two times in the reference time's zone, or None.

    "between 15:00 and 16:00 on November 3", "from 3 to 4 pm yesterday", "between 9:30 am and noon" is not read
    (no second clock time). A meridiem on one time serves the other ("between 3 and 4 pm"). Two bare numbers
    ("between 3 and 4 people") are not a window, and neither is an end that is not after its start.
    """
    found = _WINDOW_RE.search(query)
    if found is None:
        return None
    hour1, minute1, meridiem1, hour2, minute2, meridiem2 = found.groups()
    if not (minute1 or minute2 or meridiem1 or meridiem2):
        return None
    start = _clock(hour1, minute1, meridiem1 or (meridiem2 if not minute1 else None))
    end = _clock(hour2, minute2, meridiem2 or (meridiem1 if not minute2 else None))
    day = _day(query, reference_time)
    if start is None or end is None or day is None or end <= start:
        return None
    return (day + timedelta(hours=start[0], minutes=start[1]), day + timedelta(hours=end[0], minutes=end[1]))
