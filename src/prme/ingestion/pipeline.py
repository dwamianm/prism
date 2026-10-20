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
import inspect
import math
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import dateparser

from prme._temporal import DATEPARSER_LOCK as _DATEPARSER_LOCK
import structlog

from prme.epistemic.inference import OWNER_ROLES, infer_source_type
from prme.ingestion.entity_merge import EntityMerger
from prme.ingestion.errors import ExtractionError, MaterializationError, extraction_failure_code
from prme.ingestion.graph_writer import GraphWriter, WriteQueueGraphWriter
from prme.ingestion.grounding import claim_sentences, validate_grounding
from prme.ingestion.group_links import SESSION_TURNS, is_group_reference
from prme.ingestion.owner_name import carried_owner_entity, carried_owner_name, owner_name_marker
from prme.ingestion.schema import ExtractedEntity, ExtractedFact, ExtractionResult
from prme.ingestion.temporal import validate_source_time
from prme.ingestion.supersedence import SupersedenceDetector, correctable_claim
from prme.models.edges import MemoryEdge
from prme.models.events import Event
from prme.models.extraction import ExtractionRecord
from prme.models.derivation import DerivationPlan
from prme.models.extraction_work import ExtractionClaim, ExtractionProcessingResult
from prme.models.entity_identity import unresolved_personal_reference
from prme.storage._threading import run_async_to_completion
from prme.storage.index_eviction import evict_from_indexes
from prme.storage.lexical_index import has_lexical_document
from prme.models.nodes import MemoryNode
from prme.models.speaker import introduced_speaker, metadata_speaker, speaker_labeled
from prme.types import EdgeType, EpistemicType, LifecycleState, NodeType, Scope, SourceType

if TYPE_CHECKING:
    from prme.ingestion.extraction import ExtractionProvider
    from prme.ingestion.planning import PlanningGraph
    from prme.ingestion.reingest import Reingester
    from prme.models.reingest import ReingestResult
    from prme.storage.event_store import EventStore
    from prme.storage.graph_store import GraphStore
    from prme.storage.lexical_index import LexicalIndex
    from prme.storage.vector_index import VectorIndex
    from prme.storage.write_queue import WriteQueue

logger = structlog.get_logger(__name__)

# The admission validator that fresh extractions record. v14 adds explicit
# correction and caller-declared owner-reference planning.
_GROUNDING_POLICY: Literal["speech_act_v14"] = "speech_act_v14"

# Batched extraction (extraction_batch_target_tokens, #254). Seconds after an
# ingest() that does not wait before the queued turns of its owner and scope
# are extracted, unless a waiting ingest(), ingest_batch(),
# process_extractions() or the engine's close extracts them sooner.
BATCH_FLUSH_SECONDS = 30.0
# The longest the engine's close waits for the batches of queued turns (#258).
# It allows the usual drain time plus the extraction timeout for each batch
# of unfinished work, including saved output awaiting publication, up to this,
# so a close cannot hang on a provider that does not answer.
BATCH_CLOSE_SECONDS = 300.0
# Pending turns a batch looks at after its first; the token target ends a
# batch long before this.
_BATCH_SCAN_LIMIT = 1000
# Failures that mean a batched response arrived but did not validate as a
# whole: unparseable, truncated or wrongly shaped output.
_VALIDATION_FAILURES = frozenset({
    "ValidationError", "AsyncValidationError", "IncompleteOutputException", "ResponseParsingError",
    "JSONDecodeError",
})
# Failures a batch of several turns is asked again in halves for. A batch that
# takes longer than the provider's timeout, or that the provider refuses as too
# large, most likely does so because of its size, and asking the same batch
# again would fail the same way. A connection, rate-limit or server failure is
# not split: it would most likely fail both halves too, so it follows the
# per-turn retry policy at once.
_HALVABLE_FAILURES = _VALIDATION_FAILURES | {"TimeoutError", "BadRequestError"}
# Earlier claims a batched request shows (enable_extraction_corrections, #277):
# the vector hits read per turn, the claims kept per turn, and in all.
_CORRECTION_SEARCH_K = 40
_CORRECTION_MEMORIES_PER_TURN = 5
_CORRECTION_MEMORIES = 20


class BatchSplitStopped(ExtractionError):
    """A single turn of a split request timed out or was refused, so the rest of the split is not asked."""


@dataclass
class BatchRun:
    """Where a batched request's checked output goes, and how many requests it sent.

    ``_request_batch`` without one saves each turn's output as the source's
    extraction record, under the job's claim. A re-derivation of a source that
    already has a record (``reingest()``) hands in its own ``save`` instead,
    so the saved record stays the source's first extraction, and reads
    ``requests`` for the model calls it made.
    """

    save: Callable[..., Awaitable[None]]
    requests: int = 0
    check: Callable[[], Awaitable[None]] | None = None

# Fact type to NodeType mapping
_FACT_TYPE_TO_NODE_TYPE: dict[str, NodeType] = {
    "decision": NodeType.DECISION,
    "preference": NodeType.PREFERENCE,
}


def _correction_targets(result: ExtractionResult, memories=()) -> ExtractionResult:
    """Each fact's correction target, from the labels its request showed (#277).

    ``replaces_node_id`` is never taken from the model: it is the node of the
    shown memory whose label the fact names, or None. Without ``memories``
    the labels are dropped too, so a request that showed none saves what it
    saved before enable_extraction_corrections existed.
    """
    nodes = {memory.label: memory.node_id for memory in memories}
    facts = []
    for fact in result.facts:
        label = ((fact.replaces_memory or "").strip() or None) if memories else None
        target = nodes.get(label) if label else None
        if (fact.replaces_memory, fact.replaces_node_id) != (label, target):
            fact = fact.model_copy(update={"replaces_memory": label, "replaces_node_id": target})
        facts.append(fact)
    return result.model_copy(update={"facts": facts})


def _claim_line(node: MemoryNode) -> str:
    """A stored claim as subject, predicate and object, with not for a denied one."""
    meta = node.metadata or {}
    denied = "not " if meta.get("polarity") == "negative" else ""
    return f"{meta.get('subject')} {denied}{meta.get('predicate')} {meta.get('object')}"


_FIRST_PERSON_SINGULAR = frozenset({"i", "me", "my", "mine", "myself"})


def _speaker_reference(name: str, entity_type: str | None) -> bool:
    """Whether a reference means whoever speaks the turn: I, me, my, mine or myself."""
    return name.strip().casefold() in _FIRST_PERSON_SINGULAR and unresolved_personal_reference(name, entity_type)


def _speaks_for_a_group(result: ExtractionResult) -> bool:
    """Whether a claim or relationship of the extraction names we, us or our as its subject or object."""
    pairs = [(fact.subject, fact.subject_entity_type) for fact in result.facts]
    pairs += [(fact.object, fact.object_entity_type) for fact in result.facts]
    for relationship in result.relationships:
        pairs += [(relationship.source_entity, relationship.source_entity_type),
                  (relationship.target_entity, relationship.target_entity_type)]
    return any(is_group_reference(value, kind) for value, kind in pairs)


