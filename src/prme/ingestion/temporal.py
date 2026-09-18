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
