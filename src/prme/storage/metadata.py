"""Admission checks for portable metadata; existing records remain readable."""

import json


class _CollidingKeys(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _CollidingKeys
        result[key] = value
    return result


def snapshot_metadata(metadata: dict | None) -> dict | None:
    """Freeze valid JSON before waiting for a database connection or lock."""
    try:
        return json.loads(
            json.dumps(metadata, allow_nan=False), object_pairs_hook=_unique_object
        )
    except _CollidingKeys:
        raise ValueError("Metadata object keys collide after JSON serialization") from None
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError(
            "Metadata must contain finite JSON-serializable values"
        ) from None
