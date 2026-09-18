"""Canonical timestamp presentation, independent of the host timezone."""

from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """Normalize an instant; leave legacy naive values nominal, not host-local.

    A naive datetime does not identify an instant. Never call astimezone on it,
    which would silently interpret it in the current machine's timezone.
    """
    return value.astimezone(timezone.utc) if value.utcoffset() is not None else value
