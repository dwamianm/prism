"""Synchronous MemoryClient — the simplest way to use PRME.

Wraps the async MemoryEngine in a synchronous API with a dedicated
background event loop thread. Works everywhere, including inside
Jupyter notebooks, FastAPI apps, and other async contexts.

Usage::

    from prme import MemoryClient

    with MemoryClient("./my_memories") as client:
        client.store("Alice prefers dark mode", user_id="alice")
        results = client.retrieve("preferences?", user_id="alice")
        for r in results.results:
            print(r.node.content)
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
import warnings
from datetime import datetime
from typing import Any

from prme.config import PRMEConfig
from prme.types import NodeType, Scope

logger = logging.getLogger(__name__)


def config_from_directory(directory: str) -> PRMEConfig:
    """Create a PRMEConfig with all paths resolved inside *directory*.

    Creates the directory (and lexical sub-directory) if they don't exist.

    Args:
        directory: Path to the memory directory.

    Returns:
        A PRMEConfig with db_path, vector_path, and lexical_path
        pointing into the given directory.
    """
    abs_dir = os.path.abspath(directory)
    os.makedirs(abs_dir, exist_ok=True)
    lexical_dir = os.path.join(abs_dir, "lexical_index")
    os.makedirs(lexical_dir, exist_ok=True)
    return PRMEConfig(
        db_path=os.path.join(abs_dir, "memory.duckdb"),
        vector_path=os.path.join(abs_dir, "vectors.usearch"),
        lexical_path=lexical_dir,
    )


class MemoryClient:
    """Synchronous wrapper around :class:`~prme.storage.engine.MemoryEngine`.

    Manages its own event loop on a dedicated daemon thread so it works
    regardless of whether the caller is already inside an async context.

    Args:
        directory: Path to the memory directory. Created if it doesn't exist.
        config: Optional PRMEConfig override. When provided, *directory* is
            ignored and the config is used as-is.

    Example::

        with MemoryClient("./my_memories") as client:
            client.store("Alice prefers dark mode", user_id="alice")
            response = client.retrieve("preferences?", user_id="alice")
    """

    def __init__(
        self,
        directory: str = ".",
        *,
        config: PRMEConfig | None = None,
    ) -> None:
        self._config = config or config_from_directory(directory)
        self._closed = False

        # Spin up a dedicated event loop on a daemon thread.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="prme-client",
        )
        self._thread.start()

        # Create the engine on that loop.
        from prme.storage.engine import MemoryEngine

        future = asyncio.run_coroutine_threadsafe(
            MemoryEngine.create(self._config), self._loop
        )
        self._engine: MemoryEngine = future.result(timeout=60)

        # Register atexit so we clean up if the user forgets close().
        atexit.register(self._atexit_close)

    # --- Internal helpers ---

    def _run(self, coro: Any) -> Any:
        """Submit a coroutine to the background loop and block for result."""
        if self._closed:
            raise RuntimeError("MemoryClient is closed")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def _atexit_close(self) -> None:
        """Best-effort cleanup at interpreter exit."""
        if not self._closed:
            try:
                self.close()
            except Exception:
                pass

    # --- Context manager ---

    def __enter__(self) -> "MemoryClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- Public API ---

    def store(
        self,
        content: str,
        *,
        user_id: str,
        session_id: str | None = None,
        role: str = "user",
        node_type: NodeType = NodeType.NOTE,
        scope: Scope = Scope.PERSONAL,
        metadata: dict | None = None,
        confidence: float | None = None,
        event_time: datetime | None = None,
    ) -> str:
        """Store a memory. Returns the event UUID."""
        return self._run(
            self._engine.store(
                content,
                user_id=user_id,
                session_id=session_id,
                role=role,
                node_type=node_type,
                scope=scope,
                metadata=metadata,
                confidence=confidence,
                event_time=event_time,
            )
        )

    def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        scope: Scope | list[Scope] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        knowledge_at: datetime | None = None,
        token_budget: int | None = None,
    ) -> Any:
        """Retrieve memories matching a query. Returns RetrievalResponse."""
        return self._run(
            self._engine.retrieve(
                query,
                user_id=user_id,
                scope=scope,
                time_from=time_from,
                time_to=time_to,
                knowledge_at=knowledge_at,
                token_budget=token_budget,
            )
        )

    def ingest(
        self,
        content: str,
        *,
        user_id: str,
        role: str = "user",
        session_id: str | None = None,
        scope: Scope = Scope.PERSONAL,
    ) -> str:
        """Ingest content with LLM extraction. Returns event UUID."""
        return self._run(
            self._engine.ingest(
                content,
                user_id=user_id,
                role=role,
                session_id=session_id,
                wait_for_extraction=True,
                scope=scope,
            )
        )

    def ingest_batch(
        self,
        messages: list[dict],
        *,
        user_id: str,
        session_id: str | None = None,
        scope: Scope = Scope.PERSONAL,
    ) -> list[str]:
        """Ingest a batch of messages. Returns list of event UUIDs."""
        return self._run(
            self._engine.ingest_batch(
                messages,
                user_id=user_id,
                session_id=session_id,
                wait_for_extraction=True,
                scope=scope,
            )
        )

    def get_node(self, node_id: str) -> Any:
        """Get a single node by ID. Returns MemoryNode or None."""
        return self._run(self._engine.get_node(node_id))

    def query_nodes(self, **kwargs: Any) -> list[Any]:
        """Query nodes with filters. Returns list of MemoryNode."""
        return self._run(self._engine.query_nodes(**kwargs))

    def get_events(
        self,
        user_id: str,
        *,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """Retrieve events for a user. Returns list of Event."""
        return self._run(
            self._engine.get_events(
                user_id,
                session_id=session_id,
                limit=limit,
                offset=offset,
            )
        )

    def consolidate_knowledge(
        self,
        *,
        user_id: str,
        entity_names: list[str] | None = None,
    ) -> int:
        """Build entity knowledge profiles. Returns count of profiles created."""
        return self._run(
            self._engine.consolidate_knowledge(
                user_id=user_id,
                entity_names=entity_names,
            )
        )

    def organize(
        self,
        *,
        user_id: str | None = None,
        jobs: list[str] | None = None,
        budget_ms: int = 5000,
    ) -> Any:
        """Run organizer jobs. Returns OrganizeResult."""
        return self._run(
            self._engine.organize(
                user_id=user_id,
                jobs=jobs,
                budget_ms=budget_ms,
            )
        )

    def close(self) -> None:
        """Shut down the engine and background event loop."""
        if self._closed:
            return
        self._closed = True

        try:
            atexit.unregister(self._atexit_close)
        except Exception:
            pass

        # Close the engine on the background loop.
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._engine.close(), self._loop
            )
            future.result(timeout=30)
        except Exception:
            logger.warning("Error closing engine", exc_info=True)

        # Stop the event loop and join the thread.
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def __del__(self) -> None:
        if not self._closed:
            warnings.warn(
                "MemoryClient was not closed. Use 'with MemoryClient(...) as client:' "
                "or call client.close() explicitly.",
                ResourceWarning,
                stacklevel=2,
            )
