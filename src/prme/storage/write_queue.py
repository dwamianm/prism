"""Async write queue for DuckDB write serialization.

Serializes all storage backend write operations through a single
asyncio consumer, ensuring DuckDB single-writer safety under
concurrent HTTP load. Producers submit coroutine factories and
await Future-based responses.

Also provides WriteTracker for recording graph write operations
during event materialization and rolling them back on failure.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class WriteJob:
    """A unit of work for the write queue.

    Attributes:
        coro_factory: Callable that produces the coroutine to execute.
        future: Future resolved with the coroutine's result or exception.
        label: Optional label for logging and debugging.
    """

    coro_factory: Callable[[], Coroutine[Any, Any, Any]]
    future: asyncio.Future[Any]
    label: str = field(default="")


class WriteQueue:
    """Async write queue serializing all storage backend writes.

    Uses an asyncio.Queue with a single consumer coroutine to ensure
    only one write operation executes at a time. Producers call
    submit() and await the returned Future for the result.

    Args:
        maxsize: Maximum number of pending write jobs. When full,
            submit() blocks the producer, providing natural backpressure.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        self._queue: asyncio.Queue[WriteJob | None] = asyncio.Queue(
            maxsize=maxsize
        )
        self._consumer_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the single consumer coroutine.

        Idempotent -- calling start() when already running is a no-op.
        """
        if self._running:
            return
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume())
        logger.info("write_queue.started")

    async def stop(self) -> None:
        """Signal the consumer to stop and wait for completion.

        Sends a None sentinel to the queue and awaits the consumer task.
        Idempotent -- calling stop() when not running is a no-op.
        """
        if not self._running:
            return
        self._running = False
        await self._queue.put(None)
        if self._consumer_task is not None:
            await self._consumer_task
        logger.info("write_queue.stopped")

    async def submit(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
        label: str = "",
    ) -> Any:
        """Submit a write operation and await its result.

        The coroutine factory is called by the consumer (not the caller),
        ensuring all writes execute sequentially in the consumer task.

        Args:
            coro_factory: Callable that returns the coroutine to execute.
            label: Optional label for logging and debugging.

        Returns:
            The result of the executed coroutine.

        Raises:
            Exception: Any exception raised by the coroutine is propagated
                to the caller via the Future.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self._queue.put(
            WriteJob(coro_factory=coro_factory, future=future, label=label)
        )
        return await future

    async def _consume(self) -> None:
        """Process write jobs sequentially until sentinel received."""
        while True:
            job = await self._queue.get()
            if job is None:
                self._queue.task_done()
                break
            try:
                result = await job.coro_factory()
                if not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:
                logger.error(
                    "write_queue.job_failed",
                    label=job.label,
                    error=str(exc),
                )
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()

    @property
    def pending(self) -> int:
        """Number of pending jobs in the queue."""
        return self._queue.qsize()


class NoOpWriteQueue:
    """Passthrough write queue for PostgreSQL multi-writer mode.

    Satisfies the WriteQueue interface but executes coroutine factories
    immediately without serialization. PostgreSQL handles concurrency
    natively, so the single-writer queue is not needed.
    """

    async def start(self) -> None:
        """No-op -- no consumer to start."""

    async def stop(self) -> None:
        """No-op -- no consumer to stop."""

    async def submit(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
        label: str = "",
    ) -> Any:
        """Execute the coroutine factory immediately and return its result."""
        return await coro_factory()

    @property
    def pending(self) -> int:
        """Always zero -- no queue."""
        return 0


class WriteTracker:
    """Tracks graph write operations for rollback on failure.

    Records node and edge IDs created during event materialization.
    On failure, rollback() deletes all tracked artifacts in reverse
    order (edges first for referential integrity, then nodes) via
    the WriteQueue to maintain serialization.

    Note: Graph rollback only. Vector and lexical index entries for
    rolled-back nodes will be orphaned -- these stores currently lack
    delete methods. Orphaned entries are harmless (search returns an
    ID, get_node returns None, caller handles gracefully). Vector and
    lexical cleanup will be added when those delete methods are
    implemented.
    """

    def __init__(self) -> None:
        self._created_node_ids: list[str] = []
        self._created_edge_ids: list[str] = []

    def record_node(self, node_id: str) -> None:
        """Record a created node ID for potential rollback."""
        self._created_node_ids.append(node_id)

    def record_edge(self, edge_id: str) -> None:
        """Record a created edge ID for potential rollback."""
        self._created_edge_ids.append(edge_id)

    @property
    def node_ids(self) -> list[str]:
        """Return a copy of recorded node IDs."""
        return list(self._created_node_ids)

    @property
    def edge_ids(self) -> list[str]:
        """Return a copy of recorded edge IDs."""
        return list(self._created_edge_ids)

    async def rollback(self, graph_store: object, write_queue: WriteQueue) -> None:
        """Delete all tracked writes in reverse order via WriteQueue.

        Edges first (referential integrity), then nodes. Best-effort:
        logs warnings on individual delete failures but continues with
        remaining items.

        Args:
            graph_store: GraphStore instance with delete_node/delete_edge
                methods. Typed as object to avoid circular import with
                the GraphStore Protocol.
            write_queue: WriteQueue for serialized delete execution.
        """
        for edge_id in reversed(self._created_edge_ids):
            try:
                await write_queue.submit(
                    lambda eid=edge_id: graph_store.delete_edge(eid),  # type: ignore[attr-defined]
                    label=f"rollback.edge:{edge_id}",
                )
            except Exception:
                logger.warning(
                    "rollback.edge_failed", edge_id=edge_id, exc_info=True
                )

        for node_id in reversed(self._created_node_ids):
            try:
                await write_queue.submit(
                    lambda nid=node_id: graph_store.delete_node(nid),  # type: ignore[attr-defined]
                    label=f"rollback.node:{node_id}",
                )
            except Exception:
                logger.warning(
                    "rollback.node_failed", node_id=node_id, exc_info=True
                )

        if self._created_node_ids or self._created_edge_ids:
            logger.warning(
                "rollback.orphaned_indexes",
                node_count=len(self._created_node_ids),
                edge_count=len(self._created_edge_ids),
                detail="Vector and lexical index entries for rolled-back "
                "nodes may be orphaned (cleanup deferred to future phase)",
            )
