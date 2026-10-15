"""Durable, source-bound record of a validated model extraction."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from prme.types import Scope


class ExtractionRecord(BaseModel):
    """The first grounded extraction for a source, independent of graph status.

    A record proves that model output was saved, not that its claims are true
    or that graph materialization completed. ``result`` uses extraction schema
    version 1. ``grounding_policy`` records which admission validator produced
    it; omitted legacy values retain ``source_passage_v1``. Provider
    credentials and raw provider responses are excluded.

    ``batch_id`` names the :class:`ExtractionBatchRecord` whose request asked
    for this source together with other turns of its session
    (``extraction_batch_target_tokens``, #254); that record, not this one,
    keeps the request's unchecked response. ``batch_id`` is left out when
    unset, so a record extracted on its own keeps its exact bytes. A release
    before #254 cannot read a record that sets it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    grounding_policy: Literal[
        "source_passage_v1", "speech_act_v2", "speech_act_v3", "speech_act_v4",
        "speech_act_v5", "speech_act_v6", "speech_act_v7", "speech_act_v8",
        "speech_act_v9", "speech_act_v10", "speech_act_v11", "speech_act_v12", "speech_act_v13", "speech_act_v14"
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
    batch_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)

    @property
    def operation_id(self) -> str:
        return extraction_operation_id(self.event_id)

    def verify_source(self, user_id: str, scope: str, content_hash: str) -> None:
        if (self.user_id, self.scope.value, self.content_hash) != (user_id, scope, content_hash):
            raise ValueError("Extraction record does not match its immutable source")


def extraction_operation_id(event_id: UUID | str) -> str:
    return str(uuid5(UUID(str(event_id)), "prme:validated-extraction:v1"))


class ExtractionBatchTurn(BaseModel):
    """One source turn of a batched extraction request, as the model saw it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=1)
    event_id: UUID
    role: str
    speaker: str | None = None
    event_time: AwareDatetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExtractionBatchRecord(BaseModel):
    """One batched extraction request and the response it got (#254).

    With ``extraction_batch_target_tokens`` above zero, one model call
    extracts several turns of a session. This record keeps that request,
    as the turns it carried and the digest of the exact messages sent, and
    ``response``, the model's parsed output before any check, limited to
    each entry's turn, entities, facts and relationships. Unlike an
    :class:`ExtractionRecord`, it therefore holds claims that grounding
    rejected. Each turn's checked share of it is saved once as that turn's
    own :class:`ExtractionRecord`, whose ``batch_id`` names this record, so
    materialization, replanning and a restart read the per-turn records and
    never ask the model again. ``validate_batch_output`` can check the
    response again, turn by turn, without a model call; the benchmark pack
    builder reads it to count the turns and claims a model left out or
    grounding dropped.

    ``turns_asked_again`` lists, by index, the turns the response gave no
    entry, which one more request of their own asked for again (#260). This
    record saves no extraction record for them; the records that request
    saves name its own batch. It is left out when empty, so a batch record
    without it keeps its exact bytes, and code from before #260 cannot read
    a batch record that sets it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1)
    scope: Scope
    session_id: str | None = None
    # The job whose worker sent the request, under whose lease it was saved.
    # It is not among the turns of a split request's half that left it out.
    head_event_id: UUID
    provider: str
    model: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    turns: tuple[ExtractionBatchTurn, ...] = Field(min_length=1)
    response: dict[str, Any]
    turns_asked_again: tuple[int, ...] = Field(default=(), exclude_if=lambda value: not value)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _check_turn_indices(self) -> "ExtractionBatchRecord":
        # Turns are numbered from 1 in request order, so an index names one turn.
        if [turn.index for turn in self.turns] != list(range(1, len(self.turns) + 1)):
            raise ValueError("A batch's turns are numbered 1, 2, 3 and on, in order")
        asked = self.turns_asked_again
        if list(asked) != sorted(set(asked)) or not set(asked) < {turn.index for turn in self.turns}:
            # A response that gives no turn an entry is saved as it is: asking
            # for every turn again would repeat the same request.
            raise ValueError("Turns asked again must be some of the batch's turns, in order, once each")
        return self

    @property
    def id(self) -> UUID:
        return extraction_batch_id(self.head_event_id, [turn.event_id for turn in self.turns],
                                   self.request_sha256)

    @property
    def saved_turns(self) -> tuple[ExtractionBatchTurn, ...]:
        """The turns whose extraction records this batch saves: those not asked again (#260)."""
        return tuple(turn for turn in self.turns if turn.index not in self.turns_asked_again)

    def verify_turn(self, event_id: UUID | str, user_id: str, scope: str, content_hash: str) -> None:
        """Check that a source event is one of this request's turns, unchanged."""
        turn = next((turn for turn in self.turns if str(turn.event_id) == str(UUID(str(event_id)))), None)
        if turn is None or (self.user_id, self.scope.value, turn.content_hash) != (user_id, scope, content_hash):
            raise ValueError("Extraction batch does not match its immutable source")


def extraction_batch_id(head_event_id: UUID | str, event_ids, request_sha256: str) -> UUID:
    """Identity of a batched request: the same turns asked the same way share it."""
    members = json.dumps([str(UUID(str(event_id))) for event_id in event_ids] + [request_sha256])
    return uuid5(UUID(str(head_event_id)),
                 "prme:extraction-batch:v1:" + hashlib.sha256(members.encode()).hexdigest())
