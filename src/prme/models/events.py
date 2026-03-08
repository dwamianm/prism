"""Event model for the immutable append-only event log.

Events are the source of truth in PRME. All derived structures
(graph nodes, edges, embeddings) must be rebuildable from events.
"""

import hashlib
from datetime import datetime, timezone

from pydantic import ConfigDict, Field, model_validator

from prme.models.base import MemoryObject


class Event(MemoryObject):
    """An immutable event in the append-only event log.

    Events represent conversational inputs and system actions.
    The content_hash is automatically computed from content via SHA-256.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Ingestion timestamp (UTC) -- when the system recorded this event",
    )
    event_time: datetime | None = Field(
        default=None,
        description=(
            "When the event actually happened in the real world (UTC). "
            "None means same as ingestion timestamp. Enables bi-temporal "
            "queries: ingestion_time vs event_time (issue #21)."
        ),
    )
    role: str = Field(description="Role: 'user', 'assistant', or 'system'")
    content: str = Field(description="Event content text")
    content_hash: str = Field(
        default="",
        description="SHA-256 hash of content, auto-computed",
    )
    metadata: dict | None = Field(
        default=None, description="Optional structured metadata"
    )

    @model_validator(mode="before")
    @classmethod
    def compute_content_hash(cls, data: dict) -> dict:
        """Compute SHA-256 hash of content if not already provided."""
        if isinstance(data, dict):
            content = data.get("content", "")
            if content and not data.get("content_hash"):
                data["content_hash"] = hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest()
        return data
