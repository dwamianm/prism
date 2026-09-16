"""Durable, source-bound record of a validated model extraction."""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from prme.types import Scope


class ExtractionRecord(BaseModel):
    """The first grounded extraction for a source, independent of graph status.

    A record proves that model output was saved, not that its claims are true
    or that graph materialization completed. ``result`` uses extraction schema
    version 1. ``grounding_policy`` records which admission validator produced
    it; omitted legacy values retain ``source_passage_v1``. Provider
    credentials and raw provider responses are excluded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    grounding_policy: Literal[
        "source_passage_v1", "speech_act_v2", "speech_act_v3"
    ] = (
        "source_passage_v1"
    )
    event_id: UUID
    user_id: str = Field(min_length=1)
    scope: Scope
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str
    model: str
    result: dict[str, Any]
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def operation_id(self) -> str:
        return extraction_operation_id(self.event_id)

    def verify_source(self, user_id: str, scope: str, content_hash: str) -> None:
        if (self.user_id, self.scope.value, self.content_hash) != (user_id, scope, content_hash):
            raise ValueError("Extraction record does not match its immutable source")


def extraction_operation_id(event_id: UUID | str) -> str:
    return str(uuid5(UUID(str(event_id)), "prme:validated-extraction:v1"))
