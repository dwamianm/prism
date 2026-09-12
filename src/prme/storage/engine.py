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
import atexit
from contextlib import AsyncExitStack
import logging
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import duckdb

from prme.config import PRMEConfig
from prme.ingestion.errors import MaterializationError, extraction_failure_code
from prme.models import Event, MemoryNode, ProcessingResult, ProcessingStatus
from prme.models.extraction import ExtractionRecord
from prme.models.extraction_work import ExtractionStatus, ExtractionProcessingResult
from prme.quality.feedback import FeedbackSignal, FeedbackTracker
from prme.models.relevance import RelevanceRecord, RelevanceSubmission, RetrievalReceipt
from prme.models.learning import LearningConfig, LearningEvaluation, RankingMultipliers
from prme.models.profile import ProfilePublication, profile_key
from prme.models.derivation import PreparedEmbedding
from prme.storage.relevance import RelevanceRepository
from prme.quality.metrics import QualityMetrics, compute_quality_metrics
from prme.quality.tuner import WeightTuner
from prme.storage._threading import run_to_completion
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.embedding import create_embedding_provider
from prme.storage.encryption import EncryptionError
from prme.storage.event_store import EventStore
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
from prme.storage.write_queue import NoOpWriteQueue, WriteQueue
from prme.retrieval.scope import ScopeInput, normalize_scope
from prme.retrieval.selection import validate_selection
from prme.types import (
    ACTIVE_LIFECYCLE_STATES,
    DecayProfile,
    EpistemicType,
    LifecycleState,
    NodeType,
    RepresentationLevel,
    RetrievalMode,
    Scope,
    SourceType,
)

if TYPE_CHECKING:
    import asyncpg
    from prme.storage.encryption import EncryptionProvider

    from prme.ingestion.pipeline import IngestionPipeline
    from prme.organizer.models import OrganizeResult
    from prme.retrieval.config import ScoringWeights
    from prme.retrieval.models import RetrievalResponse
    from prme.retrieval.pipeline import RetrievalPipeline

