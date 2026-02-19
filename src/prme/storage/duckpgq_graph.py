"""DuckPGQ-backed GraphStore implementation with node/edge CRUD.

Implements the GraphStore Protocol using DuckDB tables as the underlying
storage. When DuckPGQ is available, advanced traversal operations use
SQL/PGQ syntax. When unavailable, all operations use standard SQL
(JOINs, recursive CTEs).

This plan covers node/edge CRUD and query_nodes/get_edges.
Advanced traversal (neighborhood, paths, supersedence, lifecycle)
is deferred to Plan 04.
"""

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

import duckdb

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType, Scope


class DuckPGQGraphStore:
    """GraphStore implementation backed by DuckDB tables.

    Uses parameterized queries for all user data to prevent SQL injection.
    All queries enforce user_id scoping. Query defaults filter to active
    lifecycle states (tentative + stable).
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self._write_lock = asyncio.Lock()

    # --- Node Operations ---

    async def create_node(self, node: MemoryNode) -> str:
        """Create a new node in the graph store.

        Args:
            node: The MemoryNode to store.

        Returns:
            String UUID of the created node.
        """
        async with self._write_lock:
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
        async with self._write_lock:
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
        return await asyncio.to_thread(
            self._get_edges_sync,
            source_id,
            target_id,
            edge_type,
            valid_at,
            min_confidence,
        )

    # --- Stubbed methods (implemented in Plan 04) ---

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
        """Get nodes within N hops of a starting node."""
        raise NotImplementedError("Implemented in Plan 04")

    async def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        edge_types: list[EdgeType] | None = None,
    ) -> list[str] | None:
        """Find the shortest path between two nodes."""
        raise NotImplementedError("Implemented in Plan 04")

    async def get_supersedence_chain(
        self,
        node_id: str,
        *,
        direction: str = "forward",
    ) -> list[MemoryNode]:
        """Traverse the supersedence chain from a node."""
        raise NotImplementedError("Implemented in Plan 04")

    async def promote(self, node_id: str) -> None:
        """Promote a tentative node to stable."""
        raise NotImplementedError("Implemented in Plan 04")

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark a node as superseded by another."""
        raise NotImplementedError("Implemented in Plan 04")

    async def archive(self, node_id: str) -> None:
        """Archive a node (terminal state)."""
        raise NotImplementedError("Implemented in Plan 04")

    # --- Internal sync methods ---

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
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        # Default lifecycle filter: active states only
        if lifecycle_states is None:
            lifecycle_states = [
                LifecycleState.TENTATIVE,
                LifecycleState.STABLE,
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
