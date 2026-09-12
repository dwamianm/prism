"""Validation for caller-supplied historical source clocks."""

from datetime import datetime


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
