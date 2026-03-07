"""DuckPGQ-backed GraphStore implementation with full graph operations.

Implements the GraphStore Protocol using DuckDB tables as the underlying
storage. All traversal operations use recursive CTEs on the edges table
as the primary implementation path. DuckPGQ SQL/PGQ is not available for
DuckDB 1.4.4 on osx_arm64, so recursive CTEs are the only code path.

Covers: node/edge CRUD, query_nodes/get_edges, lifecycle transitions
(promote, supersede, archive), graph traversal (neighborhood, shortest
path), and supersedence chain traversal.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

import duckdb

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import (
    ALLOWED_TRANSITIONS,
    DecayProfile,
    EdgeType,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    SourceType,
    validate_transition,
)

logger = logging.getLogger(__name__)


class DuckPGQGraphStore:
    """GraphStore implementation backed by DuckDB tables.

    Uses parameterized queries for all user data to prevent SQL injection.
    All queries enforce user_id scoping. Query defaults filter to active
    lifecycle states (tentative + stable).
    """

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        conn_lock: asyncio.Lock | None = None,
    ) -> None:
        self._conn = conn
        self._conn_lock = conn_lock if conn_lock is not None else asyncio.Lock()

    # --- Node Operations ---

    async def create_node(self, node: MemoryNode) -> str:
        """Create a new node in the graph store.

        Args:
            node: The MemoryNode to store.

        Returns:
            String UUID of the created node.
        """
        async with self._conn_lock:
            await asyncio.to_thread(self._create_node_sync, node)
        return str(node.id)

    async def get_node(
        self,
        node_id: str,
        *,
        include_superseded: bool = False,
    ) -> MemoryNode | None:
        """Retrieve a node by ID.

        Args:
            node_id: String UUID of the node.
            include_superseded: If False, returns None for
                superseded/archived nodes.

        Returns:
            The MemoryNode if found and visible, None otherwise.
        """
        async with self._conn_lock:
            return await asyncio.to_thread(
                self._get_node_sync, node_id, include_superseded
            )

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        user_id: str | None = None,
        scope: Scope | None = None,
        lifecycle_states: list[LifecycleState] | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
        limit: int = 100,
    ) -> list[MemoryNode]:
        """Query nodes with flexible filters.

        Defaults to filtering for active states (tentative + stable).

        Args:
            node_type: Filter by node type.
            user_id: Filter by user.
            scope: Filter by scope.
            lifecycle_states: Lifecycle filter (defaults to active).
            valid_at: Temporal validity filter.
            min_confidence: Minimum confidence threshold.
            limit: Max results.

        Returns:
            List of matching MemoryNodes.
        """
        async with self._conn_lock:
            return await asyncio.to_thread(
                self._query_nodes_sync,
                node_type,
                user_id,
                scope,
                lifecycle_states,
                valid_at,
                min_confidence,
                limit,
            )

    # --- Edge Operations ---

    async def create_edge(self, edge: MemoryEdge) -> str:
        """Create a new edge between two nodes.

        Args:
            edge: The MemoryEdge to store.

        Returns:
            String UUID of the created edge.
        """
        async with self._conn_lock:
            await asyncio.to_thread(self._create_edge_sync, edge)
        return str(edge.id)

    async def get_edges(
        self,
        *,
        source_id: str | None = None,
        target_id: str | None = None,
        edge_type: EdgeType | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
    ) -> list[MemoryEdge]:
        """Query edges with filters.

        Args:
            source_id: Filter by source node.
            target_id: Filter by target node.
            edge_type: Filter by edge type.
            valid_at: Temporal validity filter.
            min_confidence: Minimum confidence threshold.

        Returns:
            List of matching MemoryEdges.
        """
        async with self._conn_lock:
            return await asyncio.to_thread(
                self._get_edges_sync,
                source_id,
                target_id,
                edge_type,
                valid_at,
                min_confidence,
            )

    # --- Lifecycle Transitions ---

    async def promote(self, node_id: str) -> None:
        """Promote a tentative node to stable.

        Args:
            node_id: String UUID of the node to promote.

        Raises:
            ValueError: If the node doesn't exist or the transition is invalid.
        """
        async with self._conn_lock:
            await asyncio.to_thread(self._promote_sync, node_id)

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark a node as superseded by another.

        Creates a SUPERSEDES edge from new_node to old_node with optional
        evidence_id for provenance tracking. Updates the old node's
        lifecycle_state to 'superseded' and sets superseded_by pointer.

        Args:
            old_node_id: Node being replaced.
            new_node_id: Replacement node.
            evidence_id: Optional event ID providing evidence for the
                supersedence.

        Raises:
            ValueError: If either node doesn't exist or the transition
                is invalid.
        """
        async with self._conn_lock:
            await asyncio.to_thread(
                self._supersede_sync, old_node_id, new_node_id, evidence_id
            )

    async def contradict(
        self,
        node_a_id: str,
        node_b_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark two nodes as contradicting each other.

        Creates a CONTRADICTS edge (from node_b to node_a), transitions both
        nodes to CONTESTED lifecycle state, and logs a CONTRADICTION_NOTED
        operation to the operations table.

        Args:
            node_a_id: First conflicting node (typically the existing/older node).
            node_b_id: Second conflicting node (typically the new/incoming node).
            evidence_id: Optional event ID providing evidence.

        Raises:
            ValueError: If either node is not found or not in an active state.
        """
        async with self._conn_lock:
            await asyncio.to_thread(
                self._contradict_sync, node_a_id, node_b_id, evidence_id
            )

    async def resolve_contradiction(
        self,
        winner_id: str,
        loser_id: str,
        *,
        resolver_actor_id: str = "system",
        evidence_id: str | None = None,
    ) -> None:
        """Resolve a contradiction by declaring a winner and loser.

        Validates both nodes are CONTESTED and a CONTRADICTS edge exists
        between them. Transitions winner to STABLE and loser to DEPRECATED.
        Logs EPISTEMIC_TRANSITION operations for both and a
        CONTRADICTION_RESOLVED operation.

        Args:
            winner_id: Node determined to be correct (-> STABLE).
            loser_id: Node determined to be incorrect (-> DEPRECATED).
            resolver_actor_id: ID of the resolving actor.
            evidence_id: Optional supporting evidence event ID.

        Raises:
            ValueError: If nodes are not CONTESTED or no CONTRADICTS edge exists.
        """
        async with self._conn_lock:
            await asyncio.to_thread(
                self._resolve_contradiction_sync,
                winner_id,
                loser_id,
                resolver_actor_id,
                evidence_id,
            )

    async def archive(self, node_id: str) -> None:
        """Archive a node (terminal state).

        Any non-archived node can be archived. Archived is terminal --
        no further transitions are allowed.

        Args:
            node_id: String UUID of the node to archive.

        Raises:
            ValueError: If the node doesn't exist or is already archived.
        """
        async with self._conn_lock:
            await asyncio.to_thread(self._archive_sync, node_id)

    async def deprecate(self, node_id: str) -> None:
        """Deprecate a node (mark as confirmed incorrect).

        Valid transitions to DEPRECATED: CONTESTED -> DEPRECATED.
        Used by the organizer for threshold-based deprecation when
        confidence drops below the deprecate threshold.

        Args:
            node_id: String UUID of the node to deprecate.

        Raises:
            ValueError: If the node doesn't exist or the transition
                is invalid.
        """
        async with self._conn_lock:
            await asyncio.to_thread(self._deprecate_sync, node_id)

    # --- Graph Traversal ---

    async def get_neighborhood(
        self,
        node_id: str,
        *,
        max_hops: int = 2,
        edge_types: list[EdgeType] | None = None,
        valid_at: datetime | None = None,
        min_confidence: float | None = None,
        include_superseded: bool = False,
    ) -> list[MemoryNode]:
        """Get nodes within N hops of a starting node.

        Uses a recursive CTE on the edges table to find all reachable
        nodes within max_hops. Traverses edges in both directions
        (source->target and target->source) to find the full neighborhood.

        Args:
            node_id: Starting node ID.
            max_hops: Maximum number of hops (default 2).
            edge_types: Only traverse edges of these types.
            valid_at: Temporal filter for nodes.
            min_confidence: Minimum node confidence.
            include_superseded: Include superseded/archived nodes.

        Returns:
            List of reachable MemoryNodes (excluding the starting node).
        """
        async with self._conn_lock:
            return await asyncio.to_thread(
                self._get_neighborhood_sync,
                node_id,
                max_hops,
                edge_types,
                valid_at,
                min_confidence,
                include_superseded,
            )

    async def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        edge_types: list[EdgeType] | None = None,
    ) -> list[str] | None:
        """Find the shortest path between two nodes via BFS.

        Uses a recursive CTE to perform breadth-first search from
        source to target, tracking the path at each step.

        Args:
            source_id: Starting node ID.
            target_id: Target node ID.
            edge_types: Only traverse edges of these types.

        Returns:
            List of node IDs forming the shortest path (including
            source and target), or None if no path exists.
        """
        async with self._conn_lock:
            return await asyncio.to_thread(
                self._find_shortest_path_sync, source_id, target_id, edge_types
            )

    async def get_supersedence_chain(
        self,
        node_id: str,
        *,
        direction: str = "forward",
    ) -> list[MemoryNode]:
        """Traverse the supersedence chain from a node.

        SUPERSEDES edge direction: new_node -> old_node.
        - "forward" = what replaced this node (follow edges where this
          node is the TARGET of SUPERSEDES).
        - "backward" = what this node replaced (follow edges where this
          node is the SOURCE of SUPERSEDES).

        Args:
            node_id: Starting node ID.
            direction: "forward" or "backward".

        Returns:
            Ordered list of MemoryNodes in the chain.
        """
        async with self._conn_lock:
            return await asyncio.to_thread(
                self._get_supersedence_chain_sync, node_id, direction
            )

    # --- Cleanup / Rollback ---

    async def delete_node(self, node_id: str) -> None:
        """Delete a node by ID for rollback cleanup.

        No-op if the node does not exist. Logs the deletion at debug
        level for traceability.

        Args:
            node_id: String UUID of the node to delete.
        """
        # Defense-in-depth: primary write serialization is via WriteQueue
        async with self._conn_lock:
            await asyncio.to_thread(self._delete_node_sync, node_id)

    async def delete_edge(self, edge_id: str) -> None:
        """Delete an edge by ID for rollback cleanup.

        No-op if the edge does not exist. Logs the deletion at debug
        level for traceability.

        Args:
            edge_id: String UUID of the edge to delete.
        """
        # Defense-in-depth: primary write serialization is via WriteQueue
        async with self._conn_lock:
            await asyncio.to_thread(self._delete_edge_sync, edge_id)

    # --- Internal sync methods ---

    def _delete_node_sync(self, node_id: str) -> None:
        """Delete a node by ID (sync). No-op if not found."""
        self._conn.execute("DELETE FROM nodes WHERE id = ?", [node_id])
        logger.debug("graph.delete_node", extra={"node_id": node_id})

    def _delete_edge_sync(self, edge_id: str) -> None:
        """Delete an edge by ID (sync). No-op if not found."""
        self._conn.execute("DELETE FROM edges WHERE id = ?", [edge_id])
        logger.debug("graph.delete_edge", extra={"edge_id": edge_id})

    def _create_node_sync(self, node: MemoryNode) -> None:
        """Insert a node into the nodes table (sync)."""
        evidence_json = (
            json.dumps([str(ref) for ref in node.evidence_refs])
            if node.evidence_refs
            else None
        )
        metadata_json = (
            json.dumps(node.metadata) if node.metadata is not None else None
        )

        self._conn.execute(
            """
            INSERT INTO nodes (
                id, node_type, user_id, session_id, scope, content,
                metadata, confidence, salience, lifecycle_state,
                valid_from, valid_to, superseded_by, evidence_refs,
                created_at, updated_at, epistemic_type, source_type,
                decay_profile, last_reinforced_at, reinforcement_boost,
                salience_base, confidence_base, pinned
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)
            """,
            [
                str(node.id),
                node.node_type.value,
                node.user_id,
                node.session_id,
                node.scope.value,
                node.content,
                metadata_json,
                node.confidence,
                node.salience,
                node.lifecycle_state.value,
                node.valid_from,
                node.valid_to,
                str(node.superseded_by) if node.superseded_by else None,
                evidence_json,
                node.created_at,
                node.updated_at,
                node.epistemic_type.value,
                node.source_type.value,
                node.decay_profile.value,
                node.last_reinforced_at,
                node.reinforcement_boost,
                node.salience_base,
                node.confidence_base,
                node.pinned,
            ],
        )

    def _get_node_sync(
        self, node_id: str, include_superseded: bool
    ) -> MemoryNode | None:
        """Retrieve a single node by ID (sync)."""
        if include_superseded:
            result = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", [node_id]
            ).fetchone()
        else:
            result = self._conn.execute(
                """
                SELECT * FROM nodes
                WHERE id = ?
                AND lifecycle_state IN ('tentative', 'stable')
                """,
                [node_id],
            ).fetchone()

        if result is None:
            return None
        return self._row_to_node(result)

    def _query_nodes_sync(
        self,
        node_type: NodeType | None,
        user_id: str | None,
        scope: Scope | None,
        lifecycle_states: list[LifecycleState] | None,
        valid_at: datetime | None,
        min_confidence: float | None,
        limit: int,
    ) -> list[MemoryNode]:
        """Query nodes with dynamic WHERE clause (sync)."""
        conditions: list[str] = []
        params: list = []

        # Default lifecycle filter: active states only (includes CONTESTED
        # per Research Pitfall 3 -- contested nodes are active with unresolved
        # conflicts but still valid candidates for retrieval).
        if lifecycle_states is None:
            lifecycle_states = [
                LifecycleState.TENTATIVE,
                LifecycleState.STABLE,
                LifecycleState.CONTESTED,
            ]

        # Build lifecycle IN clause
        placeholders = ", ".join(["?" for _ in lifecycle_states])
        conditions.append(f"lifecycle_state IN ({placeholders})")
        params.extend([s.value for s in lifecycle_states])

        if node_type is not None:
            conditions.append("node_type = ?")
            params.append(node_type.value)

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        if scope is not None:
            conditions.append("scope = ?")
            params.append(scope.value)

        if valid_at is not None:
            conditions.append(
                "valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            )
            params.extend([valid_at, valid_at])

        if min_confidence is not None:
            conditions.append("confidence >= ?")
            params.append(min_confidence)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"""
            SELECT * FROM nodes
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    def _create_edge_sync(self, edge: MemoryEdge) -> None:
        """Insert an edge into the edges table (sync)."""
        metadata_json = (
            json.dumps(edge.metadata) if edge.metadata is not None else None
        )

        self._conn.execute(
            """
            INSERT INTO edges (
                id, source_id, target_id, edge_type, user_id,
                confidence, valid_from, valid_to, provenance_event_id,
                metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(edge.id),
                str(edge.source_id),
                str(edge.target_id),
                edge.edge_type.value,
                edge.user_id,
                edge.confidence,
                edge.valid_from,
                edge.valid_to,
                (
                    str(edge.provenance_event_id)
                    if edge.provenance_event_id
                    else None
                ),
                metadata_json,
                edge.created_at,
            ],
        )

    def _get_edges_sync(
        self,
        source_id: str | None,
        target_id: str | None,
        edge_type: EdgeType | None,
        valid_at: datetime | None,
        min_confidence: float | None,
    ) -> list[MemoryEdge]:
        """Query edges with dynamic WHERE clause (sync)."""
        conditions: list[str] = []
        params: list = []

        if source_id is not None:
            conditions.append("source_id = ?")
            params.append(source_id)

        if target_id is not None:
            conditions.append("target_id = ?")
            params.append(target_id)

        if edge_type is not None:
            conditions.append("edge_type = ?")
            params.append(edge_type.value)

        if valid_at is not None:
            conditions.append(
                "valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            )
            params.extend([valid_at, valid_at])

        if min_confidence is not None:
            conditions.append("confidence >= ?")
            params.append(min_confidence)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"""
            SELECT * FROM edges
            WHERE {where_clause}
            ORDER BY created_at DESC
        """

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_edge(row) for row in rows]

    # --- Lifecycle sync methods ---

    def _promote_sync(self, node_id: str) -> None:
        """Promote a tentative node to stable (sync)."""
        row = self._conn.execute(
            "SELECT lifecycle_state FROM nodes WHERE id = ?", [node_id]
        ).fetchone()
        if row is None:
            raise ValueError(f"Node {node_id} not found")

        current_state = LifecycleState(row[0])
        target_state = LifecycleState.STABLE

        if not validate_transition(current_state, target_state):
            raise ValueError(
                f"Cannot promote: node is {current_state.value}, "
                f"only Tentative nodes can be promoted"
            )

        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [target_state.value, node_id],
        )

    def _supersede_sync(
        self,
        old_node_id: str,
        new_node_id: str,
        evidence_id: str | None,
    ) -> None:
        """Mark a node as superseded by another (sync)."""
        # Validate both nodes exist
        old_row = self._conn.execute(
            "SELECT lifecycle_state, user_id FROM nodes WHERE id = ?",
            [old_node_id],
        ).fetchone()
        if old_row is None:
            raise ValueError(f"Old node {old_node_id} not found")

        new_row = self._conn.execute(
            "SELECT id, user_id FROM nodes WHERE id = ?", [new_node_id]
        ).fetchone()
        if new_row is None:
            raise ValueError(f"New node {new_node_id} not found")

        # Validate transition
        current_state = LifecycleState(old_row[0])
        target_state = LifecycleState.SUPERSEDED

        if not validate_transition(current_state, target_state):
            raise ValueError(
                f"Cannot supersede: node is {current_state.value}, "
                f"only Tentative or Stable nodes can be superseded"
            )

        # Update old node
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?,
                superseded_by = ?,
                updated_at = current_timestamp
            WHERE id = ?
            """,
            [target_state.value, new_node_id, old_node_id],
        )

        # Create SUPERSEDES edge: new_node -> old_node
        # Parse evidence_id as UUID if valid, otherwise store None
        provenance_uuid = None
        if evidence_id is not None:
            try:
                provenance_uuid = UUID(evidence_id)
            except ValueError:
                logger.warning(
                    "evidence_id %r is not a valid UUID, storing edge "
                    "without provenance reference",
                    evidence_id,
                )

        edge = MemoryEdge(
            source_id=UUID(new_node_id),
            target_id=UUID(old_node_id),
            edge_type=EdgeType.SUPERSEDES,
            user_id=old_row[1],  # Use the old node's user_id
            confidence=1.0,
            provenance_event_id=provenance_uuid,
        )
        self._create_edge_sync(edge)

    def _archive_sync(self, node_id: str) -> None:
        """Archive a node (sync)."""
        row = self._conn.execute(
            "SELECT lifecycle_state FROM nodes WHERE id = ?", [node_id]
        ).fetchone()
        if row is None:
            raise ValueError(f"Node {node_id} not found")

        current_state = LifecycleState(row[0])
        target_state = LifecycleState.ARCHIVED

        if not validate_transition(current_state, target_state):
            raise ValueError(
                f"Cannot archive: node is {current_state.value}, "
                f"Archived nodes cannot be transitioned"
            )

        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [target_state.value, node_id],
        )

    def _deprecate_sync(self, node_id: str) -> None:
        """Deprecate a node (sync)."""
        row = self._conn.execute(
            "SELECT lifecycle_state FROM nodes WHERE id = ?", [node_id]
        ).fetchone()
        if row is None:
            raise ValueError(f"Node {node_id} not found")

        current_state = LifecycleState(row[0])
        target_state = LifecycleState.DEPRECATED

        if not validate_transition(current_state, target_state):
            raise ValueError(
                f"Cannot deprecate: node is {current_state.value}, "
                f"transition to DEPRECATED not allowed"
            )

        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [target_state.value, node_id],
        )

    def _contradict_sync(
        self,
        node_a_id: str,
        node_b_id: str,
        evidence_id: str | None,
    ) -> None:
        """Mark two nodes as contradicting each other (sync).

        Creates CONTRADICTS edge from node_b (newer) to node_a (older),
        transitions both nodes to CONTESTED, and logs CONTRADICTION_NOTED
        operation.
        """
        import uuid as _uuid

        # Validate both nodes exist and are in active state
        row_a = self._conn.execute(
            "SELECT lifecycle_state, user_id FROM nodes WHERE id = ?",
            [node_a_id],
        ).fetchone()
        if row_a is None:
            raise ValueError(f"Node {node_a_id} not found")

        row_b = self._conn.execute(
            "SELECT lifecycle_state, user_id FROM nodes WHERE id = ?",
            [node_b_id],
        ).fetchone()
        if row_b is None:
            raise ValueError(f"Node {node_b_id} not found")

        state_a = LifecycleState(row_a[0])
        state_b = LifecycleState(row_b[0])

        if not validate_transition(state_a, LifecycleState.CONTESTED):
            raise ValueError(
                f"Cannot contest node {node_a_id}: current state "
                f"'{state_a.value}' does not allow transition to CONTESTED"
            )
        if not validate_transition(state_b, LifecycleState.CONTESTED):
            raise ValueError(
                f"Cannot contest node {node_b_id}: current state "
                f"'{state_b.value}' does not allow transition to CONTESTED"
            )

        # Transition both nodes to CONTESTED
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [LifecycleState.CONTESTED.value, node_a_id],
        )
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [LifecycleState.CONTESTED.value, node_b_id],
        )

        # Create CONTRADICTS edge: node_b (newer) -> node_a (older)
        provenance_uuid = None
        if evidence_id is not None:
            try:
                provenance_uuid = UUID(evidence_id)
            except ValueError:
                logger.warning(
                    "evidence_id %r is not a valid UUID, storing edge "
                    "without provenance reference",
                    evidence_id,
                )

        edge = MemoryEdge(
            source_id=UUID(node_b_id),
            target_id=UUID(node_a_id),
            edge_type=EdgeType.CONTRADICTS,
            user_id=row_a[1],  # Use the older node's user_id
            confidence=1.0,
            provenance_event_id=provenance_uuid,
        )
        self._create_edge_sync(edge)

        # Log CONTRADICTION_NOTED operation
        op_id = str(_uuid.uuid4())
        payload = json.dumps({
            "node_a_id": node_a_id,
            "node_b_id": node_b_id,
            "evidence_event_id": evidence_id,
        })
        self._conn.execute(
            """
            INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at)
            VALUES (?, 'CONTRADICTION_NOTED', ?, ?, 'system', now())
            """,
            [op_id, node_a_id, payload],
        )

    def _resolve_contradiction_sync(
        self,
        winner_id: str,
        loser_id: str,
        resolver_actor_id: str,
        evidence_id: str | None,
    ) -> None:
        """Resolve a contradiction by declaring a winner and loser (sync).

        Validates both nodes are CONTESTED and a CONTRADICTS edge exists
        between them. Transitions winner to STABLE and loser to DEPRECATED.
        Logs EPISTEMIC_TRANSITION operations for both and a
        CONTRADICTION_RESOLVED operation.
        """
        import uuid as _uuid

        # Validate both nodes exist and are CONTESTED
        winner_row = self._conn.execute(
            "SELECT lifecycle_state FROM nodes WHERE id = ?",
            [winner_id],
        ).fetchone()
        if winner_row is None:
            raise ValueError(f"Winner node {winner_id} not found")

        loser_row = self._conn.execute(
            "SELECT lifecycle_state FROM nodes WHERE id = ?",
            [loser_id],
        ).fetchone()
        if loser_row is None:
            raise ValueError(f"Loser node {loser_id} not found")

        winner_state = LifecycleState(winner_row[0])
        loser_state = LifecycleState(loser_row[0])

        if winner_state != LifecycleState.CONTESTED:
            raise ValueError(
                f"Winner node {winner_id} is not CONTESTED "
                f"(current: {winner_state.value})"
            )
        if loser_state != LifecycleState.CONTESTED:
            raise ValueError(
                f"Loser node {loser_id} is not CONTESTED "
                f"(current: {loser_state.value})"
            )

        # Validate a CONTRADICTS edge exists between them (either direction)
        edge_row = self._conn.execute(
            """
            SELECT id FROM edges
            WHERE edge_type = 'contradicts'
            AND (
                (source_id = CAST(? AS UUID) AND target_id = CAST(? AS UUID))
                OR
                (source_id = CAST(? AS UUID) AND target_id = CAST(? AS UUID))
            )
            LIMIT 1
            """,
            [winner_id, loser_id, loser_id, winner_id],
        ).fetchone()
        if edge_row is None:
            raise ValueError(
                f"No CONTRADICTS edge exists between {winner_id} and {loser_id}"
            )

        # Transition winner to STABLE
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [LifecycleState.STABLE.value, winner_id],
        )

        # Transition loser to DEPRECATED
        self._conn.execute(
            """
            UPDATE nodes
            SET lifecycle_state = ?, updated_at = current_timestamp
            WHERE id = ?
            """,
            [LifecycleState.DEPRECATED.value, loser_id],
        )

        # Log EPISTEMIC_TRANSITION for winner (CONTESTED -> STABLE)
        self._conn.execute(
            """
            INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at)
            VALUES (?, 'EPISTEMIC_TRANSITION', ?, ?, ?, now())
            """,
            [
                str(_uuid.uuid4()),
                winner_id,
                json.dumps({
                    "from_state": LifecycleState.CONTESTED.value,
                    "to_state": LifecycleState.STABLE.value,
                    "reason": "contradiction_resolved_winner",
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            ],
        )

        # Log EPISTEMIC_TRANSITION for loser (CONTESTED -> DEPRECATED)
        self._conn.execute(
            """
            INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at)
            VALUES (?, 'EPISTEMIC_TRANSITION', ?, ?, ?, now())
            """,
            [
                str(_uuid.uuid4()),
                loser_id,
                json.dumps({
                    "from_state": LifecycleState.CONTESTED.value,
                    "to_state": LifecycleState.DEPRECATED.value,
                    "reason": "contradiction_resolved_loser",
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            ],
        )

        # Log CONTRADICTION_RESOLVED operation
        self._conn.execute(
            """
            INSERT INTO operations (id, op_type, target_id, payload, actor_id, created_at)
            VALUES (?, 'CONTRADICTION_RESOLVED', ?, ?, ?, now())
            """,
            [
                str(_uuid.uuid4()),
                winner_id,
                json.dumps({
                    "winner_id": winner_id,
                    "loser_id": loser_id,
                    "evidence_event_id": evidence_id,
                }),
                resolver_actor_id,
            ],
        )

    # --- Traversal sync methods ---

    def _get_neighborhood_sync(
        self,
        node_id: str,
        max_hops: int,
        edge_types: list[EdgeType] | None,
        valid_at: datetime | None,
        min_confidence: float | None,
        include_superseded: bool,
    ) -> list[MemoryNode]:
        """Get neighborhood via recursive CTE (sync).

        Traverses edges in both directions to find the full neighborhood.
        Uses a single UNION ALL recursive CTE with bidirectional traversal
        in both the base and recursive cases.
        """
        logger.debug(
            "get_neighborhood using recursive CTE (SQL fallback)",
            extra={"node_id": node_id, "max_hops": max_hops},
        )

        # Build edge filter clause
        edge_conditions = []
        edge_params: list = []
        if edge_types is not None:
            placeholders = ", ".join(["?" for _ in edge_types])
            edge_conditions.append(f"e.edge_type IN ({placeholders})")
            edge_params.extend([et.value for et in edge_types])

        edge_where = (
            "AND " + " AND ".join(edge_conditions) if edge_conditions else ""
        )

        # Build node filter clause
        node_conditions = []
        node_params: list = []

        if not include_superseded:
            node_conditions.append(
                "n.lifecycle_state IN ('tentative', 'stable')"
            )

        if valid_at is not None:
            node_conditions.append(
                "n.valid_from <= ? AND (n.valid_to IS NULL OR n.valid_to > ?)"
            )
            node_params.extend([valid_at, valid_at])

        if min_confidence is not None:
            node_conditions.append("n.confidence >= ?")
            node_params.append(min_confidence)

        node_where = (
            "AND " + " AND ".join(node_conditions) if node_conditions else ""
        )

        # Recursive CTE: base case finds direct neighbors (both directions),
        # recursive case expands from there. Uses CASE to handle bidirectional
        # traversal in a single UNION ALL.
        query = f"""
            WITH RECURSIVE neighborhood AS (
                -- Base case: direct neighbors in both directions
                SELECT
                    CASE WHEN e.source_id = CAST(? AS UUID)
                         THEN e.target_id
                         ELSE e.source_id
                    END AS id,
                    1 AS depth
                FROM edges e
                WHERE (e.source_id = CAST(? AS UUID) OR e.target_id = CAST(? AS UUID))
                    {edge_where}

                UNION ALL

                -- Recursive case: expand from discovered neighbors
                SELECT
                    CASE WHEN e.source_id = nb.id
                         THEN e.target_id
                         ELSE e.source_id
                    END AS id,
                    nb.depth + 1 AS depth
                FROM neighborhood nb
                JOIN edges e ON (e.source_id = nb.id OR e.target_id = nb.id)
                    {edge_where}
                WHERE nb.depth < ?
            )
            SELECT DISTINCT n.*
            FROM neighborhood nb
            JOIN nodes n ON nb.id = n.id
            WHERE n.id != CAST(? AS UUID) {node_where}
        """

        params: list = []
        # Base case: 3 refs to node_id + edge_params
        params.append(node_id)
        params.append(node_id)
        params.append(node_id)
        params.extend(edge_params)
        # Recursive case: edge_params + max_hops
        params.extend(edge_params)
        params.append(max_hops)
        # Final WHERE: node_id + node_params
        params.append(node_id)
        params.extend(node_params)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    def _find_shortest_path_sync(
        self,
        source_id: str,
        target_id: str,
        edge_types: list[EdgeType] | None,
    ) -> list[str] | None:
        """BFS shortest path via iterative query (sync).

        Uses Python-side BFS with SQL queries per level to avoid
        DuckDB recursive CTE limitations with complex path tracking.
        """
        logger.debug(
            "find_shortest_path using iterative BFS",
            extra={"source_id": source_id, "target_id": target_id},
        )

        if source_id == target_id:
            return [source_id]

        # Build edge filter clause
        edge_conditions = []
        edge_params: list = []
        if edge_types is not None:
            placeholders = ", ".join(["?" for _ in edge_types])
            edge_conditions.append(f"edge_type IN ({placeholders})")
            edge_params.extend([et.value for et in edge_types])

        edge_where = (
            "AND " + " AND ".join(edge_conditions) if edge_conditions else ""
        )

        # BFS: track visited nodes and parent pointers
        visited: set[str] = {source_id}
        parent: dict[str, str] = {}
        frontier = [source_id]
        max_depth = 10

        for _ in range(max_depth):
            if not frontier:
                return None

            # Find all neighbors of frontier nodes
            next_frontier: list[str] = []
            for current_id in frontier:
                # Get neighbors in both directions
                query = f"""
                    SELECT
                        CASE WHEN source_id = CAST(? AS UUID)
                             THEN CAST(target_id AS VARCHAR)
                             ELSE CAST(source_id AS VARCHAR)
                        END AS neighbor_id
                    FROM edges
                    WHERE (source_id = CAST(? AS UUID) OR target_id = CAST(? AS UUID))
                        {edge_where}
                """
                params = [current_id, current_id, current_id]
                params.extend(edge_params)

                rows = self._conn.execute(query, params).fetchall()
                for row in rows:
                    neighbor_id = str(row[0])
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        parent[neighbor_id] = current_id
                        next_frontier.append(neighbor_id)

                        if neighbor_id == target_id:
                            # Reconstruct path
                            path = [target_id]
                            node = target_id
                            while node != source_id:
                                node = parent[node]
                                path.append(node)
                            path.reverse()
                            return path

            frontier = next_frontier

        return None

    def _get_supersedence_chain_sync(
        self, node_id: str, direction: str
    ) -> list[MemoryNode]:
        """Traverse supersedence chain via iterative queries (sync).

        SUPERSEDES edge direction: new_node (source) -> old_node (target).
        - "forward" = what replaced this node. This node is the TARGET
          of a SUPERSEDES edge; follow source_id to find the replacer,
          then continue from there.
        - "backward" = what this node replaced. This node is the SOURCE
          of a SUPERSEDES edge; follow target_id to find the replaced,
          then continue from there.
        """
        logger.debug(
            "get_supersedence_chain using iterative SQL",
            extra={"node_id": node_id, "direction": direction},
        )

        chain: list[MemoryNode] = []
        visited: set[str] = {node_id}
        current_id = node_id

        # Max chain length to prevent infinite loops
        max_depth = 100

        for _ in range(max_depth):
            if direction == "forward":
                # What replaced this node?
                # Find SUPERSEDES edge where current is TARGET (old node)
                row = self._conn.execute(
                    """
                    SELECT source_id FROM edges
                    WHERE target_id = ? AND edge_type = 'supersedes'
                    LIMIT 1
                    """,
                    [current_id],
                ).fetchone()
            elif direction == "backward":
                # What did this node replace?
                # Find SUPERSEDES edge where current is SOURCE (new node)
                row = self._conn.execute(
                    """
                    SELECT target_id FROM edges
                    WHERE source_id = ? AND edge_type = 'supersedes'
                    LIMIT 1
                    """,
                    [current_id],
                ).fetchone()
            else:
                raise ValueError(
                    f"Invalid direction: {direction}. Use 'forward' or 'backward'."
                )

            if row is None:
                break

            next_id = str(row[0])
            if next_id in visited:
                break  # Cycle detection

            visited.add(next_id)

            # Fetch the node (include all states since chain may have archived nodes)
            node_row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", [next_id]
            ).fetchone()
            if node_row is None:
                break

            chain.append(self._row_to_node(node_row))
            current_id = next_id

        return chain

    # --- Row conversion helpers ---

    def _row_to_node(self, row: tuple) -> MemoryNode:
        """Convert a DuckDB row tuple to a MemoryNode.

        Column order matches the CREATE TABLE definition:
        id, node_type, user_id, session_id, scope, content,
        metadata, confidence, salience, lifecycle_state,
        valid_from, valid_to, superseded_by, evidence_refs,
        created_at, updated_at
        """
        raw_id = row[0]
        node_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))

        # Parse evidence_refs from JSON
        raw_evidence = row[13]
        evidence_refs: list[UUID] = []
        if raw_evidence is not None:
            if isinstance(raw_evidence, str):
                evidence_refs = [UUID(ref) for ref in json.loads(raw_evidence)]
            elif isinstance(raw_evidence, list):
                evidence_refs = [UUID(str(ref)) for ref in raw_evidence]

        # Parse metadata
        raw_metadata = row[6]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        # Parse superseded_by
        raw_superseded = row[12]
        superseded_by = None
        if raw_superseded is not None:
            superseded_by = (
                raw_superseded
                if isinstance(raw_superseded, UUID)
                else UUID(str(raw_superseded))
            )

        # Handle timezone-naive datetimes from DuckDB
        def ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        # Graceful fallback for pre-migration rows (columns may not exist)
        raw_epistemic = row[16] if len(row) > 16 else "asserted"
        raw_source = row[17] if len(row) > 17 else "user_stated"

        # Decay/reinforcement fields (RFC-0015) -- positions 18-23
        raw_decay_profile = row[18] if len(row) > 18 else "medium"
        raw_last_reinforced = row[19] if len(row) > 19 else None
        raw_reinforcement_boost = row[20] if len(row) > 20 else 0.0
        raw_salience_base = row[21] if len(row) > 21 else row[8]  # fallback to salience
        raw_confidence_base = row[22] if len(row) > 22 else row[7]  # fallback to confidence
        raw_pinned = row[23] if len(row) > 23 else False

        return MemoryNode(
            id=node_id,
            node_type=NodeType(row[1]),
            user_id=row[2],
            session_id=row[3],
            scope=Scope(row[4]),
            content=row[5],
            metadata=metadata,
            confidence=row[7],
            salience=row[8],
            lifecycle_state=LifecycleState(row[9]),
            valid_from=ensure_tz(row[10]),
            valid_to=ensure_tz(row[11]),
            superseded_by=superseded_by,
            evidence_refs=evidence_refs,
            created_at=ensure_tz(row[14]),
            updated_at=ensure_tz(row[15]),
            epistemic_type=raw_epistemic,
            source_type=raw_source,
            decay_profile=DecayProfile(raw_decay_profile),
            last_reinforced_at=ensure_tz(raw_last_reinforced) or datetime.now(timezone.utc),
            reinforcement_boost=raw_reinforcement_boost,
            salience_base=raw_salience_base,
            confidence_base=raw_confidence_base,
            pinned=bool(raw_pinned),
        )

    def _row_to_edge(self, row: tuple) -> MemoryEdge:
        """Convert a DuckDB row tuple to a MemoryEdge.

        Column order matches the CREATE TABLE definition:
        id, source_id, target_id, edge_type, user_id,
        confidence, valid_from, valid_to, provenance_event_id,
        metadata, created_at
        """

        def to_uuid(val: object) -> UUID:
            return val if isinstance(val, UUID) else UUID(str(val))

        def ensure_tz(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        # Parse provenance_event_id
        raw_provenance = row[8]
        provenance_event_id = (
            to_uuid(raw_provenance) if raw_provenance is not None else None
        )

        # Parse metadata
        raw_metadata = row[9]
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        else:
            metadata = raw_metadata

        return MemoryEdge(
            id=to_uuid(row[0]),
            source_id=to_uuid(row[1]),
            target_id=to_uuid(row[2]),
            edge_type=EdgeType(row[3]),
            user_id=row[4],
            confidence=row[5],
            valid_from=ensure_tz(row[6]),
            valid_to=ensure_tz(row[7]),
            provenance_event_id=provenance_event_id,
            metadata=metadata,
            created_at=ensure_tz(row[10]),
        )
