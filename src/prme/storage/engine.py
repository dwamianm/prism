"""Unified MemoryEngine coordinating all four storage backends.

The MemoryEngine is the single entry point for all storage operations.
A single store() call auto-propagates to: EventStore (source of truth),
GraphStore (relational model), VectorIndex (semantic search), and
LexicalIndex (full-text search).

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
from typing import TYPE_CHECKING

import duckdb

from prme.config import PRMEConfig
from prme.models import Event, MemoryNode
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.embedding import create_embedding_provider
from prme.storage.event_store import EventStore
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
from prme.storage.write_queue import WriteQueue
from prme.types import LifecycleState, NodeType, Scope

if TYPE_CHECKING:
    from prme.ingestion.pipeline import IngestionPipeline

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
        conn: duckdb.DuckDBPyConnection,
        event_store: EventStore,
        graph_store: DuckPGQGraphStore,
        vector_index: VectorIndex,
        lexical_index: LexicalIndex,
        write_queue: WriteQueue,
        pipeline: IngestionPipeline | None = None,
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._graph_store = graph_store
        self._vector_index = vector_index
        self._lexical_index = lexical_index
        self._write_queue = write_queue
        self._pipeline = pipeline

    @classmethod
    async def create(cls, config: PRMEConfig | None = None) -> "MemoryEngine":
        """Create and initialize a MemoryEngine with all backends.

        Opens a DuckDB connection, initializes the schema, creates all
        four backend stores, starts the write queue, and sets up the
        ingestion pipeline with an extraction provider.

        If config is None, uses PRMEConfig() which loads from environment
        variables and defaults.

        Args:
            config: Optional configuration. Defaults to PRMEConfig().

        Returns:
            An initialized MemoryEngine ready for use.
        """
        if config is None:
            config = PRMEConfig()

        # Open DuckDB connection
        conn = duckdb.connect(config.db_path)

        # Initialize schema (tables, indexes, DuckPGQ attempt)
        initialize_database(conn)

        # Create backend stores
        event_store = EventStore(conn)
        graph_store = DuckPGQGraphStore(conn)

        # Create embedding provider via factory
        embedding_provider = create_embedding_provider(config.embedding)

        # Create vector index
        vector_index = VectorIndex(conn, config.vector_path, embedding_provider)

        # Create lexical index
        lexical_index = LexicalIndex(config.lexical_path)

        # Create and start write queue
        write_queue = WriteQueue(maxsize=config.write_queue_size)
        await write_queue.start()

        # Lazy import to avoid circular import (engine -> pipeline -> entity_merge -> graph_store -> engine)
        from prme.ingestion.extraction import create_extraction_provider
        from prme.ingestion.pipeline import IngestionPipeline

        # Create extraction provider and ingestion pipeline
        extraction_provider = create_extraction_provider(config.extraction)
        pipeline = IngestionPipeline(
            event_store=event_store,
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
            extraction_provider=extraction_provider,
            write_queue=write_queue,
        )

        return cls(
            conn=conn,
            event_store=event_store,
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
            write_queue=write_queue,
            pipeline=pipeline,
        )

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
        confidence: float = 0.5,
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
            confidence: Initial confidence score (0.0 to 1.0).

        Returns:
            String UUID of the created event (source of truth ID).
        """
        # Step 1: Create and persist event (source of truth) via write queue
        event = Event(
            content=content,
            user_id=user_id,
            session_id=session_id,
            role=role,
            metadata=metadata,
        )
        event_id = await self._write_queue.submit(
            lambda ev=event: self._event_store.append(ev),
            label=f"store.event:{event.id}",
        )

        # Step 2: Create graph node via write queue
        node = MemoryNode(
            user_id=user_id,
            session_id=session_id,
            node_type=node_type,
            scope=scope,
            content=content,
            metadata=metadata,
            confidence=confidence,
            evidence_refs=[event.id],
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
                lambda nid=node_id, c=content, uid=user_id, nt=node_type.value: (
                    self._lexical_index.index(nid, c, uid, nt)
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

        return event_id

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
            return await self.store(
                content,
                user_id=user_id,
                session_id=session_id,
                role=role,
                scope=scope,
                metadata=metadata,
            )
        return await self._pipeline.ingest(
            content,
            user_id=user_id,
            role=role,
            session_id=session_id,
            metadata=metadata,
            wait_for_extraction=wait_for_extraction,
            scope=scope,
        )

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

    async def search(
        self,
        query: str,
        user_id: str,
        *,
        k: int = 10,
    ) -> dict:
        """Search across vector and lexical backends in parallel.

        Returns raw results from both backends. Phase 3 builds the
        hybrid re-ranker that merges and scores these results.

        Args:
            query: Search query text.
            user_id: Scope results to this user.
            k: Maximum results per backend.

        Returns:
            Dict with 'vector_results' and 'lexical_results' keys.
        """
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

        try:
            self._conn.close()
        except Exception:
            logger.warning("Error closing DuckDB connection", exc_info=True)
