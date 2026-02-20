"""GraphWriter Protocol and WriteQueueGraphWriter implementation.

GraphWriter is a restricted write-only interface for graph operations,
providing structural (type-system-enforced) prevention of direct
GraphStore write access from ingestion components. EntityMerger and
SupersedenceDetector receive GraphWriter, not GraphStore, for writes.

WriteQueueGraphWriter routes all write operations through WriteQueue
to ensure DuckDB single-writer safety.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.storage.graph_store import GraphStore
from prme.storage.write_queue import WriteQueue, WriteTracker


@runtime_checkable
class GraphWriter(Protocol):
    """Write-only graph interface for ingestion components.

    Exposes only create_node, create_edge, and supersede -- no read
    methods. This is the structural prevention: mypy/pyright will flag
    any attempt to call query_nodes, get_edges, etc. on a GraphWriter.
    """

    async def create_node(self, node: MemoryNode) -> str:
        """Create a new node in the graph.

        Args:
            node: The MemoryNode to store.

        Returns:
            String UUID of the created node.
        """
        ...

    async def create_edge(self, edge: MemoryEdge) -> str:
        """Create a new edge between two nodes.

        Args:
            edge: The MemoryEdge to store.

        Returns:
            String UUID of the created edge.
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
                the supersedence.
        """
        ...


class WriteQueueGraphWriter:
    """GraphWriter that routes all writes through WriteQueue.

    Submits write operations to the WriteQueue for serialized execution,
    ensuring DuckDB single-writer safety. Optionally records created
    node and edge IDs in a WriteTracker for rollback on failure.

    Args:
        graph_store: The underlying GraphStore for actual writes.
        write_queue: The WriteQueue for write serialization.
        tracker: Optional WriteTracker to record created artifacts
            for rollback support.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        write_queue: WriteQueue,
        tracker: WriteTracker | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._write_queue = write_queue
        self._tracker = tracker

    async def create_node(self, node: MemoryNode) -> str:
        """Create a node via WriteQueue, recording in tracker if set.

        Uses lambda with default arg capture for the closure passed
        to write_queue.submit() (Phase 02 convention).

        Args:
            node: The MemoryNode to store.

        Returns:
            String UUID of the created node.
        """
        node_id: str = await self._write_queue.submit(
            lambda n=node: self._graph_store.create_node(n),
            label=f"graph.create_node:{node.id}",
        )
        if self._tracker is not None:
            self._tracker.record_node(node_id)
        return node_id

    async def create_edge(self, edge: MemoryEdge) -> str:
        """Create an edge via WriteQueue, recording in tracker if set.

        Uses lambda with default arg capture for the closure passed
        to write_queue.submit() (Phase 02 convention).

        Args:
            edge: The MemoryEdge to store.

        Returns:
            String UUID of the created edge.
        """
        edge_id: str = await self._write_queue.submit(
            lambda e=edge: self._graph_store.create_edge(e),
            label=f"graph.create_edge:{edge.id}",
        )
        if self._tracker is not None:
            self._tracker.record_edge(edge_id)
        return edge_id

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark a node as superseded via WriteQueue.

        No tracker recording needed -- supersede transitions state on
        existing nodes rather than creating new artifacts.

        Args:
            old_node_id: Node being replaced.
            new_node_id: Replacement node.
            evidence_id: Optional event ID providing evidence.
        """
        await self._write_queue.submit(
            lambda: self._graph_store.supersede(
                old_node_id, new_node_id, evidence_id=evidence_id
            ),
            label=f"graph.supersede:{old_node_id}->{new_node_id}",
        )
