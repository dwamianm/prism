"""Request-scoped admitted reads and explicit selected-source access.

No ordinary reader receives or constructs evidence authority. The context keeps
opaque admission records for one request, never a plaintext source cache.
"""

from __future__ import annotations

import hmac
import json
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from prme.ingestion.admitted_statements import (
    STATEMENT_VERSION, project_admitted_node, project_direct_node,
)
from prme.models import Event, MemoryNode
from prme.models.privacy_reads import (
    ProtectedEventView, ProtectedExtractionView, ProtectedOperationView,
    ProtectedProvenanceView, ProtectedReceiptView, SanitizedEvidenceView,
    SourceAvailability, SourceUnavailable, LegacySourceAvailability,
    SOURCE_LIMITATION, SELECTED_SOURCE_LIMITATION,
)
from prme.privacy.capabilities import EvidenceAccess, OperationBudget, SanitizedContent
from prme.privacy.policy import PrivacyDenied
from prme.privacy.records import PRIVACY_KEY, PrivacyRecord, SanitizationReport
from prme.types import NodeType, Scope

if TYPE_CHECKING:
    from prme.privacy.admission import AdmissionCoordinator


@dataclass
class ReadContext:
    coordinator: AdmissionCoordinator
    user_id: str
    scopes: tuple[Scope, ...] | None = None
    records: dict[UUID, PrivacyRecord] = field(default_factory=dict)
    protected: bool = False
    source_access: EvidenceAccess | None = None
    budget: OperationBudget = field(default_factory=lambda: OperationBudget(uuid4()))
    query_source: SanitizedContent | None = None
    query_sources: tuple[SanitizedContent, ...] = ()
    fallback_snapshot: object | None = None
    selected_records: tuple[PrivacyRecord, ...] = ()
    source_nodes: dict[str, MemoryNode] = field(default_factory=dict)
    evidence_lease_active: bool = False
    used_node_ids: set[UUID] = field(default_factory=set)

    @classmethod
    async def create(cls, coordinator, *, user_id: str, scopes=None, source_access=None, records=None):
        """Use resolved exact-object contributors, or classify the full range.

        Supplied records come from the authoritative identity resolver. Projection
        adds any further contributing records before the final disclosure fence.
        """
        scopes = tuple(Scope(value) for value in scopes) if scopes else None
        rows = await coordinator.owner_records(user_id, scopes) if records is None else tuple(records)
        if any(str(row.snapshot.policy.owner_id) != user_id
               or (scopes is not None and row.snapshot.policy.scope not in scopes) for row in rows):
            raise PrivacyDenied("privacy_boundary_mismatch")
        protected = bool(rows or coordinator.selected())
        if not protected and records is None:
            for scope in scopes or (None,):
                await coordinator.repository.require_legacy_range(user_id, scope.value if scope else None)
        if protected:
            try:
                if str(UUID(user_id)) != user_id:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise PrivacyDenied("privacy_boundary_mismatch") from None
            coordinator.require_runtime()
        result = cls(coordinator, user_id, scopes, {row.event_id: row for row in rows},
                     protected, source_access)
        if protected and not rows:
            scope = scopes[0] if scopes and len(scopes) == 1 else Scope.PERSONAL
            result.fallback_snapshot = coordinator.effective(None, user_id=user_id, scope=scope)
        await result.check()
        return result

    def owns(self, item) -> bool:
        return item.user_id == self.user_id and (self.scopes is None or item.scope in self.scopes)

    async def check(self) -> None:
        self.check_selected()
        await self.coordinator.check_disclosure(tuple(self.records.values()))
        self.check_lifetimes()

    def check_lifetimes(self) -> None:
        """Recheck every contributor without awaiting another authorization."""
        self.check_selected()
        for row in self.records.values():
            self.coordinator.check_deadlines(row.snapshot)
        if self.fallback_snapshot is not None:
            self.coordinator.check_deadlines(self.fallback_snapshot)

    async def disclose(self, value):
        """Fence a completed result; never hold a policy lock around more I/O."""
        async with self.guard():
            return value

    def check_selected(self) -> None:
        if self.source_access is None:
            return
        access = self.source_access
        now = self.coordinator.require_runtime().clock()
        if (access.kind != "runtime" or not 1 <= len(access.selected_ids) <= 128
                or len(set(access.selected_ids)) != len(access.selected_ids)):
            raise PrivacyDenied("privacy_denied")
        if now >= access.expires_at:
            raise PrivacyDenied("privacy_expired")
        for row in self.selected_records:
            self.coordinator.check_deadlines(row.snapshot)
            if row.snapshot.evidence_deadline is not None and now >= row.snapshot.evidence_deadline:
                raise PrivacyDenied("privacy_expired")

    @asynccontextmanager
    async def guard(self, effect="disclosure"):
        """Keep current authority through an actual effect, then check deadlines."""
        rows = tuple(self.records.values())
        self.check_selected()
        await self.coordinator.check_disclosure(rows)
        groups = {}
        for row in rows:
            saved = row.snapshot
            self.coordinator.check_deadlines(saved)
            key = (saved.policy.project_id, saved.policy.owner_id, saved.policy.scope)
            groups.setdefault(key, saved)
        if not groups and self.fallback_snapshot is not None:
            groups[None] = self.fallback_snapshot
        async with AsyncExitStack() as stack:
            for saved in groups.values():
                await stack.enter_async_context(self.coordinator.guard(saved, effect, records=rows, node_ids=self.used_node_ids))
            self.check_selected()
            await self.coordinator.erasure.assert_records_current(self.selected_records, purpose="evidence")
            for row in rows:
                self.coordinator.check_deadlines(row.snapshot)
            yield self.check_lifetimes
            self.check_selected()
            await self.coordinator.erasure.assert_records_current(self.selected_records, purpose="evidence")
            for row in rows:
                self.coordinator.check_deadlines(row.snapshot)
            if self.fallback_snapshot is not None:
                self.coordinator.check_deadlines(self.fallback_snapshot)
        # Guard release can itself await. Earlier contributors must still be
        # live after the final release, immediately before returning the value.
        self.check_lifetimes()

    @property
    def source_limitation(self):
        return SELECTED_SOURCE_LIMITATION if self.source_nodes else SOURCE_LIMITATION

    async def prepare_selected_sources(self, scopes=None) -> None:
        """Open only the explicit finite selection, never a search or cache."""
        if self.source_access is None:
            return
        from prme.epistemic.inference import infer_source_type
        self.check_selected()
        rows = []
        for event_id in self.source_access.selected_ids:
            row = await self.record_for_event(str(event_id))
            if (row is None or row.evidence is None or row.snapshot.policy.evidence_use != "runtime"
                    or (scopes is not None and row.snapshot.policy.scope not in scopes)):
                raise PrivacyDenied("privacy_denied")
            rows.append(row)
        self.selected_records = tuple(rows)
        self.check_selected()
        for row in rows:
            view = await read_source(self.coordinator, str(row.event_id), user_id=self.user_id,
                                     access=self.source_access, scope=row.snapshot.policy.scope)
            availability = self.availability(row).model_copy(update={"state": "available"})
            self.source_nodes[str(row.event_id)] = MemoryNode(
                id=row.event_id, user_id=self.user_id, scope=row.snapshot.policy.scope,
                node_type=NodeType.NOTE, content=view.content,
                created_at=row.snapshot.admitted_at, updated_at=row.snapshot.admitted_at,
                event_time=view.event_time, valid_from=view.event_time or row.snapshot.admitted_at,
                source_type=infer_source_type(NodeType.NOTE, role=view.role),
                evidence_refs=[row.event_id],
                metadata={"source_representation": "sanitized", "speaker": view.speaker,
                          "source_availability": [availability.model_dump(mode="json")]},
            )
        self.check_selected()

    @asynccontextmanager
    async def evidence_lease(self):
        if self.source_access is None:
            yield
            return
        self.check_selected()
        protector = self.coordinator.require_runtime().protector
        try:
            async with protector.hold_evidence_access(
                tuple((row.evidence, row.snapshot) for row in self.selected_records),
                self.source_access, guard=lambda: self.guard("disclosure"),
            ):
                self.check_selected()
                self.evidence_lease_active = True
                yield
                self.check_selected()
        finally:
            self.evidence_lease_active = False
            self.source_nodes.clear()

    async def record_for_event(self, event_id: str) -> PrivacyRecord | None:
        row = await self.coordinator.repository.get(event_id, user_id=self.user_id)
        if row is None or (self.scopes is not None and row.snapshot.policy.scope not in self.scopes):
            return None
        runtime = self.coordinator.require_runtime()
        if row.snapshot.policy.project_id != runtime.project_id:
            raise PrivacyDenied("privacy_boundary_mismatch")
        self.records[row.event_id] = row
        await self.coordinator.check_disclosure((row,))
        return row

    async def project_node(self, node: MemoryNode | None) -> MemoryNode | None:
        if node is None or not self.owns(node):
            return None
        rows = await self.coordinator.repository.records_for_objects(
            [("nodes", str(node.id))], owner=self.user_id,
        )
        reference = (node.metadata or {}).get(PRIVACY_KEY)
        if not rows:
            # A configured protected read does not silently reinterpret an old
            # source note as newly admitted memory. Migration owns classification.
            return None if self.protected else node.model_copy(deep=True)
        if len(await self.coordinator.erasure.current_records(rows)) != len(rows):
            return None
        try:
            async with self.coordinator.erasure.guard(rows, node_ids=(node.id,)):
                pass
        except PrivacyDenied as exc:
            if exc.code == "privacy_erased":
                return None
            raise
        self.used_node_ids.add(node.id)
        self.protected = True
        self.records.update((row.event_id, row) for row in rows)
        await self.coordinator.check_disclosure(rows)
        if not isinstance(reference, dict):
            raise PrivacyDenied("privacy_processing_unavailable")
        origin = next((row for row in rows if str(row.event_id) == reference.get("event_id")), None)
        base_reference = {key: value for key, value in reference.items() if key != "sanitization"}
        if origin is None or base_reference != origin.reference():
            raise PrivacyDenied("privacy_invalid_result")
        if "sanitization" in reference:
            try:
                SanitizationReport.model_validate(reference["sanitization"])
            except ValueError:
                raise PrivacyDenied("privacy_invalid_result") from None
        if any(row.snapshot.policy.scope != node.scope for row in rows):
            raise PrivacyDenied("privacy_boundary_mismatch")
        from prme.privacy.journals import JOURNAL_KEY, project_generated_node
        if JOURNAL_KEY in (node.metadata or {}):
            result = project_generated_node(node)
        elif origin.kind == "direct":
            result = project_direct_node(node)
        else:
            if (node.node_type != NodeType.ENTITY
                    and (node.metadata or {}).get("grounding_method") != STATEMENT_VERSION):
                raise PrivacyDenied("privacy_processing_unavailable")
            result = project_admitted_node(node)
        # Public availability is an observation; the immutable enrolled identity
        # remains separate and carries no private representation checksum.
        metadata = dict(result.metadata or {})
        metadata["source_availability"] = [
            self.availability(row).model_dump(mode="json") for row in rows
        ]
        return result.model_copy(update={"metadata": metadata})

    def availability(self, record: PrivacyRecord) -> SourceAvailability:
        saved = record.snapshot
        now = self.coordinator.require_runtime().clock()
        if record.evidence_disposition is not None:
            state = record.evidence_disposition
        elif saved.evidence_deadline is not None and now >= saved.evidence_deadline:
            state = "expired"
        elif saved.policy.capture == "none":
            state = "not_captured"
        elif record.evidence is None:
            state = "missing"
        elif self.evidence_lease_active and str(record.event_id) in self.source_nodes:
            state = "available"
        else:
            # Ordinary inspection never probes private-key readiness. Even
            # standard runtime evidence requires a separately selected grant.
            state = "sealed"
        return SourceAvailability(source_id=record.source_id, representation_id=record.representation_id,
                                  policy_id=saved.policy.policy_id, policy_revision=saved.policy.revision,
                                  state=state)

    async def event_view(self, event: Event):
        if not self.owns(event):
            return None
        enrolled = await self.coordinator.repository.get(str(event.id), user_id=self.user_id)
        if enrolled is not None and not await self.coordinator.erasure.current_records((enrolled,)):
            return None
        record = await self.record_for_event(str(event.id))
        if record is None:
            if not self.protected:
                return event
            availability = LegacySourceAvailability()
        else:
            availability = self.availability(record)
        return ProtectedEventView(id=event.id, user_id=event.user_id, scope=event.scope,
                                  timestamp=event.timestamp, event_time=event.event_time,
                                  source_availability=availability)

    async def sanitize_query(self, query: str) -> str:
        from prme.privacy.admission import _fields
        if not self.protected:
            return query
        snapshots = [row.snapshot for row in self.records.values()]
        if not snapshots:
            scope = self.scopes[0] if self.scopes and len(self.scopes) == 1 else Scope.PERSONAL
            snapshots = [self.coordinator.effective(None, user_id=self.user_id, scope=scope)]
        # Every applicable saved policy must permit this query. Multiple
        # boundaries may sanitize differently; never mix their source tokens.
        admitted = []
        unique = {}
        for saved in snapshots:
            unique.setdefault((saved.policy, saved.references), saved)
        for saved in unique.values():
            result = await self.coordinator.sanitize(
                _fields(query, None), saved, self.budget, mode="query",
            )
            admitted.append(result)
        texts = [json.loads(result.fields_json)["content"] for result in admitted]
        if not texts or len(set(texts)) != 1:
            raise PrivacyDenied("privacy_policy_conflict")
        self.query_source = admitted[0]
        self.query_sources = tuple(admitted)
        return texts[0]

    async def generated(self, value, *, sources=None):
        """Admit generated text before parsing, retrying, logging or reuse."""
        from prme.privacy.sanitizer import canonical
        sources = tuple(sources or self.query_sources)
        if not sources:
            raise PrivacyDenied("privacy_boundary_mismatch")
        admitted = []
        for source in sources:
            result = await self.coordinator.sanitize(
                canonical(value), source.source_snapshot, self.budget,
                mode="generated_output", source=source,
            )
            admitted.append(result.fields_json)
        if len(set(admitted)) != 1:
            raise PrivacyDenied("privacy_policy_conflict")
        return json.loads(admitted[0])

    async def generated_sources(self, value):
        """Admit a request-local collection of already admitted memory."""
        from prme.privacy.admission import _fields
        from prme.privacy.sanitizer import canonical
        fields = _fields(canonical(value), None)
        saved = { (row.snapshot.policy, row.snapshot.references): row.snapshot
                 for row in self.records.values() }
        if not saved and self.fallback_snapshot is not None:
            saved[None] = self.fallback_snapshot
        return tuple([
            await self.coordinator.sanitize(fields, snapshot, self.budget, mode="query")
            for snapshot in saved.values()
        ])

    @asynccontextmanager
    async def transmission_guard(self):
        from prme.ingestion.extraction import protected_provider_logs
        with protected_provider_logs():
            async with self.guard("transmission"):
                yield

    async def reformulate(self, pipeline, query):
        from prme.ingestion.extraction import InstructorExtractionProvider
        from prme.retrieval.reformulation import QueryReformulations, REFORMULATION_SYSTEM_PROMPT
        from prme.privacy.admission import capability_call
        provider = InstructorExtractionProvider(
            f"{pipeline._query_reformulation_provider}/{pipeline._query_reformulation_model}",
            model=pipeline._query_reformulation_model, max_retries=0,
            api_key=pipeline._query_reformulation_api_key,
            base_url=pipeline._query_reformulation_base_url,
            timeout=pipeline._query_reformulation_timeout or 60.0,
        ).privacy_adapter()
        count = max(1, min(pipeline._query_reformulation_count, 5))
        raw = await capability_call(provider.generate(
            self.query_source, self.query_source.source_snapshot,
            schema=QueryReformulations.model_json_schema(),
            system_prompt=REFORMULATION_SYSTEM_PROMPT + f"\nReturn at most {count} queries.",
            guard=self.transmission_guard,
        ), "privacy_provider_unavailable")
        admitted = await self.generated(raw)
        try:
            result = QueryReformulations.model_validate(admitted)
        except ValueError:
            raise PrivacyDenied("privacy_invalid_result") from None
        original = query.strip().casefold()
        seen = {original}
        values = []
        for value in result.queries:
            clean = value.strip()
            if clean and clean.casefold() not in seen:
                values.append(clean)
                seen.add(clean.casefold())
        return values[:count]

    @property
    def snapshot(self):
        if self.records:
            return next(iter(self.records.values())).snapshot
        if self.fallback_snapshot is not None:
            return self.fallback_snapshot
        raise PrivacyDenied("privacy_boundary_mismatch")


