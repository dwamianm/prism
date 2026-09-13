"""Admission checks for portable metadata; existing records remain readable."""

import json


def snapshot_metadata(metadata: dict | None) -> dict | None:
    """Freeze valid JSON before waiting for a database connection or lock."""
    try:
        return json.loads(json.dumps(metadata, allow_nan=False))
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError(
            "Metadata must contain finite JSON-serializable values"
        ) from None
