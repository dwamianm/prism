"""Durable retry identity for atomic raw-source batch admission."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from prme.models.events import Event


class FastIngestConflict(ValueError):
    """A batch request UUID is already bound to different inputs."""


class FastIngestBatchRecord(BaseModel):
    """Checksummed operation record committed with an admitted event batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    operation_id: UUID
    request_id: UUID
    user_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_ids: tuple[UUID, ...] = Field(min_length=1)
    admitted_at: AwareDatetime

    @model_validator(mode="after")
    def valid_identity(self):
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("Fast-ingest journal event IDs must be unique")
        if self.operation_id != fast_ingest_operation_id(
            self.user_id, self.request_id
        ):
            raise ValueError("Fast-ingest journal request identity mismatch")
        return self


class FastIngestAdmission(BaseModel):
    """Internal result that distinguishes a committed batch from its replay."""

    model_config = ConfigDict(frozen=True)

    event_ids: tuple[UUID, ...]
    replayed: bool = False


def parse_fast_ingest_request_id(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, (str, UUID)):
        raise ValueError("request_id must be a UUID")
    try:
        return UUID(str(value))
    except ValueError:
        raise ValueError("request_id must be a UUID") from None


def fast_ingest_operation_id(user_id: str, request_id: UUID) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        json.dumps(
            ["prme:fast-ingest-batch:v1", user_id, str(request_id)],
            separators=(",", ":"),
        ),
    )


def _input_sha256(events: Sequence[Event]) -> str:
    inputs = [
        {
            "content": event.content,
            "role": event.role,
            "session_id": event.session_id,
            "scope": event.scope.value,
            "metadata": event.metadata,
            "event_time": (
                event.event_time.astimezone(timezone.utc).isoformat()
                if event.event_time is not None
                else None
            ),
        }
        for event in events
    ]
    raw = json.dumps(
        inputs,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def make_fast_ingest_record(
    request_id: UUID,
    user_id: str,
    events: Sequence[Event],
) -> FastIngestBatchRecord:
    return FastIngestBatchRecord(
        operation_id=fast_ingest_operation_id(user_id, request_id),
        request_id=request_id,
        user_id=user_id,
        input_sha256=_input_sha256(events),
        event_ids=tuple(event.id for event in events),
        admitted_at=datetime.now(timezone.utc),
    )


def validate_fast_ingest_record(
    record: FastIngestBatchRecord,
    events: Sequence[Event],
) -> None:
    if (
        tuple(event.id for event in events) != record.event_ids
        or any(event.user_id != record.user_id for event in events)
        or _input_sha256(events) != record.input_sha256
    ):
        raise ValueError("Fast-ingest journal does not match the event batch")


def fast_ingest_payload(record: FastIngestBatchRecord) -> str:
    raw = record.model_dump_json()
    return json.dumps(
        {"record": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )


def read_fast_ingest_record(payload: str | dict) -> FastIngestBatchRecord:
    value = json.loads(payload) if isinstance(payload, str) else payload
    raw = value.get("record")
    checksum = value.get("sha256")
    if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != checksum:
        raise ValueError("Fast-ingest journal checksum mismatch")
    return FastIngestBatchRecord.model_validate_json(raw)


def replay_fast_ingest(
    row: tuple[object, object] | None,
    expected: FastIngestBatchRecord,
) -> FastIngestAdmission | None:
    if row is None:
        return None
    kind, payload = row
    if kind != "FAST_INGEST_BATCH":
        raise FastIngestConflict("request_id is already bound to another operation")
    if not isinstance(payload, (str, dict)):
        raise ValueError("Fast-ingest journal payload is invalid")
    saved = read_fast_ingest_record(payload)
    if (
        saved.operation_id != expected.operation_id
        or saved.request_id != expected.request_id
        or saved.user_id != expected.user_id
        or saved.input_sha256 != expected.input_sha256
    ):
        raise FastIngestConflict(
            "request_id is already bound to a different raw-source batch"
        )
    return FastIngestAdmission(event_ids=saved.event_ids, replayed=True)