class ProtectedVectorSearch:
    """Use the explicit guarded embedding capability and the existing index."""

    protected_reads = True

    def __init__(self, index, context):
        self.index = index
        self.context = context

    async def search(self, query, user_id, **options):
        from prme.privacy.admission import capability_call
        from prme.storage.embedding import _validated_vectors, has_query_encoder
        if user_id != self.context.user_id:
            raise PrivacyDenied("privacy_boundary_mismatch")
        runtime = self.context.coordinator.require_runtime()
        provider = self.index._provider
        def identity(value):
            return (value.model_name, value.model_version, value.dimension)
        expected = identity(provider)
        if identity(runtime.embedder) != expected:
            raise PrivacyDenied("privacy_provider_unavailable")
        await self.context.check()
        if has_query_encoder(provider):
            encoder = getattr(runtime.embedder, "encode_query", None)
            if not callable(encoder):
                raise PrivacyDenied("privacy_provider_unavailable")
            values = [await capability_call(encoder(
                query, self.context.snapshot, guard=self.context.transmission_guard,
            ), "privacy_provider_unavailable")]
        else:
            values = await capability_call(runtime.embedder.encode(
                (query,), self.context.snapshot, guard=self.context.transmission_guard,
            ), "privacy_provider_unavailable")
        if identity(provider) != expected or identity(runtime.embedder) != expected:
            raise PrivacyDenied("privacy_invalid_result")
        try:
            values = _validated_vectors(values, count=1, dimension=expected[2])
        except ValueError:
            raise PrivacyDenied("privacy_invalid_result") from None
        async with self.context.guard():
            return await self.index.search_by_vector(values[0], user_id, **options)


