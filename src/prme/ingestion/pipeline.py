"""Two-phase ingestion pipeline orchestrating extraction and materialization.

Phase 1 (immediate): Atomically persist the event and its raw-source indexing
job. Retrieval or explicit processing completes that work after failures/restart.

Phase 2 (background or awaitable): Extract entities, facts, and relationships
via LLM, validate grounding against source text, merge entities, detect
supersedence, and materialize all derived structures into the graph, vector,
and lexical stores.

On extraction failure, the event is always preserved (Phase 1 already
committed) and extraction is retried with exponential backoff (5s, 30s, 180s).
"""

from __future__ import annotations

import asyncio
import math
import time
import weakref
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import dateparser
import structlog

from prme.ingestion.entity_merge import EntityMerger
from prme.ingestion.errors import ExtractionError, MaterializationError, extraction_failure_code
from prme.ingestion.graph_writer import GraphWriter, WriteQueueGraphWriter
from prme.ingestion.grounding import validate_grounding
from prme.ingestion.schema import ExtractionResult
from prme.ingestion.temporal import validate_source_time
from prme.ingestion.supersedence import SupersedenceDetector
from prme.models.edges import MemoryEdge
from prme.models.events import Event
from prme.models.extraction import ExtractionRecord
from prme.models.derivation import DerivationPlan
from prme.models.extraction_work import ExtractionClaim, ExtractionProcessingResult
from prme.storage._threading import run_async_to_completion
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, EpistemicType, LifecycleState, NodeType, Scope, SourceType

if TYPE_CHECKING:
    from prme.ingestion.extraction import ExtractionProvider
    from prme.storage.event_store import EventStore
    from prme.storage.graph_store import GraphStore
    from prme.storage.lexical_index import LexicalIndex
    from prme.storage.vector_index import VectorIndex
    from prme.storage.write_queue import WriteQueue

logger = structlog.get_logger(__name__)

# Fact type to NodeType mapping
_FACT_TYPE_TO_NODE_TYPE: dict[str, NodeType] = {
    "decision": NodeType.DECISION,
    "preference": NodeType.PREFERENCE,
}


