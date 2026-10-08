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
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import dateparser

from prme._temporal import DATEPARSER_LOCK as _DATEPARSER_LOCK
import structlog

from prme.epistemic.inference import infer_source_type
from prme.ingestion.entity_merge import EntityMerger
from prme.ingestion.errors import ExtractionError, MaterializationError, extraction_failure_code
from prme.ingestion.graph_writer import GraphWriter, WriteQueueGraphWriter
from prme.ingestion.grounding import claim_sentences, validate_grounding
from prme.ingestion.schema import ExtractedEntity, ExtractionResult
from prme.ingestion.temporal import validate_source_time
from prme.ingestion.supersedence import SupersedenceDetector
from prme.models.edges import MemoryEdge
from prme.models.events import Event
from prme.models.extraction import ExtractionRecord
from prme.models.derivation import DerivationPlan
from prme.models.extraction_work import ExtractionClaim, ExtractionProcessingResult
from prme.models.entity_identity import unresolved_personal_reference
from prme.storage._threading import run_async_to_completion
from prme.models.nodes import MemoryNode
from prme.models.speaker import metadata_speaker, speaker_labeled
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


_FIRST_PERSON_SINGULAR = frozenset({"i", "me", "my", "mine", "myself"})


def _speaker_reference(name: str, entity_type: str | None) -> bool:
    """Whether a reference means whoever speaks the turn: I, me, my, mine or myself."""
    return name.strip().casefold() in _FIRST_PERSON_SINGULAR and unresolved_personal_reference(name, entity_type)


