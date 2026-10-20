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
    admission_kind: Literal["direct", "extraction"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @classmethod
    def from_record(cls, record):
        return cls(
            event_id=record.event_id,
            source_id=record.source_id,
            representation_id=record.representation_id,
            snapshot=record.snapshot,
            admission_kind=record.kind,
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


JOURNAL_KEY = "_prme_privacy_journal_v1"


class GeneratedNodeReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    kind: Literal["profile", "summary"]
    lineage_id: UUID
    source_node_ids: tuple[UUID, ...]


def project_generated_node(node):
    """Closed admitted generated memory; never a source/evidence fallback."""
    from prme.privacy.records import PRIVACY_KEY
    from prme.types import NodeType

    metadata = node.metadata or {}
    try:
        reference = GeneratedNodeReference.model_validate(metadata[JOURNAL_KEY])
        if (
            node.node_type != NodeType.SUMMARY
            or not node.content.strip()
            or not reference.source_node_ids
        ):
            raise ValueError()
        projected = {
            PRIVACY_KEY: metadata[PRIVACY_KEY],
            JOURNAL_KEY: reference.model_dump(mode="json"),
            "source_node_ids": [str(value) for value in reference.source_node_ids],
        }
        if reference.kind == "profile":
            name = metadata["entity_name"]
            if (
                not isinstance(name, str)
                or not name.strip()
                or name not in node.content
            ):
                raise ValueError()
            if (
                metadata.get("entity_profile") is not True
                or metadata.get("profile_format_version") != 2
            ):
                raise ValueError()
            projected.update(
                entity_profile=True, entity_name=name, profile_format_version=2
            )
            for key in (
                "source_count_available",
                "source_count_included",
                "tokens_used",
            ):
                value = metadata[key]
                if type(value) is not int or value < 0:
                    raise ValueError()
                projected[key] = value
        else:
            import re

            selected = [
                str(UUID(str(value))) for value in metadata["selected_source_node_ids"]
            ]
            if (
                metadata.get("consolidation_summary") is not True
                or not selected
                or len(set(selected)) != len(selected)
                or not set(selected) <= set(projected["source_node_ids"])
                or metadata["consolidation_key"] != str(reference.lineage_id)
            ):
                raise ValueError()
            generation = metadata["consolidation_generation"]
            if type(generation) is not int or generation < 0:
                raise ValueError()
            projected.update(
                consolidation_summary=True,
                consolidation_key=str(reference.lineage_id),
                consolidation_generation=generation,
                selected_source_node_ids=selected,
            )
            if (
                metadata.get("summary_publication_kind")
                == "hierarchical_source_excerpts_v2"
            ):
                level, period = metadata["summarization_level"], metadata["period_key"]
                patterns = {
                    "daily": r"\d{4}-\d{2}-\d{2}",
                    "weekly": r"\d{4}-W\d{2}",
                    "monthly": r"\d{4}-\d{2}",
                }
                if (
                    level not in patterns
                    or not isinstance(period, str)
                    or re.fullmatch(patterns[level], period) is None
                    or metadata["summary_publication_key"] != str(reference.lineage_id)
                    or metadata["summary_format"] != "source-excerpts-v1"
                ):
                    raise ValueError()
                projected.update(
                    summary_publication_kind="hierarchical_source_excerpts_v2",
                    summary_publication_key=str(reference.lineage_id),
                    summarization_level=level,
                    summary_format="source-excerpts-v1",
                    period_key=period,
                    source_count=len(reference.source_node_ids),
                )
            else:
                if metadata.get("consolidation_format_version") != 2:
                    raise ValueError()
                projected.update(
                    consolidation_format_version=2,
                    cluster_size=len(reference.source_node_ids),
                )
        return node.model_copy(deep=True, update={"metadata": projected})
    except (KeyError, TypeError, ValueError):
        raise PrivacyDenied("privacy_invalid_result") from None


def project_manifest_node(node, manifest):
    from prme.privacy.records import PRIVACY_KEY
    from prme.ingestion.admitted_statements import (
        project_direct_node,
        project_admitted_node,
    )

    reference = (node.metadata or {}).get(PRIVACY_KEY)
    try:
        origin = next(
            item
            for item in manifest.dependencies
            if str(item.event_id) == reference["event_id"]
        )
        if (
            node.user_id != str(origin.snapshot.policy.owner_id)
            or node.scope != origin.snapshot.policy.scope
        ):
            raise ValueError()
        expected = {
            "schema_version": 1,
            "event_id": str(origin.event_id),
            "source_id": str(origin.source_id),
            "representation_id": str(origin.representation_id),
            "policy_id": str(origin.snapshot.policy.policy_id),
            "policy_revision": origin.snapshot.policy.revision,
            "representation": "sanitized",
        }
        if {
            key: value for key, value in reference.items() if key != "sanitization"
        } != expected:
            raise ValueError()
    except (TypeError, KeyError, StopIteration, ValueError):
        raise PrivacyDenied("privacy_boundary_mismatch") from None
    if JOURNAL_KEY in (node.metadata or {}):
        return project_generated_node(node)
    if origin.admission_kind == "direct":
        return project_direct_node(node)
    if origin.admission_kind == "extraction":
        return project_admitted_node(node)
    raise PrivacyDenied("privacy_processing_unavailable")


def validate_generated_publication(plan, *, kind):
    from prme.models.derivation import node_checksum
    from prme.privacy.records import PRIVACY_KEY

    manifest = plan.privacy_manifest
    nodes = (plan.node, *plan.sources, *plan.previous)
    if manifest is None:
        if plan.schema_version != 1 or any(
            PRIVACY_KEY in (node.metadata or {}) for node in nodes
        ):
            raise PrivacyDenied("privacy_processing_unavailable")
        return
    if plan.schema_version != 2 or manifest.journal_id != plan.node.id:
        raise PrivacyDenied("privacy_invalid_result")
    reference = GeneratedNodeReference.model_validate(plan.node.metadata[JOURNAL_KEY])
    if reference.kind != kind or reference.source_node_ids != tuple(
        node.id for node in plan.sources
    ):
        raise PrivacyDenied("privacy_invalid_result")
    if set(manifest.admitted_node_ids) != {node.id for node in nodes}:
        raise PrivacyDenied("privacy_invalid_result")
    for node in nodes:
        if node_checksum(project_manifest_node(node, manifest)) != node_checksum(node):
            raise PrivacyDenied("privacy_invalid_result")


async def manifest_records(coordinator, manifest, *, user_id):
    records = []
    for dependency in manifest.dependencies:
        record = await coordinator.repository.get(
            str(dependency.event_id), user_id=user_id
        )
        if (
            record is None
            or record.snapshot != dependency.snapshot
            or record.source_id != dependency.source_id
            or record.representation_id != dependency.representation_id
            or (
                dependency.admission_kind is not None
                and record.kind != dependency.admission_kind
            )
        ):
            raise PrivacyDenied("privacy_processing_unavailable")
        records.append(record)
    if not records:
        raise PrivacyDenied("privacy_processing_unavailable")
    return tuple(records)


def generation_manifest(context, node, sources):
    from prme.privacy.records import PRIVACY_KEY

    identifiers = set()
    for source in sources:
        reference = (source.metadata or {}).get(PRIVACY_KEY)
        if not isinstance(reference, dict):
            raise PrivacyDenied("privacy_processing_unavailable")
        identifiers.add(UUID(reference["event_id"]))
        identifiers.update(
            ref for ref in source.evidence_refs if ref in context.records
        )
    try:
        dependencies = tuple(
            JournalDependency.from_record(context.records[identifier])
            for identifier in sorted(identifiers, key=str)
        )
    except KeyError:
        raise PrivacyDenied("privacy_processing_unavailable") from None
    return JournalManifest(
        journal_id=node.id,
        dependencies=dependencies,
        admitted_node_ids=tuple(
            dict.fromkeys((node.id, *(source.id for source in sources)))
        ),
        created_at=node.created_at,
    )


async def admit_generated_node(
    context, node, sources, *, kind, lineage_id, previous=()
):
    from prme.privacy.records import PRIVACY_KEY

    sanitized_sources = await context.generated_sources(
        [source.content for source in sources]
    )
    fields = {"content": node.content}
    if kind == "profile":
        fields["entity_name"] = node.metadata["entity_name"]
    fields = await context.generated(fields, sources=sanitized_sources)
    manifest = generation_manifest(context, node, (*sources, *previous))
    origin = context.records[manifest.dependencies[0].event_id]
    metadata = {
        **node.metadata,
        PRIVACY_KEY: origin.reference(),
        JOURNAL_KEY: GeneratedNodeReference(
            kind=kind,
            lineage_id=lineage_id,
            source_node_ids=tuple(source.id for source in sources),
        ).model_dump(mode="json"),
    }
    if kind == "profile":
        metadata["entity_name"] = fields["entity_name"]
    return project_generated_node(
        node.model_copy(update={"content": fields["content"], "metadata": metadata})
    ), manifest


async def encode_generated(context, texts):
    from prme.privacy.admission import capability_call

    runtime = context.coordinator.require_runtime()
    saved = next(iter(context.records.values())).snapshot
    return await capability_call(
        runtime.embedder.encode(tuple(texts), saved, guard=context.transmission_guard),
        "privacy_provider_unavailable",
    )


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
        plan = arguments.get("plan")
        owner = (
            arguments.get("user_id")
            or arguments.get("policy", {}).get("user_id")
            or (plan.node.user_id if hasattr(plan, "node") else None)
        )
        manifest = getattr(plan, "privacy_manifest", None)
        records = (
            await manifest_records(coordinator, manifest, user_id=owner)
            if manifest is not None
            else await coordinator.records_for_call(owner, arguments)
        )
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
        async with coordinator.publication_guards(records, node_ids=manifest.admitted_node_ids if manifest else ()):
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
            project_generated_node(node)
            if JOURNAL_KEY in (node.metadata or {})
            else (
                project_direct_node(node)
                if origin.kind == "direct"
                else project_admitted_node(node)
            )
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


async def generation_context(engine, *, user_id, scope=None):
    from prme.privacy.reads import ReadContext

    if user_id is None:
        if engine._privacy.selected():
            raise PrivacyDenied("privacy_boundary_mismatch")
        await engine._privacy.repository.require_legacy_range(
            None, scope.value if scope else None
        )
        return None
    context = await ReadContext.create(
        engine._privacy, user_id=user_id, scopes=(scope,) if scope else None
    )
    return context if context.protected else None


def without_availability(node):
    return node.model_copy(
        deep=True,
        update={
            "metadata": {
                key: value
                for key, value in (node.metadata or {}).items()
                if key != "source_availability"
            }
        },
    )


async def generation_graph(engine, *, user_id, scope=None):
    from prme.privacy.read_graph import AdmittedGraphView

    context = await generation_context(engine, user_id=user_id, scope=scope)
    return (
        AdmittedGraphView(engine._graph_store, context)
        if context
        else engine._graph_store
    )


async def generation_inputs(context, nodes):
    if context is None:
        return nodes
    projected = [await context.project_node(node) for node in nodes]
    if any(node is None for node in projected):
        raise PrivacyDenied("privacy_processing_unavailable")
    return [without_availability(node) for node in projected]


async def matching_summaries(engine, previous, request_hash, context):
    if context is None:
        return [
            node
            for node in previous
            if (node.metadata or {}).get("consolidation_request_hash") == request_hash
        ]
    result = []
    for node in previous:
        plan = await engine._graph_store.get_prepared_consolidation(
            str(node.id), user_id=context.user_id
        )
        if (
            plan is not None
            and plan.privacy_manifest is not None
            and plan.request_hash == request_hash
        ):
            result.append(node)
    return result