class IngestionPipeline:
    """Orchestrates two-phase ingestion: persist-then-extract.

    Phase 1 persists the event immediately via the write queue, ensuring
    no data loss. Phase 2 runs LLM extraction, grounding validation,
    entity merge, supersedence detection, and materialization either in
    the background (default) or synchronously (wait_for_extraction=True).

    All storage writes are serialized through the WriteQueue for DuckDB
    single-writer safety.

    Args:
        event_store: Append-only event log backend.
        graph_store: Graph store for nodes and edges.
        vector_index: Semantic search index.
        lexical_index: Full-text search index.
        extraction_provider: LLM-powered structured extraction.
        write_queue: Serialized write queue for DuckDB safety.
    """

    def __init__(
        self,
        event_store: EventStore,
        graph_store: GraphStore,
        vector_index: VectorIndex,
        lexical_index: LexicalIndex,
        extraction_provider: ExtractionProvider,
        write_queue: WriteQueue,
        graph_writer: GraphWriter | None = None,
        confidence_matrix: object | None = None,
        max_concurrent_extractions: int = 8,
        extraction_lease_seconds: float = 300,
    ) -> None:
        self._event_store = event_store
        self._graph_store = graph_store
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._extraction_provider = extraction_provider
        self._write_queue = write_queue
        self._graph_writer = graph_writer
        self._extraction_lease_seconds = extraction_lease_seconds
        self._scope_locks: weakref.WeakValueDictionary[tuple[str, Scope], asyncio.Lock] = weakref.WeakValueDictionary()

        # Bound concurrent background extraction tasks. Without this an
        # ingest_batch of N launches N concurrent LLM calls (issue #39).
        self._extraction_semaphore = asyncio.Semaphore(
            max(1, max_concurrent_extractions)
        )

        # Config-driven confidence matrix with module-level default fallback
        if confidence_matrix is not None:
            self._confidence_matrix = confidence_matrix
        else:
            from prme.epistemic.matrix import DEFAULT_CONFIDENCE_MATRIX
            self._confidence_matrix = DEFAULT_CONFIDENCE_MATRIX
        self._entity_merger = EntityMerger(graph_store, graph_writer) if graph_writer else EntityMerger(graph_store, WriteQueueGraphWriter(graph_store, write_queue))
        self._supersedence_detector = SupersedenceDetector(graph_store, graph_writer) if graph_writer else SupersedenceDetector(graph_store, WriteQueueGraphWriter(graph_store, write_queue))
        self._retry_tasks: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._retry_delays = (5, 30, 180)
        self._closing = False

    async def ingest(
        self,
        content: str,
        *,
        user_id: str,
        role: str = "user",
        session_id: str | None = None,
        metadata: dict | None = None,
        event_time: datetime | None = None,
        wait_for_extraction: bool = False,
        scope: Scope = Scope.PERSONAL,
    ) -> str:
        """Ingest a message through the two-phase pipeline.

        Phase 1 (immediate): Atomically persist the event and a durable
        raw-source indexing job.

        Phase 2 (background or await): Extract entities, facts, and
        relationships via LLM; validate grounding; merge entities;
        detect supersedence; materialize into graph/vector/lexical stores.

        Args:
            content: The message text to ingest.
            user_id: Owner user ID.
            role: Message role ('user', 'assistant', or 'system').
            session_id: Optional session identifier.
            metadata: Optional structured metadata.
            event_time: Timezone-aware source time; omitted uses ingestion time.
            wait_for_extraction: If True, block until extraction and
                materialization complete. Defaults to False (async).

        Returns:
            String UUID of the persisted event.
        """
        validate_source_time(event_time)

        # --- Phase 1: Persist event immediately ---
        event = Event(
            content=content,
            user_id=user_id,
            session_id=session_id,
            role=role,
            metadata=metadata,
            event_time=event_time,
            scope=scope,
        )
        event_id = await self._write_queue.submit(
            lambda ev=event: self._event_store.append(ev, defer_materialization=True, defer_extraction=True),
            label=f"event.append:{event.id}",
        )

        # Source indexing is durable deferred work. Retrieval or explicit
        # processing indexes its raw NOTE; acceptance cannot be undone by a
        # transient lexical-index failure after the event commit.

        logger.info(
            "ingestion.phase1_complete",
            event_id=event_id,
            user_id=user_id,
            role=role,
            session_id=session_id,
        )

        # --- Phase 2: Extract and materialize ---
        task = asyncio.create_task(
            self._extract_and_materialize(event, event_id, scope, raise_errors=wait_for_extraction)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        if wait_for_extraction:
            await task

        return str(event.id)

    async def ingest_batch(
        self,
        messages: list[dict],
        *,
        user_id: str,
        session_id: str | None = None,
        wait_for_extraction: bool = False,
        scope: Scope = Scope.PERSONAL,
    ) -> list[str]:
        """Ingest a batch of messages sequentially.

        Processes messages in order to preserve conversation history
        sequencing. Each message dict must have 'content' and 'role'
        keys, with optional 'metadata' and timezone-aware 'event_time'.

        Args:
            messages: List of message dicts with 'content' and 'role'.
            user_id: Owner user ID for all messages.
            session_id: Optional session identifier for all messages.
            wait_for_extraction: If True, block until all extraction
                completes. Defaults to False.

        Returns:
            List of event ID strings, one per message.
        """
        event_ids: list[str] = []
        for msg in messages:
            event_id = await self.ingest(
                msg["content"],
                user_id=user_id,
                role=msg["role"],
                session_id=session_id,
                metadata=msg.get("metadata"),
                event_time=msg.get("event_time"),
                wait_for_extraction=wait_for_extraction,
                scope=scope,
            )
            event_ids.append(event_id)
        return event_ids

    async def _extract_and_materialize(
        self, event: Event, event_id: str, scope: Scope = Scope.PERSONAL,
        *, retry_attempt: int = 0, raise_errors: bool = False, claim: ExtractionClaim | None = None,
    ) -> None:
        # Avoid turning ordinary concurrent in-process ingestion into a busy
        # error. Database claims remain the cross-worker ownership boundary.
        key = (event.user_id, scope)
        lock = self._scope_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._scope_locks[key] = lock
        async with lock:
            await self._run_extraction(event, event_id, scope, retry_attempt=retry_attempt,
                                       raise_errors=raise_errors, claim=claim)

    async def _run_extraction(
        self, event: Event, event_id: str, scope: Scope = Scope.PERSONAL,
        *, retry_attempt: int = 0, raise_errors: bool = False, claim: ExtractionClaim | None = None,
    ) -> None:
        """Run LLM extraction, validate grounding, and materialize results.

        On failure, logs the error and schedules a retry with exponential
        backoff. The event is always safe in the event store regardless.

        Args:
            event: The persisted Event model.
            event_id: String UUID of the event.
            scope: Ingestion-level scope for fallback when LLM does not classify.
        """
        work = self._event_store.extraction_work
        status = await work.status(event_id, user_id=event.user_id)
        if status is not None and status.status == "complete":
            return
        if status is not None and claim is None:
            if status.status == "failed" and raise_errors:
                await work.retry(event_id, user_id=event.user_id)
            claim = await work.claim(user_id=event.user_id, event_id=event_id,
                                     lease_seconds=self._extraction_lease_seconds, ignore_schedule=raise_errors)
            if claim is None:
                self._schedule_retry(event, event_id, attempt=retry_attempt + 1, scope=scope)
                if raise_errors:
                    raise ExtractionError("Extraction is pending behind earlier work or an active lease", event_id=event_id)
                return

        async def renew_lease():
            while True:
                await asyncio.sleep(min(30.0, self._extraction_lease_seconds / 3))
                if not await work.renew(claim, lease_seconds=self._extraction_lease_seconds):
                    return

        heartbeat = asyncio.create_task(renew_lease()) if claim is not None else None
        try:
            plan = await self._event_store.get_derivation_plan(event_id, user_id=event.user_id)
            if plan is None:
                result = await self._extract_or_load(event, claim=claim)
                await self._materialize(result, event, event_id, scope, claim=claim)
            else:
                await self._publish_plan(plan, claim=claim)
            logger.info("ingestion.phase2_complete", event_id=event_id)
        except asyncio.CancelledError:
            if claim is not None:
                await run_async_to_completion(work.fail(claim, error="Cancelled", retry_after=0))
            raise
        except Exception as exc:
            if claim is not None:
                # A lost acknowledgement after atomic completion is success.
                current = await work.status(event_id, user_id=event.user_id)
                if current is not None and current.status == "complete":
                    return
            logger.error(
                "ingestion.extraction_failed",
                event_id=event_id,
                error_type=type(exc).__name__,
            )
            reason = extraction_failure_code(exc)
            if claim is not None:
                attempt = claim.attempts
                delay = self._retry_delays[attempt - 1] if attempt <= len(self._retry_delays) else None
                retained = await work.fail(claim, error=reason, retry_after=delay)
                if retained and delay is not None:
                    self._schedule_retry(event, event_id, attempt=attempt, scope=scope)
            else:
                self._schedule_retry(event, event_id, attempt=retry_attempt + 1, scope=scope)
            if raise_errors:
                raise ExtractionError(
                    f"Extraction did not complete ({reason}); the source event is persisted",
                    event_id=event_id, reason_code=reason,
                ) from exc
        finally:
            if heartbeat is not None:
                async def stop_heartbeat():
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                await run_async_to_completion(stop_heartbeat())

    async def process_extractions(self, *, user_id: str, limit: int = 100,
                                  budget_ms: float = 5000) -> ExtractionProcessingResult:
        """Process due owned jobs within a cooperative budget, without a daemon.

        The budget is checked between jobs; an active provider call uses its
        configured timeout. Failed counts report terminal jobs still requiring
        an explicit retry. Retrieval never calls this method automatically.
        """
        if not user_id or limit < 0 or not math.isfinite(budget_ms) or budget_ms < 0:
            raise ValueError("Extraction processing requires user_id and nonnegative finite bounds")
        started, processed = time.monotonic(), 0
        work = self._event_store.extraction_work
        for _ in range(limit):
            if (time.monotonic() - started) * 1000 >= budget_ms:
                break
            claim = await work.claim(user_id=user_id, lease_seconds=self._extraction_lease_seconds)
            if claim is None:
                break
            event = await self._event_store.get(str(claim.event_id))
            if event is None or event.user_id != user_id:
                raise RuntimeError("Claimed extraction source is unavailable")
            try:
                await self._extract_and_materialize(event, str(event.id), event.scope,
                                                    claim=claim, raise_errors=True)
            except ExtractionError:
                continue
            status = await work.status(str(event.id), user_id=user_id)
            if status is not None and status.status == "complete":
                processed += 1
        pending, failed = await work.counts(user_id=user_id)
        return ExtractionProcessingResult(processed=processed, pending=pending, failed=failed)

    async def _extract_or_load(self, event: Event, *, claim: ExtractionClaim | None = None) -> ExtractionResult:
        """Reuse durable validated output after downstream failure or restart.

        This saves inference results, not graph completion. It does not make
        materialization safe to replay after an interrupted graph write.
        """
        saved = await self._event_store.get_extraction(str(event.id), user_id=event.user_id)
        if saved is None:
            async with self._extraction_semaphore:
                result = await self._extraction_provider.extract(event.content, role=event.role)
            result = validate_grounding(result, event.content)
            record = ExtractionRecord(
                event_id=event.id, user_id=event.user_id, scope=event.scope,
                content_hash=event.content_hash,
                provider=self._extraction_provider.provider_name,
                model=self._extraction_provider.model_name,
                result=result.model_dump(mode="json"),
            )
            saved = await self._write_queue.submit(
                lambda: self._event_store.record_extraction(record, claim=claim),
                label=f"extraction.record:{event.id}",
            )
        saved.verify_source(event.user_id, event.scope.value, event.content_hash)
        return ExtractionResult.model_validate(saved.result)

    async def _prepare_plan(self, result: ExtractionResult, event: Event) -> DerivationPlan:
        """Prepare fixed graph/index inputs without publishing any artifacts."""
        from prme.ingestion.planning import PlanningGraph, PlanningIndexes, PlanningQueue

        event = Event.model_validate_json(event.model_dump_json())
        result = ExtractionResult.model_validate_json(result.model_dump_json())
        graph = PlanningGraph(self._graph_store, event)
        indexes = PlanningIndexes(graph)
        await self._populate(
            result, event, str(event.id), event.scope, graph_store=graph,
            writer=graph, vector_index=indexes, lexical_index=indexes, write_queue=PlanningQueue(),
        )
        return await indexes.prepare(self._vector_index._provider)

    async def _populate(
        self, result: ExtractionResult, event: Event, event_id: str, scope: Scope,
        *, graph_store, writer, vector_index, lexical_index, write_queue,
    ) -> None:
        """Apply one set of materialization rules to durable or planning adapters."""
        entity_merger = EntityMerger(graph_store, writer)
        supersedence_detector = SupersedenceDetector(graph_store, writer)
        # Map entity name -> entity_id for relationship wiring
        from prme.ingestion.entity_references import EntityReferences
        entity_refs = EntityReferences[str]()

        # --- Entities ---
        for entity in result.entities:
            entity_scope = scope

            entity_id, is_new = await entity_merger.find_or_create_entity(
                name=entity.name,
                entity_type=entity.entity_type,
                user_id=event.user_id,
                description=entity.description,
                session_id=event.session_id,
                evidence_event_id=event_id,
                scope=entity_scope,
            )
            entity_refs.add(entity.name, entity.entity_type, entity_id)

            # Reuse does not update the durable entity description. Do not
            # overwrite its vector with this attempt's uncommitted model
            # output (or accumulate duplicate local vector entries).
            if not is_new:
                # Retain the chosen entity snapshot before the next scan page
                # replaces the planner's bounded temporary lookup cache.
                await graph_store.get_node(entity_id)
                continue

            # Index entity in vector store (not tracked for rollback)
            entity_text = entity.name
            if entity.description:
                entity_text = f"{entity.name}: {entity.description}"
            await write_queue.submit(
                lambda eid=entity_id, txt=entity_text, uid=event.user_id: (
                    vector_index.index(eid, txt, uid)
                ),
                label=f"vector.entity:{entity_id}",
            )

        # Relationships are source-cited claims, not authoritative graph edge
        # labels. Reuse a covering fact for the same endpoints and passage.
        from prme.ingestion.schema import ExtractedFact
        claims = [(fact, False) for fact in result.facts]
        covered = set()
        for fact in result.facts:
            subject, _ = entity_refs.resolve(fact.subject, fact.subject_entity_type)
            obj, _ = entity_refs.resolve(fact.object, fact.object_entity_type)
            if subject and obj:
                covered.add((subject, obj, fact.evidence_quote or event.content))
        for rel in result.relationships:
            subject, _ = entity_refs.resolve(rel.source_entity, rel.source_entity_type)
            obj, _ = entity_refs.resolve(rel.target_entity, rel.target_entity_type)
            passage = rel.evidence_quote or event.content
            if subject and obj and (subject, obj, passage) in covered:
                continue
            claims.append((ExtractedFact(
                subject=rel.source_entity, subject_entity_type=rel.source_entity_type,
                predicate=rel.relationship_type, object=rel.target_entity,
                object_entity_type=rel.target_entity_type, evidence_quote=passage,
                epistemic_type=rel.epistemic_type, confidence=rel.confidence,
            ), True))

        # --- Source-cited claims ---
        for fact, from_relationship in claims:
            fact_scope = scope

            # Resolve temporal reference
            resolved_date = self._resolve_temporal(fact.temporal_ref, reference_time=event.event_time or event.timestamp)

            # Determine node type from fact_type
            node_type = _FACT_TYPE_TO_NODE_TYPE.get(
                fact.fact_type, NodeType.FACT
            )

            # Build fact content
            # Grounding expands citations to full source paragraphs so
            # model triples cannot erase negations or trailing conditions.
            fact_content = fact.evidence_quote or event.content

            subject_entity_id, subject_link_status = entity_refs.resolve(fact.subject, fact.subject_entity_type)
            object_entity_id, object_link_status = entity_refs.resolve(fact.object, fact.object_entity_type)
            # Keep unresolved custom/legacy facts searchable without guessing a
            # namesake identity. The durable metadata makes missing links visible.
            fact_metadata: dict = {
                "subject_link_status": subject_link_status,
                "object_link_status": object_link_status,
                "object_entity_type": fact.object_entity_type,
                "extraction_kind": "relationship" if from_relationship else "fact",
                "subject_entity_type": fact.subject_entity_type,
                "subject": fact.subject,
                "predicate": fact.predicate,
                "object": fact.object,
                "evidence_quote": fact_content,
                "grounding_method": "source_passage_v1",
                "temporal_intent": fact.temporal_intent,
                "replaces_object": fact.replaces_object,
            }
            if fact.scope:
                fact_metadata["suggested_scope"] = fact.scope
            if fact.temporal_ref:
                fact_metadata["temporal_ref"] = fact.temporal_ref
            if resolved_date:
                fact_metadata["resolved_date"] = resolved_date

            # Determine epistemic type from LLM extraction
            try:
                fact_epistemic_type = EpistemicType(fact.epistemic_type)
            except ValueError:
                fact_epistemic_type = EpistemicType.ASSERTED

            # Determine source type from conversation role
            if event.role and event.role.lower() in ("user", "human"):
                fact_source_type = SourceType.USER_STATED
            elif event.role and event.role.lower() in ("assistant", "system"):
                fact_source_type = SourceType.SYSTEM_INFERRED
            else:
                fact_source_type = SourceType.USER_STATED

            # An unverified relationship is a model proposal, not a user
            # assertion. Its original message remains in evidence_refs. This
            # also keeps legacy providers on the UNVERIFIED matrix default
            # rather than the missing (unverified, user_stated) fallback.
            if from_relationship and fact_epistemic_type == EpistemicType.UNVERIFIED:
                fact_source_type = SourceType.SYSTEM_INFERRED

            # Look up default confidence from the matrix
            matrix_confidence = self._confidence_matrix.lookup_with_fallback(
                fact_epistemic_type, fact_source_type
            )

            # Create fact node via tracked writer
            from prme.types import DEFAULT_DECAY_PROFILE_MAPPING, DecayProfile

            fact_decay_profile = DEFAULT_DECAY_PROFILE_MAPPING.get(
                fact_epistemic_type, DecayProfile.MEDIUM
            )
            fact_node = MemoryNode(
                node_type=node_type,
                content=fact_content,
                user_id=event.user_id,
                session_id=event.session_id,
                scope=fact_scope,
                lifecycle_state=LifecycleState.TENTATIVE,
                confidence=matrix_confidence,
                confidence_base=matrix_confidence,
                epistemic_type=fact_epistemic_type,
                source_type=fact_source_type,
                decay_profile=fact_decay_profile,
                metadata=fact_metadata,
                evidence_refs=[event.id],
                event_time=datetime.fromisoformat(resolved_date) if resolved_date else (event.event_time or event.timestamp),
            )
            fact_node_id = await writer.create_node(fact_node)

            # Log EPISTEMIC_TYPE_ASSIGNED operation
            logger.info(
                "epistemic_type_assigned",
                op_type="EPISTEMIC_TYPE_ASSIGNED",
                target_id=fact_node_id,
                epistemic_type=fact_epistemic_type.value,
                source_type=fact_source_type.value,
                confidence_from_matrix=matrix_confidence,
                assignment_method="creation",
            )

            # Create HAS_FACT edge from subject entity to fact node
            if subject_entity_id:
                has_fact_edge = MemoryEdge(
                    source_id=UUID(subject_entity_id),
                    target_id=fact_node.id,
                    edge_type=EdgeType.HAS_FACT,
                    user_id=event.user_id,
                    provenance_event_id=event.id,
                )
                await writer.create_edge(has_fact_edge)

                # Different values can coexist. Ingestion only retires an
                # explicitly named previous value for a nonconditional
                # update; a hypothetical future must not replace reality.
                if (
                    fact.temporal_intent == "update"
                    and fact.replaces_object
                    and fact_epistemic_type in (EpistemicType.OBSERVED, EpistemicType.ASSERTED)
                ):
                    await supersedence_detector.detect_and_supersede(
                        new_fact_node_id=fact_node_id,
                        subject_entity_id=subject_entity_id,
                        predicate=fact.predicate,
                        object_value=fact.object,
                        user_id=event.user_id,
                        evidence_event_id=event_id,
                        temporal_intent="update",
                        replaces_object=fact.replaces_object,
                    )

            if object_entity_id:
                await writer.create_edge(MemoryEdge(
                    source_id=fact_node.id, target_id=UUID(object_entity_id),
                    edge_type=EdgeType.MENTIONS, user_id=event.user_id,
                    provenance_event_id=event.id,
                ))

            # Index fact in vector and lexical stores (not tracked for rollback)
            await write_queue.submit(
                lambda fid=fact_node_id, fc=fact_content, uid=event.user_id: (
                    vector_index.index(fid, fc, uid)
                ),
                label=f"vector.fact:{fact_node_id}",
            )
            await write_queue.submit(
                lambda fid=fact_node_id, fc=fact_content, uid=event.user_id, nt=node_type.value, sc=fact_scope.value: (
                    lexical_index.index(fid, fc, uid, nt, sc)
                ),
                label=f"lexical.fact:{fact_node_id}",
            )

    async def _publish_plan(self, plan: DerivationPlan, *, claim: ExtractionClaim | None = None) -> None:
        """Stage saved inputs and publish once; never delete shared retry artifacts."""
        receipt = await self._event_store.get_derivation_receipt(str(plan.event_id), user_id=plan.user_id)
        if receipt is not None:
            if receipt.plan_checksum != plan.checksum:
                raise ValueError("Derivation receipt conflicts with the requested plan")
            return
        if getattr(self._graph_store, "_conn", None) is not None:
            from prme.storage.derivation_staging import DuckDBStageFence
            fence = DuckDBStageFence(self._graph_store._conn, self._graph_store._conn_lock, plan, claim)
            for embedding in plan.embeddings:
                await self._write_queue.submit(
                    lambda item=embedding: self._vector_index.stage(item, user_id=plan.user_id, fence=fence),
                    label=f"derivation.vector:{embedding.node_id}",
                )
            # Numerical vector payloads are already durable; keep native file
            # snapshots debounced. Lexical publication is one committed batch.
            await self._write_queue.submit(
                lambda: self._lexical_index.stage(plan, fence=fence), label=f"derivation.lexical:{plan.id}",
            )
        # PostgreSQL writes its prepared vector/lexical columns in this same
        # graph transaction. No provider call is permitted inside the commit.
        await self._write_queue.submit(
            lambda: self._graph_store.commit_derivation(plan, claim=claim), label=f"derivation.commit:{plan.id}",
        )

    async def _materialize(
        self, result: ExtractionResult, event: Event, event_id: str,
        scope: Scope = Scope.PERSONAL,
        *, claim: ExtractionClaim | None = None,
    ) -> None:
        """Publish a complete saved derivation or leave its source/plan retryable.

        Fixed identities and embedding outputs are journaled before index
        staging. The final transaction publishes every node, edge, replacement
        and receipt together. Failure retains staged inputs for the same plan;
        compensating deletion could damage another attempt and is never used.
        """
        try:
            if event_id != str(event.id) or scope != event.scope:
                raise ValueError("Materialization must preserve source identity and scope")
            plan = await self._event_store.get_derivation_plan(event_id, user_id=event.user_id)
            if plan is None:
                existing = await self._graph_store.get_event_nodes(event_id, user_id=event.user_id)
                if any(node.id != event.id for node in existing):
                    # Another attempt may have published between the first
                    # plan read and this graph read. Its journal distinguishes
                    # a valid concurrent completion from unjournaled legacy data.
                    plan = await self._event_store.get_derivation_plan(event_id, user_id=event.user_id)
                    if plan is None:
                        raise ValueError("Source has legacy derived nodes; explicit migration is required")
            if plan is None:
                prepared = await self._prepare_plan(result, event)
                if claim is not None and claim.plan_revision != 1:
                    prepared = prepared.model_copy(update={"revision": claim.plan_revision})
                plan = await self._write_queue.submit(
                    lambda: self._event_store.record_derivation_plan(prepared, claim=claim),
                    label=f"derivation.prepare:{event_id}",
                )
            await self._publish_plan(plan, claim=claim)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("ingestion.materialization_failed", event_id=event_id,
                         error_type=type(exc).__name__)
            raise MaterializationError(
                f"Materialization failed for event {event_id}", event_id=event_id,
            ) from exc

    @staticmethod
    def _resolve_temporal(temporal_ref: str | None, *, reference_time: datetime | None = None) -> str | None:
        """Resolve a natural language temporal reference to an ISO date string.

        Uses dateparser to parse references like 'yesterday', 'last week',
        'in March 2024'. Returns None if the reference is unparseable.

        Args:
            temporal_ref: Raw temporal reference string, or None.

        Returns:
            ISO format date string if parseable, None otherwise.
        """
        if temporal_ref is None:
            return None
        parsed = dateparser.parse(
            temporal_ref,
            settings={
                "PREFER_DATES_FROM": "past",
                "RELATIVE_BASE": reference_time or datetime.now(timezone.utc),
                "RETURN_AS_TIMEZONE_AWARE": True,
                "TIMEZONE": "UTC",
                "TO_TIMEZONE": "UTC",
            },
        )
        if parsed is not None:
            return parsed.isoformat()
        return None

    def _schedule_retry(
        self,
        event: Event,
        event_id: str,
        attempt: int = 1,
        *,
        scope: Scope = Scope.PERSONAL,
    ) -> None:
        """Schedule a retry of extraction with exponential backoff.

        Retry delays: 5s (attempt 1), 30s (attempt 2), 180s (attempt 3).
        After 3 failed attempts, logs an error and gives up.

        Args:
            event: The event to re-extract.
            event_id: String UUID of the event.
            attempt: Current attempt number (1-based).
            scope: Ingestion-level scope to forward on retry.
        """
        if self._closing:
            return
        if attempt > len(self._retry_delays):
            logger.error(
                "ingestion.max_retries_exceeded",
                event_id=event_id,
                attempts=attempt - 1,
            )
            return

        delay = self._retry_delays[attempt - 1]
        logger.warning(
            "ingestion.retry_scheduled",
            event_id=event_id,
            attempt=attempt,
            delay_seconds=delay,
        )

        async def _retry() -> None:
            await asyncio.sleep(delay)
            await self._extract_and_materialize(event, event_id, scope, retry_attempt=attempt)

        task = asyncio.create_task(_retry())
        self._retry_tasks[event_id] = task

        def discard(completed: asyncio.Task) -> None:
            if self._retry_tasks.get(event_id) is completed:
                self._retry_tasks.pop(event_id, None)

        task.add_done_callback(discard)

    async def shutdown(self, drain_timeout: float = 10.0) -> None:
        """Drain pending background tasks then shut down.

        Waits up to *drain_timeout* seconds for in-progress extraction
        tasks to finish so that supersedence detection completes before
        the engine closes.  Any tasks still running after the timeout
        are cancelled.  Retry tasks are cancelled immediately since
        their long sleep delays make draining impractical.

        Args:
            drain_timeout: Maximum seconds to wait for background tasks.
        """
        # Background tasks can fail while draining; prevent them from
        # scheduling new retries after this cancellation pass.
        self._closing = True
        # Cancel retry tasks immediately (long sleeps, not worth draining)
        for task in self._retry_tasks.values():
            task.cancel()
        if self._retry_tasks:
            await asyncio.gather(
                *self._retry_tasks.values(), return_exceptions=True
            )
        self._retry_tasks.clear()

        # Drain background extraction tasks with a timeout
        bg_tasks = list(self._background_tasks)
        if bg_tasks:
            _done, pending = await asyncio.wait(
                bg_tasks, timeout=drain_timeout
            )
            # Cancel any that didn't finish in time
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        self._background_tasks.clear()
        logger.info("ingestion.pipeline_shutdown")