def _entities_with_unresolved_references(result: ExtractionResult) -> list[ExtractedEntity]:
    """Add one noncanonical identity for each unlisted personal reference.

    The identity is still created through EntityMerger, which binds it to the
    source event. Choosing ``personal_reference`` when providers omit or
    disagree on a type avoids asserting that a pronoun is a durable person or
    group identity.
    """
    entities = list(result.entities)
    listed_names = {entity.name.strip().casefold() for entity in entities}
    references: list[tuple[str, str | None]] = []
    for fact in result.facts:
        references.append((fact.subject, fact.subject_entity_type))
        if unresolved_personal_reference(fact.object, fact.object_entity_type):
            references.append((fact.object, fact.object_entity_type))
    for relationship in result.relationships:
        references.extend((
            (relationship.source_entity, relationship.source_entity_type),
            (relationship.target_entity, relationship.target_entity_type),
        ))

    missing: dict[str, list[tuple[str, str | None]]] = {}
    for name, entity_type in references:
        normalized = name.strip().casefold()
        if normalized in listed_names or not unresolved_personal_reference(name, entity_type):
            continue
        missing.setdefault(normalized, []).append((name, entity_type))

    for normalized, occurrences in missing.items():
        explicit_types = {entity_type for _, entity_type in occurrences if entity_type is not None}
        entity_type = next(iter(explicit_types)) if len(explicit_types) == 1 else "personal_reference"
        entities.append(ExtractedEntity(name=occurrences[0][0], entity_type=entity_type))
        listed_names.add(normalized)
    return entities


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
        merge_repeated_claims: bool = True,
        extraction_window_turns: int = 0,
        resolve_fact_text: bool = False,
        bind_speaker_references: bool = False,
        claim_sentence_text: bool = False,
        speaker_grounding: bool = False,
    ) -> None:
        self._event_store = event_store
        self._graph_store = graph_store
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._extraction_provider = extraction_provider
        self._write_queue = write_queue
        self._graph_writer = graph_writer
        self._extraction_lease_seconds = extraction_lease_seconds
        self._merge_repeated_claims = merge_repeated_claims
        # Preceding turns of the same session shown to the extractor (#91).
        # Zero sends the new turn alone, which is the default.
        self._extraction_window_turns = max(0, extraction_window_turns)
        # Ask for self-contained fact text and keep what passes its check (#91).
        self._resolve_fact_text = resolve_fact_text
        # Bind I, me and my in a turn with a named speaker to that speaker's entity.
        self._bind_speaker_references = bind_speaker_references
        # Store a claim's own sentences as its text instead of the whole paragraph.
        self._claim_sentence_text = claim_sentence_text
        # Let a named speaker's own I, me and my ground a claim about them, by name or as I.
        self._speaker_grounding = speaker_grounding
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
            role: Message role ('user', 'participant', 'assistant', 'tool',
                or 'system').
            session_id: Optional session identifier.
            metadata: Optional structured metadata. ``MemoryEngine`` adds a
                caller's speaker here under the reserved speaker key.
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
                elif not retained:
                    if retry_attempt == 0:
                        # The event loop can be starved after a provider returns but
                        # before its heartbeat or fenced publication runs. The old
                        # generation must not publish, but an expired, uncontested
                        # lease can be reclaimed immediately. Reuse any durable
                        # extraction/plan boundary; otherwise the provider is
                        # invoked again under the new generation. Bound this inline
                        # recovery to one attempt so persistent failures still
                        # follow the ordinary retry policy.
                        recovered = await work.claim(
                            user_id=event.user_id,
                            event_id=event_id,
                            lease_seconds=self._extraction_lease_seconds,
                            ignore_schedule=True,
                        )
                        if recovered is not None:
                            await self._run_extraction(
                                event,
                                event_id,
                                scope,
                                retry_attempt=1,
                                raise_errors=raise_errors,
                                claim=recovered,
                            )
                            return
                    # A successor may have won the reclaim race, or the one
                    # bounded inline recovery may itself have lost its lease.
                    # Keep the durable job moving through the ordinary claim
                    # path instead of leaving an expired running row idle.
                    self._schedule_retry(
                        event,
                        event_id,
                        attempt=retry_attempt + 1,
                        scope=scope,
                    )
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


    async def _extraction_window(self, event: Event) -> list[Event]:
        """The turns before this one in its session, oldest first (#91).

        Empty unless the window is on and the event names a session. Without a
        session there is nothing to bound the window to, and turns from another
        conversation would be worse than none. The event itself is already
        stored by this point, so it is dropped from its own window.
        """
        if self._extraction_window_turns <= 0 or not event.session_id:
            return []
        recent = await self._event_store.get_by_user(
            event.user_id,
            session_id=event.session_id,
            limit=self._extraction_window_turns + 1,
        )
        earlier = [item for item in recent if str(item.id) != str(event.id)]
        return [item for item in reversed(earlier[: self._extraction_window_turns]) if item.content]

    async def _extract_or_load(self, event: Event, *, claim: ExtractionClaim | None = None) -> ExtractionResult:
        """Reuse durable validated output after downstream failure or restart.

        This saves inference results, not graph completion. It does not make
        materialization safe to replay after an interrupted graph write.
        """
        saved = await self._event_store.get_extraction(str(event.id), user_id=event.user_id)
        if saved is None:
            window = await self._extraction_window(event)
            # A turn's speaker labels it, so the extractor can tell who said
            # what; a turn whose text already names its speaker is unchanged.
            lines = [speaker_labeled(item.content, metadata_speaker(item.metadata)) for item in window]
            # Each option is named only when on, so a provider written against
            # the earlier signature keeps working while they are off.
            extra: dict = {"context": lines} if lines else {}
            if self._resolve_fact_text:
                from prme.ingestion.extraction import FactTextResolution

                extra["resolve"] = FactTextResolution(
                    speaker=metadata_speaker(event.metadata),
                    source_time=event.event_time or event.timestamp,
                )
            speaker = metadata_speaker(event.metadata) if self._speaker_grounding else None
            if speaker is not None:
                extra["speaker"] = speaker
            async with self._extraction_semaphore:
                result = await self._extraction_provider.extract(
                    event.content, role=event.role, **extra
                )
            result = validate_grounding(result, event.content, speaker=speaker)
            result = self._check_resolved_text(result, event, window)
            record = ExtractionRecord(
                event_id=event.id, user_id=event.user_id, scope=event.scope,
                content_hash=event.content_hash,
                provider=self._extraction_provider.provider_name,
                model=self._extraction_provider.model_name,
                # v14 adds explicit correction and caller-declared owner-reference planning.
                grounding_policy="speech_act_v14",
                result=result.model_dump(mode="json"),
            )
            saved = await self._write_queue.submit(
                lambda: self._event_store.record_extraction(record, claim=claim),
                label=f"extraction.record:{event.id}",
            )
        saved.verify_source(event.user_id, event.scope.value, event.content_hash)
        return ExtractionResult.model_validate(saved.result)

    def _check_resolved_text(self, result: ExtractionResult, event: Event, window: list[Event]) -> ExtractionResult:
        """Keep each fact's resolved text only when it passes its check (#91).

        A resolution record is only ever set here. One arriving from the
        provider is dropped, and so is resolved text while the option is off.
        """
        from prme.ingestion.resolution import ResolutionSources, WindowTurn, resolve_fact_text

        sources = ResolutionSources(
            event_id=str(event.id),
            text=event.content,
            speaker=metadata_speaker(event.metadata),
            reference_time=event.event_time or event.timestamp,
            window=tuple(
                WindowTurn(str(item.id), metadata_speaker(item.metadata), item.content) for item in window
            ),
        )
        facts = []
        for fact in result.facts:
            resolution = None
            text = fact.resolved_text if self._resolve_fact_text else None
            if text:
                resolution, reason = resolve_fact_text(
                    text, fact.evidence_quote or event.content, sources,
                    subject=fact.subject, object_value=fact.object,
                    resolve_date=self._resolve_temporal,
                )
                if resolution is None:
                    logger.warning(
                        "resolved_text_discarded", subject=fact.subject,
                        predicate=fact.predicate, reason=reason,
                    )
                    text = None
            facts.append(fact.model_copy(update={"resolved_text": text, "resolution": resolution}))
        return result.model_copy(update={"facts": facts})

    async def _prepare_plan(
        self,
        result: ExtractionResult,
        event: Event,
        *,
        materialization_policy: Literal[
            "temporal_validity_v7", "speech_act_v8", "speech_act_v9", "speech_act_v10", "speech_act_v11", "speech_act_v12", "speech_act_v13", "speech_act_v14"
        ] = "speech_act_v14",
    ) -> DerivationPlan:
        """Prepare fixed graph/index inputs without publishing any artifacts."""
        from prme.ingestion.planning import PlanningGraph, PlanningIndexes, PlanningQueue

        event = Event.model_validate_json(event.model_dump_json())
        result = ExtractionResult.model_validate_json(result.model_dump_json())
        graph = PlanningGraph(self._graph_store, event)
        indexes = PlanningIndexes(graph)
        current_policy = materialization_policy in {"speech_act_v12", "speech_act_v13", "speech_act_v14"}
        from prme.models.owner_reference import OWNER_REFERENCE_KEY
        owner_reference = (materialization_policy == "speech_act_v14"
                           and event.role.casefold() in {"user", "human"}
                           and (event.metadata or {}).get(OWNER_REFERENCE_KEY) is True)
        await self._populate(
            result, event, str(event.id), event.scope, graph_store=graph,
            writer=graph, vector_index=indexes, lexical_index=indexes, write_queue=PlanningQueue(),
            merge_claims=self._merge_repeated_claims and current_policy,
            first_person_forms=materialization_policy in {"speech_act_v13", "speech_act_v14"},
            explicit_corrections=materialization_policy == "speech_act_v14",
            owner_reference=owner_reference,
            speaker=(metadata_speaker(event.metadata)
                     if self._bind_speaker_references and current_policy else None),
            sentence_text=self._claim_sentence_text and current_policy,
            grounding_speaker=(metadata_speaker(event.metadata)
                               if self._speaker_grounding and current_policy else None),
        )
        return await indexes.prepare(
            self._vector_index._provider,
            materialization_policy=materialization_policy,
        )

    async def _populate(
        self, result: ExtractionResult, event: Event, event_id: str, scope: Scope,
        *, graph_store, writer, vector_index, lexical_index, write_queue,
        merge_claims: bool = False, speaker: str | None = None, sentence_text: bool = False,
        grounding_speaker: str | None = None, first_person_forms: bool = False,
        explicit_corrections: bool = False, owner_reference: bool = False,
    ) -> None:
        """Apply one set of materialization rules to durable or planning adapters.

        ``merge_claims`` folds a repeated claim into one current record (#209).
        ``speaker`` binds first-person singular references to that speaker's
        entity (enable_speaker_references). ``sentence_text`` stores a claim's
        own sentences as its text (enable_claim_sentence_text), and with
        ``grounding_speaker`` (enable_speaker_grounding) the speaker's own I, me
        or my mentions them there as in grounding. Only current-policy
        plans use these, so a saved extraction replanned under an older
        materialization policy keeps that policy's behavior.
        """
        from prme.ingestion.claim_merge import claim_key, find_repeated_claims, merged_claim

        entity_merger = EntityMerger(graph_store, writer)
        supersedence_detector = SupersedenceDetector(graph_store, writer)
        # Map entity name -> entity_id for relationship wiring
        from prme.ingestion.entity_references import EntityReferences
        entity_refs = EntityReferences[str]()

        # --- Entities ---
        for entity in _entities_with_unresolved_references(result):
            entity_scope = scope
            # The turn's named speaker is who says I, me and my in it.
            bound = speaker is not None and _speaker_reference(entity.name, entity.entity_type)
            name, entity_type, description = (
                (speaker, "person", None) if bound else (entity.name, entity.entity_type, entity.description)
            )

            owner_bound = owner_reference and _speaker_reference(entity.name, entity.entity_type)
            if owner_bound:
                entity_id, is_new = await entity_merger.find_or_create_owner_reference(
                    event.user_id, scope=entity_scope, evidence_event_id=event_id,
                )
                name, description = "I", None
            else:
                entity_id, is_new = await entity_merger.find_or_create_entity(
                    name=name,
                    entity_type=entity_type,
                    user_id=event.user_id,
                    description=description,
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
            entity_text = name
            if description:
                entity_text = f"{name}: {description}"
            await write_queue.submit(
                lambda eid=entity_id, txt=entity_text, uid=event.user_id: (
                    vector_index.index(eid, txt, uid)
                ),
                label=f"vector.entity:{entity_id}",
            )

        # Provider type labels for a pronoun can be absent or inconsistent.
        # When its name maps to exactly one event-local identity, register each
        # supplied type as an extraction-local alias to that same identity.
        personal_references: list[tuple[str, str | None]] = []
        for fact in result.facts:
            personal_references.append((fact.subject, fact.subject_entity_type))
            personal_references.append((fact.object, fact.object_entity_type))
        for relationship in result.relationships:
            personal_references.extend((
                (relationship.source_entity, relationship.source_entity_type),
                (relationship.target_entity, relationship.target_entity_type),
            ))
        for name, entity_type in personal_references:
            if entity_type is None or not unresolved_personal_reference(name, entity_type):
                continue
            identity, status = entity_refs.resolve(name)
            if status == "resolved" and identity is not None:
                entity_refs.add(name, entity_type, identity)

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
                polarity=rel.polarity, condition=rel.condition,
            ), True))

        # --- Source-cited claims ---
        planned_claims: set[tuple] = set()
        planned_fact_ids: set[str] = set()
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
            # A checked self-contained text becomes the fact's text and the
            # passage stays its evidence (#91). Turns in the window that gave
            # it a name are listed in its resolution, not in evidence_refs:
            # they supplied a name, not support, and promotion counts
            # evidence_refs as support.
            resolution = fact.resolution if fact.resolved_text else None
            # Without an accepted resolution, the claim's own sentences can be
            # its text; the paragraph stays its evidence.
            sentences = (
                claim_sentences(fact_content, fact.subject, fact.object, speaker=grounding_speaker,
                                first_person_forms=first_person_forms)
                if sentence_text and resolution is None else None
            )

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
                "polarity": fact.polarity,
                "evidence_quote": fact_content,
                "grounding_method": (
                    "resolved_text_v1" if resolution is not None
                    else "claim_sentences_v1" if sentences is not None else "source_passage_v1"
                ),
                "temporal_intent": fact.temporal_intent,
                "replaces_object": fact.replaces_object,
            }
            if speaker is not None:
                bound_fields = [field for field, value, kind in (
                    ("subject", fact.subject, fact.subject_entity_type),
                    ("object", fact.object, fact.object_entity_type),
                ) if _speaker_reference(value, kind)]
                if bound_fields:
                    # The literal pronoun stays in subject/object; this names who it is.
                    fact_metadata["speaker_reference"] = {"speaker": speaker, "fields": bound_fields}
            if owner_reference:
                owner_fields = [field for field, value, kind in (
                    ("subject", fact.subject, fact.subject_entity_type),
                    ("object", fact.object, fact.object_entity_type),
                ) if _speaker_reference(value, kind)]
                if owner_fields:
                    fact_metadata["owner_reference"] = {"user_id": event.user_id, "fields": owner_fields}
            if fact.condition is not None:
                fact_metadata["condition"] = fact.condition
                fact_metadata["condition_state"] = "unknown"
            if fact.quantity is not None:
                fact_metadata["quantity"] = {
                    "value": str(fact.quantity.value),
                    "unit": fact.quantity.unit,
                    "source_text": fact.quantity.source_text,
                    "grounding": "object_decimal_v1",
                }
            if resolution is not None:
                fact_metadata["resolution"] = resolution
                fact_content = fact.resolved_text
            elif sentences is not None:
                fact_content = sentences
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
            fact_source_type = infer_source_type(node_type, role=event.role)

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
            effective_time = (
                datetime.fromisoformat(resolved_date)
                if resolved_date
                else (event.event_time or event.timestamp)
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
                event_time=effective_time,
                valid_from=effective_time,
            )
            copies: list[MemoryNode] = []
            key = claim_key(fact_node, object_entity_id) if merge_claims and subject_entity_id else None
            if key is not None and subject_entity_id is not None:
                claim_identity = (subject_entity_id, key, fact.temporal_intent, fact.replaces_object)
                if claim_identity in planned_claims:
                    # The same message stated this claim twice; one record holds it.
                    continue
                planned_claims.add(claim_identity)
                copies = await find_repeated_claims(
                    graph_store, subject_entity_id, fact_node, key, object_entity_id,
                    exclude=planned_fact_ids,
                )
                if copies:
                    fact_node = merged_claim(fact_node, copies)
            fact_node_id = await writer.create_node(fact_node)
            planned_fact_ids.add(fact_node_id)

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

                # Earlier copies of a repeated claim retire into the merged record.
                for copy in copies:
                    await writer.supersede(str(copy.id), fact_node_id, evidence_id=event_id)

                # Different values can coexist. V14 additionally recognizes
                # negative updates without a separate replacement field.
                # Earlier policies retain their exact replacement rule.
                if explicit_corrections:
                    await supersedence_detector.detect_explicit_update(
                        fact_node_id, subject_entity_id, user_id=event.user_id,
                        evidence_event_id=event_id,
                    )
                elif (
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
                        polarity=fact.polarity,
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
                extraction = await self._event_store.get_extraction(
                    event_id, user_id=event.user_id
                )
                materialization_policy: Literal[
                    "temporal_validity_v7", "speech_act_v8", "speech_act_v9", "speech_act_v10", "speech_act_v11", "speech_act_v12", "speech_act_v13", "speech_act_v14"
                ] = (
                    "speech_act_v14"
                    if extraction is not None and extraction.grounding_policy == "speech_act_v14"
                    else "speech_act_v13"
                    if extraction is not None and extraction.grounding_policy == "speech_act_v13"
                    else "speech_act_v12"
                    if extraction is not None
                    and extraction.grounding_policy in {
                        "speech_act_v6",
                        "speech_act_v7",
                        "speech_act_v8",
                        "speech_act_v9",
                        "speech_act_v10",
                        "speech_act_v11",
                        "speech_act_v12",
                    }
                    else (
                        "speech_act_v11"
                        if extraction is not None
                        and extraction.grounding_policy == "speech_act_v5"
                        else (
                            "speech_act_v10"
                            if extraction is not None
                            and extraction.grounding_policy == "speech_act_v4"
                            else (
                                "speech_act_v9"
                                if extraction is not None
                                and extraction.grounding_policy == "speech_act_v3"
                                else (
                                    "speech_act_v8"
                                    if extraction is not None
                                    and extraction.grounding_policy == "speech_act_v2"
                                    else "temporal_validity_v7"
                                )
                            )
                        )
                    )
                )
                prepared = await self._prepare_plan(
                    result,
                    event,
                    materialization_policy=materialization_policy,
                )
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
        with _DATEPARSER_LOCK:
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
