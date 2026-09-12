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
from collections.abc import Coroutine, Iterator
from typing import Any, Literal, TypeVar
from uuid import UUID

from prme.config import PRMEConfig
from prme.models.relevance import RelevanceRecord, RelevanceSubmission, RetrievalReceipt
from prme.models.learning import LearningConfig, LearningEvaluation, RankingMultipliers
from prme.retrieval.config import ScoringWeights
from prme.retrieval.scope import ScopeInput
from prme.models.processing import ProcessingResult, ProcessingStatus
from prme.models.profile import ProfileJobStatus, ProfileProcessingResult
from prme.models.extraction import ExtractionRecord
from prme.models.extraction_work import ExtractionStatus, ExtractionProcessingResult
from prme.types import EpistemicType, LifecycleState, NodeType, RepresentationLevel, RetrievalMode, Scope, SourceType
from prme.models import Event, MemoryNode
from prme.organizer.models import OrganizeResult
from prme.retrieval.models import RetrievalResponse

_Result = TypeVar("_Result")

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
        # A failed constructor must not leave a usable client or emit a
        # destructor error that hides the original configuration failure.
        self._closed = True
        self._config = config or config_from_directory(directory)

        # Spin up a dedicated event loop on a daemon thread.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="prme-client",
        )
        self._thread.start()

        # Create the engine on that loop.
        from prme.storage.engine import MemoryEngine

        future = asyncio.run_coroutine_threadsafe(
            MemoryEngine.create(self._config), self._loop
        )
        try:
            self._engine: MemoryEngine = future.result(timeout=60)
        except BaseException:
            future.cancel()
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            raise
        self._closed = False

        # Register atexit so we clean up if the user forgets close().
        atexit.register(self._atexit_close)

    def _run_loop(self) -> None:
        """Own and close the worker loop even when engine creation fails."""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    # --- Internal helpers ---

    def _run(self, coro: Coroutine[Any, Any, _Result]) -> _Result:
        """Submit a coroutine to the background loop and block for result."""
        if self._closed:
            coro.close()
            raise RuntimeError("MemoryClient is closed")
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:
            # Submission can fail if shutdown races this call. The loop
            # never took ownership, so release the unawaited coroutine.
            coro.close()
            raise
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
        epistemic_type: EpistemicType | None = None,
        source_type: SourceType | None = None,
        event_time: datetime | None = None,
        ttl_days: int | None = ...,
    ) -> str:
        """Store a memory. Returns the event UUID.

        Classification overrides and TTL match the async engine. Omit ttl_days
        to use the configured per-type default, or pass None for no expiry.
        """
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
                epistemic_type=epistemic_type,
                source_type=source_type,
                event_time=event_time,
                ttl_days=ttl_days,
            )
        )

    def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        scope: ScopeInput = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        reference_time: datetime | None = None,
        knowledge_at: datetime | None = None,
        event_time_from: datetime | None = None,
        event_time_to: datetime | None = None,
        token_budget: int | None = None,
        min_score: float | None = None,
        limit: int | None = None,
        weights: ScoringWeights | None = None,
        ranking_multipliers: RankingMultipliers | None = None,
        min_fidelity: RepresentationLevel | None = None,
        retrieval_mode: RetrievalMode = RetrievalMode.DEFAULT,
        include_cross_scope: bool = True,
    ) -> RetrievalResponse:
        """Retrieve memories with engine-equivalent temporal and ranking controls.

        ranking_multipliers is an explicit request-only trial. It does not
        activate a profile or change the next request's scoring configuration.
        """
        return self._run(
            self._engine.retrieve(
                query,
                user_id=user_id,
                scope=scope,
                time_from=time_from,
                time_to=time_to,
                reference_time=reference_time,
                knowledge_at=knowledge_at,
                event_time_from=event_time_from, event_time_to=event_time_to,
                token_budget=token_budget,
                min_score=min_score, limit=limit,
                weights=weights, ranking_multipliers=ranking_multipliers, min_fidelity=min_fidelity,
                retrieval_mode=retrieval_mode, include_cross_scope=include_cross_scope,
            )
        )

    def ingest(
        self,
        content: str,
        *,
        user_id: str,
        role: str = "user",
        session_id: str | None = None,
        metadata: dict | None = None,
        event_time: datetime | None = None,
        scope: Scope = Scope.PERSONAL,
    ) -> str:
        """Ingest with extraction; event_time is the timezone-aware source clock."""
        return self._run(
            self._engine.ingest(
                content,
                user_id=user_id,
                role=role,
                session_id=session_id,
                metadata=metadata,
                event_time=event_time,
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

    def promote(self, node_id: str, *, user_id: str | None = None) -> None:
        """Promote a tentative node; supply user_id to enforce ownership."""
        self._run(self._engine.promote(node_id, user_id=user_id))

    def archive(self, node_id: str, *, user_id: str | None = None) -> None:
        """Retire a node from retrieval while retaining its durable source."""
        self._run(self._engine.archive(node_id, user_id=user_id))

    def get_retrieval_receipt(self, request_id: str, *, user_id: str) -> RetrievalReceipt | None:
        return self._run(self._engine.get_retrieval_receipt(request_id, user_id=user_id))

    def record_relevance(self, submission: RelevanceSubmission, *, user_id: str) -> RelevanceRecord:
        """Save explicit relevance labels; reuse feedback_id when retrying."""
        return self._run(self._engine.record_relevance(submission, user_id=user_id))

    def get_relevance(self, feedback_id: str, *, user_id: str) -> RelevanceRecord | None:
        return self._run(self._engine.get_relevance(feedback_id, user_id=user_id))

    def list_relevance(self, *, user_id: str, limit: int = 100,
                       after_id: str | None = None) -> list[RelevanceRecord]:
        return self._run(self._engine.list_relevance(user_id=user_id, limit=limit, after_id=after_id))

    def evaluate_learning(self, *, user_id: str, scopes: list[Scope] | None = None,
                          surface: Literal["results", "context"] = "results", config: LearningConfig | None = None,
                          query_groups: dict[UUID, str] | None = None,
                          max_records: int = 10000) -> LearningEvaluation:
        """Fit and evaluate a scoped offline proposal; leave active weights unchanged."""
        return self._run(self._engine.evaluate_learning(user_id=user_id, scopes=scopes,
            surface=surface, config=config, query_groups=query_groups, max_records=max_records))

    def get_node(self, node_id: str, *, user_id: str | None = None, include_superseded: bool = False) -> MemoryNode | None:
        """Get a single node by ID. Returns MemoryNode or None."""
        return self._run(self._engine.get_node(node_id, user_id=user_id, include_superseded=include_superseded))

    def ingest_fast(
        self, content: str, *, user_id: str, role: str = "user",
        session_id: str | None = None, metadata: dict | None = None,
        scope: Scope = Scope.PERSONAL, event_time: datetime | None = None,
    ) -> str:
        """Durably accept a raw event; indexing resumes on processing/retrieval."""
        return self._run(self._engine.ingest_fast(
            content, user_id=user_id, role=role, session_id=session_id,
            metadata=metadata, scope=scope, event_time=event_time,
        ))

    def extraction_status(self, event_id: str, *, user_id: str) -> ExtractionStatus | None:
        """Inspect durable extraction separately from raw-source indexing."""
        return self._run(self._engine.extraction_status(event_id, user_id=user_id))

    def retry_extraction(self, event_id: str, *, user_id: str, replan: bool = False) -> ExtractionStatus | None:
        """Queue an owned extraction retry; execute it with process_extractions()."""
        return self._run(self._engine.retry_extraction(event_id, user_id=user_id, replan=replan))

    def process_extractions(self, *, user_id: str, limit: int = 100,
                            budget_ms: float = 5000) -> ExtractionProcessingResult:
        """Run due owned extraction jobs within a cooperative time budget."""
        return self._run(self._engine.process_extractions(user_id=user_id, limit=limit, budget_ms=budget_ms))

    def processing_status(self, event_id: str, *, user_id: str) -> ProcessingStatus | None:
        """Read owned source/index status for store(), ingest_fast() or ingest()."""
        return self._run(self._engine.processing_status(event_id, user_id=user_id))

    def process_pending(self, *, user_id: str, budget_ms: int = 1000) -> ProcessingResult:
        """Process one bounded batch of source/index work; failures stay pending."""
        return self._run(self._engine.process_pending(user_id=user_id, budget_ms=budget_ms))

    def query_nodes(self, **kwargs: Any) -> list[MemoryNode]:
        """Query nodes with filters. Returns list of MemoryNode."""
        return self._run(self._engine.query_nodes(**kwargs))

    def scan_nodes(
        self, *, user_id: str, scope: Scope | None = None,
        node_type: NodeType | None = None,
        lifecycle_states: list[LifecycleState] | None = None,
        after_id: str | None = None, limit: int = 100,
    ) -> list[MemoryNode]:
        """Read a scoped page in immutable ID order; no semantic ranking."""
        return self._run(self._engine.scan_nodes(
            user_id=user_id, scope=scope, node_type=node_type,
            lifecycle_states=lifecycle_states, after_id=after_id, limit=limit,
        ))

    def iter_nodes(
        self, *, user_id: str, scope: Scope | None = None,
        node_type: NodeType | None = None,
        lifecycle_states: list[LifecycleState] | None = None, batch_size: int = 100,
    ) -> Iterator[MemoryNode]:
        """Stream all matching nodes in bounded pages; not a transaction snapshot."""
        cursor = None
        while True:
            page = self.scan_nodes(
                user_id=user_id, scope=scope, node_type=node_type,
                lifecycle_states=lifecycle_states, after_id=cursor, limit=batch_size,
            )
            yield from page
            if len(page) < batch_size:
                break
            cursor = str(page[-1].id)

    def get_event(self, event_id: str, *, user_id: str | None = None) -> Event | None:
        """Read original source content, optionally enforcing owner identity."""
        return self._run(self._engine.get_event(event_id, user_id=user_id))

    def get_extraction(self, event_id: str, *, user_id: str) -> ExtractionRecord | None:
        """Read saved grounded output; this does not imply graph completion."""
        return self._run(self._engine.get_extraction(event_id, user_id=user_id))

    def get_event_nodes(self, event_id: str, *, user_id: str) -> list[MemoryNode]:
        """Read every scoped node citing this event, regardless of lifecycle."""
        return self._run(self._engine.get_event_nodes(event_id, user_id=user_id))

    def get_events(
        self,
        user_id: str,
        *,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
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
        scope: Scope | None = None,
        entity_names: list[str] | None = None,
        max_profile_tokens: int = 500,
    ) -> int:
        """Build scoped profiles under an exact token limit, matching the async API."""
        return self._run(
            self._engine.consolidate_knowledge(
                user_id=user_id,
                scope=scope,
                entity_names=entity_names,
                max_profile_tokens=max_profile_tokens,
            )
        )

    def profile_jobs(self, *, user_id: str, scope: Scope | None = None,
                     status: str = 'pending', limit: int = 100) -> list[ProfileJobStatus]:
        """Inspect owned profile preparations without exposing source text."""
        return self._run(self._engine.profile_jobs(
            user_id=user_id, scope=scope, status=status, limit=limit))

    def resume_profile(self, profile_id: str, *, user_id: str) -> str | None:
        """Resume saved profile inputs without repeating model inference."""
        return self._run(self._engine.resume_profile(profile_id, user_id=user_id))

    def process_profiles(self, *, user_id: str, scope: Scope | None = None,
                         limit: int = 100, budget_ms: float = 5000) -> ProfileProcessingResult:
        """Process owned preparations with a cooperative budget between jobs."""
        return self._run(self._engine.process_profiles(
            user_id=user_id, scope=scope, limit=limit, budget_ms=budget_ms))

    def organize(
        self,
        *,
        user_id: str | None = None,
        jobs: list[str] | None = None,
        budget_ms: int = 5000,
    ) -> OrganizeResult:
        """Run organizer jobs. Returns OrganizeResult."""
        return self._run(
            self._engine.organize(
                user_id=user_id,
                jobs=jobs,
                budget_ms=budget_ms,
            )
        )

    def close(self) -> None:
        """Shut down the engine and background event loop.

        Raises:
            EncryptionError: If encryption-at-rest is enabled and the pack
                could not be re-encrypted on close. The pack is plaintext on
                disk in that case, so the failure is surfaced rather than
                swallowed (fail closed). Loop and thread teardown still
                completes before the error is raised. Other (non-encryption)
                shutdown errors are logged and swallowed as best effort.
        """
        if self._closed:
            return
        self._closed = True

        try:
            atexit.unregister(self._atexit_close)
        except Exception:
            pass

        # Close the engine on the background loop. Capture an encryption
        # failure so the pack-is-plaintext signal is not lost, but still
        # finish tearing down the loop/thread before re-raising it.
        from prme.storage.encryption import EncryptionError

        encryption_error: EncryptionError | None = None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._engine.close(), self._loop
            )
            future.result(timeout=30)
        except EncryptionError as exc:
            encryption_error = exc
        except Exception:
            logger.warning("Error closing engine", exc_info=True)

        # Stop the event loop and join the thread.
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

        if encryption_error is not None:
            raise encryption_error

    def __del__(self) -> None:
        if not getattr(self, "_closed", True):
            warnings.warn(
                "MemoryClient was not closed. Use 'with MemoryClient(...) as client:' "
                "or call client.close() explicitly.",
                ResourceWarning,
                stacklevel=2,
            )
