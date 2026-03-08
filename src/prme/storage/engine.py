"""Unified MemoryEngine coordinating all four storage backends.

The MemoryEngine is the single entry point for all storage and retrieval
operations. A single store() call auto-propagates to: EventStore (source
of truth), GraphStore (relational model), VectorIndex (semantic search),
and LexicalIndex (full-text search).

The retrieve() method provides hybrid retrieval through the 6-stage
RetrievalPipeline: query analysis, candidate generation, epistemic
filtering, scoring, context packing, and operation logging.

The ingest() method provides full two-phase ingestion: immediate event
persistence followed by LLM extraction and materialization of entities,
facts, relationships, and supersedence chains. All writes are serialized
through a WriteQueue for DuckDB single-writer safety.

Developers interact with this class exclusively. Backend coordination,
error handling, and lifecycle transitions are managed here.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from datetime import datetime
from typing import TYPE_CHECKING, Any

import duckdb

from prme.config import PRMEConfig
from prme.models import Event, MemoryNode
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.embedding import create_embedding_provider
from prme.storage.event_store import EventStore
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
from prme.storage.write_queue import NoOpWriteQueue, WriteQueue
from prme.types import (
    DecayProfile,
    EpistemicType,
    LifecycleState,
    NodeType,
    RepresentationLevel,
    Scope,
    SourceType,
)

if TYPE_CHECKING:
    import asyncpg

    from prme.ingestion.pipeline import IngestionPipeline
    from prme.organizer.models import OrganizeResult
    from prme.retrieval.config import ScoringWeights
    from prme.retrieval.models import RetrievalResponse
    from prme.retrieval.pipeline import RetrievalPipeline

from prme.organizer.maintenance import MaintenanceRunner

logger = logging.getLogger(__name__)


class MemoryEngine:
    """Unified memory interface. Single entry point for all storage operations.

    Coordinates writes across EventStore, GraphStore, VectorIndex, and
    LexicalIndex. All writes are serialized through a WriteQueue for
    DuckDB single-writer safety. The ingest() method provides full
    two-phase ingestion with LLM extraction via IngestionPipeline.

    A single store() call propagates content to all four backends.
    Lifecycle transitions (promote, supersede, archive) are passed
    through to the GraphStore.

    Use the async create() factory method to initialize.
    """

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection | None,
        event_store: Any,
        graph_store: Any,
        vector_index: Any,
        lexical_index: Any,
        write_queue: WriteQueue | NoOpWriteQueue,
        pipeline: IngestionPipeline | None = None,
        retrieval_pipeline: RetrievalPipeline | None = None,
        confidence_matrix: object | None = None,
        epistemic_weights: dict[str, float] | None = None,
        unverified_confidence_threshold: float | None = None,
        pool: asyncpg.Pool | None = None,
        config: PRMEConfig | None = None,
    ) -> None:
        self._conn = conn
        self._pool = pool
        self._event_store = event_store
        self._graph_store = graph_store
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._write_queue = write_queue
        self._pipeline = pipeline
        self._retrieval_pipeline = retrieval_pipeline
        self._config = config if config is not None else PRMEConfig()
        self._maintenance_runner: MaintenanceRunner | None = None

        # Config-driven overrides with module-level defaults as fallback
        from prme.epistemic.matrix import DEFAULT_CONFIDENCE_MATRIX

        self._confidence_matrix = (
            confidence_matrix if confidence_matrix is not None
            else DEFAULT_CONFIDENCE_MATRIX
        )
        self._epistemic_weights = epistemic_weights
        self._unverified_confidence_threshold = (
            unverified_confidence_threshold
            if unverified_confidence_threshold is not None
            else 0.30
        )

    @classmethod
    async def create(cls, config: PRMEConfig | None = None) -> "MemoryEngine":
        """Create and initialize a MemoryEngine with all backends.

        Dispatches to ``_create_duckdb()`` or ``_create_postgres()``
        based on ``config.backend``. When ``database_url`` is set,
        all storage uses PostgreSQL; otherwise, file-based DuckDB.

        Args:
            config: Optional configuration. Defaults to PRMEConfig().

        Returns:
            An initialized MemoryEngine ready for use.
        """
        if config is None:
            config = PRMEConfig()

        if config.backend == "postgres":
            return await cls._create_postgres(config)
        return await cls._create_duckdb(config)

    @classmethod
    async def _create_duckdb(cls, config: PRMEConfig) -> "MemoryEngine":
        """Create a MemoryEngine backed by DuckDB (file-based)."""
        # Open DuckDB connection
        conn = duckdb.connect(config.db_path)

        # Initialize schema (tables, indexes, DuckPGQ attempt)
        initialize_database(conn)

        # Create shared connection lock for DuckDB thread-safety.
        conn_lock = asyncio.Lock()

        # Create backend stores
        event_store = EventStore(conn, conn_lock)
        graph_store = DuckPGQGraphStore(conn, conn_lock)

        # Create embedding provider via factory
        embedding_provider = create_embedding_provider(config.embedding)

        # Create vector index
        vector_index = VectorIndex(conn, config.vector_path, embedding_provider, conn_lock)

        # Create lexical index
        lexical_index = LexicalIndex(config.lexical_path)

        # Create and start write queue
        write_queue = WriteQueue(maxsize=config.write_queue_size)
        await write_queue.start()

        # Lazy import to avoid circular import
        from prme.ingestion.extraction import create_extraction_provider
        from prme.ingestion.graph_writer import WriteQueueGraphWriter
        from prme.ingestion.pipeline import IngestionPipeline

        extraction_provider = create_extraction_provider(config.extraction)
        graph_writer = WriteQueueGraphWriter(graph_store, write_queue)

        from prme.epistemic.matrix import DEFAULT_CONFIDENCE_MATRIX as _default_matrix

        _active_confidence_matrix = _default_matrix.with_overrides(
            config.confidence_overrides
        )

        pipeline = IngestionPipeline(
            event_store=event_store,
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
            extraction_provider=extraction_provider,
            write_queue=write_queue,
            graph_writer=graph_writer,
            confidence_matrix=_active_confidence_matrix,
        )

        from prme.retrieval.pipeline import RetrievalPipeline

        retrieval_pipeline = RetrievalPipeline(
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
            conn=conn,
            conn_lock=conn_lock,
            scoring_weights=config.scoring,
            packing_config=config.packing,
            epistemic_weights=config.epistemic_weights,
            unverified_confidence_threshold=config.unverified_confidence_threshold,
        )

        # Run epistemic backfill migration for existing nodes
        from prme.epistemic.migration import backfill_epistemic_types

        backfill_count = await backfill_epistemic_types(graph_store)
        if backfill_count > 0:
            logger.info(
                "Backfilled epistemic types for %d existing nodes",
                backfill_count,
            )

        logger.debug(
            "PRMEConfig: scoring=%s, packing_budget=%d, confidence_overrides=%d",
            config.scoring.version_id,
            config.packing.token_budget,
            len(config.confidence_overrides),
        )

        engine = cls(
            conn=conn,
            event_store=event_store,
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
            write_queue=write_queue,
            pipeline=pipeline,
            retrieval_pipeline=retrieval_pipeline,
            confidence_matrix=_active_confidence_matrix,
            epistemic_weights=config.epistemic_weights,
            unverified_confidence_threshold=config.unverified_confidence_threshold,
            config=config,
        )
        engine._maintenance_runner = MaintenanceRunner(engine, config.organizer)
        return engine

    @classmethod
    async def _create_postgres(cls, config: PRMEConfig) -> "MemoryEngine":
        """Create a MemoryEngine backed by PostgreSQL."""
        from prme.storage.pg import (
            PgEventStore,
            PgGraphStore,
            PgLexicalIndex,
            PgVectorIndex,
            create_pool,
            initialize_pg_database,
        )

        assert config.database_url is not None

        # Create asyncpg pool and initialize schema
        pool = await create_pool(config.database_url)
        await initialize_pg_database(pool, embedding_dim=config.embedding.dimension)

        # Create Pg backends
        event_store = PgEventStore(pool)
        graph_store = PgGraphStore(pool)
        embedding_provider = create_embedding_provider(config.embedding)
        vector_index = PgVectorIndex(pool, embedding_provider)
        lexical_index = PgLexicalIndex(pool)

        # NoOpWriteQueue — PostgreSQL handles multi-writer natively
        write_queue = NoOpWriteQueue()
        await write_queue.start()

        # Lazy imports for ingestion pipeline
        from prme.ingestion.extraction import create_extraction_provider
        from prme.ingestion.graph_writer import WriteQueueGraphWriter
        from prme.ingestion.pipeline import IngestionPipeline

        extraction_provider = create_extraction_provider(config.extraction)
        graph_writer = WriteQueueGraphWriter(graph_store, write_queue)

        from prme.epistemic.matrix import DEFAULT_CONFIDENCE_MATRIX as _default_matrix

        _active_confidence_matrix = _default_matrix.with_overrides(
            config.confidence_overrides
        )

        pipeline = IngestionPipeline(
            event_store=event_store,
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
            extraction_provider=extraction_provider,
            write_queue=write_queue,
            graph_writer=graph_writer,
            confidence_matrix=_active_confidence_matrix,
        )

        from prme.retrieval.pipeline import RetrievalPipeline

        retrieval_pipeline = RetrievalPipeline(
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
            conn=None,
            conn_lock=None,
            pool=pool,
            scoring_weights=config.scoring,
            packing_config=config.packing,
            epistemic_weights=config.epistemic_weights,
            unverified_confidence_threshold=config.unverified_confidence_threshold,
        )

        # Run epistemic backfill migration
        from prme.epistemic.migration import backfill_epistemic_types

        backfill_count = await backfill_epistemic_types(graph_store)
        if backfill_count > 0:
            logger.info(
                "Backfilled epistemic types for %d existing nodes",
                backfill_count,
            )

        logger.debug(
            "PRMEConfig[postgres]: scoring=%s, packing_budget=%d",
            config.scoring.version_id,
            config.packing.token_budget,
        )

        engine = cls(
            conn=None,
            pool=pool,
            event_store=event_store,
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
            write_queue=write_queue,
            pipeline=pipeline,
            retrieval_pipeline=retrieval_pipeline,
            confidence_matrix=_active_confidence_matrix,
            epistemic_weights=config.epistemic_weights,
            unverified_confidence_threshold=config.unverified_confidence_threshold,
            config=config,
        )
        engine._maintenance_runner = MaintenanceRunner(engine, config.organizer)
        return engine

    # --- Core Operations ---

    async def store(
        self,
        content: str,
        *,
        user_id: str,
        session_id: str | None = None,
        role: str = "user",
        node_type: NodeType = NodeType.NOTE,
        scope: Scope = Scope.PERSONAL,
        metadata: dict | None = None,
        confidence: float | None = None,
        epistemic_type: EpistemicType | None = None,
        source_type: SourceType | None = None,
    ) -> str:
        """Store content across all four backends in one call.

        Auto-propagation pipeline:
        1. Create Event, append to EventStore via write queue (MUST succeed).
        2. Create MemoryNode with evidence_refs=[event.id], store in GraphStore
           via write queue.
        3. Index into VectorIndex and LexicalIndex via write queue.

        If vector/lexical indexing fails, the error is logged but the
        store() call does not fail -- the event is already persisted and
        derived indexes can be rebuilt from the event log.

        Args:
            content: Text content to store.
            user_id: Owner user ID.
            session_id: Optional session identifier.
            role: Event role ('user', 'assistant', or 'system').
            node_type: Type of memory node to create.
            scope: Memory scope (personal, project, org).
            metadata: Optional structured metadata.
            confidence: Initial confidence score. If None, derived from
                the (epistemic_type, source_type) confidence matrix.
            epistemic_type: Epistemic classification. If None, inferred
                from node_type via heuristic.
            source_type: Source provenance type. If None, inferred from
                node_type and role via heuristic.

        Returns:
            String UUID of the created event (source of truth ID).
        """
        # Infer epistemic_type and source_type if not provided
        # Lazy imports to avoid circular dependencies
        from prme.epistemic.inference import infer_epistemic_type, infer_source_type

        if epistemic_type is None:
            epistemic_type = infer_epistemic_type(node_type)
        if source_type is None:
            source_type = infer_source_type(node_type, role=role)
        if confidence is None:
            confidence = self._confidence_matrix.lookup_with_fallback(
                epistemic_type, source_type
            )

        # Step 1: Create and persist event (source of truth) via write queue
        event = Event(
            content=content,
            user_id=user_id,
            session_id=session_id,
            role=role,
            scope=scope,
            metadata=metadata,
        )
        event_id = await self._write_queue.submit(
            lambda ev=event: self._event_store.append(ev),
            label=f"store.event:{event.id}",
        )

        # Step 1.5: Novelty scoring (surprise-gated storage, issue #20)
        novelty_result = None
        if self._config.enable_surprise_gating:
            try:
                novelty_result = await self._compute_novelty(content, user_id)
            except Exception:
                logger.warning(
                    "Novelty scoring failed for event %s. "
                    "Non-fatal; proceeding with default salience.",
                    event_id,
                    exc_info=True,
                )

        # Step 2: Create graph node via write queue
        from prme.types import DEFAULT_DECAY_PROFILE_MAPPING

        decay_profile = DEFAULT_DECAY_PROFILE_MAPPING.get(
            epistemic_type, DecayProfile.MEDIUM
        )

        # Apply novelty adjustment to initial salience (issue #20)
        salience_base = 0.5  # default
        if novelty_result is not None:
            salience_base = max(
                0.0, min(1.0, 0.5 + novelty_result.salience_adjustment)
            )

        # Add novelty score to metadata if computed
        if novelty_result is not None:
            node_metadata = dict(metadata) if metadata else {}
            node_metadata["novelty_score"] = round(
                novelty_result.novelty_score, 4
            )
            node_metadata["max_similarity"] = round(
                novelty_result.max_similarity, 4
            )
            if novelty_result.nearest_node_id:
                node_metadata["nearest_node_id"] = (
                    novelty_result.nearest_node_id
                )
            metadata = node_metadata

        node = MemoryNode(
            user_id=user_id,
            session_id=session_id,
            node_type=node_type,
            scope=scope,
            content=content,
            metadata=metadata,
            confidence=confidence,
            confidence_base=confidence,
            salience=salience_base,
            salience_base=salience_base,
            epistemic_type=epistemic_type,
            source_type=source_type,
            evidence_refs=[event.id],
            decay_profile=decay_profile,
        )
        node_id = await self._write_queue.submit(
            lambda n=node: self._graph_store.create_node(n),
            label=f"store.node:{node.id}",
        )

        # Step 3: Index into vector and lexical via write queue
        try:
            await self._write_queue.submit(
                lambda nid=node_id, c=content, uid=user_id: (
                    self._vector_index.index(nid, c, uid)
                ),
                label=f"store.vector:{node_id}",
            )
            await self._write_queue.submit(
                lambda nid=node_id, c=content, uid=user_id, nt=node_type.value, sc=scope.value: (
                    self._lexical_index.index(nid, c, uid, nt, sc)
                ),
                label=f"store.lexical:{node_id}",
            )
        except Exception:
            logger.warning(
                "Vector/lexical indexing failed for event %s. "
                "Event is persisted; indexes can be rebuilt.",
                event_id,
                exc_info=True,
            )

        # Step 3.5: Re-mention reinforcement (opt-in)
        if self._config.reinforce_similarity_threshold is not None:
            try:
                await self._check_remention_reinforcement(
                    content, str(node.id), user_id, event.id,
                )
            except Exception:
                logger.warning(
                    "Re-mention reinforcement check failed for node %s. "
                    "Non-fatal; node is stored successfully.",
                    node.id,
                    exc_info=True,
                )

        # Step 4: Supersedence detection (opt-in)
        if self._config.enable_store_supersedence:
            try:
                await self._check_store_supersedence(
                    content, str(node.id), user_id
                )
            except Exception:
                logger.warning(
                    "Store supersedence check failed for node %s. "
                    "Non-fatal; node is stored successfully.",
                    node.id,
                    exc_info=True,
                )

            # Step 5: Oscillation detection (runs after supersedence)
            try:
                await self._check_oscillation(str(node.id))
            except Exception:
                logger.warning(
                    "Oscillation check failed for node %s. "
                    "Non-fatal; node is stored successfully.",
                    node.id,
                    exc_info=True,
                )

        return event_id

    async def _check_store_supersedence(
        self, content: str, new_node_id: str, user_id: str
    ) -> None:
        """Check if new content supersedes existing nodes.

        Uses keyword-based contradiction detection to find existing nodes
        that the new content explicitly replaces. Non-fatal: any error is
        caught by the caller so store() never fails due to this check.
        """
        from prme.organizer.contradiction import ContentContradictionDetector

        detector = ContentContradictionDetector()
        if not detector.has_contradiction_signal(content):
            return

        # Find similar existing nodes via vector search
        try:
            similar = await self._vector_index.search(content, user_id, k=10)
        except Exception:
            return  # Vector search failure is non-fatal

        if not similar:
            return

        # Get the content of similar nodes (excluding the new node itself)
        existing_contents: list[tuple[str, str]] = []
        for result in similar:
            sid = result["node_id"]
            if sid == new_node_id:
                continue
            node = await self._graph_store.get_node(
                sid, include_superseded=False
            )
            if node is not None:
                existing_contents.append((sid, node.content))

        if not existing_contents:
            return

        # Find which nodes are superseded
        superseded_node_ids = detector.find_superseded_content(
            content, existing_contents
        )

        # Mark them as superseded
        for old_id in superseded_node_ids:
            try:
                await self.supersede(old_id, new_node_id)
                logger.info(
                    "Store supersedence: node %s superseded by %s",
                    old_id,
                    new_node_id,
                )
            except (ValueError, Exception):
                logger.debug(
                    "Could not supersede node %s (may already be superseded)",
                    old_id,
                    exc_info=True,
                )

    async def _check_remention_reinforcement(
        self,
        content: str,
        new_node_id: str,
        user_id: str,
        event_id: str,
    ) -> None:
        """Reinforce existing similar nodes when new content re-mentions a topic.

        When reinforce_similarity_threshold is set, searches for existing nodes
        with similarity >= threshold and calls reinforce() on each match. The
        new node itself is always skipped. Superseded/archived nodes are also
        skipped. The entire block is non-fatal.

        Args:
            content: The newly stored content text.
            new_node_id: The node ID of the just-created node (to skip).
            user_id: Owner user ID for scoping vector search.
            event_id: Event ID from the new store, passed as evidence_id.
        """
        threshold = self._config.reinforce_similarity_threshold
        if threshold is None:
            return

        # Vector-search for similar existing nodes
        try:
            similar = await self._vector_index.search(content, user_id, k=5)
        except Exception:
            logger.debug(
                "Vector search failed during re-mention check for node %s",
                new_node_id,
                exc_info=True,
            )
            return

        if not similar:
            return

        for result in similar:
            sid = result["node_id"]
            score = result.get("score", 0.0)

            # Skip the newly created node itself
            if sid == new_node_id:
                continue

            # Check similarity threshold
            if score < threshold:
                continue

            # Skip superseded/archived nodes
            existing_node = await self._graph_store.get_node(
                sid, include_superseded=False
            )
            if existing_node is None:
                continue

            # Reinforce the matching existing node
            try:
                await self.reinforce(sid, evidence_id=str(event_id))
                logger.info(
                    "Re-mention reinforcement: node %s reinforced "
                    "(similarity=%.3f) by new node %s",
                    sid,
                    score,
                    new_node_id,
                )
            except Exception:
                logger.debug(
                    "Could not reinforce node %s during re-mention check",
                    sid,
                    exc_info=True,
                )

    async def _compute_novelty(self, content: str, user_id: str):
        """Compute novelty score for incoming content.

        Uses vector similarity to measure how surprising the content is
        relative to existing knowledge. Higher novelty = more surprising.

        Args:
            content: Text content to evaluate.
            user_id: Owner user ID for scoping vector search.

        Returns:
            NoveltyResult with score and salience adjustment.
        """
        from prme.ingestion.novelty import NoveltyScorer

        scorer = NoveltyScorer(
            high_novelty_threshold=self._config.novelty_high_threshold,
            low_novelty_threshold=self._config.novelty_low_threshold,
            salience_boost=self._config.novelty_salience_boost,
            salience_penalty=self._config.novelty_salience_penalty,
        )
        return await scorer.score(content, user_id, self._vector_index)

    async def _check_oscillation(self, new_node_id: str) -> None:
        """Check if a new node is part of a flip-flop oscillation pattern.

        Traverses the supersedence chain backward and checks for content
        similarity loops. If oscillation is detected, reduces confidence_base
        on the new node. Non-fatal: any error is caught by the caller so
        store() never fails due to this check.
        """
        from prme.organizer.oscillation import OscillationDetector

        detector = OscillationDetector()
        results = await detector.detect_oscillations(
            self._graph_store, new_node_id
        )

        if not results:
            return

        for osc in results:
            logger.info(
                "Oscillation detected for node %s: topic=%r, cycles=%d, penalty=%.2f",
                new_node_id,
                osc.topic,
                osc.cycle_count,
                osc.confidence_penalty,
            )

            # Reduce confidence_base on the new node
            node = await self._graph_store.get_node(
                new_node_id, include_superseded=True
            )
            if node is not None:
                new_confidence = max(
                    0.0, node.confidence_base - osc.confidence_penalty
                )
                await self._graph_store.update_node(
                    new_node_id, confidence_base=new_confidence
                )

    # --- Ingestion Operations ---

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
        """Ingest a message with LLM-powered extraction and materialization.

        Delegates to the IngestionPipeline for two-phase ingestion:
        Phase 1 persists the event immediately, Phase 2 runs extraction
        and materialization (background by default, or synchronously if
        wait_for_extraction=True).

        If no pipeline is configured, falls back to store() behavior.

        Args:
            content: The message text to ingest.
            user_id: Owner user ID.
            role: Message role ('user', 'assistant', or 'system').
            session_id: Optional session identifier.
            metadata: Optional structured metadata.
            wait_for_extraction: If True, block until extraction completes.

        Returns:
            String UUID of the persisted event.
        """
        if self._pipeline is None:
            event_id = await self.store(
                content,
                user_id=user_id,
                session_id=session_id,
                role=role,
                scope=scope,
                metadata=metadata,
            )
        else:
            event_id = await self._pipeline.ingest(
                content,
                user_id=user_id,
                role=role,
                session_id=session_id,
                metadata=metadata,
                wait_for_extraction=wait_for_extraction,
                scope=scope,
            )

        # Opportunistic maintenance (RFC-0015 Layer 2)
        if self._maintenance_runner:
            await self._maintenance_runner.maybe_run()

        return event_id

    async def ingest_batch(
        self,
        messages: list[dict],
        *,
        user_id: str,
        session_id: str | None = None,
        wait_for_extraction: bool = False,
        scope: Scope = Scope.PERSONAL,
    ) -> list[str]:
        """Ingest a batch of messages with LLM extraction.

        Delegates to the IngestionPipeline for sequential batch
        processing. Each message dict must have 'content' and 'role'
        keys, with optional 'metadata'.

        If no pipeline is configured, falls back to sequential store().

        Args:
            messages: List of message dicts with 'content' and 'role'.
            user_id: Owner user ID for all messages.
            session_id: Optional session identifier for all messages.
            wait_for_extraction: If True, block until all extractions
                complete.

        Returns:
            List of event ID strings, one per message.
        """
        if self._pipeline is None:
            event_ids: list[str] = []
            for msg in messages:
                eid = await self.store(
                    msg["content"],
                    user_id=user_id,
                    session_id=session_id,
                    role=msg["role"],
                    scope=scope,
                    metadata=msg.get("metadata"),
                )
                event_ids.append(eid)
            return event_ids
        return await self._pipeline.ingest_batch(
            messages,
            user_id=user_id,
            session_id=session_id,
            wait_for_extraction=wait_for_extraction,
            scope=scope,
        )

    # --- Retrieval Operations ---

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        scope: Scope | list[Scope] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        token_budget: int | None = None,
        weights: ScoringWeights | None = None,
        min_fidelity: RepresentationLevel | None = None,
        include_cross_scope: bool = True,
    ) -> RetrievalResponse:
        """Retrieve memories via the hybrid retrieval pipeline.

        This is the unified entry point for memory retrieval. Delegates
        to the 6-stage RetrievalPipeline which handles query analysis,
        candidate generation, epistemic filtering, scoring, context
        packing, and operation logging.

        Args:
            query: Natural language query text.
            user_id: User ID for scoping all backend queries.
            scope: Optional scope filter. Accepts a single Scope, a list of
                Scopes, or None (no filter -- returns results from all scopes).
            time_from: Explicit start of temporal window.
            time_to: Explicit end of temporal window.
            token_budget: Override default token budget for this request.
            weights: Override default scoring weights.
            min_fidelity: Override minimum representation level.
            include_cross_scope: Whether to include cross-scope hints.
                Defaults to True. Set to False to disable.

        Returns:
            RetrievalResponse with packed MemoryBundle, scored results,
            metadata (request_id, timing, candidate counts), filter metadata,
            and always-on score traces.

        Raises:
            NotImplementedError: If no retrieval pipeline is configured.
        """
        if self._retrieval_pipeline is None:
            raise NotImplementedError(
                "RetrievalPipeline not configured. Use MemoryEngine.create() "
                "to initialize with all backends, or pass a retrieval_pipeline "
                "to the constructor."
            )

        result = await self._retrieval_pipeline.retrieve(
            query,
            user_id=user_id,
            scope=scope,
            time_from=time_from,
            time_to=time_to,
            token_budget=token_budget,
            weights=weights,
            min_fidelity=min_fidelity,
            include_cross_scope=include_cross_scope,
        )

        # Opportunistic maintenance (RFC-0015 Layer 2)
        if self._maintenance_runner:
            await self._maintenance_runner.maybe_run()

        return result

    async def search(
        self,
        query: str,
        user_id: str,
        *,
        k: int = 10,
    ) -> dict:
        """Search across vector and lexical backends in parallel.

        .. deprecated::
            Use ``retrieve()`` instead. This method returns raw backend
            results without scoring, filtering, or context packing.
            It is retained for backward compatibility only.

        Args:
            query: Search query text.
            user_id: Scope results to this user.
            k: Maximum results per backend.

        Returns:
            Dict with 'vector_results' and 'lexical_results' keys.
        """
        warnings.warn(
            "MemoryEngine.search() is deprecated. Use retrieve() for "
            "hybrid retrieval with scoring, filtering, and context packing.",
            DeprecationWarning,
            stacklevel=2,
        )
        vector_results, lexical_results = await asyncio.gather(
            self._vector_index.search(query, user_id, k=k),
            self._lexical_index.search(query, user_id, limit=k),
        )
        return {
            "vector_results": vector_results,
            "lexical_results": lexical_results,
        }

    # --- Node Operations (delegated to GraphStore) ---

    async def get_node(
        self,
        node_id: str,
        *,
        include_superseded: bool = False,
    ) -> MemoryNode | None:
        """Retrieve a node by ID.

        Args:
            node_id: String UUID of the node.
            include_superseded: If True, return superseded/archived nodes.

        Returns:
            The MemoryNode if found and visible, None otherwise.
        """
        return await self._graph_store.get_node(
            node_id, include_superseded=include_superseded
        )

    async def query_nodes(self, **kwargs) -> list[MemoryNode]:
        """Query nodes with flexible filters.

        Defaults to active lifecycle states (tentative + stable).
        Accepts all keyword arguments supported by GraphStore.query_nodes().

        Returns:
            List of matching MemoryNodes.
        """
        return await self._graph_store.query_nodes(**kwargs)

    # --- Event Operations (delegated to EventStore) ---

    async def get_event(self, event_id: str) -> Event | None:
        """Retrieve an event by ID.

        Args:
            event_id: String UUID of the event.

        Returns:
            The Event if found, None otherwise.
        """
        return await self._event_store.get(event_id)

    async def get_events(
        self, user_id: str, **kwargs
    ) -> list[Event]:
        """Retrieve events for a user.

        Args:
            user_id: The user to query events for.
            **kwargs: Additional filters (session_id, limit, offset).

        Returns:
            List of Events.
        """
        return await self._event_store.get_by_user(user_id, **kwargs)

    # --- Lifecycle Transitions (delegated to GraphStore) ---

    async def promote(self, node_id: str) -> None:
        """Promote a tentative node to stable.

        Args:
            node_id: Node to promote.

        Raises:
            ValueError: If the transition is invalid.
        """
        await self._graph_store.promote(node_id)

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark a node as superseded by another.

        Args:
            old_node_id: Node being replaced.
            new_node_id: Replacement node.
            evidence_id: Optional event ID for provenance.

        Raises:
            ValueError: If the transition is invalid.
        """
        await self._graph_store.supersede(
            old_node_id, new_node_id, evidence_id=evidence_id
        )

    async def archive(self, node_id: str) -> None:
        """Archive a node (terminal state).

        Args:
            node_id: Node to archive.

        Raises:
            ValueError: If the transition is invalid.
        """
        await self._graph_store.archive(node_id)

    # --- Snapshots ---

    async def snapshot(
        self,
        entity_id: str,
        *,
        at_time: datetime | None = None,
    ) -> Any:
        """Generate a point-in-time snapshot for an entity.

        Convenience method that delegates to
        ``prme.retrieval.snapshots.generate_entity_snapshot``.

        Args:
            entity_id: String UUID of the entity node.
            at_time: Optional temporal filter -- only include neighbors
                and edges valid at this time.

        Returns:
            EntitySnapshot with grouped neighbors and summary text.

        Raises:
            ValueError: If the entity node does not exist or is not ENTITY type.
        """
        from prme.retrieval.snapshots import generate_entity_snapshot

        return await generate_entity_snapshot(self, entity_id, at_time=at_time)

    # --- Reinforcement ---

    async def reinforce(
        self,
        node_id: str,
        evidence_id: str | None = None,
    ) -> None:
        """Reinforce a memory node, boosting its confidence and salience.

        Bumps reinforcement_boost by +0.15 (capped at 0.5) and
        confidence_base by +0.05 (capped at 0.95). Updates
        last_reinforced_at to now. Optionally appends an evidence
        reference.

        Args:
            node_id: The node to reinforce.
            evidence_id: Optional event ID to append to evidence_refs.

        Raises:
            ValueError: If the node does not exist.
        """
        from datetime import timezone
        from uuid import UUID

        node = await self._graph_store.get_node(node_id, include_superseded=True)
        if node is None:
            raise ValueError(f"Node {node_id!r} not found")

        new_boost = min(node.reinforcement_boost + 0.15, 0.5)
        new_confidence_base = min(node.confidence_base + 0.05, 0.95)
        now = datetime.now(timezone.utc)

        updates: dict = {
            "reinforcement_boost": new_boost,
            "confidence_base": new_confidence_base,
            "last_reinforced_at": now,
        }

        if evidence_id is not None:
            new_refs = list(node.evidence_refs) + [UUID(evidence_id)]
            updates["evidence_refs"] = new_refs

        await self._graph_store.update_node(node_id, **updates)

    # --- Organization (RFC-0015) ---

    async def organize(
        self,
        *,
        user_id: str | None = None,
        jobs: list[str] | None = None,
        budget_ms: int = 5000,
    ) -> OrganizeResult:
        """Run explicit memory organization jobs (RFC-0015 Layer 3).

        Iterates through requested jobs, calling each with a time budget.
        Stops early if the total budget is exceeded.

        Args:
            user_id: Optional user scope for organization.
            jobs: List of job names to run. Defaults to ALL_JOBS.
            budget_ms: Total time budget in milliseconds.

        Returns:
            OrganizeResult with per-job results and timing.
        """
        from prme.organizer.jobs import ALL_JOBS, run_job
        from prme.organizer.models import OrganizeResult

        import time

        if jobs is None:
            jobs = list(ALL_JOBS)

        start = time.monotonic()
        result = OrganizeResult()

        for job_name in jobs:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            remaining_ms = budget_ms - elapsed_ms

            if remaining_ms <= 0:
                result.jobs_skipped.append(job_name)
                continue

            try:
                job_result = await run_job(
                    job_name, self, self._config.organizer, remaining_ms
                )
                result.jobs_run.append(job_name)
                result.per_job[job_name] = job_result
            except ValueError as exc:
                logger.warning("Skipping invalid job %r: %s", job_name, exc)
                result.jobs_skipped.append(job_name)
            except Exception:
                logger.warning(
                    "Job %r failed during organize()", job_name, exc_info=True
                )
                result.jobs_skipped.append(job_name)

        total_ms = (time.monotonic() - start) * 1000.0
        result.duration_ms = round(total_ms, 2)
        result.budget_remaining_ms = round(max(0.0, budget_ms - total_ms), 2)
        return result

    async def end_session(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
    ) -> OrganizeResult:
        """Convenience: lightweight organize at end of conversation.

        Runs promote and feedback_apply jobs with a 1-second budget.

        Args:
            user_id: User whose session is ending.
            session_id: Optional session identifier.

        Returns:
            OrganizeResult from the lightweight organize pass.
        """
        return await self.organize(
            user_id=user_id,
            jobs=["promote", "feedback_apply"],
            budget_ms=1000,
        )

    # --- Resource Management ---

    async def close(self) -> None:
        """Close and save all backends.

        Shuts down the ingestion pipeline (cancels background tasks),
        stops the write queue, saves the VectorIndex to disk, closes
        the LexicalIndex, and closes the DuckDB connection.
        """
        # Shutdown pipeline first (cancel background extraction tasks)
        if self._pipeline is not None:
            try:
                await self._pipeline.shutdown()
            except Exception:
                logger.warning("Error shutting down pipeline", exc_info=True)

        # Stop write queue (drain pending jobs)
        try:
            await self._write_queue.stop()
        except Exception:
            logger.warning("Error stopping write queue", exc_info=True)

        try:
            await self._vector_index.close()
        except Exception:
            logger.warning("Error closing vector index", exc_info=True)

        try:
            await self._lexical_index.close()
        except Exception:
            logger.warning("Error closing lexical index", exc_info=True)

        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                logger.warning("Error closing DuckDB connection", exc_info=True)

        if self._pool is not None:
            try:
                await self._pool.close()
            except Exception:
                logger.warning("Error closing PostgreSQL pool", exc_info=True)