def _introduced_speaker(event: Event, result: ExtractionResult) -> str | None:
    """Who a user turn says it is ("I am Alex Rivera"), when the extraction lists that person."""
    if event.role.casefold() not in OWNER_ROLES:
        return None
    return introduced_speaker(
        event.content, [entity.name for entity in result.entities if entity.entity_type.strip().casefold() == "person"],
    )


def _claim_statement(fact: ExtractedFact, speaker: str | None) -> str:
    """One sentence for a claim whose source sentences are another claim's text.

    The subject, predicate and object, where the speaker's name stands for a
    first-person singular reference that binds to the speaker, and the speaker
    (or the speaker's group, for we) for one that names nobody, because a
    predicate such as wants_to_use reads after a name and not after I.
    """
    def named(value: str, entity_type: str | None) -> str:
        if _speaker_reference(value, entity_type):
            return speaker or "the speaker"
        if is_group_reference(value, entity_type):
            return "the speaker's group"
        return value

    statement = " ".join((
        named(fact.subject, fact.subject_entity_type),
        fact.predicate.replace("_", " "),
        named(fact.object, fact.object_entity_type),
    ))
    if fact.polarity == "negative":
        statement = f"it is not true that {statement}"
    if fact.condition:
        statement = f"{statement} ({fact.condition})"
    return f"{statement[:1].upper()}{statement[1:]}."


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


