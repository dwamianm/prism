"""Async write queue for DuckDB write serialization.

Serializes all storage backend write operations through a single
asyncio consumer, ensuring DuckDB single-writer safety under
concurrent HTTP load. Producers submit coroutine factories and
await Future-based responses.
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
