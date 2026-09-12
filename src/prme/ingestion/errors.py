"""Custom exception hierarchy for the ingestion pipeline.

Provides structured error types for all ingestion failure modes:
write queue errors, entity merge failures, supersedence detection
issues, and extraction materialization errors.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base exception for all ingestion pipeline errors."""


class ExtractionError(IngestionError):
    """Extraction failed; event_id locates its source and reason_code is sanitized."""

    def __init__(self, message: str, *, event_id: str | None = None, reason_code: str | None = None) -> None:
        self.event_id = event_id
        self.reason_code = reason_code
        super().__init__(message)


class WriteQueueError(IngestionError):
    """Error during write queue operation.

    Attributes:
        label: Optional label identifying the failed write job.
    """

    def __init__(self, message: str, *, label: str | None = None) -> None:
        self.label = label
        super().__init__(message)


class EntityMergeError(IngestionError):
    """Error during entity merge operation.

    Attributes:
        entity_name: Name of the entity that caused the error, if known.
    """

    def __init__(self, message: str, *, entity_name: str | None = None) -> None:
        self.entity_name = entity_name
        super().__init__(message)


class SupersedenceError(IngestionError):
    """Error during supersedence detection.

    Attributes:
        fact_id: ID of the fact that caused the error, if known.
    """

    def __init__(self, message: str, *, fact_id: str | None = None) -> None:
        self.fact_id = fact_id
        super().__init__(message)


class MaterializationError(IngestionError):
    """Error during extraction result materialization.

    Attributes:
        event_id: ID of the event whose materialization failed, if known.
    """

    def __init__(self, message: str, *, event_id: str | None = None, reason_code: str | None = None) -> None:
        self.event_id = event_id
        self.reason_code = reason_code
        super().__init__(message)


# Stable provider/schema categories take precedence over implementation causes.
# In particular, asyncio.wait_for raises TimeoutError from CancelledError.
_FAILURE_CATEGORIES = {
    "APITimeoutError": "TimeoutError",
    **{name: name for name in (
        "AuthenticationError", "PermissionDeniedError", "RateLimitError",
        "BadRequestError", "NotFoundError", "UnprocessableEntityError",
        "APIConnectionError", "InternalServerError", "ValidationError",
    )},
}


def _bounded_reason(name: str) -> str:
    if name and len(name) <= 100 and name.isascii() and all(c.isalnum() or c == "_" for c in name):
        return name
    return "ExtractionError"


def extraction_failure_code(error: BaseException) -> str:
    """Return a bounded category without reading exception messages or responses.

    Prefer explicit extraction/provider/schema categories to transport details.
    Unknown errors retain their underlying class name for compatibility. Cyclic
    or excessively long chains cannot block durable failure recording.
    """
    pending, seen = [error], set()
    fallback = "ExtractionError"
    while pending and len(seen) < 32:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ExtractionError) and current.reason_code:
            return _bounded_reason(current.reason_code)
        if isinstance(current, TimeoutError):
            return "TimeoutError"
        name = type(current).__name__
        if name in _FAILURE_CATEGORIES:
            return _FAILURE_CATEGORIES[name]
        fallback = _bounded_reason(name)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        # Instructor versions may preserve validation failures in attempts,
        # without attaching the final validation exception as __cause__.
        if name == "InstructorRetryException":
            attempts = getattr(current, "failed_attempts", None)
            if isinstance(attempts, (list, tuple)) and attempts:
                last = getattr(attempts[-1], "exception", None)
                if isinstance(last, BaseException):
                    pending.append(last)
    return fallback