class ProtectedLexicalSearch:
    protected_reads = True

    def __init__(self, index, context):
        self.index = index
        self.context = context

    async def search(self, query, user_id, **options):
        if user_id != self.context.user_id:
            raise PrivacyDenied("privacy_boundary_mismatch")
        async with self.context.guard():
            return await self.index.search(query, user_id, **options)


async def prepare_retrieval(engine, query, *, user_id, scopes, source_access=None):
    """Clone only the request facade; never replace an engine-global backend."""
    from copy import copy
    from prme.privacy.read_graph import AdmittedGraphView

    context = await ReadContext.create(engine._privacy, user_id=user_id, source_access=source_access)
    if not context.protected:
        return engine._retrieval_pipeline, query, None
    query = await context.sanitize_query(query)
    pipeline = copy(engine._retrieval_pipeline)
    pipeline._graph_store = AdmittedGraphView(engine._graph_store, context)
    pipeline._vector_index = ProtectedVectorSearch(engine._vector_index, context)
    pipeline._lexical_index = ProtectedLexicalSearch(engine._lexical_index, context)
    pipeline._privacy_context = context
    await context.prepare_selected_sources(scopes)
    if pipeline._reranker is not None:
        adapter = getattr(pipeline._reranker, "privacy_adapter", None)
        if adapter is None:
            raise PrivacyDenied("privacy_provider_unavailable")
        pipeline._reranker = adapter(context)
    if pipeline._temporal_relation_enricher is not None:
        enricher = copy(pipeline._temporal_relation_enricher)
        for name in ("_resolver", "_gate"):
            adapter = getattr(getattr(enricher, name), "privacy_adapter", None)
            if adapter is None:
                raise PrivacyDenied("privacy_provider_unavailable")
            setattr(enricher, name, adapter(context))
        pipeline._temporal_relation_enricher = enricher
    return pipeline, query, context


