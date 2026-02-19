"""Unified MemoryEngine coordinating all four storage backends.

The MemoryEngine is the single entry point for all storage operations.
A single store() call auto-propagates to: EventStore (source of truth),
GraphStore (relational model), VectorIndex (semantic search), and
LexicalIndex (full-text search).

Developers interact with this class exclusively. Backend coordination,
error handling, and lifecycle transitions are managed here.
"""

import asyncio
import logging

import duckdb

from prme.config import PRMEConfig
from prme.models import Event, MemoryNode
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.embedding import FastEmbedProvider
from prme.storage.event_store import EventStore
from prme.storage.lexical_index import LexicalIndex
from prme.storage.schema import initialize_database
from prme.storage.vector_index import VectorIndex
from prme.types import LifecycleState, NodeType, Scope

logger = logging.getLogger(__name__)


class MemoryEngine:
    """Unified memory interface. Single entry point for all storage operations.

    Coordinates writes across EventStore, GraphStore, VectorIndex, and
    LexicalIndex. A single store() call propagates content to all four
    backends. Lifecycle transitions (promote, supersede, archive) are
    passed through to the GraphStore.

    Use the async create() factory method to initialize.
    """

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        event_store: EventStore,
        graph_store: DuckPGQGraphStore,
        vector_index: VectorIndex,
        lexical_index: LexicalIndex,
    ) -> None:
        self._conn = conn
        self._event_store = event_store
        self._graph_store = graph_store
        self._vector_index = vector_index
        self._lexical_index = lexical_index

    @classmethod
    async def create(cls, config: PRMEConfig | None = None) -> "MemoryEngine":
        """Create and initialize a MemoryEngine with all backends.

        Opens a DuckDB connection, initializes the schema, and creates
        all four backend stores. If config is None, uses PRMEConfig()
        which loads from environment variables and defaults.

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

        # Create embedding provider
        embedding_provider = FastEmbedProvider(
            model_name=config.embedding.model_name,
            dimension=config.embedding.dimension,
        )

        # Create vector index
        vector_index = VectorIndex(conn, config.vector_path, embedding_provider)

        # Create lexical index
        lexical_index = LexicalIndex(config.lexical_path)

        return cls(
            conn=conn,
            event_store=event_store,
            graph_store=graph_store,
            vector_index=vector_index,
            lexical_index=lexical_index,
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
        1. Create Event, append to EventStore (source of truth, MUST succeed).
        2. Create MemoryNode with evidence_refs=[event.id], store in GraphStore.
        3. Index into VectorIndex and LexicalIndex in parallel.

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
        # Step 1: Create and persist event (source of truth)
        event = Event(
            content=content,
            user_id=user_id,
            session_id=session_id,
            role=role,
            metadata=metadata,
        )
        event_id = await self._event_store.append(event)

        # Step 2: Create graph node
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
        node_id = await self._graph_store.create_node(node)

        # Step 3: Index into vector and lexical in parallel
        try:
            await asyncio.gather(
                self._vector_index.index(node_id, content, user_id),
                self._lexical_index.index(
                    node_id, content, user_id, node_type.value
                ),
            )
        except Exception:
            logger.warning(
                "Vector/lexical indexing failed for event %s. "
                "Event is persisted; indexes can be rebuilt.",
                event_id,
                exc_info=True,
            )

        return event_id

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

        Saves the VectorIndex to disk, closes the LexicalIndex,
        and closes the DuckDB connection.
        """
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
