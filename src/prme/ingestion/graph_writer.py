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
from prme.storage._threading import run_async_to_completion
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

    async def contradict(
        self,
        node_a_id: str,
        node_b_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark two nodes as contradicting each other.

        Args:
            node_a_id: First conflicting node (typically the existing/older node).
            node_b_id: Second conflicting node (typically the new/incoming node).
            evidence_id: Optional event ID providing evidence.
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
        defer_supersedence: bool = False,
    ) -> None:
        self._graph_store = graph_store
        self._write_queue = write_queue
        self._tracker = tracker
        self._defer_supersedence = defer_supersedence
        self._replacements: list[tuple[str, str, str | None]] = []
        self._committed = False

    @property
    def committed(self) -> bool:
        """Whether the final materialization write finished, even after cancellation."""
        return self._committed

    async def create_node(self, node: MemoryNode) -> str:
        """Create a node via WriteQueue, recording in tracker if set.

        Records the result inside the protected queued operation, so caller
        cancellation cannot lose the identity of a completed write.

        Args:
            node: The MemoryNode to store.

        Returns:
            String UUID of the created node.
        """
        async def create_and_record() -> str:
            node_id = await self._graph_store.create_node(node)
            if self._tracker is not None:
                self._tracker.record_node(node_id)
            return node_id
        return await run_async_to_completion(self._write_queue.submit(
            create_and_record, label=f"graph.create_node:{node.id}",
        ))

    async def create_edge(self, edge: MemoryEdge) -> str:
        """Create an edge via WriteQueue, recording in tracker if set.

        Records the result inside the protected queued operation, so caller
        cancellation cannot lose the identity of a completed write.

        Args:
            edge: The MemoryEdge to store.

        Returns:
            String UUID of the created edge.
        """
        async def create_and_record() -> str:
            edge_id = await self._graph_store.create_edge(edge)
            if self._tracker is not None:
                self._tracker.record_edge(edge_id)
            return edge_id
        return await run_async_to_completion(self._write_queue.submit(
            create_and_record, label=f"graph.create_edge:{edge.id}",
        ))

    async def supersede(
        self,
        old_node_id: str,
        new_node_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark a node as superseded via WriteQueue.

        In deferred mode, commit replacements only after materialization has
        completed. This prevents rollback from deleting a live replacement.

        Args:
            old_node_id: Node being replaced.
            new_node_id: Replacement node.
            evidence_id: Optional event ID providing evidence.
        """
        if self._defer_supersedence:
            self._replacements.append((old_node_id, new_node_id, evidence_id))
            return
        await self._write_queue.submit(
            lambda: self._graph_store.supersede(
                old_node_id, new_node_id, evidence_id=evidence_id
            ),
            label=f"graph.supersede:{old_node_id}->{new_node_id}",
        )

    async def commit_supersedences(self) -> None:
        """Atomically apply deferred transitions as the final ingestion write."""
        replacements = list(self._replacements)
        async def commit_and_record() -> None:
            if replacements:
                await self._graph_store.supersede_many(replacements)
            self._replacements.clear()
            self._committed = True
        await run_async_to_completion(self._write_queue.submit(
            commit_and_record, label="graph.supersede_many",
        ))

    async def contradict(
        self,
        node_a_id: str,
        node_b_id: str,
        *,
        evidence_id: str | None = None,
    ) -> None:
        """Mark two nodes as contradicting via WriteQueue.

        No tracker recording needed -- contradict transitions state on
        existing nodes rather than creating new artifacts.

        Args:
            node_a_id: First conflicting node (typically the existing/older node).
            node_b_id: Second conflicting node (typically the new/incoming node).
            evidence_id: Optional event ID providing evidence.
        """
        await self._write_queue.submit(
            lambda: self._graph_store.contradict(
                node_a_id, node_b_id, evidence_id=evidence_id
            ),
            label=f"graph.contradict:{node_a_id}<->{node_b_id}",
        )
