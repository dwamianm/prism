"""Versioned private processing checkpoints and admitted journal validation.

Checkpoint bytes exist only in the processing slot, protected by its existing
purpose, generation, policy, actual claim and finite deadline. They are not
ordinary operation payloads or diagnostic evidence.
"""

from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from prme.privacy.policy import PrivacyDenied, PolicySnapshot


class JournalDependency(BaseModel):
    """Exact original retention/policy references for covered-copy erasure."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: UUID
    source_id: UUID
    representation_id: UUID
    snapshot: PolicySnapshot

    @classmethod
    def from_record(cls, record):
        return cls(
            event_id=record.event_id,
            source_id=record.source_id,
            representation_id=record.representation_id,
            snapshot=record.snapshot,
        )


class JournalManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    journal_id: UUID
    retention: Literal["memory"] = "memory"
    dependencies: tuple[JournalDependency, ...]
    admitted_node_ids: tuple[UUID, ...] = ()
    created_at: AwareDatetime

    @model_validator(mode="after")
    def distinct(self):
        if len({item.event_id for item in self.dependencies}) != len(
            self.dependencies
        ) or len(set(self.admitted_node_ids)) != len(self.admitted_node_ids):
            raise ValueError("Duplicate protected journal identity")
        return self


class SnapshotEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["protected_snapshot"] = "protected_snapshot"
    schema_version: Literal[1] = 1
    family: Literal[
        "lifecycle",
        "reinforcement",
        "supersedence",
        "condition_evaluation",
        "oscillation_penalty",
        "organizer_merge",
        "consolidation_retirement",
        "retention",
    ]
    manifest: JournalManifest
    record: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class JournalScope:
    coordinator: object
    records: tuple

    def check(self):
        for record in self.records:
            self.coordinator.check_deadlines(record.snapshot)


_journal_scope: ContextVar[JournalScope | None] = ContextVar(
    "prme_protected_journal", default=None
)


@contextmanager
def journal_scope(coordinator, records):
    """Carry an already-held authority through one native transaction/thread.

    This is no authorization token: writers still acquire the coordinator's
    policy guard, and protected nodes cannot serialize without that scope.
    """
    scope = JournalScope(coordinator, tuple(records))
    scope.check()
    token = _journal_scope.set(scope)
    try:
        yield scope
        scope.check()
    finally:
        _journal_scope.reset(token)


def journal_deadline():
    scope = _journal_scope.get()
    if scope is not None:
        scope.check()


def protected_mutation(function):
    """Existing backend transaction entry; no replacement authority or lock."""
    import functools
    import inspect

    signature = inspect.signature(function)

    @functools.wraps(function)
    async def guarded(store, *args, **kwargs):
        coordinator = getattr(store, "_privacy", None)
        if coordinator is None:
            return await function(store, *args, **kwargs)
        current = _journal_scope.get()
        if current is not None and current.coordinator is coordinator:
            current.check()
            return await function(store, *args, **kwargs)
        arguments = signature.bind(store, *args, **kwargs).arguments
        owner = arguments.get("user_id")
        records = await coordinator.records_for_call(owner, arguments)
        if not records and not coordinator.selected():
            return await function(store, *args, **kwargs)
        if not records:
            raise PrivacyDenied("privacy_processing_unavailable")
        # Actor identifiers are opaque; display text is not an authority ID.
        for key in ("actor_id", "reviewer_id"):
            if kwargs.get(key) not in (None, "system", "organizer", "privacy"):
                try:
                    if str(UUID(kwargs[key])) != kwargs[key]:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise PrivacyDenied("privacy_unsupported_input") from None
        if kwargs.get("reason") is not None:
            from prme.privacy.capabilities import OperationBudget
            from prme.privacy.admission import _fields

            reason = kwargs["reason"]
            budget = OperationBudget(uuid4())
            for record in records:
                sanitized = await coordinator.sanitize(
                    _fields(reason, None, retrieval_content=reason),
                    record.snapshot,
                    budget,
                )
                reason = json.loads(sanitized.fields_json)["retrieval_content"]
            kwargs = {**kwargs, "reason": reason}
        async with coordinator.publication_guards(records):
            with journal_scope(coordinator, records):
                return await function(store, *args, **kwargs)

    return guarded


def _snapshot_projection(record, project, *, edges=True):
    """Walk typed snapshot models only; dictionaries are never a redaction API."""
    from prme.models.nodes import MemoryNode
    from prme.models.edges import MemoryEdge
    from prme.ingestion.admitted_statements import project_admitted_edge

    if isinstance(record, MemoryNode):
        return project(record)
    if isinstance(record, MemoryEdge):
        return project_admitted_edge(record) if edges else record
    if isinstance(record, BaseModel):
        return record.model_copy(
            update={
                key: _snapshot_projection(getattr(record, key), project, edges=edges)
                for key in type(record).model_fields
            }
        )
    if isinstance(record, (tuple, list)):
        return type(record)(
            _snapshot_projection(value, project, edges=edges) for value in record
        )
    return record


def snapshot_payload(record, family):
    """Return a protected family, or None for the unchanged legacy codec."""
    from prme.privacy.records import PRIVACY_KEY
    from prme.ingestion.admitted_statements import (
        project_admitted_node,
        project_direct_node,
    )
    from prme.storage import _snapshot_json

    nodes = []
    _snapshot_projection(record, lambda node: nodes.append(node) or node, edges=False)
    marked = [node for node in nodes if PRIVACY_KEY in (node.metadata or {})]
    scope = _journal_scope.get()
    if not marked and scope is None:
        return None
    if scope is None or not nodes or len(marked) != len(nodes):
        raise PrivacyDenied("privacy_processing_unavailable")
    scope.check()
    records = {value.event_id: value for value in scope.records}
    dependencies = {}

    def project(node):
        reference = (node.metadata or {})[PRIVACY_KEY]
        try:
            origin = records[UUID(reference["event_id"])]
        except (TypeError, KeyError, ValueError):
            raise PrivacyDenied("privacy_processing_unavailable") from None
        base = {key: value for key, value in reference.items() if key != "sanitization"}
        if (
            base != origin.reference()
            or node.user_id != str(origin.snapshot.policy.owner_id)
            or node.scope != origin.snapshot.policy.scope
        ):
            raise PrivacyDenied("privacy_boundary_mismatch")
        for ref in (*node.evidence_refs, origin.event_id):
            if ref in records:
                dependencies[ref] = records[ref]
        return (
            project_direct_node(node)
            if origin.kind == "direct"
            else project_admitted_node(node)
        )

    projected = _snapshot_projection(record, project)
    raw = _snapshot_json.dumps(projected.model_dump(mode="python"))
    manifest = JournalManifest(
        journal_id=UUID(str(record.operation_id)),
        dependencies=tuple(
            JournalDependency.from_record(dependencies[key])
            for key in sorted(dependencies, key=str)
        ),
        admitted_node_ids=tuple(sorted({node.id for node in nodes}, key=str)),
        created_at=scope.coordinator.require_runtime().clock(),
    )
    return SnapshotEnvelope(
        family=family,
        manifest=manifest,
        record=raw,
        sha256=hashlib.sha256(raw.encode()).hexdigest(),
    ).model_dump_json()


def validate_snapshot(value, record, family):
    if value.get("kind") != "protected_snapshot":
        return
    envelope = SnapshotEnvelope.model_validate(value)
    if (
        envelope.family != family
        or envelope.manifest.journal_id != UUID(str(record.operation_id))
        or envelope.sha256 != hashlib.sha256(envelope.record.encode()).hexdigest()
    ):
        raise PrivacyDenied("privacy_invalid_result")


def partition_continuations(continuation, count):
    """Disjoint allowances for separately claimed members of one admission.

    Precharge each item for the allowance assigned to all other items. The sum
    of usable byte/time/leaf balances cannot exceed the original remainder.
    This uses the sanitizer's actual limits and continuation, not a new budget.
    """
    from prme.privacy.capabilities import BudgetContinuation
    from prme.privacy.sanitizer import (
        MAX_INPUT_BYTES,
        MAX_LEAVES,
        DETECTOR_SECONDS,
        restore_budget,
    )

    if type(count) is not int or count < 1:
        raise PrivacyDenied("privacy_processing_unavailable")
    restore_budget(continuation)
    limits = MAX_INPUT_BYTES, int(DETECTOR_SECONDS * 1e9), MAX_LEAVES
    used = (
        continuation.input_bytes,
        continuation.detector_nanoseconds,
        continuation.leaves,
    )
    grants = [(limit - value) // count for limit, value in zip(limits, used)]
    if min(grants) <= 0:
        raise PrivacyDenied("privacy_processing_unavailable")
    return tuple(
        BudgetContinuation(
            uuid4(), *(limit - grant for limit, grant in zip(limits, grants))
        )
        for _ in range(count)
    )


def save_checkpoint(source: bytes, extraction: dict | None) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "kind": "processing_checkpoint",
            "source": json.loads(source),
            "extraction": extraction,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def load_checkpoint(value: bytes) -> tuple[bytes, dict | None]:
    try:
        data = json.loads(value)
        if "kind" not in data:
            return value, None  # The existing authenticated sanitized-input v1.
        if (
            set(data) != {"schema_version", "kind", "source", "extraction"}
            or data["kind"] != "processing_checkpoint"
            or data["schema_version"] != 1
            or not isinstance(data["source"], dict)
            or (
                data["extraction"] is not None
                and not isinstance(data["extraction"], dict)
            )
        ):
            raise ValueError()
        return json.dumps(data["source"], allow_nan=False).encode(), data["extraction"]
    except (ValueError, TypeError, KeyError):
        raise PrivacyDenied("privacy_processing_unavailable") from None