def _accepts_keyword(function, name: str) -> bool:
    """Whether ``function`` takes the keyword argument ``name`` (a ``**kwargs`` takes any)."""
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return True
    return name in parameters or any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())


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
        extraction_batch_target_tokens: int = 0,
        extraction_timeout: float = 30.0,
        extraction_corrections: bool = False,
        link_mentions: bool = False,
        claim_fixes: bool = False,
        owner_name_carry: bool = False,
        link_owner_groups: bool = False,
    ) -> None:
        if extraction_corrections and extraction_batch_target_tokens <= 0:
            raise ValueError("Extraction corrections need batched extraction")
        if extraction_batch_target_tokens > 0:
            from prme.ingestion.extraction import BatchExtractionProvider

            if extraction_window_turns > 0 or resolve_fact_text:
                raise ValueError("Batched extraction cannot be combined with the extraction window "
                                 "or fact text resolution")
            if not isinstance(extraction_provider, BatchExtractionProvider):
                raise ValueError("Batched extraction needs a provider with extract_batch()")
        self._privacy = None
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
        # Extract several turns of a session per model call (#254). Zero, the
        # default, extracts each turn in its own call as soon as it is ingested.
        self._batch_target_tokens = max(0, extraction_batch_target_tokens)
        self._batch_flush_seconds = BATCH_FLUSH_SECONDS
        # The provider's timeout for one batched request, extraction.batch_timeout (#258).
        # The engine's close allows it for each batched request queued turns
        # still need, up to BATCH_CLOSE_SECONDS in all.
        self._extraction_timeout = extraction_timeout
        self._batch_close_seconds = BATCH_CLOSE_SECONDS
        # Show each batched request earlier claims it may correct (#277).
        self._extraction_corrections = extraction_corrections
        # Link claims and the raw note to the entities their text names (#259).
        self._link_mentions = link_mentions
        # A plural claim keeps its we, a turn that says who it is names its I,
        # and no two claims of a message share a text (enable_ingest_claim_fixes).
        self._claim_fixes = claim_fixes
        # A user turn that names nobody keeps the name an earlier user turn gave its I
        # (enable_owner_name_carry).
        self._owner_name_carry = owner_name_carry
        # A claim a user turn states about we is also linked to the owner's entity
        # (enable_owner_group_links).
        self._link_owner_groups = link_owner_groups
        # One pending flush per owner and scope, armed by ingest() without waiting.
        self._flush_timers: dict[tuple[str, Scope], asyncio.Task] = {}
        # The owner and scope of each flush running in the background.
        self._flushing: dict[asyncio.Task, tuple[str, Scope]] = {}
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
        self._reingester: Reingester | None = None

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

        With extraction_batch_target_tokens above zero (#254), Phase 2 runs
        in batches: a waiting call extracts every queued turn of its owner
        and scope up to and including this one, and a call that does not
        wait queues the turn for the flush :meth:`_arm_flush` schedules.
        """
        validate_source_time(event_time)

        # --- Phase 1: Persist event immediately ---
        event, event_id = await self._append_event(content, user_id=user_id, role=role, session_id=session_id,
                                                   metadata=metadata, event_time=event_time, scope=scope)

        # --- Phase 2: Extract and materialize ---
        if self._batch_target_tokens > 0:
            if wait_for_extraction:
                through = await self._event_store.extraction_work.work_order(user_id=user_id, event_id=event_id)
                await self._flush(user_id, scope, through=through, wait_for=[event_id])
            else:
                self._arm_flush(user_id, scope)
            return str(event.id)
        task = asyncio.create_task(
            self._extract_and_materialize(event, event_id, scope, raise_errors=wait_for_extraction)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        if wait_for_extraction:
            await task

        return str(event.id)

    def queue_extraction(self, event: Event, scope: Scope = Scope.PERSONAL) -> None:
        """Start the background extraction of a saved event that has a durable job (#260).

        The part of Phase 2 of :meth:`ingest` that does not wait, for a source
        that was saved another way: a ``store()`` that asked for extraction
        saves its event, its node and its job in one transaction and calls
        this once its node is in place. Batched, the event joins the flush
        :meth:`_arm_flush` schedules, 30 seconds after the owner's first queued
        turn; unbatched, it extracts at once in a background task.
        """
        if self._batch_target_tokens > 0:
            self._arm_flush(event.user_id, scope)
            return
        task = asyncio.create_task(self._extract_and_materialize(event, str(event.id), scope))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _append_event(
        self, content: str, *, user_id: str, role: str, session_id: str | None, metadata: dict | None,
        event_time: datetime | None, scope: Scope,
    ) -> tuple[Event, str]:
        """Phase 1: persist the event and its durable extraction job; returns it and its stored ID."""
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
        return event, event_id

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

        With extraction_batch_target_tokens above zero (#254), every message
        is persisted first and the batch's end is a flush point: its turns,
        and any queued before them, are extracted in batches, awaited when
        ``wait_for_extraction`` is set and in the background otherwise. A
        failed extraction is then raised after every message is persisted.
        """
        if self._batch_target_tokens > 0:
            return await self._ingest_batch_batched(messages, user_id=user_id, session_id=session_id,
                                                    wait_for_extraction=wait_for_extraction, scope=scope)
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

    async def _ingest_batch_batched(
        self, messages: list[dict], *, user_id: str, session_id: str | None, wait_for_extraction: bool,
        scope: Scope,
    ) -> list[str]:
        for msg in messages:
            validate_source_time(msg.get("event_time"))
        event_ids = []
        try:
            for msg in messages:
                event, _ = await self._append_event(msg["content"], user_id=user_id, role=msg["role"],
                                                    session_id=session_id, metadata=msg.get("metadata"),
                                                    event_time=msg.get("event_time"), scope=scope)
                event_ids.append(str(event.id))
        except BaseException:
            # The messages saved before the failure are still extracted.
            if event_ids:
                self._arm_flush(user_id, scope)
            raise
        if not event_ids:
            return event_ids
        through = await self._event_store.extraction_work.work_order(user_id=user_id, event_id=event_ids[-1])
        if wait_for_extraction:
            await self._flush(user_id, scope, through=through, wait_for=event_ids)
        else:
            self._spawn_flush(user_id, scope, through=through)
        return event_ids

    def _scope_lock(self, user_id: str, scope: Scope) -> asyncio.Lock:
        # Avoid turning ordinary concurrent in-process ingestion into a busy
        # error. Database claims remain the cross-worker ownership boundary.
        key = (user_id, scope)
        lock = self._scope_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._scope_locks[key] = lock
        return lock

    async def _extract_and_materialize(
        self, event: Event, event_id: str, scope: Scope = Scope.PERSONAL,
        *, retry_attempt: int = 0, raise_errors: bool = False, claim: ExtractionClaim | None = None,
        through: int | None = None,
    ) -> None:
        async with self._scope_lock(event.user_id, scope):
            await self._run_extraction(event, event_id, scope, retry_attempt=retry_attempt,
                                       raise_errors=raise_errors, claim=claim, through=through)

    def _spawn(self, coroutine) -> asyncio.Task:
        """Run background extraction work that shutdown drains, logging a failure it did not handle."""
        async def run() -> None:
            try:
                await coroutine
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("ingestion.background_extraction_failed", error_type=type(exc).__name__)

        task = asyncio.create_task(run())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _spawn_flush(self, user_id: str, scope: Scope, *, through: int | None = None) -> None:
        """Flush in the background, as a task the engine's close waits for (#254, #258)."""
        task = self._spawn(self._flush(user_id, scope, through=through))
        self._flushing[task] = (user_id, Scope(scope))
        task.add_done_callback(lambda done: self._flushing.pop(done, None))

    def _arm_flush(self, user_id: str, scope: Scope) -> None:
        """Extract the owner's queued turns in this scope after BATCH_FLUSH_SECONDS (#254).

        An ingest() that does not wait queues its turn, so turns that arrive
        together can share a request. The first such turn arms one flush;
        turns that arrive before it fires join it, and the next turn after it
        arms the next. So no queued turn waits longer than the delay, while a
        caller that ingests a session's turns and then flushes them itself,
        with a waiting ingest(), ingest_batch() or process_extractions(),
        decides how they are batched, independent of timing. The engine's
        close extracts what an armed flush was still waiting for, within the
        drain time :meth:`_drain_seconds` gives it; a turn still queued when
        that ends, or when the process stops, stays pending, durably, until
        the next flush of its owner and scope or process_extractions().
        """
        scope = Scope(scope)
        key = (user_id, scope)
        armed = self._flush_timers.get(key)
        if self._closing or (armed is not None and not armed.done()):
            return

        async def fire() -> None:
            await asyncio.sleep(self._batch_flush_seconds)
            if self._flush_timers.get(key) is asyncio.current_task():
                del self._flush_timers[key]
            self._spawn_flush(user_id, scope)

        self._flush_timers[key] = asyncio.create_task(fire())

    async def _flush(self, user_id: str, scope: Scope, *, through: int | None = None,
                     wait_for: list[str] | None = None) -> None:
        """Extract the queued turns of one owner and scope, several per call (#254).

        Processes the jobs queued at or before work order ``through`` (by
        default, every job queued when the flush begins) in work order, under
        the scope lock, so no turn ingested later joins one of its batches.
        Each job is claimed and published on its own, as without batching;
        the first job of a batch sends the request, and the jobs after it
        load the extraction records it saved. A job whose extraction fails
        stops the flush, since later jobs wait behind it, and its retry
        flushes again. The jobs in ``wait_for`` are the caller's: a failure
        of one of them is raised, as a waiting ingest() raises its own.

        A flush that leaves jobs it was meant to process, because another
        worker still holds an earlier one, a terminal failure ended a retry
        or a claim met a write conflict, arms the next flush, so they do not
        wait for the owner's next ingest().
        """
        scope = Scope(scope)
        wait_for = wait_for or []
        waiting = set(wait_for)
        work = self._event_store.extraction_work
        async with self._scope_lock(user_id, scope):
            if through is None:
                through = await work.work_order(user_id=user_id)
            try:
                while through is not None:
                    claim = await work.claim(user_id=user_id, scope=scope.value, through_order=through,
                                             lease_seconds=self._extraction_lease_seconds)
                    if claim is None:
                        break
                    event = await self._event_store.get(str(claim.event_id))
                    if event is None or event.user_id != user_id:
                        raise RuntimeError("Claimed extraction source is unavailable")
                    await self._run_extraction(event, str(event.id), event.scope, claim=claim,
                                               raise_errors=str(event.id) in waiting, through=through)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A caller's own failure is raised, and the jobs left behind it
                # still get a flush.
                await self._arm_if_unfinished(user_id, scope, through)
                raise
            await self._arm_if_unfinished(user_id, scope, through)
            for event_id in wait_for:
                status = await work.status(event_id, user_id=user_id)
                if status is not None and status.status == "complete":
                    continue
                # Not reached by the flush: it waits behind earlier work, or
                # failed before. Claim it as a waiting ingest() does, which
                # raises when it still cannot run.
                event = await self._event_store.get(event_id)
                if event is None:
                    raise RuntimeError("Extraction source is unavailable")
                await self._run_extraction(event, event_id, event.scope, raise_errors=True, through=through)

    async def _arm_if_unfinished(self, user_id: str, scope: Scope, through: int | None) -> None:
        """Arm the next flush when jobs at or before ``through`` are still unfinished (#254)."""
        if through is None:
            return
        try:
            unfinished = await self._event_store.extraction_work.pending_count(
                user_id=user_id, scope=scope.value, through_order=through)
        except Exception as exc:
            # The store that failed this check would fail the flush too; the
            # next ingest() or process_extractions() picks the work up.
            logger.error("ingestion.extraction_flush_check_failed", error_type=type(exc).__name__)
            return
        if unfinished:
            self._arm_flush(user_id, scope)

    async def _run_extraction(
        self, event: Event, event_id: str, scope: Scope = Scope.PERSONAL,
        *, retry_attempt: int = 0, raise_errors: bool = False, claim: ExtractionClaim | None = None,
        through: int | None = None,
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
            from prme.privacy.records import PRIVACY_KEY
            if PRIVACY_KEY in (event.metadata or {}):
                if self._privacy is None:
                    from prme.privacy.policy import PrivacyDenied
                    raise PrivacyDenied("privacy_capability_unavailable")
                await self._privacy.resume(event, claim=claim)
                return
            plan = await self._event_store.get_derivation_plan(event_id, user_id=event.user_id)
            if plan is None:
                result = await self._extract_or_load(event, claim=claim, through=through)
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
                                through=through,
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

    async def reingest(self, *, user_id: str, scope: Scope | str | None = None, limit: int | None = None,
                       cursor: str | None = None, dry_run: bool = False) -> ReingestResult:
        """Re-derive an owner's stored sources under the present settings; see ``MemoryEngine.reingest``."""
        from prme.ingestion.reingest import Reingester

        if self._reingester is None:
            self._reingester = Reingester(self)
        return await self._reingester.run(user_id=user_id, scope=scope, limit=limit, cursor=cursor, dry_run=dry_run)

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
        # Batched extraction (#254) is a flush: it takes the jobs queued when
        # it begins, so a turn ingested meanwhile never joins its batches.
        through = await work.work_order(user_id=user_id) if self._batch_target_tokens > 0 else None
        for _ in range(limit):
            if (time.monotonic() - started) * 1000 >= budget_ms:
                break
            claim = await work.claim(user_id=user_id, lease_seconds=self._extraction_lease_seconds,
                                     through_order=through)
            if claim is None:
                break
            event = await self._event_store.get(str(claim.event_id))
            if event is None or event.user_id != user_id:
                raise RuntimeError("Claimed extraction source is unavailable")
            try:
                await self._extract_and_materialize(event, str(event.id), event.scope,
                                                    claim=claim, raise_errors=True, through=through)
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

    async def _extract_or_load(self, event: Event, *, claim: ExtractionClaim | None = None,
                               through: int | None = None) -> ExtractionResult:
        """Reuse durable validated output after downstream failure or restart.

        This saves inference results, not graph completion. It does not make
        materialization safe to replay after an interrupted graph write.
        With batched extraction, a turn whose output a batch already saved
        loads it here like any saved output, and a turn without one sends a
        batch (``through`` bounds the turns it may carry).
        """
        saved = await self._event_store.get_extraction(str(event.id), user_id=event.user_id)
        if saved is None and self._batch_target_tokens > 0:
            saved = await self._extract_in_batch(event, claim=claim, through=through)
        elif saved is None:
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
            if self._claim_fixes and _accepts_keyword(self._extraction_provider.extract, "plural_first_person"):
                # A provider written against the earlier signature is not asked, so the default that turned
                # the option on does not break it; its "I" for a "we" is discarded as before.
                extra["plural_first_person"] = True
            async with self._extraction_semaphore:
                result = await self._extraction_provider.extract(
                    event.content, role=event.role, **extra
                )
            result = validate_grounding(result, event.content, speaker=speaker)
            result = self._check_resolved_text(result, event, window)
            # This request showed no earlier memories, so no fact names one.
            result = _correction_targets(result)
            record = ExtractionRecord(
                event_id=event.id, user_id=event.user_id, scope=event.scope,
                content_hash=event.content_hash,
                provider=self._extraction_provider.provider_name,
                model=self._extraction_provider.model_name,
                grounding_policy=_GROUNDING_POLICY,
                result=result.model_dump(mode="json"),
            )
            saved = await self._write_queue.submit(
                lambda: self._event_store.record_extraction(record, claim=claim),
                label=f"extraction.record:{event.id}",
            )
        saved.verify_source(event.user_id, event.scope.value, event.content_hash)
        return ExtractionResult.model_validate(saved.result)

    async def _extract_in_batch(self, head: Event, *, claim: ExtractionClaim | None,
                                through: int | None) -> ExtractionRecord:
        """Extract ``head`` in one request with the queued turns of its session after it (#254).

        The request's turns follow event time, and their order in the queue
        breaks ties. Every turn's checked output is saved as its own
        extraction record, under ``head``'s claim; ``head``'s record is
        returned. A failure is raised only when ``head`` got no record, so a
        turn whose part of a split request failed, or whose request asking
        again for the turns a response left out failed (#260), waits for its
        own job, when it sends a request of its own.
        """
        events = await self._batch_events(head, through)
        events.sort(key=lambda item: item.event_time or item.timestamp)
        failure: ExtractionError | None = None
        try:
            await self._request_batch(head, events, claim=claim)
        except ExtractionError as exc:
            failure = exc
        saved = await self._event_store.get_extraction(str(head.id), user_id=head.user_id)
        if saved is None:
            raise failure or ExtractionError("Batched extraction saved no output for this turn",
                                             event_id=str(head.id), reason_code="ExtractionError")
        if failure is not None:
            # Part of a split request, or the request asking again for turns
            # a response left out, failed; its turns send their own requests later.
            logger.warning("ingestion.extraction_batch_part_failed", event_id=str(head.id),
                           reason=failure.reason_code)
        return saved

    async def _batch_events(self, head: Event, through: int | None) -> list[Event]:
        """``head`` and the queued turns of its session that fit with it, in queue order.

        Turns are added while the sum of their text tokens stays within the
        target. The first that does not fit ends the batch, so a turn larger
        than the target goes alone. A turn without a session goes alone, and
        a turn whose output is already saved needs no request. The engine's
        close uses the same session and token rules (:meth:`_queued_requests`),
        but also reserves time for saved output still awaiting publication.
        A change to the batching rules here belongs there too.
        """
        from prme.retrieval.tokenization import count_tokens

        events = [head]
        if not head.session_id:
            return events
        used = count_tokens(head.content)
        pending = await self._event_store.extraction_work.pending_in_session(
            str(head.id), user_id=head.user_id, through_order=through, limit=_BATCH_SCAN_LIMIT,
        )
        for event_id in pending:
            if used >= self._batch_target_tokens:
                break
            event = await self._event_store.get(event_id)
            if (event is None or event.user_id != head.user_id or event.scope != head.scope
                    or event.session_id != head.session_id):
                continue
            if await self._event_store.get_extraction(event_id, user_id=head.user_id) is not None:
                continue
            tokens = count_tokens(event.content)
            if used + tokens > self._batch_target_tokens:
                break
            events.append(event)
            used += tokens
        return events

    async def _request_batch(self, head: Event, events: list[Event], *,
                             claim: ExtractionClaim | None, ask_again: bool = True,
                             run: BatchRun | None = None) -> None:
        """Send one batched request and save each turn's output, or ask again in halves.

        A response that fails validation as a whole, or a request that times
        out or is refused as too large (``_HALVABLE_FAILURES``), is asked
        again as two halves, recursively, so one bad turn does not fail its
        neighbors. A batch of n turns whose responses keep failing validation
        makes at most 2n - 1 requests. A single turn that times out or is
        refused points at the provider rather than at the batch's size, so
        it stops the whole split (``BatchSplitStopped``): a provider that does
        not answer costs about log2(n) + 1 requests, not 2n - 1. A single
        turn that still fails is raised, and so is any other failure. Each
        request that succeeds is saved at once, with its turns' records.

        A response that gives some of its turns no entry is saved without
        their records, naming them in ``turns_asked_again``, and then one
        more request asks for those turns alone (#260). With ``ask_again``
        off, as for that request and the halves it may be split into, a turn
        left out is saved as stating nothing, so each response is asked
        again at most once. A response that gives none of its turns an entry
        is saved that way too: asking for all of them would repeat the same
        request.
        """
        from prme.ingestion.extraction import (
            BatchTurn,
            batch_request_sha256,
            batch_response_fields,
            batch_turns_without_entry,
            validate_batch_output,
        )
        from prme.models.extraction import ExtractionBatchMemory, ExtractionBatchRecord, ExtractionBatchTurn

        memories = []

        async def check():
            if self._privacy is not None:
                await self._privacy.require_legacy_inputs(events)
                records = await self._privacy.repository.records_for_objects(
                    [("nodes", str(memory.node_id)) for memory in memories], owner=head.user_id)
                if records:
                    from prme.privacy.policy import PrivacyDenied
                    raise PrivacyDenied("privacy_provider_unavailable")
            if run is not None and run.check is not None:
                await run.check()

        await check()
        turns = [
            BatchTurn(index=index, role=event.role, content=event.content,
                      speaker=metadata_speaker(event.metadata), time=event.event_time or event.timestamp)
            for index, event in enumerate(events, start=1)
        ]
        memories = await self._correction_memories(events, check=check)
        # Named only when there are memories, so a provider written before
        # enable_extraction_corrections keeps working while it is off.
        extra = {"memories": memories} if memories else {}
        try:
            if run is not None:
                run.requests += 1
            async with self._extraction_semaphore:
                await check()
                raw = await self._extraction_provider.extract_batch(turns, **extra)
            await check()
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump(mode="json")
            # The speaker is shown to the model either way; grounding accepts
            # the speaker's own I, me and my only with speaker grounding on.
            speakers = {turn.index: turn.speaker for turn in turns} if self._speaker_grounding else {}
            results = validate_batch_output(raw, turns, speakers=speakers, plural_first_person=self._claim_fixes)
            without_entry = batch_turns_without_entry(raw, turns)
        except ExtractionError as exc:
            if len(events) == 1 and exc.reason_code in _HALVABLE_FAILURES - _VALIDATION_FAILURES:
                raise BatchSplitStopped(str(exc), event_id=exc.event_id, reason_code=exc.reason_code) from exc
            if len(events) == 1 or exc.reason_code not in _HALVABLE_FAILURES:
                raise
            logger.warning("ingestion.extraction_batch_halved", event_id=str(head.id), turns=len(events),
                           reason=exc.reason_code)
            middle = len(events) // 2
            failure: ExtractionError | None = None
            for half in (events[:middle], events[middle:]):
                try:
                    await self._request_batch(head, half, claim=claim, ask_again=ask_again, run=run)
                except BatchSplitStopped:
                    raise
                except ExtractionError as half_failure:
                    if half_failure.reason_code not in _HALVABLE_FAILURES:
                        raise
                    failure = failure or half_failure
            if failure is not None:
                raise failure
            return
        # Turns left out are asked again unless this request already asks
        # again, or the response gives no turn an entry (#260).
        missing = without_entry if ask_again and len(without_entry) < len(turns) else []
        batch = ExtractionBatchRecord(
            user_id=head.user_id, scope=head.scope, session_id=head.session_id, head_event_id=head.id,
            provider=self._extraction_provider.provider_name, model=self._extraction_provider.model_name,
            request_sha256=batch_request_sha256(turns, memories),
            turns=tuple(
                ExtractionBatchTurn(index=turn.index, event_id=event.id, role=event.role, speaker=turn.speaker,
                                    event_time=turn.time, content_hash=event.content_hash)
                for turn, event in zip(turns, events, strict=True)
            ),
            response=batch_response_fields(raw),
            turns_asked_again=tuple(missing),
            memories=tuple(ExtractionBatchMemory(label=memory.label, node_id=memory.node_id)
                           for memory in memories),
        )
        records = []
        for turn, event in zip(turns, events, strict=True):
            if turn.index in missing:
                # Saved by the request that asks for it again, below.
                continue
            # The same checks a single turn's output gets, against this turn alone.
            speaker = turn.speaker if self._speaker_grounding else None
            result = validate_grounding(results[turn.index], event.content, speaker=speaker)
            result = self._check_resolved_text(result, event, [])
            result = _correction_targets(result, memories)
            records.append(ExtractionRecord(
                event_id=event.id, user_id=event.user_id, scope=event.scope,
                content_hash=event.content_hash,
                provider=self._extraction_provider.provider_name,
                model=self._extraction_provider.model_name,
                grounding_policy=_GROUNDING_POLICY,
                result=result.model_dump(mode="json"),
                batch_id=batch.id,
            ))
        save = run.save if run is not None else (
            lambda saved, saved_records: self._event_store.record_extraction_batch(saved, saved_records, claim=claim)
        )
        async def save_checked():
            await check()
            await save(batch, records)

        await self._write_queue.submit(
            save_checked,
            label=f"extraction.batch:{batch.id}",
        )
        logger.info("ingestion.extraction_batch_saved", event_id=str(head.id), batch_id=str(batch.id),
                    turns=len(events))
        if missing:
            # The turns' records are saved after the others', so a failure of
            # this request costs only them; their own jobs ask for them later.
            logger.warning("ingestion.extraction_batch_turns_asked_again", event_id=str(head.id),
                           batch_id=str(batch.id), turns=len(missing))
            await self._request_batch(head, [events[index - 1] for index in missing], claim=claim,
                                      ask_again=False, run=run)
        elif without_entry:
            logger.warning("ingestion.extraction_batch_turns_missing", event_id=str(head.id),
                           batch_id=str(batch.id), turns=without_entry, batch_turns=len(turns))

    async def _correction_memories(self, events: list[Event], *, check=None):
        """Earlier claims a batched request shows the model (enable_extraction_corrections, #277).

        For each turn in order, the claims nearest its text in the vector
        index, of its owner and scope, that a correction could retire
        (:func:`correctable_claim`) and that are not effective after the
        turn: up to ``_CORRECTION_MEMORIES_PER_TURN`` per turn and
        ``_CORRECTION_MEMORIES`` in all, labeled m1, m2 and on in that order.
        The request's own turns have no claims yet, so a correction in the
        same request as the claim it corrects is not covered. Empty when
        the option is off.
        """
        from prme.ingestion.extraction import CorrectionMemory

        if not self._extraction_corrections:
            return []
        chosen: dict[str, MemoryNode] = {}
        for event in events:
            moment = event.event_time or event.timestamp
            if check is not None:
                await check()
            if self._privacy is not None:
                await self._privacy.require_legacy_inputs(events)
            hits = await self._vector_index.search(
                event.content, event.user_id, k=_CORRECTION_SEARCH_K, scope=[event.scope.value],
            )
            shown = 0
            for hit in hits:
                if shown >= _CORRECTION_MEMORIES_PER_TURN or len(chosen) >= _CORRECTION_MEMORIES:
                    break
                node_id = str(hit["node_id"])
                if node_id in chosen:
                    shown += 1
                    continue
                node = await self._graph_store.get_node(node_id)
                if node is not None and self._privacy is not None:
                    await self._privacy.require_legacy_inputs(events, (node,))
                if (node is None or (node.user_id, node.scope) != (event.user_id, event.scope)
                        or not correctable_claim(node) or (node.event_time or node.valid_from) > moment):
                    continue
                chosen[node_id] = node
                shown += 1
        return [
            CorrectionMemory(
                label=f"m{number}", node_id=str(node.id), claim=_claim_line(node), text=node.content,
                time=node.event_time or node.valid_from,
                speaker=((node.metadata or {}).get("speaker_reference") or {}).get("speaker"),
            )
            for number, node in enumerate(chosen.values(), start=1)
        ]

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
        privacy=None,
        materialization_policy: Literal[
            "temporal_validity_v7", "speech_act_v8", "speech_act_v9", "speech_act_v10", "speech_act_v11", "speech_act_v12", "speech_act_v13", "speech_act_v14"
        ] = "speech_act_v14",
        before_prepare: Callable[[PlanningGraph], Awaitable[None]] | None = None,
        effect_check: Callable[[], Awaitable[None]] | None = None,
    ) -> DerivationPlan:
        """Prepare fixed graph/index inputs without publishing any artifacts.

        ``before_prepare`` sees the planned graph after the extraction is
        applied and before the embeddings are computed, so a caller can
        add replacements or adjust the planned nodes (``reingest()``).
        """
        from prme.ingestion.planning import PlanningGraph, PlanningIndexes, PlanningQueue

        event = Event.model_validate_json(event.model_dump_json())
        result = ExtractionResult.model_validate_json(result.model_dump_json())
        async def legacy_check(nodes=()):
            if self._privacy is not None:
                await self._privacy.require_legacy_inputs((event,), nodes)
            if effect_check is not None:
                await effect_check()

        if privacy is None:
            await legacy_check()
        graph = PlanningGraph(self._graph_store, event, legacy_check=legacy_check if privacy is None else None)
        indexes = PlanningIndexes(graph)
        current_policy = materialization_policy in {"speech_act_v12", "speech_act_v13", "speech_act_v14"}
        from prme.models.owner_reference import OWNER_REFERENCE_KEY
        owner_reference = (materialization_policy == "speech_act_v14"
                           and event.role.casefold() in {"user", "human"}
                           and (event.metadata or {}).get(OWNER_REFERENCE_KEY) is True)
        speaker = metadata_speaker(event.metadata) if self._bind_speaker_references and current_policy else None
        # A turn that says who it is, with no caller-named speaker, names its own I.
        introduced = (_introduced_speaker(event, result)
                      if self._claim_fixes and self._bind_speaker_references and current_policy
                      and speaker is None and not owner_reference else None)
        # A user turn that names nobody keeps the name an earlier user turn gave its I; the
        # turns that do name their speaker mark the entity a later turn will find.
        carried = None
        owner_name_basis = None
        if (privacy is None and self._owner_name_carry and self._bind_speaker_references and current_policy
                and not owner_reference and event.role.casefold() in OWNER_ROLES):
            owner_name_basis = "speaker" if speaker is not None else "self_introduction" if introduced else None
            if owner_name_basis is None and any(
                _speaker_reference(entity.name, entity.entity_type)
                for entity in _entities_with_unresolved_references(result)
            ):
                carried = await carried_owner_name(graph, user_id=event.user_id, scope=event.scope)
        # A we claim of the owner's turn can repeat one of an earlier turn of the session; the owner's
        # group links find it (enable_owner_group_links). Only a turn whose claims speak for a group looks.
        group_owner = None
        group_session: set[str] = set()
        if (self._link_owner_groups and self._merge_repeated_claims and current_policy and event.session_id
                and not owner_reference and event.role.casefold() in OWNER_ROLES
                and (event.metadata or {}).get(OWNER_REFERENCE_KEY) is not True
                and _speaks_for_a_group(result)):
            owner = await carried_owner_entity(
                graph, user_id=event.user_id, scope=event.scope, name=metadata_speaker(event.metadata),
            )
            if owner is not None:
                group_owner = str(owner.id)
                # The session's turns up to this one bound which earlier copies are read.
                group_session = {str(turn.id) for turn in await self._event_store.get_by_user(
                    event.user_id, session_id=event.session_id, scopes=[event.scope], until=event.timestamp,
                    limit=SESSION_TURNS,
                )}
        await self._populate(
            result, event, str(event.id), event.scope, graph_store=graph,
            writer=graph, vector_index=indexes, lexical_index=indexes, write_queue=PlanningQueue(),
            merge_claims=self._merge_repeated_claims and current_policy,
            first_person_forms=materialization_policy in {"speech_act_v13", "speech_act_v14"},
            explicit_corrections=materialization_policy == "speech_act_v14",
            owner_reference=owner_reference,
            speaker=speaker or introduced or carried, speaker_introduced=introduced is not None,
            speaker_carried=carried is not None, owner_name_basis=owner_name_basis,
            sentence_text=self._claim_sentence_text and current_policy,
            distinct_text=self._claim_fixes,
            grounding_speaker=(metadata_speaker(event.metadata)
                               if self._speaker_grounding and current_policy else None),
            group_owner=group_owner, group_session=group_session,
        )
        if before_prepare is not None:
            await before_prepare(graph)
        return await indexes.prepare(
            self._vector_index._provider,
            materialization_policy=materialization_policy, privacy=privacy,
        )

    async def _populate(
        self, result: ExtractionResult, event: Event, event_id: str, scope: Scope,
        *, graph_store, writer, vector_index, lexical_index, write_queue,
        merge_claims: bool = False, speaker: str | None = None, sentence_text: bool = False,
        grounding_speaker: str | None = None, first_person_forms: bool = False,
        explicit_corrections: bool = False, owner_reference: bool = False,
        speaker_introduced: bool = False, distinct_text: bool = False,
        speaker_carried: bool = False, owner_name_basis: str | None = None,
        group_owner: str | None = None, group_session: set[str] | None = None,
    ) -> None:
        """Apply one set of materialization rules to durable or planning adapters.

        ``merge_claims`` folds a repeated claim into one current record (#209);
        with ``group_owner``, the owner's entity, a claim about we also folds a
        copy that an earlier turn of the session, one of ``group_session``,
        stated (enable_owner_group_links).
        ``speaker`` binds first-person singular references to that speaker's
        entity (enable_speaker_references); ``speaker_introduced`` says the turn
        itself gave the name ("I am Alex Rivera") rather than the caller, and
        ``speaker_carried`` that an earlier user turn gave it and this one
        names nobody (enable_owner_name_carry). With ``owner_name_basis``, a
        person entity this turn creates under the speaker's name is marked as
        the name a user turn gave its owner, for a later turn to find.
        ``sentence_text`` stores a claim's own sentences as its text
        (enable_claim_sentence_text), and with ``distinct_text`` a claim whose
        sentences are already another claim's text from this message gets a
        statement of its own (enable_ingest_claim_fixes).
        With ``grounding_speaker`` (enable_speaker_grounding) the speaker's own
        I, me or my mentions them there as in grounding. Only current-policy
        plans use these, so a saved extraction replanned under an older
        materialization policy keeps that policy's behavior.
        """
        from prme.ingestion.claim_merge import (
            claim_key,
            find_repeated_claims,
            find_repeated_group_claims,
            merged_claim,
        )

        entity_merger = EntityMerger(graph_store, writer)
        supersedence_detector = SupersedenceDetector(graph_store, writer)
        # Map entity name -> entity_id for relationship wiring
        from prme.ingestion.entity_references import EntityReferences
        entity_refs = EntityReferences[str]()

        async def index_new_node(node_id: str, node_type: NodeType, node_scope: Scope,
                                 vector_text: str, lexical_text: str, kind: str) -> None:
            """Index a node this derivation creates (not tracked for rollback).

            Every such node gets a vector; the shared lexical policy, which
            rebuild_indexes() and single-file packs follow too, decides
            whether it also gets a lexical document.
            """
            await write_queue.submit(
                lambda: vector_index.index(node_id, vector_text, event.user_id),
                label=f"vector.{kind}:{node_id}",
            )
            if has_lexical_document(node_type.value, derived=True):
                await write_queue.submit(
                    lambda: lexical_index.index(node_id, lexical_text, event.user_id,
                                                node_type.value, node_scope.value),
                    label=f"lexical.{kind}:{node_id}",
                )

        # --- Entities ---
        entities = _entities_with_unresolved_references(result)
        owner_marker = owner_name_marker(event_id, owner_name_basis) if owner_name_basis and speaker else None
        marked_name = (speaker or "").strip().casefold()
        if owner_name_basis == "speaker" and not any(
            entity.entity_type.strip().casefold() == "person" and entity.name.strip().casefold() == marked_name
            for entity in entities
        ):
            # The caller names the owner, so the owner has their entity even when the turn lists none.
            entities.append(ExtractedEntity(name=speaker, entity_type="person"))
        for entity in entities:
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
                names_owner = (owner_marker is not None and entity_type == "person"
                               and name.strip().casefold() == marked_name)
                entity_id, is_new = await entity_merger.find_or_create_entity(
                    name=name,
                    entity_type=entity_type,
                    user_id=event.user_id,
                    description=description,
                    session_id=event.session_id,
                    evidence_event_id=event_id,
                    scope=entity_scope,
                    owner_name=owner_marker if names_owner else None,
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

            # The entity's vector embeds its description too; its node
            # content, and so any lexical document, is the name.
            entity_text = name
            if description:
                entity_text = f"{name}: {description}"
            await index_new_node(entity_id, NodeType.ENTITY, entity_scope, entity_text, name, "entity")

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
        claim_texts: set[str] = set()
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
                    if speaker_introduced:
                        fact_metadata["speaker_reference"]["basis"] = "self_introduction"
                    elif speaker_carried:
                        fact_metadata["speaker_reference"]["basis"] = "owner_name"
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
            if sentence_text and distinct_text and resolution is None:
                if fact_content in claim_texts:
                    # Another claim of this message already has this text.
                    # A carried name stays out of the text: every later claim would then name the
                    # owner, and a question that names them would match all of them.
                    fact_content = _claim_statement(fact, None if speaker_carried else speaker)
                    fact_metadata["grounding_method"] = "claim_statement_v1"
                else:
                    claim_texts.add(fact_content)
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
                if group_owner is not None:
                    # A we entity is local to its message, so its own subject finds at most this
                    # event's earlier copy (a reingest); the owner's group links reach the same
                    # claim in an earlier turn of the session.
                    known = {copy.id for copy in copies}
                    copies = sorted([*copies, *(copy for copy in await find_repeated_group_claims(
                        graph_store, group_owner, fact_node, key, object_entity_id,
                        session_events=group_session or set(), exclude=planned_fact_ids,
                    ) if copy.id not in known)], key=lambda node: (node.event_time or node.valid_from, str(node.id)))
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

            # A correction the extractor named among the earlier claims its
            # request showed (enable_extraction_corrections, #277). It needs
            # no shared subject, so it does not wait for one.
            if explicit_corrections and fact.replaces_node_id:
                await supersedence_detector.apply_named_correction(
                    fact_node_id, fact.replaces_node_id, user_id=event.user_id,
                    evidence_event_id=event_id,
                )

            if object_entity_id:
                await writer.create_edge(MemoryEdge(
                    source_id=fact_node.id, target_id=UUID(object_entity_id),
                    edge_type=EdgeType.MENTIONS, user_id=event.user_id,
                    provenance_event_id=event.id,
                ))

            await index_new_node(fact_node_id, node_type, fact_scope, fact_content, fact_content, "fact")

    async def _publish_plan(self, plan: DerivationPlan, *, claim: ExtractionClaim | None = None) -> None:
        """Stage saved inputs and publish once; never delete shared retry artifacts.

        After the commit, the nodes the plan replaced leave the search
        indexes, so the only documents removed are those of retired nodes.
        """
        receipt = await self._event_store.get_derivation_receipt(str(plan.event_id), user_id=plan.user_id)
        if receipt is not None:
            if receipt.plan_checksum != plan.checksum:
                raise ValueError("Derivation receipt conflicts with the requested plan")
        else:
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
        # The committed plan retired every node it replaced, such as the
        # earlier copies of a repeated claim. Evict them from the search
        # indexes as the engine's supersede() does, so the indexes hold the
        # documents a rebuild or the open of a single-file pack would build
        # from the graph (#247). The eviction is idempotent and best effort:
        # a failure is logged and the index_compaction organizer job, or
        # prme rebuild, removes what it left. The same backstops repair a
        # process that stopped between the commit and this step, because a
        # managed extraction's completed work row ends its retry before it
        # gets here; the receipt branch above evicts only for the unmanaged
        # callers that publish a committed plan again.
        for edge in plan.replacements:
            await evict_from_indexes(
                self._write_queue, self._lexical_index, self._vector_index, str(edge.target_id),
            )
        # Link the entities to the claims and the note whose text names them
        # (#259). The note may not exist yet; its own materialization links it.
        if self._link_mentions:
            from prme.ingestion.mention_links import link_event_mentions

            await link_event_mentions(
                str(plan.event_id), plan.nodes, user_id=plan.user_id, graph_store=self._graph_store,
                event_store=self._event_store, with_note=True,
                create_edge=(
                    self._graph_writer or WriteQueueGraphWriter(self._graph_store, self._write_queue)
                ).create_edge,
            )
        # A claim a user turn states about we gets one more link, to the owner's entity.
        if self._link_owner_groups:
            from prme.ingestion.group_links import link_group_claims

            await link_group_claims(
                str(plan.event_id), plan.nodes, user_id=plan.user_id, graph_store=self._graph_store,
                event_store=self._event_store,
                create_edge=(
                    self._graph_writer or WriteQueueGraphWriter(self._graph_store, self._write_queue)
                ).create_edge,
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
                # The source's own node: the raw NOTE, whose id is the event's,
                # or the node a store() saved with the event (#260).
                direct = await self._event_store.get_direct_store(event_id, user_id=event.user_id)
                source_id = direct.node.id if direct is not None else event.id
                if any(node.id != source_id for node in existing):
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
            if self._batch_target_tokens > 0:
                # The jobs queued behind this one wait for it, so its retry
                # flushes the queue from it on (#254), as a background flush
                # that the engine's close waits for like any other (#258).
                # A retry the close did not cancel starts nothing after it.
                if not self._closing:
                    self._spawn_flush(event.user_id, scope)
                return
            await self._extract_and_materialize(event, event_id, scope, retry_attempt=attempt)

        task = asyncio.create_task(_retry())
        self._retry_tasks[event_id] = task

        def discard(completed: asyncio.Task) -> None:
            if self._retry_tasks.get(event_id) is completed:
                self._retry_tasks.pop(event_id, None)

        task.add_done_callback(discard)

    async def _drain_seconds(self, drain_timeout: float, keys: list[tuple[str, Scope]]) -> float:
        """How long the engine's close waits for background extraction (#258).

        Without batching, or with no flush to wait for, it is
        ``drain_timeout``. Otherwise the close conservatively allows the
        provider's timeout for each batch of unfinished work in these owners
        and scopes, counted by :meth:`_queued_requests`, plus the usual drain.
        Saved output still needs publication, so it retains its allowance
        while the flush and count run concurrently. The total is bounded by
        BATCH_CLOSE_SECONDS.
        """
        if self._batch_target_tokens <= 0 or not keys:
            return drain_timeout
        cap = max(drain_timeout, self._batch_close_seconds)
        seconds, requests = drain_timeout, 0
        try:
            for user_id, scope in dict.fromkeys(keys):
                if seconds >= cap:
                    break
                # Counting stops once the cap is reached.
                limit = (math.ceil((cap - seconds) / self._extraction_timeout)
                         if self._extraction_timeout > 0 else _BATCH_SCAN_LIMIT)
                queued = await self._queued_requests(user_id, scope, limit=limit)
                requests += queued
                seconds += queued * self._extraction_timeout
        except Exception as exc:
            # The store that failed this count would fail the flush too.
            logger.error("ingestion.extraction_close_count_failed", error_type=type(exc).__name__)
        seconds = min(cap, seconds)
        logger.info("ingestion.extraction_close_drain", seconds=seconds, batched_requests=requests)
        return seconds

    async def _queued_requests(self, user_id: str, scope: Scope, *, limit: int) -> int:
        """Conservative unfinished-batch count for the close's request allowance (#258).

        Uses :meth:`_batch_events`' session and token rules: in work order, a
        turn joins the request its session has open while their text stays
        within the target; one that does not fit, or has no session, starts
        another batch. Unlike an actual request, the count includes saved
        output whose work is still unfinished: saving extraction must not
        remove the time reserved for its publication. Completed work is
        excluded by ``unfinished``. Counting stops at ``limit``.
        """
        from prme.retrieval.tokenization import count_tokens

        requests = 0
        # The tokens of the request each session has open.
        open_requests: dict[str, int] = {}
        work = self._event_store.extraction_work
        for event_id in await work.unfinished(user_id=user_id, scope=Scope(scope).value, limit=_BATCH_SCAN_LIMIT):
            if requests >= limit:
                break
            event = await self._event_store.get(event_id)
            if event is None:
                continue
            tokens = count_tokens(event.content)
            used = open_requests.get(event.session_id) if event.session_id else None
            if used is not None and used < self._batch_target_tokens and used + tokens <= self._batch_target_tokens:
                open_requests[event.session_id] = used + tokens
                continue
            requests += 1
            if event.session_id:
                open_requests[event.session_id] = tokens
        return requests

    async def shutdown(self, drain_timeout: float = 10.0) -> None:
        """Drain pending background tasks then shut down.

        Waits up to *drain_timeout* seconds for in-progress extraction
        tasks to finish so that supersedence detection completes before
        the engine closes.  Any tasks still running after the timeout
        are cancelled.  Retry tasks are cancelled immediately since
        their long sleep delays make draining impractical.

        With batched extraction (#258), the flushes of queued turns get
        longer: *drain_timeout* plus the extraction timeout for each batch
        of unfinished work, including saved output awaiting publication
        (:meth:`_drain_seconds`). The flushes start before their work is
        counted, and the count is part of the wait, which never passes
        BATCH_CLOSE_SECONDS in all.

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

        # A flush still waiting for its delay runs now, so the turns it waited
        # for are extracted within the drain below, as a background
        # extraction is without batching (#254). The drain allows for their
        # batched requests and those of the flushes already running (#258).
        waiting = [key for key, timer in self._flush_timers.items() if not timer.done()]
        for key in waiting:
            self._flush_timers[key].cancel()
        if self._flush_timers:
            await asyncio.gather(*self._flush_timers.values(), return_exceptions=True)
        self._flush_timers.clear()
        for key in waiting:
            self._spawn_flush(*key)
        if self._batch_target_tokens > 0:
            # The flushes run while their requests are counted, and the count
            # is part of the wait, so the whole wait stays within the cap.
            started = time.monotonic()
            cap = max(drain_timeout, self._batch_close_seconds)
            try:
                budget = await asyncio.wait_for(
                    self._drain_seconds(drain_timeout, list(self._flushing.values())), timeout=cap)
            except asyncio.TimeoutError:
                logger.warning("ingestion.extraction_close_count_timed_out", seconds=cap)
                budget = cap
            drain_timeout = max(0.0, budget - (time.monotonic() - started))

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
