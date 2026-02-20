"""Custom exception hierarchy for the ingestion pipeline.

Provides structured error types for all ingestion failure modes:
write queue errors, entity merge failures, supersedence detection
issues, and extraction materialization errors.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base exception for all ingestion pipeline errors."""


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

    def __init__(self, message: str, *, event_id: str | None = None) -> None:
        self.event_id = event_id
        super().__init__(message)
