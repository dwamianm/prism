"""GraphStore Protocol defining full-spec graph operations for PRME.

The GraphStore is a Protocol (structural typing) so that alternative
implementations (e.g., RyuGraph, pure SQL) can satisfy it without
inheriting from a base class. This is the primary escape hatch for
DuckPGQ limitations.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.types import EdgeType, LifecycleState, NodeType, Scope


@runtime_checkable
class GraphStore(Protocol):
    """Full-spec graph store interface for PRME.

    Covers: node/edge CRUD, graph traversal (neighborhood, shortest path),
    supersedence chain traversal, and lifecycle transitions.

    All methods are async. Implementations wrap synchronous database
    calls with asyncio.to_thread().

    All queries enforce user_id scoping to prevent cross-user data leakage.
    Query defaults filter to active lifecycle states (tentative + stable)
    unless explicitly overridden.
    """

    # --- Node Operations ---

    async def create_node(self, node: MemoryNode) -> str:
        """Create a new node in the graph.

        Args:
            node: The MemoryNode to store.

        Returns:
            String UUID of the created node.
        """
        ...

    async def get_node(
        self,
        node_id: str,
        *,
        include_superseded: bool = False,
    ) -> MemoryNode | None:
        """Retrieve a node by ID.

        Args:
            node_id: String UUID of the node.
            include_superseded: If False (default), returns None for
                superseded/archived nodes.

        Returns:
            The MemoryNode if found and visible, None otherwise.
        """
        ...

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
        """Query nodes with filters.

        Args:
            node_type: Filter by node type.
            user_id: Filter by owner user.
            scope: Filter by memory scope.
            lifecycle_states: Filter by lifecycle states. Defaults to
                [TENTATIVE, STABLE] (active only).
            valid_at: Temporal filter -- return nodes valid at this time.
            min_confidence: Minimum confidence threshold.
            limit: Maximum results to return.

        Returns:
            List of matching MemoryNodes.
        """
        ...

    # --- Edge Operations ---

    async def create_edge(self, edge: MemoryEdge) -> str:
        """Create a new edge between two nodes.

        Args:
            edge: The MemoryEdge to store.

        Returns:
            String UUID of the created edge.
        """
        ...

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
            source_id: Filter by source node ID.
            target_id: Filter by target node ID.
            edge_type: Filter by edge type.
            valid_at: Temporal filter.
            min_confidence: Minimum confidence threshold.

        Returns:
            List of matching MemoryEdges.
        """
        ...

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

        Args:
            node_id: Starting node ID.
            max_hops: Maximum number of hops (default 2).
            edge_types: Only traverse edges of these types.
            valid_at: Temporal filter for edges.
            min_confidence: Minimum edge confidence.
            include_superseded: Include superseded/archived nodes.

        Returns:
            List of reachable MemoryNodes (excluding the starting node).
        """
        ...

    async def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        edge_types: list[EdgeType] | None = None,
    ) -> list[str] | None:
        """Find the shortest path between two nodes.

        Args:
            source_id: Starting node ID.
            target_id: Target node ID.
            edge_types: Only traverse edges of these types.

        Returns:
            List of node IDs forming the shortest path (including
            source and target), or None if no path exists.
        """
        ...

    # --- Supersedence ---

    async def get_supersedence_chain(
        self,
        node_id: str,
        *,
        direction: str = "forward",
    ) -> list[MemoryNode]:
        """Traverse the supersedence chain from a node.

        Args:
            node_id: Starting node ID.
            direction: "forward" = what replaced this node,
                       "backward" = what this node replaced.

        Returns:
            Ordered list of MemoryNodes in the chain.
        """
        ...

    # --- Lifecycle Transitions ---

    async def promote(self, node_id: str) -> None:
        """Promote a tentative node to stable.

        Args:
            node_id: Node to promote.

        Raises:
            ValueError: If the transition is invalid.
        """
        ...

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
            evidence_id: Optional event ID providing evidence for
                the supersedence. Required for automated transitions,
                optional for manual.

        Raises:
            ValueError: If the transition is invalid.
        """
        ...

    async def archive(self, node_id: str) -> None:
        """Archive a node (terminal state).

        Args:
            node_id: Node to archive.

        Raises:
            ValueError: If the transition is invalid.
        """
        ...

    # --- Cleanup / Rollback ---

    async def delete_node(self, node_id: str) -> None:
        """Delete a node by ID.

        Used for rollback cleanup when event materialization fails.
        Also needed for Phase 5 archival. No-op if the node does
        not exist.

        Args:
            node_id: String UUID of the node to delete.
        """
        ...

    async def delete_edge(self, edge_id: str) -> None:
        """Delete an edge by ID.

        Used for rollback cleanup when event materialization fails.
        Also needed for Phase 5 archival. No-op if the edge does
        not exist.

        Args:
            edge_id: String UUID of the edge to delete.
        """
        ...
