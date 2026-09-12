"""Restart-safe deferred indexing from immutable source events.

Events and work records are committed in one database transaction. The batch
size bounds memory usage, never the amount of acknowledged work. Tantivy
replacements share a durable commit before individual work acknowledgements.
A failed item remains pending and cannot prevent healthy batch items progressing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from prme.ingestion.errors import extraction_failure_code

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)


class DurableMaterializationQueue:
    def __init__(self, event_store: Any, *, batch_size: int = 500) -> None:
        self._store = event_store
        self._batch_size = batch_size
        self._debt = 0
        self._lock = asyncio.Lock()

    async def debt(self) -> int:
        self._debt = await self._store.materialization_count()
        return self._debt

    def debt_sync(self) -> int:
        """Last observed pending count; call debt() for a database refresh."""
        return self._debt

    def note_added(self) -> None:
        self._debt += 1

    async def process_one(self, engine: MemoryEngine, event) -> None:
        """Process one accepted source under the same local lock as draining."""
        async with self._lock:
            status = await self._store.processing_status(str(event.id), user_id=event.user_id)
            if status is not None and status.status == "complete":
                return
            try:
                await engine._materialize_event(event)
            except Exception as exc:
                await self._store.finish_materialization(str(event.id), error=extraction_failure_code(exc))
                raise
            else:
                await self._store.finish_materialization(str(event.id))
            finally:
                await self.debt()

    async def drain(
        self, engine: MemoryEngine, budget_ms: int = 100, *, user_id: str | None = None,
    ) -> int:
        if budget_ms <= 0:
            return 0
        async with self._lock:
            start = time.monotonic()
            completed = 0
            events = await self._store.pending_materializations(
                user_id=user_id, limit=self._batch_size,
            )
            if callable(getattr(engine._lexical_index, "replace_many", None)):
                remaining = max(0, budget_ms - (time.monotonic() - start) * 1000)
                outcomes = await engine._materialize_batch(events, budget_ms=remaining)
                for event, error in outcomes:
                    await self._store.finish_materialization(str(event.id), error=error)
                    if error is None:
                        completed += 1
                    else:
                        logger.warning("Materialization failed for %s (%s)", event.id, error)
                await self.debt()
                return completed
            for event in events:
                if (time.monotonic() - start) * 1000 >= budget_ms:
                    break
                try:
                    await engine._materialize_event(event)
                except Exception as exc:
                    # Retain only exception type, not provider responses which
                    # could contain secrets or source content.
                    reason = extraction_failure_code(exc)
                    await self._store.finish_materialization(str(event.id), error=reason)
                    logger.warning("Materialization failed for %s (%s)", event.id, reason)
                else:
                    await self._store.finish_materialization(str(event.id))
                    completed += 1
            await self.debt()
            return completed
