"""Results and records of re-deriving a stored source (``MemoryEngine.reingest``, prism-monorepo #278)."""

from datetime import datetime, timezone
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from prme.types import Scope


class ReingestResult(BaseModel):
    """What one ``reingest()`` call looked at, did and left.

    A source is current when its entities, claims and links were derived under
    the engine's present extraction settings (``fingerprint``). Counts are of
    sources, meaning events that have a memory (``store()``, ``ingest()`` and
    the like); they do not count the nodes derived from them.

    ``selected`` is what the call took up to its ``limit``, in event order.
    A dry run selects the same sources a real run would, makes no model call
    and changes nothing: its ``model_calls`` is the number of requests the run
    would send, its ``source_tokens`` the tokens of the text those requests
    carry, and ``processed`` is 0. A real run's ``model_calls`` counts the
    requests it sent, halves and repeats included.

    ``remaining`` counts the sources that are still not current after the call,
    a source that failed among them. ``cursor`` continues after the last selected
    source and is ``None`` when none is left after it; a call without a cursor
    starts again from the first source that is not current, so a stopped run
    needs no cursor to continue and a source that failed is tried again.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str
    scope: Scope | None = None
    dry_run: bool = False
    total: int = Field(ge=0)
    current: int = Field(ge=0)
    unfinished: int = Field(default=0, ge=0)
    selected: int = Field(ge=0)
    processed: int = Field(ge=0)
    failed: int = Field(ge=0)
    remaining: int = Field(ge=0)
    cursor: str | None = None
    model_calls: int = Field(ge=0)
    source_tokens: int = Field(ge=0)
    fingerprint: str


def reingest_marker_id(event_id: UUID | str, fingerprint: str) -> str:
    """Operation ID of the record that says a source was re-derived under ``fingerprint``."""
    return str(uuid5(UUID(str(event_id)), f"prme:reingest-committed:v1:{fingerprint}"))


def reingest_extraction_id(event_id: UUID | str, extraction_key: str) -> str:
    """Operation ID of the model output a re-derivation saved for a source under ``extraction_key``."""
    return str(uuid5(UUID(str(event_id)), f"prme:reingest-extraction:v1:{extraction_key}"))


class ReingestArchive(BaseModel):
    """An earlier claim the re-derivation retired without a claim of the same statement to replace it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: UUID
    replaced_by: tuple[UUID, ...] = ()


class ReingestRecord(BaseModel):
    """Journal entry that commits with a source's new records and marks the source current.

    ``archived`` lists the earlier claims retired by archiving them, and the
    plan's replacements the ones retired by supersedence; the entry's ID is
    ``reingest_marker_id(event_id, fingerprint)``, so a second commit of the
    same source under the same settings finds the first and does nothing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    event_id: UUID
    user_id: str = Field(min_length=1)
    scope: Scope
    fingerprint: str
    extraction_key: str
    plan_id: UUID
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    node_ids: tuple[UUID, ...]
    edge_ids: tuple[UUID, ...]
    superseded: tuple[UUID, ...] = ()
    archived: tuple[ReingestArchive, ...] = ()
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def operation_id(self) -> str:
        return reingest_marker_id(self.event_id, self.fingerprint)
