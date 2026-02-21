"""Two-phase ingestion pipeline orchestrating extraction and materialization.

Phase 1 (immediate): Persist event to EventStore and index content in
lexical store for instant searchability.

Phase 2 (background or awaitable): Extract entities, facts, and relationships
via LLM, validate grounding against source text, merge entities, detect
supersedence, and materialize all derived structures into the graph, vector,
and lexical stores.

On extraction failure, the event is always preserved (Phase 1 already
committed) and extraction is retried with exponential backoff (5s, 30s, 180s).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import dateparser
import structlog

from prme.ingestion.entity_merge import EntityMerger
from prme.ingestion.errors import MaterializationError
from prme.ingestion.graph_writer import GraphWriter, WriteQueueGraphWriter
from prme.ingestion.grounding import validate_grounding
from prme.ingestion.schema import ExtractionResult
from prme.ingestion.supersedence import SupersedenceDetector
from prme.models.edges import MemoryEdge
from prme.models.events import Event
from prme.models.nodes import MemoryNode
from prme.storage.write_queue import WriteTracker
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
    ) -> None:
        self._event_store = event_store
        self._graph_store = graph_store
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._extraction_provider = extraction_provider
        self._write_queue = write_queue
        self._graph_writer = graph_writer
        self._entity_merger = EntityMerger(graph_store, graph_writer) if graph_writer else EntityMerger(graph_store, WriteQueueGraphWriter(graph_store, write_queue))
        self._supersedence_detector = SupersedenceDetector(graph_store, graph_writer) if graph_writer else SupersedenceDetector(graph_store, WriteQueueGraphWriter(graph_store, write_queue))
        self._retry_tasks: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()

    async def ingest(
        self,
        content: str,
        *,
        user_id: str,
        role: str = "user",
        session_id: str | None = None,
        metadata: dict | None = None,
        wait_for_extraction: bool = False,
        scope: Scope = Scope.PERSONAL,
    ) -> str:
        """Ingest a message through the two-phase pipeline.

        Phase 1 (immediate): Create and persist the event, index raw
        content in the lexical store for instant searchability.

        Phase 2 (background or await): Extract entities, facts, and
        relationships via LLM; validate grounding; merge entities;
        detect supersedence; materialize into graph/vector/lexical stores.

        Args:
            content: The message text to ingest.
            user_id: Owner user ID.
            role: Message role ('user', 'assistant', or 'system').
            session_id: Optional session identifier.
            metadata: Optional structured metadata.
            wait_for_extraction: If True, block until extraction and
                materialization complete. Defaults to False (async).

        Returns:
            String UUID of the persisted event.
        """
        # --- Phase 1: Persist event immediately ---
        event = Event(
            content=content,
            user_id=user_id,
            session_id=session_id,
            role=role,
            metadata=metadata,
            scope=scope,
        )
        event_id = await self._write_queue.submit(
            lambda ev=event: self._event_store.append(ev),
            label=f"event.append:{event.id}",
        )

        # Index raw content in lexical store for instant searchability
        await self._write_queue.submit(
            lambda eid=str(event.id), c=content, uid=user_id: (
                self._lexical_index.index(eid, c, uid, "event")
            ),
            label=f"lexical.index:{event.id}",
        )

        logger.info(
            "ingestion.phase1_complete",
            event_id=event_id,
            user_id=user_id,
            role=role,
            session_id=session_id,
        )

        # --- Phase 2: Extract and materialize ---
        task = asyncio.create_task(
            self._extract_and_materialize(event, event_id, scope)
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
        keys, with optional 'metadata'.

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
                wait_for_extraction=wait_for_extraction,
                scope=scope,
            )
            event_ids.append(event_id)
        return event_ids

    async def _extract_and_materialize(
        self, event: Event, event_id: str, scope: Scope = Scope.PERSONAL
    ) -> None:
        """Run LLM extraction, validate grounding, and materialize results.

        On failure, logs the error and schedules a retry with exponential
        backoff. The event is always safe in the event store regardless.

        Args:
            event: The persisted Event model.
            event_id: String UUID of the event.
            scope: Ingestion-level scope for fallback when LLM does not classify.
        """
        try:
            result = await self._extraction_provider.extract(
                event.content, role=event.role
            )
            result = validate_grounding(result, event.content)
            await self._materialize(result, event, event_id, scope)
            logger.info(
                "ingestion.phase2_complete",
                event_id=event_id,
                entities=len(result.entities),
                facts=len(result.facts),
                relationships=len(result.relationships),
            )
        except Exception:
            logger.error(
                "ingestion.extraction_failed",
                event_id=event_id,
                exc_info=True,
            )
            self._schedule_retry(event, event_id, scope=scope)

    async def _materialize(
        self,
        result: ExtractionResult,
        event: Event,
        event_id: str,
        scope: Scope = Scope.PERSONAL,
    ) -> None:
        """Materialize extraction results into graph, vector, and lexical stores.

        Creates a per-event WriteTracker to record all graph artifacts. On
        failure, rolls back all tracked graph nodes and edges. Vector and
        lexical index writes are NOT rolled back (orphaned entries are
        harmless and logged as a warning by WriteTracker).

        Per-object scope from LLM extraction overrides the ingestion-level
        scope. If the LLM did not classify scope (None), the ingestion-level
        scope is used as fallback.

        Args:
            result: Grounding-validated extraction result.
            event: The source event.
            event_id: String UUID of the event.
            scope: Ingestion-level scope for fallback when LLM does not classify.
        """
        tracker = WriteTracker()
        tracked_writer = WriteQueueGraphWriter(
            self._graph_store, self._write_queue, tracker=tracker
        )
        entity_merger = EntityMerger(self._graph_store, tracked_writer)
        supersedence_detector = SupersedenceDetector(self._graph_store, tracked_writer)

        try:
            # Map entity name -> entity_id for relationship wiring
            entity_id_map: dict[str, str] = {}

            # --- Entities ---
            for entity in result.entities:
                # Resolve scope: LLM-extracted scope overrides ingestion-level default
                entity_scope = Scope(entity.scope) if entity.scope else scope

                entity_id, _is_new = await entity_merger.find_or_create_entity(
                    name=entity.name,
                    entity_type=entity.entity_type,
                    user_id=event.user_id,
                    description=entity.description,
                    session_id=event.session_id,
                    evidence_event_id=event_id,
                    scope=entity_scope,
                )
                entity_id_map[entity.name.strip().lower()] = entity_id

                # Index entity in vector store (not tracked for rollback)
                entity_text = entity.name
                if entity.description:
                    entity_text = f"{entity.name}: {entity.description}"
                await self._write_queue.submit(
                    lambda eid=entity_id, txt=entity_text, uid=event.user_id: (
                        self._vector_index.index(eid, txt, uid)
                    ),
                    label=f"vector.entity:{entity_id}",
                )

            # --- Facts ---
            for fact in result.facts:
                # Resolve scope: LLM-extracted scope overrides ingestion-level default
                fact_scope = Scope(fact.scope) if fact.scope else scope

                # Resolve temporal reference
                resolved_date = self._resolve_temporal(fact.temporal_ref)

                # Determine node type from fact_type
                node_type = _FACT_TYPE_TO_NODE_TYPE.get(
                    fact.fact_type, NodeType.FACT
                )

                # Build fact content
                fact_content = f"{fact.subject} {fact.predicate} {fact.object}"

                # Build metadata
                fact_metadata: dict = {
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "object": fact.object,
                }
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

                # Look up default confidence from the matrix
                # Lazy import to avoid circular imports
                from prme.epistemic.matrix import DEFAULT_CONFIDENCE_MATRIX

                matrix_confidence = DEFAULT_CONFIDENCE_MATRIX.lookup_with_fallback(
                    fact_epistemic_type, fact_source_type
                )

                # Create fact node via tracked writer
                fact_node = MemoryNode(
                    node_type=node_type,
                    content=fact_content,
                    user_id=event.user_id,
                    session_id=event.session_id,
                    scope=fact_scope,
                    lifecycle_state=LifecycleState.TENTATIVE,
                    confidence=matrix_confidence,
                    epistemic_type=fact_epistemic_type,
                    source_type=fact_source_type,
                    metadata=fact_metadata,
                    evidence_refs=[event.id],
                )
                fact_node_id = await tracked_writer.create_node(fact_node)

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
                subject_key = fact.subject.strip().lower()
                subject_entity_id = entity_id_map.get(subject_key)
                if subject_entity_id:
                    has_fact_edge = MemoryEdge(
                        source_id=UUID(subject_entity_id),
                        target_id=fact_node.id,
                        edge_type=EdgeType.HAS_FACT,
                        user_id=event.user_id,
                        provenance_event_id=event.id,
                    )
                    await tracked_writer.create_edge(has_fact_edge)

                    # Detect supersedence for the new fact
                    await supersedence_detector.detect_and_supersede(
                        new_fact_node_id=fact_node_id,
                        subject_entity_id=subject_entity_id,
                        predicate=fact.predicate,
                        object_value=fact.object,
                        user_id=event.user_id,
                        evidence_event_id=event_id,
                    )

                # Index fact in vector and lexical stores (not tracked for rollback)
                await self._write_queue.submit(
                    lambda fid=fact_node_id, fc=fact_content, uid=event.user_id: (
                        self._vector_index.index(fid, fc, uid)
                    ),
                    label=f"vector.fact:{fact_node_id}",
                )
                await self._write_queue.submit(
                    lambda fid=fact_node_id, fc=fact_content, uid=event.user_id, nt=node_type.value: (
                        self._lexical_index.index(fid, fc, uid, nt)
                    ),
                    label=f"lexical.fact:{fact_node_id}",
                )

            # --- Relationships ---
            for rel in result.relationships:
                source_key = rel.source_entity.strip().lower()
                target_key = rel.target_entity.strip().lower()
                source_entity_id = entity_id_map.get(source_key)
                target_entity_id = entity_id_map.get(target_key)

                if source_entity_id and target_entity_id:
                    # Map relationship_type to EdgeType
                    edge_type = _relationship_type_to_edge_type(
                        rel.relationship_type
                    )
                    rel_edge = MemoryEdge(
                        source_id=UUID(source_entity_id),
                        target_id=UUID(target_entity_id),
                        edge_type=edge_type,
                        user_id=event.user_id,
                        confidence=rel.confidence,
                        provenance_event_id=event.id,
                    )
                    await tracked_writer.create_edge(rel_edge)
                else:
                    logger.warning(
                        "ingestion.relationship_skipped",
                        source_entity=rel.source_entity,
                        target_entity=rel.target_entity,
                        reason="One or both entities not found in extraction",
                        source_found=source_entity_id is not None,
                        target_found=target_entity_id is not None,
                    )

            # --- Summary (not tracked for rollback) ---
            if result.summary:
                await self._write_queue.submit(
                    lambda eid=event_id, s=result.summary, uid=event.user_id: (
                        self._vector_index.index(eid, s, uid)
                    ),
                    label=f"vector.summary:{event_id}",
                )
                await self._write_queue.submit(
                    lambda eid=event_id, s=result.summary, uid=event.user_id: (
                        self._lexical_index.index(eid, s, uid, "summary")
                    ),
                    label=f"lexical.summary:{event_id}",
                )
        except Exception as exc:
            logger.error(
                "ingestion.materialization_failed",
                event_id=event_id,
                exc_info=True,
            )
            # Rollback all graph artifacts from this event
            await tracker.rollback(self._graph_store, self._write_queue)
            logger.info(
                "ingestion.rollback_complete",
                event_id=event_id,
                rolled_back_nodes=len(tracker.node_ids),
                rolled_back_edges=len(tracker.edge_ids),
            )
            raise MaterializationError(
                f"Materialization failed for event {event_id}",
                event_id=event_id,
            ) from exc

    @staticmethod
    def _resolve_temporal(temporal_ref: str | None) -> str | None:
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
                "RELATIVE_BASE": datetime.now(timezone.utc),
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
        if attempt > 3:
            logger.error(
                "ingestion.max_retries_exceeded",
                event_id=event_id,
                attempts=attempt - 1,
            )
            return

        delay = 5 * (6 ** (attempt - 1))
        logger.warning(
            "ingestion.retry_scheduled",
            event_id=event_id,
            attempt=attempt,
            delay_seconds=delay,
        )

        async def _retry() -> None:
            await asyncio.sleep(delay)
            try:
                await self._extract_and_materialize(event, event_id, scope)
            except Exception:
                logger.error(
                    "ingestion.retry_failed",
                    event_id=event_id,
                    attempt=attempt,
                    exc_info=True,
                )
                self._schedule_retry(event, event_id, attempt + 1, scope=scope)

        task = asyncio.create_task(_retry())
        self._retry_tasks[event_id] = task

    async def shutdown(self) -> None:
        """Cancel all background and retry tasks and wait for completion.

        Should be called during engine shutdown to ensure clean teardown.
        """
        # Cancel background extraction tasks
        for task in self._background_tasks:
            task.cancel()

        # Cancel retry tasks
        for task in self._retry_tasks.values():
            task.cancel()

        # Gather all tasks, suppressing CancelledError
        all_tasks = list(self._background_tasks) + list(
            self._retry_tasks.values()
        )
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

        self._background_tasks.clear()
        self._retry_tasks.clear()
        logger.info("ingestion.pipeline_shutdown")


def _relationship_type_to_edge_type(relationship_type: str) -> EdgeType:
    """Map an extracted relationship type string to an EdgeType enum.

    Falls back to RELATES_TO for unrecognized types.

    Args:
        relationship_type: The extraction-produced relationship type.

    Returns:
        The matching EdgeType enum member.
    """
    mapping: dict[str, EdgeType] = {
        "relates_to": EdgeType.RELATES_TO,
        "part_of": EdgeType.PART_OF,
        "caused_by": EdgeType.CAUSED_BY,
        "supports": EdgeType.SUPPORTS,
        "mentions": EdgeType.MENTIONS,
    }
    return mapping.get(relationship_type.strip().lower(), EdgeType.RELATES_TO)
