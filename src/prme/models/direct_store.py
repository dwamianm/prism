"""Immutable typed input for replaying a direct store without inference."""

import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from prme.models.events import Event
from prme.models.nodes import MemoryNode


class DirectStoreRecord(BaseModel):
    """Initial node values, committed with the source and deferred work."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1, 2] = 1
    event_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    node: MemoryNode

    @property
    def operation_id(self) -> str:
        return direct_store_operation_id(self.event_id)

    def operation_payload(self) -> str:
        # Keep serialized values inside a JSON string: JSONB normalization must
        # not change the initial node snapshot (including signed zero).
        raw = self.model_dump_json()
        return json.dumps({"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()})

    @classmethod
    def from_operation_payload(cls, payload: str | dict) -> "DirectStoreRecord":
        body = json.loads(payload) if isinstance(payload, str) else payload
        raw = body["record"]
        if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != body["sha256"]:
            raise ValueError("Direct store record checksum does not match")
        return cls.model_validate_json(raw)

    def verify_source(self, event: Event) -> None:
        if (self.event_id != event.id or self.content_hash != event.content_hash
                or self.node.user_id != event.user_id
                or self.node.scope != event.scope or self.node.session_id != event.session_id
                or self.node.evidence_refs != [event.id]):
            raise ValueError("Direct store record does not match its source event")
        if self.schema_version == 1 and self.node.content != event.content:
            raise ValueError("Version 1 direct store content must match its source event")
        if self.schema_version == 2 and self.node.content == event.content:
            raise ValueError("Version 2 direct store requires a distinct retrieval projection")


def direct_store_operation_id(event_id: UUID | str) -> str:
    return str(uuid5(UUID(str(event_id)), "prme:direct-store:v1"))