async def inspect_source(coordinator, event_id: str, *, user_id: str, scope: Scope | None = None):
    """Inspect one owned source without any evidence-private capability call."""
    row = await coordinator.repository.get(event_id, user_id=user_id)
    if row is None or (scope is not None and row.snapshot.policy.scope != Scope(scope)):
        return SourceUnavailable()
    scopes = [scope] if scope else None
    try:
        context = await ReadContext.create(coordinator, user_id=user_id, scopes=scopes, records=(row,))
    except PrivacyDenied as exc:
        if exc.code != "privacy_erased":
            raise
        # The exact-record disclosure check already denies an erased source.
        # Inspection reports that state from the owner-bound context instead,
        # in the source's own scope so a non-personal default still resolves.
        context = await ReadContext.create(coordinator, user_id=user_id,
                                           scopes=scopes or [row.snapshot.policy.scope])
    async with context.guard():
        available = context.availability(row)
        try:
            await coordinator.erasure.assert_records_current((row,), purpose="evidence")
        except PrivacyDenied as exc:
            if exc.code != "privacy_erased":
                raise
            return available.model_copy(update={"state": "expired" if available.state == "expired" else "erased"})
        return available


async def read_source(coordinator, event_id: str, *, user_id: str, access: EvidenceAccess,
                      scope: Scope | None = None) -> SanitizedEvidenceView:
    """Explicit selected evidence access; no diagnostic capability is minted here."""
    row = await coordinator.repository.get(event_id, user_id=user_id)
    if row is None or (scope is not None and row.snapshot.policy.scope != Scope(scope)):
        raise PrivacyDenied("privacy_denied")
    runtime = coordinator.require_runtime()
    if row.snapshot.policy.project_id != runtime.project_id or access is None:
        raise PrivacyDenied("privacy_denied")
    if (row.evidence is None or (row.snapshot.evidence_deadline is not None
                                and runtime.clock() >= row.snapshot.evidence_deadline)):
        raise PrivacyDenied("privacy_denied")
    if access.kind == "runtime" and row.snapshot.policy.evidence_use != "runtime":
        raise PrivacyDenied("privacy_denied")
    opened = getattr(runtime.protector, "open_held", None)
    if opened is None:
        raise PrivacyDenied("privacy_capability_unavailable")
    try:
        async with opened(row.evidence, access, original_snapshot=row.snapshot,
                          guard=lambda: coordinator.guard(row.snapshot, "disclosure", records=(row,), purpose="evidence")) as raw:
            from prme.privacy.sanitizer import canonical, sanitized_checksum
            data = json.loads(raw)
            fields = {name: data[name] for name in (
                "content", "retrieval_content", "metadata", "speaker", "value_bindings",
            )}
            if (data["representation"] != "sanitized" or data["checksum_scope"] != "sanitized_source_unit_v1"
                    or data["sanitizer_version"] != row.snapshot.policy.sanitizer_version
                    or data["registry_version"] != row.snapshot.policy.registry_version
                    or not hmac.compare_digest(data["private_checksum"], sanitized_checksum(
                        canonical(fields), data["role"], data["event_time"],
                    ))):
                raise PrivacyDenied("privacy_invalid_result")
            view = SanitizedEvidenceView(
                event_id=row.event_id, source_id=row.source_id, representation_id=row.representation_id,
                content=data["content"], speaker=data["speaker"], role=data["role"],
                event_time=data["event_time"], sanitizer_version=data["sanitizer_version"],
                registry_version=data["registry_version"],
            )
            return view
    except PrivacyDenied:
        raise
    except Exception:
        raise PrivacyDenied("privacy_invalid_result") from None