from prme.organizer.maintenance import MaintenanceRunner
from prme.storage.durable_queue import DurableMaterializationQueue

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
        self._materialization_queue = DurableMaterializationQueue(
            event_store, batch_size=self._config.materialization_queue_size,
        )

        # Encryption at rest (issue #14)
        self._encryption_provider: EncryptionProvider | None = None
        # Best-effort atexit re-encryption guard (issue #37). Registered in
        # the create() classmethods once an encryption provider exists.
        self._atexit_registered = False

        # Quality assessment and auto-tuning (issue #24)
        self._relevance = RelevanceRepository(conn=conn, pool=pool, conn_lock=getattr(event_store, "_conn_lock", None))
        self._feedback_tracker = FeedbackTracker()
        self._weight_tuner = WeightTuner(
            self._config.scoring, learning_rate=0.01,
        )

        self._closed = False

        # Session turn tracking for automatic Q-A pairing.
        # Maps (user_id, session_id) -> (role, content, node_type, scope)
        # When consecutive messages in the same session have different roles,
        # a merged Q-A node is created for better retrieval coverage.
        self._last_session_turn: dict[
            tuple[str, str], tuple[str, str, NodeType, Scope]
        ] = {}

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
    @asynccontextmanager
    async def open(cls, config: PRMEConfig | None = None):
        """Async context manager for MemoryEngine lifecycle.

        Usage::

            async with MemoryEngine.open(config) as engine:
                await engine.store(...)
        """
        engine = await cls.create(config)
        try:
            yield engine
        finally:
            await engine.close()

    @classmethod
    async def create(cls, config: PRMEConfig | None = None) -> "MemoryEngine":
        """Create and initialize a MemoryEngine with all backends.

        Dispatches to ``_create_duckdb()`` or ``_create_postgres()``
        based on ``config.backend``. When ``database_url`` is set,
        all storage uses PostgreSQL; otherwise, file-based DuckDB.
        Missing local storage directories are created, including parents.

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
        from pathlib import Path

        from prme.storage.encryption import EncryptionError, EncryptionProvider

        # A fresh configured pack must work through either public client.
        # Prepare all directories before opening/decrypting storage so a path
        # collision fails without touching existing pack contents.
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.vector_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.lexical_path).mkdir(parents=True, exist_ok=True)

        async with AsyncExitStack() as startup:
            encryption_provider: EncryptionProvider | None = None
            needs_encryption = False

            def restore_encryption() -> None:
                if not needs_encryption or encryption_provider is None:
                    return
                try:
                    cls._encrypt_pack_files(config, encryption_provider)
                except Exception as exc:
                    raise EncryptionError(
                        "Failed to re-encrypt memory pack after startup failure; "
                        "the pack may remain plaintext."
                    ) from exc

            # Registered first, so encryption runs after all handles close.
            # A wrong key before any successful decryption leaves files alone.
            startup.callback(restore_encryption)
            # --- Decrypt memory pack if encryption is enabled (issue #14) ---
            if (
                config.encryption_enabled
                and config.encryption_key
                and config.encryption_key.get_secret_value()
            ):
                encryption_provider = EncryptionProvider(
                    config.encryption_key.get_secret_value()
                )

                # Decrypt DuckDB file if encrypted version exists
                db_enc = Path(config.db_path + ".enc")
                if db_enc.exists():
                    encryption_provider.decrypt_file(db_enc)
                    needs_encryption = True
                    logger.info("Decrypted DuckDB file: %s", db_enc)

                # Decrypt vector index file if encrypted version exists
                vec_enc = Path(config.vector_path + ".enc")
                if vec_enc.exists():
                    encryption_provider.decrypt_file(vec_enc)
                    needs_encryption = True
                    logger.info("Decrypted vector index: %s", vec_enc)

                # Decrypt lexical index directory if it has encrypted files
                lexical_dir = Path(config.lexical_path)
                if lexical_dir.is_dir():
                    enc_files = list(lexical_dir.glob("*.enc"))
                    if enc_files:
                        for encrypted_path in sorted(enc_files):
                            encryption_provider.decrypt_file(encrypted_path)
                            needs_encryption = True
                        logger.info(
                            "Decrypted %d lexical index files", len(enc_files)
                        )

            needs_encryption = encryption_provider is not None

            # Open DuckDB connection
            conn = duckdb.connect(config.db_path)
            startup.callback(conn.close)
            # TIMESTAMPTZ preserves instants but DuckDB presents them in the
            # host timezone by default. A portable pack uses canonical UTC.
            conn.execute("SET TimeZone = 'UTC'")

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
            vector_index = VectorIndex(
                conn,
                config.vector_path,
                embedding_provider,
                conn_lock,
                save_interval=config.vector_save_interval,
                exact_search=config.vector_exact_search,
            )

            startup.push_async_callback(vector_index.close)

            # Create lexical index
            lexical_index = LexicalIndex(
                config.lexical_path,
                commit_interval=config.lexical_commit_interval,
                commit_max_delay_s=config.lexical_commit_max_delay_s,
            )

            startup.push_async_callback(lexical_index.close)

            # Create and start write queue
            write_queue = WriteQueue(maxsize=config.write_queue_size)
            startup.push_async_callback(write_queue.stop)
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
                max_concurrent_extractions=config.max_concurrent_extractions,
                extraction_lease_seconds=config.extraction.lease_seconds,
            )

            startup.push_async_callback(pipeline.shutdown)

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
                enable_reranker=config.enable_reranker,
                reranker_model=config.reranker_model,
                reranker_top_k=config.reranker_top_k,
                enable_query_reformulation=config.enable_query_reformulation,
                query_reformulation_count=config.query_reformulation_count,
                query_reformulation_provider=config.extraction.provider,
                query_reformulation_model=config.extraction.model,
                temporal_languages=config.temporal_languages,
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
            engine._encryption_provider = encryption_provider
            engine._maintenance_runner = MaintenanceRunner(engine, config.organizer)
            engine._register_atexit_encrypt()
            startup.callback(atexit.unregister, engine._atexit_encrypt)
            await engine._materialization_queue.debt()
            startup.pop_all()
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

        async with AsyncExitStack() as startup:
            assert config.database_url is not None

            # Create asyncpg pool and initialize schema
            pool = await create_pool(config.database_url.get_secret_value())
            startup.push_async_callback(pool.close)
            await initialize_pg_database(pool, embedding_dim=config.embedding.dimension)

            # Create Pg backends
            event_store = PgEventStore(pool)
            graph_store = PgGraphStore(pool)
            embedding_provider = create_embedding_provider(config.embedding)
            vector_index = PgVectorIndex(pool, embedding_provider)
            startup.push_async_callback(vector_index.close)
            lexical_index = PgLexicalIndex(pool)
            startup.push_async_callback(lexical_index.close)

            # NoOpWriteQueue — PostgreSQL handles multi-writer natively
            write_queue = NoOpWriteQueue()
            startup.push_async_callback(write_queue.stop)
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
                max_concurrent_extractions=config.max_concurrent_extractions,
                extraction_lease_seconds=config.extraction.lease_seconds,
            )

            startup.push_async_callback(pipeline.shutdown)

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
                enable_reranker=config.enable_reranker,
                reranker_model=config.reranker_model,
                reranker_top_k=config.reranker_top_k,
                enable_query_reformulation=config.enable_query_reformulation,
                query_reformulation_count=config.query_reformulation_count,
                query_reformulation_provider=config.extraction.provider,
                query_reformulation_model=config.extraction.model,
                temporal_languages=config.temporal_languages,
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
            await engine._materialization_queue.debt()
            startup.pop_all()
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
        event_time: datetime | None = None,
        ttl_days: int | None = ...,
    ) -> str:
        """Store content across all four backends in one call.

        Atomically save the source, complete initial node values and a repair
        job, then create the graph node and persist both search indexes.
        Index failures remain nonfatal after confirming the node is durable;
        processing_status() reports pending work and process_pending() retries
        it, including after restart. A graph creation failure raises
        MaterializationError with the accepted event_id for recovery.
        Optional reinforcement, supersedence and QA pairing run afterward and
        are outside this repair job's completion boundary.

        Args:
            content: Text content to store.
            user_id: Owner user ID.
            session_id: Optional session identifier.
            role: Event role ('user', 'assistant', 'tool', or 'system').
            node_type: Type of memory node to create.
            scope: Memory scope (personal, project, org).
            metadata: Optional structured metadata.
            confidence: Initial confidence score. If None, derived from
                the (epistemic_type, source_type) confidence matrix.
            epistemic_type: Epistemic classification. If None, inferred
                from node_type via heuristic.
            source_type: Source provenance type. If None, inferred from
                node_type and role via heuristic.
            event_time: Optional datetime of when the event actually happened
                in the real world (UTC). None means same as ingestion time.
                Enables bi-temporal queries (issue #21).
            ttl_days: Time-to-live in days from creation. Ellipsis (default)
                means look up from organizer config by node_type. None means
                no TTL. An explicit int overrides the config default.

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
            event_time=event_time,
        )
        event_id = str(event.id)

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
        from prme.types import DEFAULT_DECAY_PROFILE_MAPPING, NODE_TYPE_DECAY_OVERRIDES

        # INSTRUCTION nodes override the epistemic-type-based decay profile
        if node_type in NODE_TYPE_DECAY_OVERRIDES:
            decay_profile = NODE_TYPE_DECAY_OVERRIDES[node_type]
        else:
            decay_profile = DEFAULT_DECAY_PROFILE_MAPPING.get(
                epistemic_type, DecayProfile.MEDIUM
            )

        # INSTRUCTION nodes get higher default confidence (behavioral patterns)
        if node_type == NodeType.INSTRUCTION and confidence == self._confidence_matrix.lookup_with_fallback(epistemic_type, source_type):
            confidence = max(confidence, 0.7)

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

        # Resolve TTL: ellipsis = use config default; None = no TTL; int = explicit
        if ttl_days is ...:
            resolved_ttl = self._config.organizer.default_ttl_days.get(
                node_type.value, None
            )
        else:
            resolved_ttl = ttl_days

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
            event_time=event_time,
            ttl_days=resolved_ttl,
        )
        # Acknowledged work retains all explicit node values before graph or
        # index writes. A restart cannot reinterpret a FACT/INSTRUCTION as NOTE.
        await self._write_queue.submit(
            lambda: self._event_store.append(event, store_node=node),
            label=f"store.event:{event.id}",
        )
        self._materialization_queue.note_added()
        try:
            await self._materialization_queue.process_one(self, event)
        except Exception as exc:
            # Keep the existing non-fatal index-outage contract, but only after
            # confirming the intended node is durable. Creation failures still
            # fail the call and leave its accepted work available for recovery.
            reason = extraction_failure_code(exc)
            try:
                existing = await self._graph_store.get_node(str(node.id), include_superseded=True)
                durable = (existing is not None and existing.user_id == user_id
                           and existing.scope == scope and event.id in existing.evidence_refs)
            except Exception:
                durable = False
            if not durable:
                raise MaterializationError(
                    f"Store materialization did not complete ({reason}); the source and work are persisted",
                    event_id=event_id, reason_code=reason,
                ) from exc
            logger.warning("Indexing pending for stored event %s (%s)", event_id, reason)

        # Step 3.5: Re-mention reinforcement (opt-in)
        if self._config.reinforce_similarity_threshold is not None:
            try:
                await self._check_remention_reinforcement(
                    content, str(node.id), user_id, event.id, scope=node.scope,
                )
            except Exception:
                logger.warning(
                    "Re-mention reinforcement check failed for node %s. "
                    "Non-fatal; node is stored successfully.",
                    node.id,
                    exc_info=True,
                )

        # Step 3.6: Explicit repetitions of a user instruction can reinforce
        # that same instruction. Similarity alone cannot validate a rule.
        try:
            await self._check_instruction_reinforcement(node, event)
        except Exception:
            logger.warning(
                "Instruction reinforcement check failed for node %s. "
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

        # Step 6: Q-A Turn Pairing (reconstructive memory)
        # When consecutive messages in the same session are from different
        # roles, create a merged node for better retrieval coverage.
        # This addresses the "orphaned answer" problem where a question is
        # retrievable but the adjacent answer is not.
        if session_id is not None and self._config.enable_qa_pairing:
            session_key = (user_id, session_id)
            prev = self._last_session_turn.get(session_key)
            if prev is not None:
                prev_role, prev_content, prev_nt, prev_scope = prev
                if prev_role != role and len(prev_content) + len(content) < 1000:
                    merged = f"{prev_content}\n{content}"
                    try:
                        merged_node = MemoryNode(
                            user_id=user_id,
                            session_id=session_id,
                            node_type=node_type,
                            scope=scope,
                            content=merged,
                            metadata={"qa_pair": True},
                            confidence=confidence,
                            confidence_base=confidence,
                            salience=salience_base,
                            salience_base=salience_base,
                            epistemic_type=epistemic_type,
                            source_type=source_type,
                            evidence_refs=[event.id],
                            decay_profile=decay_profile,
                            event_time=event_time,
                        )
                        merged_id = await self._write_queue.submit(
                            lambda n=merged_node: self._graph_store.create_node(n),
                            label=f"store.qa_pair:{merged_node.id}",
                        )
                        await self._write_queue.submit(
                            lambda nid=merged_id, c=merged, uid=user_id: (
                                self._vector_index.index(nid, c, uid)
                            ),
                            label=f"store.qa_vector:{merged_id}",
                        )
                        await self._write_queue.submit(
                            lambda nid=merged_id, c=merged, uid=user_id, nt=node_type.value, sc=scope.value: (
                                self._lexical_index.index(nid, c, uid, nt, sc)
                            ),
                            label=f"store.qa_lexical:{merged_id}",
                        )
                    except Exception:
                        logger.debug(
                            "Q-A pair creation failed for session %s; non-fatal",
                            session_id,
                            exc_info=True,
                        )
            self._last_session_turn[session_key] = (role, content, node_type, scope)

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
        *,
        scope: Scope,
    ) -> None:
        """Reinforce existing similar nodes when new content re-mentions a topic.

        When reinforce_similarity_threshold is set, searches for existing nodes
        with similarity >= threshold and calls reinforce() on each match. The
        new node itself is always skipped. Superseded/archived nodes are also
        skipped. Only the same owner and scope can be reinforced; instructions
        use the stricter explicit-repetition policy. The block is non-fatal.

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
            if (existing_node is None or existing_node.user_id != user_id
                    or existing_node.scope != scope
                    or existing_node.node_type == NodeType.INSTRUCTION
                    or existing_node.lifecycle_state not in ACTIVE_LIFECYCLE_STATES):
                continue

            # Reinforce the matching existing node
            try:
                await self.reinforce(sid, evidence_id=str(event_id), user_id=user_id)
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

    async def _check_instruction_reinforcement(
        self, node: MemoryNode, event: Event,
    ) -> None:
        """Reinforce an exact, explicit user instruction in the same scope.

        Vector hits only propose candidates. A related statement, observation,
        assistant echo, speculation or different scope cannot confirm a rule.
        This optional reinforcement still tolerates vector unavailability.
        """
        supported_types = {EpistemicType.OBSERVED, EpistemicType.ASSERTED}
        if (node.node_type != NodeType.INSTRUCTION
                or node.source_type != SourceType.USER_STATED
                or node.epistemic_type not in supported_types
                or event.role.casefold() not in {"user", "human"}):
            return
        try:
            similar = await self._vector_index.search(node.content, node.user_id, k=5)
        except Exception:
            return

        for result in similar:
            sid = result["node_id"]
            if sid == str(node.id):
                continue
            existing = await self._graph_store.get_node(sid, include_superseded=False)
            if (existing is None or existing.user_id != node.user_id
                    or existing.scope != node.scope
                    or existing.node_type != NodeType.INSTRUCTION
                    or existing.source_type != SourceType.USER_STATED
                    or existing.epistemic_type not in supported_types
                    or existing.lifecycle_state not in ACTIVE_LIFECYCLE_STATES
                    or existing.content != node.content
                    or event.id in existing.evidence_refs
                    or (node.event_time or node.created_at) < (existing.event_time or existing.created_at)):
                continue
            try:
                await self.reinforce(sid, evidence_id=str(event.id), user_id=node.user_id)
                logger.info("Repeated instruction %s reinforced by source %s", sid, event.id)
            except Exception:
                logger.debug("Could not reinforce instruction %s", sid, exc_info=True)

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
        event_time: datetime | None = None,
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
            event_time: Timezone-aware source time. Relative dates in extracted
                facts use this clock; omitted uses ingestion time.
            wait_for_extraction: If True, block until extraction completes.

        Returns:
            String UUID of the persisted event.
        """
        from prme.ingestion.temporal import validate_source_time

        validate_source_time(event_time)
        if self._pipeline is None:
            event_id = await self.store(
                content,
                user_id=user_id,
                session_id=session_id,
                role=role,
                scope=scope,
                metadata=metadata,
                event_time=event_time,
            )
        else:
            event_id = await self._pipeline.ingest(
                content,
                user_id=user_id,
                role=role,
                session_id=session_id,
                metadata=metadata,
                event_time=event_time,
                wait_for_extraction=wait_for_extraction,
                scope=scope,
            )

        # Opportunistic maintenance (RFC-0015 Layer 2). Scheduled rather
        # than awaited: nothing in the response depends on it, so its time
        # budget must not land on the caller's latency (issue #62).
        if self._maintenance_runner:
            self._maintenance_runner.schedule(user_id=user_id)

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
        keys, with optional 'metadata' and timezone-aware 'event_time'.

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
                eid = await self.ingest(
                    msg["content"],
                    user_id=user_id,
                    session_id=session_id,
                    role=msg["role"],
                    scope=scope,
                    metadata=msg.get("metadata"),
                    event_time=msg.get("event_time"),
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

    # --- Fast Ingestion (issue #25) ---

    async def ingest_fast(
        self,
        content: str,
        *,
        user_id: str,
        role: str = "user",
        session_id: str | None = None,
        metadata: dict | None = None,
        scope: Scope = Scope.PERSONAL,
        event_time: datetime | None = None,
    ) -> str:
        """Durably accept a raw event and defer its indexing.

        Persists the event and its deferred work in one transaction. Graph
        materialization and indexing run during retrieve() or organize(),
        including after a restart. This path makes no embedding or LLM call.

        Materialization creates a raw NOTE linked to the original event;
        use ingest() for LLM-powered entity/fact extraction. Latency depends
        on database commit time; no fixed wall-clock guarantee is made.

        Args:
            content: The message text to ingest.
            user_id: Owner user ID.
            role: Message role ('user', 'assistant', or 'system').
            session_id: Optional session identifier.
            metadata: Optional structured metadata.
            scope: Memory scope (personal, project, org).
            event_time: When the event happened, if different from ingestion.

        Returns:
            String UUID of the persisted event.
        """
        # Step 1: Persist event to event store (source of truth)
        event = Event(
            content=content,
            user_id=user_id,
            session_id=session_id,
            role=role,
            scope=scope,
            metadata=metadata,
            event_time=event_time,
        )
        event_id = await self._write_queue.submit(
            lambda ev=event: self._event_store.append(ev, defer_materialization=True),
            label=f"ingest_fast.event:{event.id}",
        )
        self._materialization_queue.note_added()

        logger.info(
            "ingest_fast.complete event_id=%s debt=%d",
            event_id,
            self._materialization_queue.debt_sync(),
        )
        return event_id

    async def _materialize_event(self, event: Event) -> None:
        """Materialize a saved direct store or raw source without a new event.

        Direct stores retain their complete initial node snapshot; raw NOTE
        identity and timestamps derive from the event. Retries preserve existing
        graph state and never reactivate retired nodes. Active-node completion
        is acknowledged only after both indexes are durable.
        """
        from prme.epistemic.inference import infer_epistemic_type, infer_source_type

        status = await self._event_store.processing_status(str(event.id), user_id=event.user_id)
        if status is not None and status.status == "complete":
            return
        direct = await self._event_store.get_direct_store(str(event.id), user_id=event.user_id)
        node_id = str(direct.node.id) if direct is not None else str(event.id)
        node = await self._graph_store.get_node(node_id, include_superseded=True)
        if node is None:
            if direct is not None:
                node = direct.node
            else:
                epistemic_type = infer_epistemic_type(NodeType.NOTE)
                source_type = infer_source_type(NodeType.NOTE, role=event.role)
                confidence = self._confidence_matrix.lookup_with_fallback(epistemic_type, source_type)
                node = MemoryNode(
                    id=event.id, content=event.content, user_id=event.user_id,
                    session_id=event.session_id, scope=event.scope, metadata=event.metadata,
                    node_type=NodeType.NOTE, evidence_refs=[event.id],
                    epistemic_type=epistemic_type, source_type=source_type,
                    confidence=confidence, confidence_base=confidence,
                    created_at=event.created_at, updated_at=event.created_at,
                    valid_from=event.timestamp, last_reinforced_at=event.timestamp,
                    event_time=event.event_time,
                    ttl_days=self._config.organizer.default_ttl_days.get("note"),
                )
            try:
                await self._write_queue.submit(
                    lambda: self._graph_store.create_node(node),
                    label=f"materialize.node:{node_id}",
                )
            except Exception:
                # Another PostgreSQL consumer may have created the same
                # deterministic node. Never swallow an unrelated write error.
                existing = await self._graph_store.get_node(node_id, include_superseded=True)
                if existing is None:
                    raise
                node = existing
        if node.user_id != event.user_id or node.scope != event.scope or event.id not in node.evidence_refs:
            raise ValueError("Materialization identity does not match its source event")
        if node.lifecycle_state not in ACTIVE_LIFECYCLE_STATES:
            # An explicit retirement during a retry must not resurrect data.
            return
        # Replace partial writes independently and persist each healthy index.
        # Any failure keeps the durable job pending, even when one search path
        # is already usable. Cancellation still propagates immediately here.
        errors: list[Exception] = []
        try:
            await self._write_queue.submit(
                lambda: self._lexical_index.index(
                    node_id, node.content, node.user_id, node.node_type.value, node.scope.value, replace=True,
                ), label=f"materialize.lexical:{node_id}",
            )
            if hasattr(self._lexical_index, "flush"):
                await self._lexical_index.flush()
        except Exception as exc:
            errors.append(exc)
        try:
            await self._write_queue.submit(
                lambda: self._vector_index.index(node_id, node.content, node.user_id, replace=True),
                label=f"materialize.vector:{node_id}",
            )
            # index() commits the numerical payload and metadata before it
            # returns (in DuckDB, or the PostgreSQL vector row). A USearch
            # snapshot is derived: startup restores unsaved keys from those
            # payloads without inference. Preserve the configured snapshot
            # cadence instead of rewriting the whole index for every source.
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise errors[0]

    @property
    def materialization_debt(self) -> int:
        """Return the count of pending materialization items.

        This is a synchronous best-effort read suitable for metrics
        and logging. The count may be slightly stale under concurrent
        access.

        Returns:
            Number of items awaiting graph materialization.
        """
        return self._materialization_queue.debt_sync()

    async def extraction_status(self, event_id: str, *, user_id: str) -> ExtractionStatus | None:
        """Inspect durable LLM extraction separately from raw NOTE indexing."""
        return await self._event_store.extraction_work.status(event_id, user_id=user_id)

    async def retry_extraction(self, event_id: str, *, user_id: str, replan: bool = False) -> ExtractionStatus | None:
        """Make owned failed/pending work eligible for explicit processing.

        Does not interrupt a live worker, repeat completed work, or call a
        provider. With replan=True, queue a new plan revision from saved
        extraction; processing may compute new embeddings. Old plans remain
        immutable. Call process_extractions() to execute eligible jobs.
        """
        if replan:
            await self._event_store.extraction_work.replan(event_id, user_id=user_id)
        else:
            await self._event_store.extraction_work.retry(event_id, user_id=user_id)
        return await self.extraction_status(event_id, user_id=user_id)

    async def process_extractions(self, *, user_id: str, limit: int = 100,
                                  budget_ms: float = 5000) -> ExtractionProcessingResult:
        """Run due owned extraction jobs; never invoked automatically by retrieval.

        Budget is cooperative between jobs. Provider calls retain their
        configured timeouts. Failed counts are terminal jobs needing retry.
        """
        return await self._pipeline.process_extractions(user_id=user_id, limit=limit, budget_ms=budget_ms)

    async def processing_status(self, event_id: str, *, user_id: str) -> ProcessingStatus | None:
        """Read durable source materialization status within one user's events.

        Tracks new store(), ingest_fast() and LLM pipeline source writes. Returns
        None for unknown, other-user or legacy untracked events. Completion covers
        the direct node or raw NOTE and its indexes, not LLM extraction or optional
        post-store maintenance. Later lifecycle changes may retire the node.
        """
        if not user_id:
            raise ValueError("user_id must be nonempty")
        return await self._event_store.processing_status(event_id, user_id=user_id)

    async def process_pending(self, *, user_id: str, budget_ms: int = 1000) -> ProcessingResult:
        """Process one bounded batch of this user's pending source/index work.

        Failed items remain pending for retry. The time budget is cooperative:
        an individual operation can exceed it. A zero budget only reads current
        counts. Repeat passes while pending remains, inspecting failed/status
        before retrying persistent errors. Never runs organizer jobs or an LLM.
        """
        if not user_id:
            raise ValueError("user_id must be nonempty")
        if budget_ms < 0:
            raise ValueError("budget_ms must be nonnegative")
        processed = await self._materialization_queue.drain(self, budget_ms=budget_ms, user_id=user_id)
        pending, failed = await self._event_store.processing_counts(user_id=user_id)
        return ProcessingResult(processed=processed, pending=pending, failed=failed)

    # --- Retrieval Operations ---

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        scope: ScopeInput = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        reference_time: datetime | None = None,
        knowledge_at: datetime | None = None,
        event_time_from: datetime | None = None,
        event_time_to: datetime | None = None,
        token_budget: int | None = None,
        min_score: float | None = None,
        limit: int | None = None,
        weights: ScoringWeights | None = None,
        ranking_multipliers: RankingMultipliers | None = None,
        min_fidelity: RepresentationLevel | None = None,
        include_cross_scope: bool = True,
        retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
    ) -> RetrievalResponse:
        """Retrieve memories via the hybrid retrieval pipeline.

        This is the unified entry point for memory retrieval. Delegates
        to the 6-stage RetrievalPipeline which handles query analysis,
        candidate generation, epistemic filtering, scoring, context
        packing, and operation logging.

        Bi-temporal query support (issue #21):
        - ``knowledge_at``: Point-in-time knowledge snapshot. Only returns
          nodes whose ingestion time (created_at) <= knowledge_at. Answers
          "what did the system know as of this datetime?"
        - ``event_time_from``/``event_time_to``: Filter by when events
          actually happened in the real world. Answers "what happened
          during this time period?"

        Args:
            query: Natural language query text.
            user_id: User ID for scoping all backend queries.
            scope: Optional scope filter. Accepts a single Scope, a list of
                Scopes, or None (no filter -- returns results from all scopes).
            time_from: Explicit start of temporal window.
            time_to: Explicit end of temporal window.
            reference_time: Timezone-aware clock for relative query dates and
                scoring decay. Defaults to request time. This does not apply
                a historical knowledge cutoff; use knowledge_at for that.
            knowledge_at: Point-in-time knowledge snapshot (bi-temporal).
                Only includes nodes ingested on or before this datetime.
            event_time_from: Filter by event_time >= this value (bi-temporal).
            event_time_to: Filter by event_time <= this value (bi-temporal).
            token_budget: Override default token budget for this request.
            min_score: Inclusive ranking score floor; not a probability.
            limit: Maximum primary results before context packing. Zero returns none.
            weights: Override default scoring weights.
            ranking_multipliers: Explicit request-only adjustment for full-pipeline
                trials; does not activate or persist a learned profile.
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
        validate_selection(min_score, limit)
        scope = normalize_scope(scope)
        if ranking_multipliers is not None:
            ranking_multipliers = RankingMultipliers.model_validate_json(ranking_multipliers.model_dump_json())
        if self._retrieval_pipeline is None:
            raise NotImplementedError(
                "RetrievalPipeline not configured. Use MemoryEngine.create() "
                "to initialize with all backends, or pass a retrieval_pipeline "
                "to the constructor."
            )

        # Drain materialization queue before retrieval (issue #25)
        if await self._materialization_queue.debt() > 0:
            try:
                drained = await self._materialization_queue.drain(
                    self, budget_ms=self._config.materialization_budget_ms, user_id=user_id,
                )
                if drained > 0:
                    logger.debug(
                        "retrieve: drained %d materialization items", drained
                    )
            except Exception:
                logger.warning(
                    "retrieve: materialization drain failed; continuing",
                    exc_info=True,
                )

        result = await self._retrieval_pipeline.retrieve(
            query,
            user_id=user_id,
            scope=scope,
            time_from=time_from,
            time_to=time_to,
            reference_time=reference_time,
            knowledge_at=knowledge_at,
            event_time_from=event_time_from,
            event_time_to=event_time_to,
            token_budget=token_budget,
            min_score=min_score, limit=limit,
            weights=weights,
            ranking_multipliers=ranking_multipliers,
            min_fidelity=min_fidelity,
            include_cross_scope=include_cross_scope,
            retrieval_mode=retrieval_mode,
        )

        # Opportunistic maintenance (RFC-0015 Layer 2). Scheduled rather
        # than awaited: nothing in the response depends on it, so its time
        # budget must not land on the caller's latency (issue #62).
        if self._maintenance_runner:
            self._maintenance_runner.schedule(user_id=user_id)

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

    async def _owned_node(
        self,
        node_id: str,
        user_id: str | None,
        *,
        include_superseded: bool = True,
    ) -> MemoryNode | None:
        """Fetch a node, enforcing ownership when a user is supplied.

        Node-ID operations are otherwise unscoped: knowing an ID is enough to
        read or mutate another user's memory (issue #35). Passing ``user_id``
        makes the operation fail exactly as it would for a nonexistent node,
        so a caller cannot learn that an ID they do not own exists -- RFC-0004
        S6 forbids revealing the existence of objects outside the caller's
        namespace.

        Returns None when the node is missing or owned by someone else.
        """
        node = await self._graph_store.get_node(
            node_id, include_superseded=include_superseded
        )
        if node is None:
            return None
        if user_id is not None and node.user_id != user_id:
            logger.warning(
                "Ownership check rejected node %s for user %r",
                node_id,
                user_id,
            )
            return None
        return node

    async def _require_owned(self, node_id: str, user_id: str) -> MemoryNode:
        """Return a node the user owns, or raise the standard not-found error.

        The message matches the graph store's own wording so an unauthorized
        mutation is indistinguishable from one against a nonexistent node.
        """
        node = await self._owned_node(node_id, user_id)
        if node is None:
            raise ValueError(f"Node {node_id} not found")
        return node

    async def get_node(
        self,
        node_id: str,
        *,
        include_superseded: bool = False,
        user_id: str | None = None,
    ) -> MemoryNode | None:
        """Retrieve a node by ID.

        Args:
            node_id: String UUID of the node.
            include_superseded: If True, return superseded/archived nodes.
            user_id: When given, the node is returned only if this user owns
                it. A node owned by anyone else reads as not found.

        Returns:
            The MemoryNode if found and visible, None otherwise.
        """
        if user_id is not None:
            return await self._owned_node(
                node_id, user_id, include_superseded=include_superseded
            )
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

    async def count_nodes(self, **kwargs) -> int:
        """Count nodes matching the filters without materializing them.

        Defaults to active lifecycle states (tentative + stable + contested).
        Accepts the keyword arguments supported by GraphStore.count_nodes()
        (user_id, lifecycle_states).

        Returns:
            Number of matching nodes.
        """
        return await self._graph_store.count_nodes(**kwargs)

    async def scan_nodes(
        self, *, user_id: str, scope: Scope | None = None,
        node_type: NodeType | None = None,
        lifecycle_states: list[LifecycleState] | None = None,
        after_id: str | None = None, limit: int = 100,
    ) -> list[MemoryNode]:
        """Read a scoped page in ID order; pass the last ID as the next cursor.

        This enumerates stored nodes without semantic ranking. Defaults to
        active states. Independent pages do not form a transaction snapshot.
        """
        return await self._graph_store.scan_nodes(
            user_id=user_id, scope=scope, node_type=node_type,
            lifecycle_states=lifecycle_states, after_id=after_id, limit=limit,
        )

    async def iter_nodes(
        self, *, user_id: str, scope: Scope | None = None,
        node_type: NodeType | None = None,
        lifecycle_states: list[LifecycleState] | None = None, batch_size: int = 100,
    ) -> AsyncIterator[MemoryNode]:
        """Stream every matching node in bounded pages, without a top-k cap.

        Complete for an unchanged store. Concurrent writes can change which
        nodes match between pages; use an unchanged pack for audited counts.
        Does not materialize pending ingestion or call a model.
        """
        cursor = None
        while True:
            page = await self.scan_nodes(
                user_id=user_id, scope=scope, node_type=node_type,
                lifecycle_states=lifecycle_states, after_id=cursor, limit=batch_size,
            )
            for node in page:
                yield node
            if len(page) < batch_size:
                break
            cursor = str(page[-1].id)

    # --- Event Operations (delegated to EventStore) ---

    async def get_extraction(self, event_id: str, *, user_id: str) -> ExtractionRecord | None:
        """Read saved grounded model output for an owned source event.

        This does not call a model or complete pending processing. A record
        confirms saved output, not graph completion or semantic correctness.
        Returns None when no record exists for an owned event.
        """
        if not user_id:
            raise ValueError("get_extraction requires user_id")
        return await self._event_store.get_extraction(event_id, user_id=user_id)

    async def get_event(self, event_id: str, *, user_id: str | None = None) -> Event | None:
        """Retrieve an event by ID.

        Args:
            event_id: String UUID of the event.

        Returns:
            The Event if found, None otherwise.
        """
        event = await self._event_store.get(event_id)
        if event is not None and user_id is not None and event.user_id != user_id:
            return None
        return event

    async def get_event_nodes(self, event_id: str, *, user_id: str) -> list[MemoryNode]:
        """Get nodes citing an owned event, including retired derived knowledge.

        Empty means no visible derivations (or no owned event), not that pending
        extraction has completed. Use processing_status for durable raw jobs.
        """
        if not user_id:
            raise ValueError("get_event_nodes requires user_id")
        event = await self.get_event(event_id, user_id=user_id)
        if event is None:
            return []
        return await self._graph_store.get_event_nodes(event_id, user_id=user_id)

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

    async def promote(self, node_id: str, *, user_id: str | None = None) -> None:
        """Promote a tentative node to stable.

        Args:
            node_id: Node to promote.
            user_id: When given, the promotion only applies to a node this
                user owns; anyone else's node raises as if it did not exist.

        Raises:
            ValueError: If the transition is invalid, or the node is missing
                or owned by another user.
        """
        if user_id is not None:
            await self._require_owned(node_id, user_id)
        await self._graph_store.promote(node_id)

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Mark a node as superseded by another.

        Args:
            old_node_id: Node being replaced.
            new_node_id: Replacement node.
            evidence_id: Optional event ID for provenance.
            user_id: When given, both nodes must belong to this user. A
                supersedence edge that crosses users is never legitimate: it
                would let one user's memory retire another's.

        Raises:
            ValueError: If the transition is invalid, or either node is
                missing or owned by another user.
        """
        if user_id is not None:
            await self._require_owned(old_node_id, user_id)
            await self._require_owned(new_node_id, user_id)
        await self._graph_store.supersede(
            old_node_id, new_node_id, evidence_id=evidence_id
        )
        # Evict the superseded node from the search indexes so its content
        # stops surfacing and the indexes do not grow without bound. The
        # event log remains the source of truth, so this is reconstructable.
        await self._evict_from_indexes(old_node_id)

    async def archive(self, node_id: str, *, user_id: str | None = None) -> None:
        """Archive a node (terminal state).

        Args:
            node_id: Node to archive.
            user_id: When given, the archival only applies to a node this
                user owns; anyone else's node raises as if it did not exist.

        Raises:
            ValueError: If the transition is invalid, or the node is missing
                or owned by another user.
        """
        if user_id is not None:
            await self._require_owned(node_id, user_id)
        await self._graph_store.archive(node_id)
        # Archived content is not retrievable, so drop it from the indexes.
        await self._evict_from_indexes(node_id)

    async def _delete_from_indexes(self, node_id: str) -> None:
        """Delete lexical first, retaining vector metadata as a retry anchor."""
        await self._write_queue.submit(
            lambda: self._lexical_index.delete_by_node_id(node_id),
            label=f"evict.lexical:{node_id}",
        )
        await self._write_queue.submit(
            lambda: self._vector_index.delete_by_node_id(node_id),
            label=f"evict.vector:{node_id}",
        )

    async def _evict_from_indexes(self, node_id: str) -> None:
        """Best-effort lifecycle eviction; compaction retries remaining drift.

        A lexical failure retains vector metadata so maintenance can discover
        the unfinished deletion. Retired graph state already prevents retrieval.
        """
        try:
            await self._delete_from_indexes(node_id)
        except Exception:
            logger.warning("evict.index_failed", extra={"node_id": node_id}, exc_info=True)

    # --- Rebuild (issue #45) ---

    async def rebuild_indexes(self, *, batch_size: int = 256) -> dict[str, int]:
        """Rebuild the vector and lexical indexes from the durable graph.

        Drops both derived search indexes and re-materializes them from the
        active nodes already persisted in the graph store (the ``nodes``
        table in the DuckDB pack), embedding each node's content afresh.

        This delivers the rebuildability claim while staying bounded and
        non-destructive: it touches only the *derived* search artifacts
        (``vectors.usearch`` and the lexical index directory). The event log
        and the graph nodes/edges -- the durable source of truth -- are never
        modified, so a rebuild is safe to re-run and idempotent. It is the
        clean recovery path for index corruption, embedding-model migration,
        and the index drift left when an eviction fails (the same drift the
        ``index_compaction`` organizer job reconciles, issue #41).

        Only nodes in active lifecycle states (tentative/stable/contested)
        are re-indexed, matching the visibility contract that vector and
        lexical search already enforce (issue #38) -- superseded, archived,
        and deprecated nodes are intentionally left out of the indexes.

        Nodes are processed in deterministic ``id`` order so that, combined
        with exact vector search (``vector_exact_search``), two rebuilds from
        the same pack produce identical indexes and identical retrieval.

        Args:
            batch_size: Number of nodes to fetch from the graph per page.
                Bounds peak memory on large packs; does not affect results.

        Returns:
            A summary dict with counts: ``nodes_indexed`` (rows written to
            both indexes), ``nodes_skipped`` (active rows with empty content,
            which cannot be embedded), and ``total_active`` (active rows
            seen).
        """
        if self._conn is None:
            raise RuntimeError(
                "rebuild_indexes is only supported on the DuckDB backend"
            )

        active_states = [s.value for s in ACTIVE_LIFECYCLE_STATES]

        # Clear both derived indexes first (serialized through the write
        # queue like every other index mutation). After this point the
        # indexes are empty; the re-index loop below repopulates them.
        await self._write_queue.submit(
            lambda: self._vector_index.clear(),
            label="rebuild.clear.vector",
        )
        await self._write_queue.submit(
            lambda: self._lexical_index.clear(),
            label="rebuild.clear.lexical",
        )

        nodes_indexed = 0
        nodes_skipped = 0
        total_active = 0
        offset = 0
        placeholders = ", ".join("?" for _ in active_states)

        while True:
            # Page through active nodes in deterministic id order. Reading
            # the nodes table directly (rather than the graph_store query
            # API) keeps the rebuild user-agnostic -- every active node in
            # the pack is re-indexed regardless of owner -- and lets us
            # ORDER BY id for reproducibility (query_nodes orders by
            # created_at). The rebuild is already gated to the DuckDB
            # backend above, so direct SQL is safe here.
            async with self._event_store._conn_lock:
                rows = await run_to_completion(
                    lambda ph=placeholders, off=offset: self._conn.execute(
                        "SELECT id, content, user_id, node_type, scope "
                        "FROM nodes "
                        f"WHERE COALESCE(lifecycle_state, 'tentative') IN ({ph}) "
                        "ORDER BY id "
                        "LIMIT ? OFFSET ?",
                        [*active_states, batch_size, off],
                    ).fetchall(),
                )
            if not rows:
                break

            for row in rows:
                total_active += 1
                node_id = str(row[0])
                content = row[1]
                user_id = row[2]
                node_type = row[3]
                scope = row[4]

                if not content or not content.strip():
                    # Empty content cannot be embedded; skip but count it so
                    # the caller can reconcile against total node counts.
                    nodes_skipped += 1
                    continue

                await self._write_queue.submit(
                    lambda nid=node_id, c=content, uid=user_id: (
                        self._vector_index.index(nid, c, uid)
                    ),
                    label=f"rebuild.vector:{node_id}",
                )
                await self._write_queue.submit(
                    lambda nid=node_id, c=content, uid=user_id, nt=node_type, sc=scope: (
                        self._lexical_index.index(nid, c, uid, nt, sc)
                    ),
                    label=f"rebuild.lexical:{node_id}",
                )
                nodes_indexed += 1

            offset += batch_size

        # Flush pending index writes to disk so the rebuilt artifacts are
        # durable even if the process exits before the next debounced save.
        await self._write_queue.submit(
            lambda: self._vector_index.save(),
            label="rebuild.save.vector",
        )
        await self._write_queue.submit(
            lambda: self._lexical_index.flush(),
            label="rebuild.flush.lexical",
        )

        logger.info(
            "rebuild_indexes.complete",
            extra={
                "nodes_indexed": nodes_indexed,
                "nodes_skipped": nodes_skipped,
                "total_active": total_active,
            },
        )
        return {
            "nodes_indexed": nodes_indexed,
            "nodes_skipped": nodes_skipped,
            "total_active": total_active,
        }

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
        *,
        user_id: str | None = None,
    ) -> None:
        """Reinforce a memory node, boosting its confidence and salience.

        Bumps reinforcement_boost by +0.15 (capped at 0.5) and
        confidence_base by +0.05 (capped at 0.95). Updates
        last_reinforced_at to now. Optionally appends an evidence
        reference.

        Args:
            node_id: The node to reinforce.
            evidence_id: Optional event ID to append to evidence_refs.
            user_id: When given, only a node this user owns is reinforced;
                anyone else's node raises as if it did not exist.

        Raises:
            ValueError: If the node does not exist, or is owned by another
                user when user_id is given.
        """
        from datetime import timezone
        from uuid import UUID

        node = await self._owned_node(node_id, user_id)
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

    async def get_retrieval_receipt(self, request_id: str, *, user_id: str) -> RetrievalReceipt | None:
        """Read the immutable returned-candidate snapshot through its owner."""
        return await self._relevance.get_receipt(request_id, user_id=user_id)

    async def record_relevance(self, submission: RelevanceSubmission, *, user_id: str) -> RelevanceRecord:
        """Durably record explicit labels; retry the same feedback_id safely.

        Labels describe saved response candidates, not current graph state.
        Recording does not change weights or facts. Legacy feedback_apply does
        not consume these records; evaluated scoped learning is separate.
        """
        return await self._relevance.record(submission, user_id=user_id)

    async def get_relevance(self, feedback_id: str, *, user_id: str) -> RelevanceRecord | None:
        return await self._relevance.get(feedback_id, user_id=user_id)

    async def list_relevance(self, *, user_id: str, limit: int = 100,
                             after_id: str | None = None) -> list[RelevanceRecord]:
        """Page by feedback UUID; concurrent inserts may precede the cursor."""
        return await self._relevance.list(user_id=user_id, limit=limit, after_id=after_id)

    async def evaluate_learning(self, *, user_id: str, scopes: list[Scope] | None = None,
                                surface: Literal["results", "context"] = "results", config: LearningConfig | None = None,
                                query_groups: dict[UUID, str] | None = None,
                                max_records: int = 10000) -> LearningEvaluation:
        """Fit an offline ranking proposal from a bounded, saved feedback cut.

        This returns an inspectable report; it does not activate a profile or
        mutate engine-global weights. Scope filters must match saved requests.
        """
        from prme.retrieval.learning import evaluate_learning

        scope_copy = list(scopes) if scopes is not None else None
        group_copy = dict(query_groups) if query_groups is not None else None
        receipts, records = await self._relevance.learning_snapshot(user_id=user_id, max_records=max_records)
        return await asyncio.to_thread(evaluate_learning, receipts, records, user_id=user_id,
                                       scopes=scope_copy, surface=surface, config=config, query_groups=group_copy)

    # --- Quality Feedback (Issue #24) ---

    async def feedback(self, signal: FeedbackSignal) -> None:
        """Record a feedback signal for quality tracking and auto-tuning.

        Signals indicate whether surfaced memories were used, ignored,
        corrected, or contradicted. Accumulated signals are processed
        by the feedback_apply organizer job to auto-tune scoring weights.

        Args:
            signal: The feedback signal to record.
        """
        self._feedback_tracker.record(signal)
        logger.debug(
            "Feedback recorded: type=%s, query=%r, nodes=%d",
            signal.signal_type.value,
            signal.query[:50],
            len(signal.surfaced_node_ids),
        )

    @property
    def quality_metrics(self) -> QualityMetrics:
        """Compute current quality metrics over the last 30 days.

        Returns:
            QualityMetrics with retrieval quality, signal rates,
            and weight adjustment history.
        """
        signals = self._feedback_tracker.get_signals(window_days=30)

        # Compute weight deltas from default.
        from prme.retrieval.config import ScoringWeights as _SW

        defaults = _SW()
        current = self._config.scoring
        adjustments = {}
        for field_name in [
            "w_semantic", "w_lexical", "w_graph",
            "w_recency", "w_salience", "w_confidence",
            "w_epistemic",
        ]:
            delta = getattr(current, field_name) - getattr(defaults, field_name)
            if abs(delta) > 1e-9:
                adjustments[field_name] = round(delta, 6)

        return compute_quality_metrics(signals, adjustments)

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
            user_id: When given, every job only reads and mutates nodes owned
                by this user. Multi-tenant deployments must pass it and drive
                maintenance as a loop over tenants. Omitting it covers the
                whole store, but pairwise jobs still reject cross-owner merges.
                Feedback-derived scoring weights remain engine-global.
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

        # Drain materialization queue first (issue #25)
        materialized = 0
        if await self._materialization_queue.debt() > 0:
            try:
                materialized = await self._materialization_queue.drain(
                    self, budget_ms=self._config.materialization_budget_ms, user_id=user_id,
                )
            except Exception:
                logger.warning(
                    "organize: materialization drain failed; continuing",
                    exc_info=True,
                )

        for job_name in jobs:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            remaining_ms = budget_ms - elapsed_ms

            if remaining_ms <= 0:
                result.jobs_skipped.append(job_name)
                continue

            try:
                job_result = await run_job(
                    job_name, self, self._config.organizer, remaining_ms, user_id
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

        # Include materialization debt in result details (issue #25)
        if materialized > 0 or self._materialization_queue.debt_sync() > 0:
            from prme.organizer.models import JobResult as _JobResult

            result.per_job["materialization_drain"] = _JobResult(
                job="materialization_drain",
                nodes_processed=materialized,
                nodes_modified=materialized,
                details={
                    "materialized": materialized,
                    "debt_remaining": self._materialization_queue.debt_sync(),
                },
            )

        return result

    async def consolidate_knowledge(
        self,
        *,
        user_id: str,
        scope: Scope | None = None,
        entity_names: list[str] | None = None,
        max_profile_tokens: int = 500,
    ) -> int:
        """Build entity knowledge profiles from stored nodes.

        Groups active source nodes within each owner/scope by entity name,
        then creates SUMMARY nodes containing consolidated knowledge
        per entity. These profiles are indexed for retrieval, making
        entity-centric facts directly searchable.

        This works without LLM extraction — it uses simple name matching
        to detect entity mentions in node content.

        Args:
            user_id: User whose knowledge to consolidate.
            scope: Only rebuild this scope. Omit to process each scope separately.
            entity_names: Optional list of entity names to consolidate.
                If None, auto-detects names from content (proper nouns).
            max_profile_tokens: Maximum complete profile tokens, including source
                metadata, using the configured packing tokenizer.

        Returns:
            Number of entity profiles published. Each replacement is atomic;
            the complete multi-entity call is not a single transaction.

        Raises:
            StaleProfileError: A source or concurrent publication changed during
                preparation. Call again to build from fresh source snapshots.
            Exception: Embedding, staging or storage failure. A failed
                preparation preserves the prior profile. A lost commit
                acknowledgement can still mean the replacement was committed.
        """
        import re
        from prme.organizer.profiles import build_profile, mentions_entity

        if type(max_profile_tokens) is not int or max_profile_tokens < 0:
            raise ValueError("max_profile_tokens must be a nonnegative integer")
        if entity_names is not None:
            if not isinstance(entity_names, (list, tuple)) or any(
                not isinstance(name, str) or not name.strip() for name in entity_names
            ):
                raise ValueError("entity_names must contain nonempty strings")
            entity_names = list(dict.fromkeys(name.strip() for name in entity_names))

        if scope is None:
            # Profiles have one namespace. Never pool evidence from different
            # scopes, even when the explicit owner and entity name match.
            total = 0
            for namespace in Scope:
                total += await self.consolidate_knowledge(
                    user_id=user_id, scope=namespace, entity_names=entity_names,
                    max_profile_tokens=max_profile_tokens,
                )
            return total
        scope = Scope(scope)

        # Scan by immutable ID instead of interpreting a newest-N window as
        # complete history. Otherwise older qualifying evidence can disappear
        # from a rebuild and cause a still-supported profile to be retired.
        all_nodes: list[MemoryNode] = []
        existing_profiles: dict[str, list[MemoryNode]] = {}
        after_id: str | None = None
        while True:
            page = await self._graph_store.scan_nodes(
                user_id=user_id, scope=scope,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
                after_id=after_id, limit=500,
            )
            if not page:
                break
            for node in page:
                meta = node.metadata or {}
                if meta.get("entity_profile"):
                    # Generated profiles cannot qualify as fresh evidence.
                    name = meta.get("entity_name")
                    if node.node_type == NodeType.SUMMARY and name:
                        if entity_names is None or name in entity_names:
                            existing_profiles.setdefault(name, []).append(node)
                elif entity_names is None or any(mentions_entity(node.content, name) for name in entity_names):
                    # Explicit names need retain only relevant source nodes,
                    # rather than every unrelated node in a large namespace.
                    all_nodes.append(node)
            after_id = str(page[-1].id)

        async def retire_profile(name: str) -> None:
            for stale in existing_profiles.get(name, []):
                stale_id = str(stale.id)
                try:
                    await self.archive(stale_id, user_id=user_id)
                except ValueError:
                    # Already terminal; still remove a stale index entry.
                    await self._evict_from_indexes(stale_id)

        # Auto-detect entity names if not provided
        if entity_names is None:
            name_counts: dict[str, int] = {}
            name_re = re.compile(r'\b([A-Z][a-z]{2,})\b')
            skip = {"The", "This", "That", "What", "When", "Where", "How",
                    "Who", "Why", "Does", "Did", "Has", "Have", "Was",
                    "Will", "Can", "Could", "Would", "Should", "May",
                    "Also", "Just", "Very", "Really", "Some", "Most",
                    "Each", "Every", "After", "Before", "Since", "Until",
                    "About", "From", "Into", "Over", "With", "Note",
                    "Image", "Observation", "Known", "Facts", "LATEST",
                    "OLDER", "RECENT", "MOST", "USE", "THIS", "VALUE",
                    "AGGREGATION", "TASK", "IMPORTANT"}
            for node in all_nodes:
                names = name_re.findall(node.content)
                for name in names:
                    if name not in skip and len(name) > 2:
                        name_counts[name] = name_counts.get(name, 0) + 1
            # Keep names that appear 3+ times (likely real entities)
            entity_names = [
                name for name, count in name_counts.items()
                if count >= 3
            ]
            # Reconsider existing views even if their sources no longer qualify.
            entity_names = list(dict.fromkeys([*entity_names, *existing_profiles]))

        if not entity_names:
            return 0

        profiles_created = 0
        for entity_name in entity_names:
            generation = await self._graph_store.profile_generation(profile_key(user_id, scope, entity_name))
            # Find all nodes mentioning this entity
            related = []
            for node in all_nodes:
                if mentions_entity(node.content, entity_name):
                    related.append(node)

            if len(related) < 2:
                # An old profile cannot supply its own missing evidence.
                await retire_profile(entity_name)
                continue

            excerpt = build_profile(
                entity_name, related, token_budget=max_profile_tokens,
                tokenizer=self._config.packing.tokenizer,
            )
            if excerpt is None:
                continue
            profile_text, selected_sources, profile_tokens = excerpt
            confidence = min(
                self._confidence_matrix.lookup_with_fallback(EpistemicType.INFERRED, SourceType.SYSTEM_INFERRED),
                *(node.confidence for node in selected_sources),
            )

            # Store as SUMMARY node
            profile_node = MemoryNode(
                user_id=user_id,
                node_type=NodeType.SUMMARY,
                scope=scope,
                content=profile_text,
                metadata={
                    "entity_profile": True, "entity_name": entity_name,
                    "source_node_ids": [str(node.id) for node in selected_sources],
                    "source_count_available": len(related), "source_count_included": len(selected_sources),
                    "profile_format_version": 2, "tokens_used": profile_tokens,
                    "tokenizer": self._config.packing.tokenizer,
                },
                evidence_refs=list(dict.fromkeys(ref for node in selected_sources for ref in node.evidence_refs)),
                confidence=confidence,
                confidence_base=confidence,
                salience=0.7,
                salience_base=0.7,
                epistemic_type=EpistemicType.INFERRED,
                source_type=SourceType.SYSTEM_INFERRED,
                decay_profile=DecayProfile.FAST,
            )
            provider = self._vector_index._provider
            vectors = await provider.embed([profile_text])
            if len(vectors) != 1:
                raise ValueError("Profile embedding provider must return exactly one vector")
            plan = ProfilePublication(
                node=profile_node, sources=tuple(selected_sources),
                previous=tuple(existing_profiles.get(entity_name, [])), generation=generation,
                embedding=PreparedEmbedding(
                    node_id=profile_node.id, content=profile_text,
                    model=provider.model_name, version=provider.model_version,
                    dimension=provider.dimension, values=tuple(vectors[0]),
                ),
            )
            if self._pool is None:
                # Stage before graph visibility. Durable vector staging protects
                # interrupted preparation from ordinary orphan compaction.
                await self._vector_index.stage(plan.embedding, user_id=user_id)
                await self._lexical_index.stage_profile(plan)
            # PostgreSQL writes pgvector and generated text search in this same
            # graph transaction. Cancellation never triggers destructive cleanup
            # of a commit whose acknowledgement might have been lost.
            await self._write_queue.submit(
                lambda p=plan: self._graph_store.publish_profile(p),
                label=f"consolidate.publish:{entity_name}",
            )
            for stale in existing_profiles.get(entity_name, []):
                await self._evict_from_indexes(str(stale.id))
            profiles_created += 1

        return profiles_created

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

        Idempotent — safe to call multiple times.

        When encryption at rest is enabled, the memory pack is re-encrypted
        after all backends are closed. If that encryption fails the pack is
        left plaintext on disk, so this method fails closed: it logs at ERROR
        and raises ``EncryptionError`` rather than returning silently (issue
        #37).

        Raises:
            EncryptionError: If re-encrypting the memory pack on close fails.
        """
        if self._closed:
            return
        self._closed = True

        # Let any scheduled maintenance pass finish before the write queue
        # and connections it uses go away (issue #62).
        if self._maintenance_runner is not None:
            try:
                await self._maintenance_runner.drain()
            except Exception:
                logger.warning(
                    "Error draining opportunistic maintenance", exc_info=True
                )

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

        # --- Encrypt memory pack after all backends are closed (issue #14) ---
        # Fail closed: if encryption fails the pack is plaintext on disk, so
        # the caller MUST be told rather than silently shipping plaintext
        # (issue #37). The backends are already closed at this point, so
        # re-raising here only surfaces the encryption failure; it does not
        # leak resources.
        if self._encryption_provider is not None and self._config is not None:
            # close() encrypts authoritatively, so drop the best-effort
            # atexit net to avoid a redundant second pass at exit.
            if self._atexit_registered:
                atexit.unregister(self._atexit_encrypt)
                self._atexit_registered = False
            try:
                self._encrypt_memory_pack()
            except Exception as exc:
                logger.error(
                    "Failed to encrypt memory pack on close; pack remains "
                    "PLAINTEXT on disk at %s",
                    self._config.db_path,
                    exc_info=True,
                )
                raise EncryptionError(
                    "Failed to encrypt memory pack on close; the pack is "
                    "plaintext on disk. See logs for the underlying error."
                ) from exc

    # --- Encryption at rest helpers (issue #14) ---

    def _register_atexit_encrypt(self) -> None:
        """Register a best-effort atexit handler to re-encrypt on exit.

        This is a safety net for the plaintext-on-disk window (issue #37):
        if the process exits normally without a clean ``close()``, the pack
        is still re-encrypted. It is *best effort only* — a hard crash
        (``SIGKILL``, power loss) bypasses ``atexit`` entirely, and if the
        backends still hold the DuckDB file lock the re-encryption may fail.
        The authoritative path remains an explicit ``await close()``.
        """
        if self._encryption_provider is None or self._atexit_registered:
            return
        atexit.register(self._atexit_encrypt)
        self._atexit_registered = True

    def _atexit_encrypt(self) -> None:
        """Best-effort synchronous re-encryption on interpreter exit.

        Swallows all errors: at interpreter shutdown there is no caller to
        propagate to, and a failure here is no worse than the pre-existing
        plaintext-on-disk state. Errors are logged so the window is visible.
        """
        if self._closed or self._encryption_provider is None:
            return
        try:
            self._encrypt_memory_pack()
            logger.warning(
                "Re-encrypted memory pack via atexit handler; the engine was "
                "not closed cleanly. Prefer an explicit close()."
            )
        except Exception:
            logger.error(
                "atexit re-encryption failed; memory pack may remain "
                "plaintext on disk",
                exc_info=True,
            )

    def _encrypt_memory_pack(self) -> None:
        """Encrypt pack files after all storage handles are closed."""
        assert self._encryption_provider is not None
        assert self._config is not None
        self._encrypt_pack_files(self._config, self._encryption_provider)

    @staticmethod
    def _encrypt_pack_files(config: PRMEConfig, provider: EncryptionProvider) -> None:
        """Shared normal-close and failed-startup encryption path."""
        from pathlib import Path

        from prme.storage.encryption import write_manifest

        encrypted_files: list[Path] = []

        # Encrypt DuckDB file
        db_path = Path(config.db_path)
        if db_path.exists():
            enc = provider.encrypt_file(db_path)
            encrypted_files.append(enc)

        # Encrypt vector index file
        vec_path = Path(config.vector_path)
        if vec_path.exists():
            enc = provider.encrypt_file(vec_path)
            encrypted_files.append(enc)

        # Encrypt lexical index directory
        lex_path = Path(config.lexical_path)
        if lex_path.is_dir():
            enc_list = provider.encrypt_directory(lex_path)
            encrypted_files.extend(enc_list)

        # A failed open may decrypt only part of an existing pack. Retain
        # untouched encrypted artifacts in the inventory as well.
        existing = [Path(config.db_path + ".enc"), Path(config.vector_path + ".enc")]
        if lex_path.is_dir():
            existing.extend(lex_path.glob("*.enc"))
        encrypted_files = sorted(set(encrypted_files) | {p for p in existing if p.is_file()})

        # Write manifest
        if encrypted_files:
            base_dir = db_path.parent
            write_manifest(base_dir, encrypted_files)
            logger.info(
                "Encrypted %d memory pack files", len(encrypted_files)
            )

    def lock(self) -> None:
        """Encrypt all memory pack files without closing the engine.

        This is a convenience method for encrypting the pack while
        the engine is still open. The engine must be closed before
        calling this if backends hold file locks.

        Raises:
            RuntimeError: If encryption is not configured.
        """
        if self._encryption_provider is None:
            raise RuntimeError(
                "Cannot lock: encryption is not configured. "
                "Set encryption_enabled=True and encryption_key in config."
            )
        self._encrypt_memory_pack()

    def unlock(self) -> None:
        """Decrypt all memory pack files.

        This is a convenience method for decrypting an encrypted pack.
        Should be called before opening backends that need the files.

        Raises:
            RuntimeError: If encryption is not configured.
        """
        from pathlib import Path

        if self._encryption_provider is None:
            raise RuntimeError(
                "Cannot unlock: encryption is not configured. "
                "Set encryption_enabled=True and encryption_key in config."
            )

        assert self._config is not None

        # Decrypt DuckDB file
        db_enc = Path(self._config.db_path + ".enc")
        if db_enc.exists():
            self._encryption_provider.decrypt_file(db_enc)

        # Decrypt vector index file
        vec_enc = Path(self._config.vector_path + ".enc")
        if vec_enc.exists():
            self._encryption_provider.decrypt_file(vec_enc)

        # Decrypt lexical index directory
        lex_dir = Path(self._config.lexical_path)
        if lex_dir.is_dir():
            self._encryption_provider.decrypt_directory(lex_dir)
