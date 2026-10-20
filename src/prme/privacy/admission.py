"""One opt-in admission coordinator for Python, transports and deferred work.

Only admitted memory and opaque source envelopes reach ordinary persistence.
Sanitizer, encryption, extraction and embeddings are explicit capabilities;
production implementations are supplied by the separately owned integrations.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Concatenate, ParamSpec, TypeVar
from uuid import UUID, uuid4

from prme.models import Event, MemoryNode
from prme.models.derivation import DerivationPlan, PreparedEmbedding, PreparedLexicalDocument
from prme.privacy.capabilities import (
    AdmissionContent, BudgetContinuation, OperationBudget, PayloadContext, PrivacyRuntime, ProcessingAccess,
    RetryContext, SanitizedContent, SourceOnlyRegion,
)
from prme.privacy.policy import PolicySnapshot, PrivacyDenied, PrivacyPolicy, snapshot
from prme.privacy.records import PRIVACY_KEY, PrivacyRecord, SanitizationReport
from prme.storage._threading import run_async_to_completion
from prme.storage.privacy import PrivacyRepository
from prme.types import NodeType, Scope


def finite_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise PrivacyDenied("privacy_unsupported_input") from None


async def capability_call(operation: Awaitable[T], code: str = "privacy_capability_unavailable") -> T:
    """A capability failure must not turn private provider input into an error."""
    try:
        return await operation
    except PrivacyDenied:
        raise
    except Exception:
        raise PrivacyDenied(code) from None


async def preflight_runtime(config, runtime: PrivacyRuntime | None) -> None:
    """Readiness precedes files/schema/providers for an explicit protected default."""
    if config.privacy_policy is None:
        return
    validate_protected_config(config)
    if runtime is None or any(getattr(runtime, name, None) is None
                              for name in ("authority", "sanitizer", "protector", "embedder")):
        raise PrivacyDenied("privacy_capability_unavailable")
    policy = config.privacy_policy
    if policy.project_id != runtime.project_id or (config.namespace_id is not None and config.namespace_id != policy.project_id):
        raise PrivacyDenied("privacy_boundary_mismatch")
    if policy.unlimited_evidence and not runtime.allow_unlimited_evidence:
        raise PrivacyDenied("privacy_configuration_invalid")
    saved = snapshot(policy, None, runtime.clock())
    async with runtime.authority.guard(saved, "admission"):
        purposes = tuple(p for p, required in (("evidence", policy.capture != "none"),
                                               ("processing", policy.durable_processing_input)) if required)
        await capability_call(runtime.protector.ready(saved, purposes))


def validate_protected_config(config) -> None:
    # The legacy post-publication helper selects an additional owner entity
    # outside the plan's admitted dependencies. Its protected integration is
    # unavailable until it carries those dependencies and their authority.
    if config.enable_owner_group_links:
        raise PrivacyDenied("privacy_configuration_invalid")


P = ParamSpec("P")
T = TypeVar("T")


def privacy_disclosure(function: Callable[Concatenate[Any, P], Awaitable[T]]) -> Callable[Concatenate[Any, P], Awaitable[T]]:
    """Public read fence, including reopening with no configured policy/runtime.

    Source projection is PV-05's responsibility. This wraps the actual public
    return boundary; it does not decode any payload or rewrite historic receipts.
    """
    import functools
    import inspect
    signature = inspect.signature(function)

    @functools.wraps(function)
    async def guarded(self: Any, *args: P.args, **kwargs: P.kwargs) -> T:
        bound = signature.bind(self, *args, **kwargs)
        arguments = bound.arguments
        owner = arguments.get("user_id") or arguments.get("kwargs", {}).get("user_id")
        coordinator = self._privacy
        records = await coordinator.records_for_call(owner, arguments)
        if records and owner is None:
            raise PrivacyDenied("privacy_boundary_mismatch")
        await coordinator.check_disclosure(records)
        if function.__name__ in _UNAVAILABLE_EFFECTS and (records or coordinator.selected()):
            # The legacy retrieval pipeline includes unguarded optional providers
            # and receipt source augmentation. PV-04/PV-05 integrate those paths.
            raise PrivacyDenied("privacy_provider_unavailable")
        result = await function(self, *args, **kwargs)
        records = await coordinator.records_for_call(owner, arguments)
        if records and owner is None:
            raise PrivacyDenied("privacy_boundary_mismatch")
        await coordinator.check_disclosure(records)
        return result
    return guarded


_UNAVAILABLE_EFFECTS = frozenset({
    "retrieve", "organize", "consolidate_knowledge", "resume_profile", "process_profiles", "end_session",
    "rebuild_indexes", "snapshot", "embed_texts", "propose_product_alignment", "find_product_alignment_candidates",
    "evaluate_full_retrieval", "create_ranking_profile", "record_relevance", "record_answer_citations",
    "promote", "evaluate_condition", "supersede", "contradict", "resolve_contradiction", "archive", "reinforce",
    "review_alias_proposal", "feedback",
})


def _fields(content: str, metadata: dict | None, speaker: str | None = None,
            retrieval_content: str | None = None, value_bindings=None) -> str:
    if not isinstance(content, str) or (retrieval_content is not None and not isinstance(retrieval_content, str)):
        raise PrivacyDenied("privacy_unsupported_input")
    if metadata is not None and not isinstance(metadata, dict):
        raise PrivacyDenied("privacy_unsupported_input")
    if any(str(key).startswith("_prme_privacy") for key in (metadata or {})):
        raise PrivacyDenied("privacy_reserved_metadata")
    if "_prme_chat_message" in (metadata or {}):
        # Lossless framework history is not an admitted-memory representation.
        raise PrivacyDenied("privacy_unsupported_input")
    bindings = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in value_bindings] if value_bindings else []
    return finite_json({"content": content, "retrieval_content": retrieval_content, "metadata": metadata or {},
                        "speaker": speaker, "value_bindings": bindings})


@dataclass(frozen=True, repr=False)
class PreparedAdmission:
    record: PrivacyRecord
    event: Event
    source: SanitizedContent
    budget: OperationBudget
    generated_reports: list[SanitizationReport] = field(default_factory=list)


class AdmissionCoordinator:
    def __init__(self, engine, runtime: PrivacyRuntime | None = None):
        self.engine = engine
        self.runtime = runtime
        policy = engine._config.privacy_policy
        self.default = PrivacyPolicy.model_validate_json(policy.model_dump_json()) if policy else None
        self.repository = PrivacyRepository(engine._event_store)

    def selected(self, policy: PrivacyPolicy | None = None) -> bool:
        return policy is not None or self.default is not None

    async def require_legacy_reingest(self, user_id: str, scope=None) -> None:
        """Reingestion has no claimed protected processing capability."""
        if self.selected():
            raise PrivacyDenied("privacy_provider_unavailable")
        await self.repository.require_legacy_range(user_id, None if scope is None else Scope(scope).value)

    async def require_legacy_inputs(self, events=(), nodes=()) -> None:
        """Recheck actual participating identities at an ordinary provider edge.

        This never authorizes protected work through an absent claim. Supported
        protected work uses the coordinator's saved-snapshot capability guards.
        """
        if self.selected():
            raise PrivacyDenied("privacy_provider_unavailable")
        for table, objects in (("events", events), ("nodes", nodes)):
            for item in objects:
                if PRIVACY_KEY in (item.metadata or {}):
                    raise PrivacyDenied("privacy_provider_unavailable")
                records = await self.repository.records_for_objects([(table, str(item.id))], owner=item.user_id)
                if records:
                    raise PrivacyDenied("privacy_provider_unavailable")

    async def legacy_group_reads(self, user_id: str, scope: Scope, *, event: Event | None = None,
                                 effect_check: Callable[[], Awaitable[None]] | None = None):
        """Refuse protected group inputs before a helper can inspect/cache them."""
        scope = Scope(scope)

        async def check(*, events=(), nodes=()):
            events = tuple(events) + ((event,) if event is not None else ())
            for item in (*events, *nodes):
                if (item.user_id, item.scope) != (user_id, scope):
                    raise PrivacyDenied("privacy_boundary_mismatch")
            await self.require_legacy_inputs(events, nodes)
            # Group lookup scans this owner/scope beyond the plan's references.
            await self.repository.require_legacy_range(user_id, scope.value)
            if effect_check is not None:
                await effect_check()

        def checked(read, kind=None, *, single=False):
            async def guarded(*args, **kwargs):
                await check()
                result = await read(*args, **kwargs)
                values = (() if result is None else (result,)) if single else result
                await check(**{kind: values} if kind is not None else {})
                return result
            return guarded

        await check()  # The caller's initial event is checked before any helper.
        graph, events = self.engine._graph_store, self.engine._event_store
        return (
            SimpleNamespace(scan_nodes=checked(graph.scan_nodes, "nodes"),
                            query_nodes=checked(graph.query_nodes, "nodes"),
                            get_edges=checked(graph.get_edges)),
            SimpleNamespace(get=checked(events.get, "events", single=True),
                            get_by_user=checked(events.get_by_user, "events")),
            check,
        )

    async def link_legacy_groups(self, event_id: str, sources, *, user_id: str, scope: Scope,
                                 event: Event | None = None,
                                 effect_check: Callable[[], Awaitable[None]] | None = None,
                                 submit=None) -> int:
        """Best-effort legacy links; protected group processing is unavailable."""
        from prme.ingestion.group_links import link_group_claims
        import structlog
        try:
            graph, events, check = await self.legacy_group_reads(
                user_id, scope, event=event, effect_check=effect_check)
            await check(nodes=sources)
            submit = submit or self.engine._write_queue.submit

            async def create_edge(edge):
                # The native operation repeats the boundary after queue/connection
                # waits. Keep any caller's checked queue (whole request scope and
                # saved references), owned until the accepted write settles.
                return await run_async_to_completion(submit(
                    lambda: self.repository.create_legacy_group_edge(
                        self.engine._graph_store, edge, user_id=user_id, scope=scope),
                    label=f"privacy.group_link:{edge.id}",
                ))

            return await link_group_claims(event_id, sources, user_id=user_id, graph_store=graph,
                                           event_store=events, create_edge=create_edge)
        except Exception as exc:
            # The legacy plan has committed. Refusal of its optional links is
            # not a rollback or a successful protected group publication.
            structlog.get_logger(__name__).warning(
                "group_links.unavailable", event_id=event_id, error_type=type(exc).__name__)
            return 0

    def require_runtime(self) -> PrivacyRuntime:
        validate_protected_config(self.engine._config)
        if self.runtime is None or any(getattr(self.runtime, name, None) is None
                                       for name in ("authority", "sanitizer", "protector", "embedder")):
            raise PrivacyDenied("privacy_capability_unavailable")
        return self.runtime

    def effective(self, policy: PrivacyPolicy | None, *, user_id: str, scope: Scope) -> PolicySnapshot:
        runtime = self.require_runtime()
        try:
            saved = snapshot(self.default, policy, runtime.clock())
            owner = UUID(user_id)
        except (ValueError, TypeError):
            raise PrivacyDenied("privacy_configuration_invalid") from None
        selected = saved.policy
        if str(owner) != user_id or (selected.owner_id, selected.scope, selected.project_id) != (owner, scope, runtime.project_id):
            raise PrivacyDenied("privacy_boundary_mismatch")
        if self.engine._config.namespace_id is not None and selected.project_id != self.engine._config.namespace_id:
            raise PrivacyDenied("privacy_boundary_mismatch")
        if selected.unlimited_evidence and not runtime.allow_unlimited_evidence:
            raise PrivacyDenied("privacy_configuration_invalid")
        return saved

    @asynccontextmanager
    async def guard(self, saved: PolicySnapshot, effect: str, *, processing: bool = False):
        runtime = self.require_runtime()
        if saved.policy.project_id != runtime.project_id:
            raise PrivacyDenied("privacy_boundary_mismatch")
        self.check_deadlines(saved, processing=processing)
        async with runtime.authority.guard(saved, effect):
            # A lease can wait behind policy activation or another writer.
            self.check_deadlines(saved, processing=processing)
            yield

    def check_deadlines(self, saved: PolicySnapshot, *, processing: bool = False) -> None:
        now = self.require_runtime().clock()
        if saved.memory_deadline is not None and now >= saved.memory_deadline:
            raise PrivacyDenied("privacy_expired")
        if processing and now >= saved.processing_deadline:
            raise PrivacyDenied("privacy_expired")

    async def sanitize(self, fields: str, saved: PolicySnapshot, budget: OperationBudget,
                       *, mode="ingress", source: SanitizedContent | None = None,
                       source_role: str = "user", source_time: str | None = None) -> SanitizedContent:
        runtime = self.require_runtime()
        try:
            async with self.guard(saved, "admission"):
                result = await runtime.sanitizer.admit(AdmissionContent(
                    fields_json=fields, snapshot=saved, mode=mode, budget=budget,
                    issued_tokens=source.issued_tokens if source else (),
                    pseudonym_epoch=source.pseudonym_epoch if source else None, source=source,
                    source_role=source.source_role if source else source_role,
                    source_time=source.source_time if source else source_time))
            if not isinstance(result, SanitizedContent):
                raise PrivacyDenied("privacy_invalid_result")
            if (result.sanitizer_version, result.registry_version) != (saved.policy.sanitizer_version, saved.policy.registry_version):
                raise PrivacyDenied("privacy_invalid_result")
            if not result.complete or not result.unambiguous:
                raise PrivacyDenied("privacy_evidence_ambiguous")
            if mode == "generated_output" and result.representation_role != "admitted_memory":
                raise PrivacyDenied("privacy_invalid_result")
            # Reparse to reject nonfinite JSON and mutable/lazy output objects.
            finite_json(json.loads(result.fields_json, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())))
            return result
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_invalid_result") from None

    async def prepare(self, content: str, *, user_id: str, scope: Scope, policy: PrivacyPolicy | None,
                      kind: str, asynchronous: bool, session_id: str | None = None, role: str = "user",
                      speaker: str | None = None, metadata: dict | None = None, event_time: datetime | None = None,
                      retrieval_content: str | None = None, value_bindings=None, first_person_owner: bool = False,
                      budget: OperationBudget | None = None) -> PreparedAdmission:
        saved = self.effective(policy, user_id=user_id, scope=scope)
        if asynchronous and not saved.policy.durable_processing_input:
            raise PrivacyDenied("privacy_async_forbidden")
        try:
            if session_id is not None:
                UUID(session_id)
            if role not in {"user", "human", "participant", "assistant", "tool", "system"}:
                raise ValueError()
            from prme.ingestion.temporal import validate_source_time
            from prme.models.owner_reference import attach_owner_reference
            validate_source_time(event_time)
            metadata = attach_owner_reference(metadata, first_person_owner, role=role, speaker=speaker)
            fields = _fields(content, metadata, speaker, retrieval_content, value_bindings)
        except PrivacyDenied:
            raise
        except (TypeError, ValueError):
            raise PrivacyDenied("privacy_unsupported_input") from None
        budget = budget or OperationBudget(uuid4())
        source = await self.sanitize(fields, saved, budget, source_role=role,
                                     source_time=event_time.isoformat() if event_time else None)
        data = json.loads(source.fields_json)
        if not isinstance(data, dict) or set(data) != {"content", "retrieval_content", "metadata", "speaker", "value_bindings"}:
            raise PrivacyDenied("privacy_invalid_result")
        if not isinstance(data["content"], str) or not isinstance(data["metadata"], dict):
            raise PrivacyDenied("privacy_invalid_result")
        # Complete source unit includes attribution and source clock. Selection
        # happens here, not in the sanitizer/protector fake used in component tests.
        evidence_bytes = finite_json({**data, "role": role,
                                     "event_time": event_time.isoformat() if event_time else None,
                                     "representation": "sanitized", "private_checksum": source.private_checksum,
                                     "checksum_scope": "sanitized_source_unit_v1" if source.source_snapshot else "sanitized_operation_fields",
                                     "sanitizer_version": source.sanitizer_version,
                                     "registry_version": source.registry_version,
                                     "detector_versions": source.detector_versions,
                                     "action_counts": source.action_counts}).encode()
        if saved.policy.capture == "minimal" and len(evidence_bytes) > saved.policy.minimal_max_bytes:
            raise PrivacyDenied("privacy_evidence_too_large")
        event_id = uuid4()
        record = PrivacyRecord(event_id=event_id, snapshot=saved, source_id=source.source_id,
                               representation_id=source.representation_id, kind=kind,
                               sanitization=self.sanitization_report(source))
        purposes = tuple(p for p, needed in (("evidence", saved.policy.capture != "none"),
                                            ("processing", kind == "extraction" and asynchronous)) if needed)
        runtime = self.require_runtime()
        async with self.guard(saved, "admission"):
            await capability_call(runtime.protector.ready(saved, purposes))
            evidence = await capability_call(runtime.protector.seal(evidence_bytes, self.context(record, "evidence"),
                original_snapshot=record.snapshot)) if "evidence" in purposes else None
            processing = await capability_call(runtime.protector.seal(self.source_bytes(source), self.context(record, "processing"),
                original_snapshot=record.snapshot)) if "processing" in purposes else None
        record = record.model_copy(update={"evidence": evidence, "processing": processing,
                                           "processing_state": "pending" if processing else "not_required"})
        for purpose, payload in (("evidence", evidence), ("processing", processing)):
            if payload is not None:
                context = payload.context
                if (context.event_id, context.purpose, context.policy_id, context.policy_revision,
                    context.project_id, context.owner_id, context.scope, context.source_id, context.representation_id) != (
                    event_id, purpose, saved.policy.policy_id, saved.policy.revision, saved.policy.project_id,
                    saved.policy.owner_id, saved.policy.scope.value, source.source_id, source.representation_id):
                    raise PrivacyDenied("privacy_invalid_result")
        # This hash describes the empty envelope, never the removed source.
        event = Event(id=event_id, content="", user_id=user_id, session_id=session_id, role=role, scope=scope,
                      metadata={PRIVACY_KEY: record.reference()}, event_time=event_time,
                      timestamp=saved.admitted_at, created_at=saved.admitted_at)
        return PreparedAdmission(record, event, source, budget)

    @staticmethod
    def sanitization_report(source: SanitizedContent) -> SanitizationReport | None:
        # Older explicit component capabilities have no production provenance.
        if source.source_snapshot is None:
            return None
        return SanitizationReport(detector_versions=source.detector_versions, action_counts=source.action_counts)

    @staticmethod
    def context(record: PrivacyRecord, purpose: str) -> PayloadContext:
        p = record.snapshot.policy
        return PayloadContext(project_id=p.project_id, owner_id=p.owner_id, scope=p.scope.value,
                              event_id=record.event_id, source_id=record.source_id, representation_id=record.representation_id,
                              slot_id=uuid4(), purpose=purpose,
                              payload_kind="sanitized_input" if purpose == "processing" else "sanitized_evidence",
                              policy_id=p.policy_id, policy_revision=p.revision)

    @staticmethod
    def source_bytes(source: SanitizedContent) -> bytes:
        from dataclasses import asdict
        return finite_json({"fields_json": source.fields_json, "source_id": str(source.source_id),
                            "representation_id": str(source.representation_id), "private_checksum": source.private_checksum,
                            "sanitizer_version": source.sanitizer_version, "registry_version": source.registry_version,
                            "detector_versions": source.detector_versions, "action_counts": source.action_counts,
                            "issued_tokens": source.issued_tokens,
                            "pseudonym_epoch": str(source.pseudonym_epoch) if source.pseudonym_epoch else None,
                            "source_snapshot": source.source_snapshot.model_dump(mode="json") if source.source_snapshot else None,
                            "source_role": source.source_role, "source_time": source.source_time,
                            "continuation": {**asdict(source.continuation), "operation_id": str(source.continuation.operation_id)}
                                            if source.continuation else None,
                            "source_only": [asdict(region) for region in source.source_only]}).encode()

    @staticmethod
    def load_source(value: bytes) -> SanitizedContent:
        try:
            data = json.loads(value)
            for key in ("source_id", "representation_id", "pseudonym_epoch"):
                if data[key] is not None:
                    data[key] = UUID(data[key])
            for key in ("issued_tokens", "detector_versions"):
                data[key] = tuple(data[key])
            data["action_counts"] = tuple(tuple(item) for item in data["action_counts"])
            if data.get("source_snapshot") is not None:
                data["source_snapshot"] = PolicySnapshot.model_validate(data["source_snapshot"])
            if data.get("continuation") is not None:
                continuation = data["continuation"]
                continuation["operation_id"] = UUID(continuation["operation_id"])
                data["continuation"] = BudgetContinuation(**continuation)
            data["source_only"] = tuple(SourceOnlyRegion(path=tuple(region["path"]),
                start=region["start"], end=region["end"]) for region in data.get("source_only", ()))
            source = SanitizedContent(**data)
            if source.source_snapshot is not None:
                from prme.privacy.sanitizer import validate_source
                validate_source(source, source.source_snapshot)
            return source
        except Exception:
            raise PrivacyDenied("privacy_processing_unavailable") from None

    def direct_node(self, prepared: PreparedAdmission, **options) -> MemoryNode:
        from prme.epistemic.inference import infer_epistemic_type, infer_source_type
        from prme.ingestion.admitted_statements import project_direct_node
        from prme.models.speaker import attach_speaker
        from prme.models.value_bindings import attach_value_bindings
        data = json.loads(prepared.source.fields_json)
        content = data["retrieval_content"] if data["retrieval_content"] is not None else data["content"]
        metadata = attach_value_bindings(source_content=data["content"], retrieval_content=content,
                                         metadata=data["metadata"], value_bindings=data["value_bindings"] or None)
        metadata = attach_speaker(metadata, data["speaker"])
        metadata = {**(metadata or {}), PRIVACY_KEY: prepared.record.reference()}
        node_type = options.get("node_type", NodeType.NOTE)
        epistemic = options.get("epistemic_type") or infer_epistemic_type(node_type, content=content, role=prepared.event.role)
        source_type = options.get("source_type") or infer_source_type(node_type, role=prepared.event.role)
        confidence = options.get("confidence")
        if confidence is None:
            confidence = self.engine._confidence_matrix.lookup_with_fallback(epistemic, source_type)
        from prme.types import EpistemicType
        if epistemic == EpistemicType.CONDITIONAL:
            if not isinstance(metadata.get("condition"), str) or not metadata["condition"].strip() or metadata.get("condition_state", "unknown") != "unknown":
                raise PrivacyDenied("privacy_invalid_result")
            metadata["condition_state"] = "unknown"
        values = {k: options[k] for k in ("valid_from", "valid_to") if options.get(k) is not None}
        ttl = options.get("ttl_days", ...)
        if ttl is ...:
            ttl = self.engine._config.organizer.default_ttl_days.get(node_type.value)
        node = MemoryNode(user_id=prepared.event.user_id, session_id=prepared.event.session_id, scope=prepared.event.scope,
                          node_type=node_type, content=content, metadata=metadata, evidence_refs=[prepared.event.id],
                          event_time=prepared.event.event_time, epistemic_type=epistemic, source_type=source_type,
                          confidence=confidence, confidence_base=confidence, ttl_days=ttl, **values)
        return project_direct_node(node)

    async def encode(self, texts: tuple[str, ...], saved: PolicySnapshot, *, processing: bool = False):
        runtime = self.require_runtime()
        # Each capability acquires the guard at the actual send, including retries.
        async with self.guard(saved, "transmission", processing=processing):
            pass
        try:
            return await runtime.embedder.encode(texts, saved,
                guard=lambda: self.guard(saved, "transmission", processing=processing))
        except PrivacyDenied:
            raise
        except Exception:
            raise PrivacyDenied("privacy_provider_unavailable") from None

    async def direct_plan(self, record: PrivacyRecord, event: Event, node: MemoryNode) -> DerivationPlan:
        embedder = self.require_runtime().embedder
        values = await self.encode((node.content,), record.snapshot)
        if len(values) != 1:
            raise PrivacyDenied("privacy_invalid_result")
        return DerivationPlan(event_id=event.id, user_id=event.user_id, scope=event.scope, content_hash=event.content_hash,
                              nodes=(node,), embeddings=(PreparedEmbedding(node_id=node.id, content=node.content,
                                  model=embedder.model_name, version=embedder.model_version, dimension=embedder.dimension, values=values[0]),),
                              lexical_documents=(PreparedLexicalDocument(node_id=node.id, content=node.content),))

    async def publish(self, record: PrivacyRecord, plan: DerivationPlan, *, claim=None):
        if record.kind == "extraction" and record.processing_state == "pending" and claim is None:
            raise PrivacyDenied("privacy_processing_unavailable")
        references = await self.validate_references(record, plan.references)

        async def publish_plan():
            await self.engine._event_store.record_derivation_plan(plan, claim=claim)
            await self.engine._pipeline._publish_plan(plan, claim=claim)

        async with self.guard(record.snapshot, "publication", processing=record.processing_state == "pending"):
            for reference in references:
                # validate_references requires the same authority/revisions;
                # the held lease covers them, but each has its own deadline.
                self.check_deadlines(reference.snapshot)
            # Queue consumers own already-enqueued effects independently of
            # the caller. Keep this lease until they settle, then propagate
            # cancellation. This preserves serialization, not rollback.
            await run_async_to_completion(publish_plan())

    async def store(self, content: str, *, policy=None, deferred=False, extract=False, **kwargs) -> str:
        from prme.ingestion.temporal import validate_validity_window
        try:
            validate_validity_window(kwargs.get("valid_from"), kwargs.get("valid_to"))
        except (ValueError, TypeError):
            raise PrivacyDenied("privacy_unsupported_input") from None
        if extract or (extract is None and self.engine._config.enable_store_extraction):
            # Explicit background extraction is handled by ingest; never downgrade it.
            if not self.effective(policy, user_id=kwargs["user_id"], scope=kwargs.get("scope", Scope.PERSONAL)).policy.durable_processing_input:
                raise PrivacyDenied("privacy_async_forbidden")
            raise PrivacyDenied("privacy_provider_unavailable")
        prep_keys = {k: kwargs.pop(k) for k in list(kwargs) if k in {
            "user_id", "scope", "session_id", "role", "speaker", "metadata", "event_time", "retrieval_content", "value_bindings"}}
        prep_keys.setdefault("scope", Scope.PERSONAL)
        prepared = await self.prepare(content, policy=policy, kind="direct", asynchronous=deferred, **prep_keys)
        try:
            node = self.direct_node(prepared, **kwargs)
        except (ValueError, TypeError):
            raise PrivacyDenied("privacy_invalid_result") from None
        # Synchronous store computes embeddings before accepting durable state.
        plan = None if deferred else await self.direct_plan(prepared.record, prepared.event, node)
        async with self.guard(prepared.record.snapshot, "publication"), self.writer_guard((prepared.record,)):
            await self.repository.accept((prepared.event,), (prepared.record,), (node,))
        if plan is not None:
            await self.publish(prepared.record, plan)
            await self.engine._event_store.finish_materialization(str(prepared.event.id))
        else:
            self.engine._materialization_queue.note_added()
        return str(prepared.event.id)

    @asynccontextmanager
    async def writer_guard(self, records):
        async with self.require_runtime().protector.writer_guard(tuple(
            payload for record in records for payload in (record.evidence, record.processing)
            if payload is not None)):
            yield

    async def extraction_plan(self, prepared: PreparedAdmission) -> DerivationPlan:
        from prme.ingestion.grounding import validate_grounding
        from prme.ingestion.schema import ExtractionResult
        runtime = self.require_runtime()
        if runtime.extractor is None:
            raise PrivacyDenied("privacy_provider_unavailable")
        saved = prepared.record.snapshot
        async with self.guard(saved, "transmission", processing=True):
            pass
        result = await capability_call(runtime.extractor.extract(prepared.source, saved,
            guard=lambda: self.guard(saved, "transmission", processing=True)), "privacy_resubmit_required")
        result_fields = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        admitted = await self.sanitize(finite_json({"extraction": result_fields}), saved, prepared.budget,
                                       mode="generated_output", source=prepared.source)
        report = self.sanitization_report(admitted)
        if report is not None:
            prepared.generated_reports.append(report)
        try:
            result = ExtractionResult.model_validate(json.loads(admitted.fields_json)["extraction"])
        except (TypeError, ValueError, KeyError):
            raise PrivacyDenied("privacy_invalid_result") from None
        source = json.loads(prepared.source.fields_json)
        if prepared.source.source_snapshot is not None:
            from prme.ingestion.grounding import validate_private_grounding
            validate_private_grounding(result, source["content"], speaker=source["speaker"])
        result = validate_grounding(result, source["content"], speaker=source["speaker"], content_free_logs=True)
        # This transient event's identity/hash refers to the durable empty envelope.
        # Its actual sanitized checksum remains in the private source representation.
        from prme.models.speaker import attach_speaker
        event = prepared.event.model_copy(update={"content": source["content"],
                                                  "metadata": attach_speaker(source["metadata"], source["speaker"])})
        return await capability_call(self.engine._pipeline._prepare_plan(result, event, privacy=(self, prepared)),
                                     "privacy_invalid_result")

    async def final_generated_bundle(self, prepared: PreparedAdmission, fields: dict) -> tuple[dict, SanitizationReport | None]:
        """The exact final copies are checked before plan/index preparation."""
        result = await self.sanitize(finite_json(fields), prepared.record.snapshot, prepared.budget,
                                     mode="generated_output", source=prepared.source)
        report = self.sanitization_report(result)
        if report is not None:
            from collections import Counter
            prepared.generated_reports.append(report)
            counts = Counter()
            for item in prepared.generated_reports:
                counts.update(dict(item.action_counts))
            report = SanitizationReport(detector_versions=report.detector_versions,
                                        action_counts=tuple(sorted(counts.items())))
        return json.loads(result.fields_json), report

    async def validate_references(self, record: PrivacyRecord, nodes) -> tuple[PrivacyRecord, ...]:
        """Refuse unclassified/cross-policy planner inputs until integrated replay.

        No legacy supporting passage is copied into a new protected plan. PV-05
        and PV-06 own broader source projections and multi-policy work contexts.
        """
        records = []
        for node in nodes:
            reference = (node.metadata or {}).get(PRIVACY_KEY)
            if not isinstance(reference, dict) or not isinstance(reference.get("event_id"), str):
                raise PrivacyDenied("privacy_provider_unavailable")
            saved = await self.repository.get(reference["event_id"], user_id=node.user_id)
            if saved is None or saved.snapshot.policy != record.snapshot.policy or saved.snapshot.references != record.snapshot.references:
                raise PrivacyDenied("privacy_provider_unavailable")
            records.append(saved)
        await self.check_disclosure(records)
        return tuple(records)

    async def ingest_many(self, messages, *, user_id, scope=Scope.PERSONAL, session_id=None,
                          wait_for_extraction=False, policy=None) -> list[str]:
        if self.require_runtime().extractor is None:
            raise PrivacyDenied("privacy_provider_unavailable")
        prepared = []
        budget = OperationBudget(uuid4())
        for msg in messages:
            prepared.append(await self.prepare(policy=policy, kind="extraction", asynchronous=not wait_for_extraction,
                user_id=user_id, scope=scope, session_id=session_id, budget=budget, **dict(msg)))
        if not wait_for_extraction and len(prepared) > 1 and any(item.source.continuation for item in prepared):
            # Disjoint durable grants and replay accounting belong to the live
            # claim integration. Never let every item reuse the batch remainder.
            raise PrivacyDenied("privacy_processing_unavailable")
        plans = []
        if wait_for_extraction:
            try:
                for item in prepared:
                    plans.append(await self.extraction_plan(item))
            except PrivacyDenied:
                raise
            except Exception:
                raise PrivacyDenied("privacy_resubmit_required") from None
        async with self.publication_guards([item.record for item in prepared]), self.writer_guard([p.record for p in prepared]):
            await self.repository.accept([p.event for p in prepared], [p.record for p in prepared],
                                          [None] * len(prepared), extraction=not wait_for_extraction)
        for index, item in enumerate(prepared):
            if wait_for_extraction:
                await self.publish(item.record, plans[index])
            else:
                self.engine._pipeline.queue_extraction(item.event, scope)
        return [str(p.event.id) for p in prepared]

    async def resume(self, event: Event, *, claim) -> None:
        record = await self.repository.get(str(event.id), user_id=event.user_id)
        if record is None or claim is None or claim.event_id != event.id or claim.user_id != event.user_id:
            raise PrivacyDenied("privacy_processing_unavailable")
        async with self.guard(record.snapshot, "admission", processing=True):
            status = await self.engine._event_store.extraction_work.status(str(event.id), user_id=event.user_id)
            if status is None or status.status != "running" or status.generation != claim.generation or status.lease_expires_at != claim.lease_expires_at:
                raise PrivacyDenied("privacy_processing_unavailable")
            if self.require_runtime().clock() >= claim.lease_expires_at:
                raise PrivacyDenied("privacy_processing_unavailable")
        plan = await self.engine._event_store.get_derivation_plan(str(event.id), user_id=event.user_id)
        if plan is None:
            if record.processing is None:
                raise PrivacyDenied("privacy_processing_unavailable")
            access = ProcessingAccess(work_id=event.id, work_generation=claim.generation, lease_deadline=claim.lease_expires_at,
                                      processing_deadline=record.snapshot.processing_deadline, claimed_event_ids=(event.id,))
            raw = await capability_call(self.require_runtime().protector.open(record.processing, access,
                original_snapshot=record.snapshot,
                guard=lambda: self.guard(record.snapshot, "disclosure", processing=True)), "privacy_processing_unavailable")
            source = self.load_source(raw)
            if (source.source_id, source.representation_id) != (record.source_id, record.representation_id):
                raise PrivacyDenied("privacy_processing_unavailable")
            if source.continuation is not None:
                from prme.privacy.sanitizer import restore_budget, validate_source
                validate_source(source, record.snapshot)
                if status.attempts != 1 or claim.attempts != 1:
                    # A retry cannot replenish detector time/bytes. PV-06 must
                    # durably consume continuation before allowing another try.
                    raise PrivacyDenied("privacy_processing_unavailable")
                budget = restore_budget(source.continuation)
            else:
                # Explicit component capabilities can omit production accounting.
                # LocalSanitizer refuses generated input without its provenance.
                budget = OperationBudget(uuid4())
            plan = await self.extraction_plan(PreparedAdmission(record, event, source, budget))
        await self.publish(record, plan, claim=claim)

    async def materialize_direct(self, event: Event) -> bool:
        record = await self.repository.get(str(event.id), user_id=event.user_id)
        if record is None:
            raise PrivacyDenied("privacy_processing_unavailable")
        if record.kind != "direct":
            raise PrivacyDenied("privacy_processing_unavailable")
        async with self.guard(record.snapshot, "publication"):
            intent = await self.engine._event_store.get_direct_store(str(event.id), user_id=event.user_id)
            if intent is None:
                raise PrivacyDenied("privacy_processing_unavailable")
        plan = await self.engine._event_store.get_derivation_plan(str(event.id), user_id=event.user_id)
        if plan is None:
            plan = await self.direct_plan(record, event, intent.node)
        await self.publish(record, plan)
        return True

    async def inspect(self, *, user_id: str, scope=Scope.PERSONAL, policy=None) -> dict:
        from prme.privacy.recognizers import coverage_descriptor
        saved = self.effective(policy, user_id=user_id, scope=scope)
        async with self.guard(saved, "disclosure"):
            runtime = self.require_runtime()
            return {"schema_version": 1, "policy": saved.model_dump(mode="json"), "profile": saved.policy.profile,
                    "unlimited_evidence": saved.policy.unlimited_evidence,
                    "unlimited_memory": saved.memory_deadline is None,
                    "processing_failure_recovery": ("caller_resubmission_required" if not saved.policy.durable_processing_input
                                                    else "retry_pending_work_before_processing_deadline"),
                    "providers": {"extraction": runtime.extractor.provider_name if runtime.extractor else "unavailable",
                                  "embedding": runtime.embedder.model_name},
                    "provider_use": "sanitized input and admitted memory may be sent to the explicitly configured providers",
                    "sanitizer_coverage": coverage_descriptor(),
                    "protected_product_ready": False,
                    "unsupported_operations": ["protected_reingest"],
                    "integration_status": "requires sanitizer, cryptography, source separation, journals, erasure and release gate"}

    async def owner_records(self, user_id: str, scopes=None):
        rows = await self.repository.for_owner(user_id)
        return tuple(row for row in rows if scopes is None or row.snapshot.policy.scope in scopes)

    async def records_for_call(self, owner, arguments) -> tuple[PrivacyRecord, ...]:
        # Unscoped operator calls to unrelated legacy objects remain supported.
        # Resolve only identity metadata, never a source payload. This resolution is
        # not authorization: a protected object still requires explicit owner.
        supplemental_evidence = arguments.get("evidence_id")
        identifiers = [("events" if name == "event_id" else "nodes", value)
                       for name, value in arguments.items() if value is not None and name in {
                           "node_id", "old_node_id", "new_node_id", "node_a_id", "node_b_id",
                           "winner_id", "loser_id", "event_id"}]
        if identifiers:
            records = await self.repository.records_for_objects(identifiers, owner=owner,
                                                                 supplemental_evidence=supplemental_evidence)
            if records is None:
                # Missing/ambiguous unscoped identities cannot establish a
                # legacy boundary or reveal protected-object availability.
                return await self.repository._read("1=1", ())
            return records
        if owner is not None:
            return await self.repository.for_owner(owner)
        # A global scan has no narrower declared boundary.
        return await self.repository._read("1=1", ())

    async def check_disclosure(self, records) -> None:
        for record in records:
            async with self.guard(record.snapshot, "disclosure"):
                pass

    @asynccontextmanager
    async def publication_guards(self, records):
        # Validate every snapshot, then hold one lease per scoped authority.
        # Re-entering the same non-reentrant scoped lock would deadlock a batch.
        by_boundary = {}
        for record in records:
            async with self.guard(record.snapshot, "publication"):
                pass
            p = record.snapshot.policy
            by_boundary.setdefault((str(p.project_id), str(p.owner_id), p.scope.value), record.snapshot)
        async with AsyncExitStack() as stack:
            for key in sorted(by_boundary):
                await stack.enter_async_context(self.guard(by_boundary[key], "publication"))
            for record in records:
                self.check_deadlines(record.snapshot)
            yield

    async def fast_many(self, items, *, user_id: str, request_id=None, policy=None):
        from prme.models.processing import FastIngestItem
        from prme.storage.fast_ingest import FastIngestAdmission
        try:
            items = tuple(FastIngestItem.model_validate(i) for i in items)
        except (ValueError, TypeError):
            raise PrivacyDenied("privacy_unsupported_input") from None
        runtime = self.require_runtime()
        request = finite_json({"items": [i.model_dump(mode="json") for i in items],
                               "policy": policy.model_dump(mode="json") if policy else None}).encode()
        if request_id is not None:
            request_id = UUID(str(request_id))
            if runtime.retry_committer is None:
                raise PrivacyDenied("privacy_retry_unavailable")
            old = await self.repository.request(request_id, user_id=user_id)
            if old:
                await self.check_disclosure(old)
                if old[0].retry is None or not await capability_call(runtime.retry_committer.verify(request, old[0].retry), "privacy_retry_unavailable"):
                    raise PrivacyDenied("privacy_retry_conflict")
                return FastIngestAdmission(event_ids=tuple(row.event_id for row in old), replayed=True)
            if await self.repository.has_legacy_request(request_id, user_id=user_id):
                raise PrivacyDenied("privacy_retry_conflict")
        budget = OperationBudget(uuid4())
        prepared = [await self.prepare(policy=policy, kind="direct", asynchronous=True, user_id=user_id,
                                       budget=budget, **item.model_dump()) for item in items]
        if not prepared:
            return FastIngestAdmission(event_ids=())
        if request_id is not None:
            commitment = await capability_call(runtime.retry_committer.commit(request, RetryContext(request_id=request_id, snapshot=prepared[0].record.snapshot)), "privacy_retry_unavailable")
            prepared = [PreparedAdmission(p.record.model_copy(update={"retry": commitment, "request_index": i}),
                                           p.event, p.source, p.budget) for i, p in enumerate(prepared)]
        nodes = [self.direct_node(p) for p in prepared]
        try:
            async with self.publication_guards([p.record for p in prepared]), self.writer_guard([p.record for p in prepared]):
                await self.repository.accept([p.event for p in prepared], [p.record for p in prepared], nodes)
        except Exception as exc:
            import duckdb
            if request_id is None or not (isinstance(exc, duckdb.ConstraintException) or getattr(exc, "sqlstate", None) == "23505"):
                raise
            # Another process may have committed this request after preflight.
            # Its original snapshot/envelopes win; compare using its saved key.
            old = await self.repository.request(request_id, user_id=user_id)
            if not old or old[0].retry is None:
                raise PrivacyDenied("privacy_retry_unavailable") from None
            await self.check_disclosure(old)
            if not await capability_call(runtime.retry_committer.verify(request, old[0].retry), "privacy_retry_unavailable"):
                raise PrivacyDenied("privacy_retry_conflict") from None
            return FastIngestAdmission(event_ids=tuple(row.event_id for row in old), replayed=True)
        self.engine._materialization_queue.note_added()
        return FastIngestAdmission(event_ids=tuple(p.event.id for p in prepared))