async def require_legacy_history(coordinator, user_id: str | None) -> None:
    if coordinator.selected():
        raise PrivacyDenied("privacy_unsupported_input")
    try:
        await coordinator.repository.require_legacy_range(user_id, None)
    except PrivacyDenied:
        raise PrivacyDenied("privacy_unsupported_input") from None


async def project_public_result(coordinator, method: str, result, arguments: dict, records):
    """Final closed projections for the ordinary public response siblings."""
    owner = arguments.get("user_id") or arguments.get("kwargs", {}).get("user_id")
    if not records and not coordinator.selected():
        await coordinator.require_legacy_call(owner, arguments)
        if method == "get_event_nodes":
            await coordinator.require_legacy_inputs(nodes=result)
        return result
    if not owner:
        raise PrivacyDenied("privacy_boundary_mismatch")
    context = await ReadContext.create(
        coordinator, user_id=owner,
        records=records if coordinator._call_identifiers(arguments) else None,
    )
    context.records.update((record.event_id, record) for record in records)
    projected = await _project_public_result(context, method, result, arguments, records)
    return await context.disclose(projected)


async def _project_public_result(context, method, result, arguments, records):
    coordinator, owner = context.coordinator, context.user_id
    if method == "get_event":
        return None if result is None else await context.event_view(result)
    if method == "get_events":
        values = [await context.event_view(event) for event in result]
        return [value for value in values if value is not None]
    if method == "get_extraction":
        row = await context.record_for_event(arguments["event_id"])
        return None if row is None else ProtectedExtractionView(
            event_id=row.event_id, source_availability=context.availability(row),
        )
    if method == "get_node":
        return await context.project_node(result)
    if method == "evaluate_condition":
        return await context.project_node(result)
    if method in {"promote", "archive", "supersede", "reinforce"} and result is None:
        return None
    if method in {"query_nodes", "scan_nodes", "get_event_nodes"}:
        values = [await context.project_node(node) for node in result]
        return [value for value in values if value is not None]
    if method == "get_provenance":
        if result is None:
            return None
        node = await context.project_node(result.node)
        if node is None:
            return None
        evidence = [await context.event_view(event) for event in result.evidence_events]
        operations = []
        for operation in result.operations:
            operations.append(ProtectedOperationView(
                id=operation.id, target_id=operation.target_id,
                created_at=operation.created_at,
            ))
        from prme.privacy.read_graph import AdmittedGraphView
        graph = AdmittedGraphView(coordinator.engine._graph_store, context)
        edges = await graph.get_edges(node_ids=[str(node.id)], include_superseded=True)
        allowed = {edge.id: edge for edge in edges}
        return ProtectedProvenanceView(
            node=node, evidence_events=tuple(item for item in evidence if isinstance(item, ProtectedEventView)),
            missing_evidence_refs=result.missing_evidence_refs, operations=tuple(operations),
            contradiction_edges=tuple(allowed[edge.id] for edge in result.contradiction_edges if edge.id in allowed),
            next_operation_cursor=result.next_operation_cursor,
        )
    if method == "get_retrieval_receipt":
        if result is None:
            return None
        from prme.models.privacy_receipts import ProtectedRetrievalReceipt
        if isinstance(result, ProtectedRetrievalReceipt):
            return result
        return ProtectedReceiptView(
            receipt_id=result.request_id, user_id=owner, created_at=result.reference_time,
            source_availability=tuple(context.availability(row) for row in records),
        )
    if method in {"record_relevance", "get_relevance", "list_relevance"}:
        from prme.models.relevance import RelevanceRecord
        values = result if isinstance(result, list) else [result]
        if all(value is None or isinstance(value, RelevanceRecord) for value in values):
            return result
    if method in {"record_answer_citations", "get_answer_citations", "list_answer_citations"}:
        from prme.models.privacy_receipts import ProtectedCitationRecord
        values = result if isinstance(result, list) else [result]
        if all(value is None or isinstance(value, ProtectedCitationRecord) for value in values):
            return result
    if method in {"contradict", "resolve_contradiction"}:
        return tuple([await context.project_node(node) for node in result])
    if method == "review_alias_proposal":
        from prme.storage.alias_review import AliasProposalReviewResult

        if isinstance(result, AliasProposalReviewResult):
            return result
    if method == "list_alias_proposals":
        from prme.storage.alias_proposal import ProtectedAliasProposalRecord

        if all(
            isinstance(item.proposal, ProtectedAliasProposalRecord) for item in result
        ):
            return result
    if method in {"organize", "end_session"}:
        from prme.privacy.diagnostics import project_organize

        return project_organize(result)
    if method == "discard_profile" and type(result) is bool:
        return result
    if method == "collect_profile_staging":
        from prme.privacy.diagnostics import project_collection
        return project_collection(result)
    if method == "consolidate_knowledge" and type(result) is int and result >= 0:
        return result
    if method == "rebuild_indexes" and isinstance(result, dict):
        fields = {"nodes_indexed", "nodes_skipped", "total_active"}
        if set(result) == fields and all(type(value) is int and value >= 0 for value in result.values()):
            return result
    if method == "resume_profile" and (result is None or str(UUID(result)) == result):
        return result
    if method == "profile_jobs":
        from prme.privacy.diagnostics import project_jobs
        return project_jobs(result, identity="plan_id")
    if method == "process_profiles":
        from prme.privacy.diagnostics import project_processing
        return project_processing(result)
    if method in {
        "retrieve", "aggregate_assertions", "aggregate_quantities", "aggregate_quantities_from_text",
        "get_assertion_state", "count_nodes", "processing_status", "extraction_status", "snapshot",
    }:
        return result
    # Unsupported raw receipts, alias payloads, historical evaluation and
    # extension readers are explicit denials until a closed contract exists.
    raise PrivacyDenied("privacy_unsupported_input")
