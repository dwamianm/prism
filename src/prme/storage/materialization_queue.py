"""In-memory materialization queue for dual-stream ingestion (issue #25).

Queues deferred graph materialization work from the fast ingestion path
(ingest_fast). Items are drained during retrieve() or organize() calls
via opportunistic maintenance. The queue is in-memory only — pending
items are lost on restart, which is acceptable because events are already
persisted and can be replayed.

Thread safety is provided via an asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)


@dataclass
class PendingMaterialization:
    """A deferred materialization work item.

    Attributes:
        event_id: String UUID of the already-persisted event.
        content: Raw message text.
        user_id: Owner user ID.
        role: Message role ('user', 'assistant', or 'system').
        session_id: Optional session identifier.
        scope: Memory scope string (e.g., 'personal', 'project', 'org').
        metadata: Optional structured metadata from the original ingest.
        queued_at: Timestamp when the item was queued.
    """

    event_id: str
    content: str
    user_id: str
    role: str = "user"
    session_id: str | None = None
    scope: str | None = None
    metadata: dict[str, Any] | None = None
    queued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MaterializationQueue:
    """In-memory queue for deferred graph materialization.

    The fast ingestion path (ingest_fast) persists events and updates the
    vector index immediately, then queues graph materialization work here.
    The drain() method processes pending items within a time budget,
    called during retrieve() or organize().

    Args:
        max_size: Maximum number of pending items. When full, oldest
            items are dropped with a warning.
    """

    def __init__(self, max_size: int = 500) -> None:
        self._pending: deque[PendingMaterialization] = deque()
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def add(
        self,
        event_id: str,
        content: str,
        user_id: str,
        role: str = "user",
        session_id: str | None = None,
        scope: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Queue a materialization work item.

        If the queue is at max capacity, the oldest item is dropped
        and a warning is logged.

        Args:
            event_id: String UUID of the persisted event.
            content: Raw message text.
            user_id: Owner user ID.
            role: Message role.
            session_id: Optional session identifier.
            scope: Memory scope string.
            metadata: Optional structured metadata.
        """
        async with self._lock:
            if len(self._pending) >= self._max_size:
                dropped = self._pending.popleft()
                logger.warning(
                    "materialization_queue.overflow: dropping oldest item "
                    "event_id=%s to make room (max_size=%d)",
                    dropped.event_id,
                    self._max_size,
                )

            self._pending.append(
                PendingMaterialization(
                    event_id=event_id,
                    content=content,
                    user_id=user_id,
                    role=role,
                    session_id=session_id,
                    scope=scope,
                    metadata=metadata,
                )
            )
            logger.debug(
                "materialization_queue.added event_id=%s (depth=%d)",
                event_id,
                len(self._pending),
            )

    async def drain(self, engine: MemoryEngine, budget_ms: int = 100) -> int:
        """Process pending materialization items within a time budget.

        Pops items from the front of the queue and calls engine.store()
        for each to perform full graph materialization. Stops when the
        budget is exhausted or the queue is empty.

        Args:
            engine: MemoryEngine instance for store() calls.
            budget_ms: Maximum milliseconds to spend draining.

        Returns:
            Number of items successfully materialized.
        """
        start = time.monotonic()
        materialized = 0

        while True:
            # Check budget
            elapsed_ms = (time.monotonic() - start) * 1000.0
            if elapsed_ms >= budget_ms:
                break

            # Pop next item (under lock)
            async with self._lock:
                if not self._pending:
                    break
                item = self._pending.popleft()

            # Materialize via engine.store() — this does full graph write
            try:
                store_kwargs: dict[str, Any] = {
                    "user_id": item.user_id,
                    "role": item.role,
                    "metadata": item.metadata,
                }
                if item.session_id is not None:
                    store_kwargs["session_id"] = item.session_id
                if item.scope is not None:
                    store_kwargs["scope"] = item.scope
                await engine.store(item.content, **store_kwargs)
                materialized += 1
                logger.debug(
                    "materialization_queue.drained event_id=%s",
                    item.event_id,
                )
            except Exception:
                logger.warning(
                    "materialization_queue.drain_failed event_id=%s; "
                    "re-queuing at front",
                    item.event_id,
                    exc_info=True,
                )
                # Re-queue at front for retry on next drain pass
                async with self._lock:
                    self._pending.appendleft(item)
                break  # Stop draining on failure

        if materialized > 0:
            logger.info(
                "materialization_queue.drain_complete: materialized=%d, "
                "remaining=%d, elapsed_ms=%.1f",
                materialized,
                await self.debt(),
                (time.monotonic() - start) * 1000.0,
            )

        return materialized

    async def debt(self) -> int:
        """Return the count of pending materialization items.

        Returns:
            Number of items awaiting materialization.
        """
        async with self._lock:
            return len(self._pending)

    def debt_sync(self) -> int:
        """Return the count of pending items (synchronous, no lock).

        This is a best-effort read for property access and metrics.
        It does not acquire the lock, so the count may be slightly
        stale under concurrent access.

        Returns:
            Approximate number of items awaiting materialization.
        """
        return len(self._pending)
