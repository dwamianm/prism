"""Opportunistic maintenance runner for bounded background work.

Runs lightweight maintenance tasks during retrieve/ingest calls,
bounded by a time budget and cooldown interval. Implements RFC-0015
Layer 2 (opportunistic maintenance).

The MaintenanceRunner checks a cooldown timer and, if sufficient time
has elapsed since the last pass, runs a bounded maintenance cycle:
auto-promotion of eligible tentative nodes and threshold-based archival
of decayed nodes.

The pass is deliberately store-wide rather than per-tenant. It has no
request to take an identity from, and both of its tasks are per-node
lifecycle transitions driven by that node's own age and decay: neither
reads one node to decide something about another, so there is nothing to
leak between tenants. The jobs that do compare nodes to each other
(deduplicate, alias_resolve, consolidate) are Layer 3 only, and those take
a user scope from ``organize()`` (issue #66).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from prme.config import OrganizerConfig
from prme.organizer.decay import compute_effective_confidence, compute_effective_salience
from prme.organizer.models import MaintenanceResult
from prme.types import LifecycleState, NodeType

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)


class MaintenanceRunner:
    """Runs bounded opportunistic maintenance during retrieve/ingest."""

    def __init__(self, engine: MemoryEngine, config: OrganizerConfig) -> None:
        self._engine = engine
        self._config = config
        self._last_maintained_at: float = 0.0  # epoch seconds, 0 = never run
        self._task: asyncio.Task[MaintenanceResult | None] | None = None

    def _is_due(self) -> bool:
        """Whether a maintenance pass is enabled and past its cooldown."""
        if not self._config.opportunistic_enabled:
            return False
        # First call always runs (last_maintained_at == 0)
        if self._last_maintained_at <= 0:
            return True
        elapsed = time.monotonic() - self._last_maintained_at
        return elapsed >= self._config.opportunistic_cooldown

    def schedule(self) -> None:
        """Start a maintenance pass in the background if one is due.

        Callers on the user-visible path (retrieve, ingest) use this instead
        of awaiting ``maybe_run()``: the pass produces nothing the response
        depends on, and its time budget would otherwise land on the caller's
        latency (issue #62). At most one pass runs at a time, and the cooldown
        is marked at launch so a burst of concurrent calls cannot stampede.
        """
        if self._task is not None and not self._task.done():
            return
        if not self._is_due():
            return

        self._last_maintained_at = time.monotonic()
        self._task = asyncio.create_task(self._run_scheduled())

    async def _run_scheduled(self) -> MaintenanceResult | None:
        """Background wrapper: never lets a failure escape as a task error."""
        try:
            result = await self._run_maintenance()
            self._last_maintained_at = time.monotonic()
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Opportunistic maintenance failed; continuing normally",
                exc_info=True,
            )
            self._last_maintained_at = time.monotonic()
            return None

    async def drain(self) -> None:
        """Await an in-flight background pass, if any.

        Called on engine close so a scheduled pass finishes (or surfaces its
        failure) before the write queue and connections go away.
        """
        task = self._task
        if task is None or task.done():
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def maybe_run(self) -> MaintenanceResult | None:
        """Check cooldown and run maintenance if due. Returns None if skipped.

        Runs the pass inline. ``schedule()`` is the non-blocking variant used
        on the hot path.
        """
        if not self._is_due():
            return None

        try:
            result = await self._run_maintenance()
            self._last_maintained_at = time.monotonic()
            return result
        except Exception:
            logger.warning(
                "Opportunistic maintenance failed; continuing normally",
                exc_info=True,
            )
            # Reset cooldown even on failure to avoid hammering
            self._last_maintained_at = time.monotonic()
            return None

    async def _run_maintenance(self) -> MaintenanceResult:
        """Run bounded maintenance pass: materialize, promote, archive, feedback_apply."""
        start = time.monotonic()
        result = MaintenanceResult()
        batch_size = self._config.opportunistic_batch_size
        now_dt = datetime.now(timezone.utc)

        # --- Materialization drain (issue #25) ---
        # Process pending fast-ingested items before other maintenance
        try:
            engine = self._engine
            if engine._materialization_queue.debt_sync() > 0:
                budget_ms = getattr(
                    engine._config, "materialization_budget_ms", 100
                )
                await engine._materialization_queue.drain(
                    engine, budget_ms=budget_ms
                )
        except Exception:
            logger.warning(
                "Materialization drain failed during maintenance",
                exc_info=True,
            )

        # --- Auto-promotion ---
        try:
            promoted = await self._auto_promote(batch_size, now_dt)
            result.nodes_promoted = promoted
        except Exception:
            logger.warning("Auto-promotion failed during maintenance", exc_info=True)

        # --- Threshold archival ---
        try:
            archived, deprecated = await self._threshold_archive(batch_size, now_dt)
            result.nodes_archived = archived
            result.nodes_deprecated = deprecated
        except Exception:
            logger.warning("Threshold archival failed during maintenance", exc_info=True)

        # --- Feedback apply (placeholder) ---
        result.feedback_applied = 0

        elapsed_ms = (time.monotonic() - start) * 1000.0
        result.duration_ms = round(elapsed_ms, 2)
        return result

    async def _auto_promote(
        self, batch_size: int, now: datetime
    ) -> int:
        """Promote eligible tentative nodes.

        Queries tentative nodes older than promotion_age_days with
        at least promotion_evidence_count evidence refs, then promotes
        each via engine.promote().

        The age cutoff is pushed into SQL (created_before) and results are
        ordered oldest-first so each bounded pass drains the oldest eligible
        nodes. Because promotion moves a node out of the TENTATIVE state,
        successive passes advance through the backlog instead of repeatedly
        re-examining the newest window (which would starve older nodes).

        Returns count of nodes promoted.
        """
        cutoff = now - timedelta(days=self._config.promotion_age_days)

        # Query the oldest tentative nodes created at/before the cutoff.
        tentative_nodes = await self._engine.query_nodes(
            lifecycle_states=[LifecycleState.TENTATIVE],
            created_before=cutoff,
            oldest_first=True,
            limit=batch_size,
        )

        promoted = 0
        for node in tentative_nodes:
            if len(node.evidence_refs) >= self._config.promotion_evidence_count:
                try:
                    await self._engine.promote(str(node.id))
                    promoted += 1
                except ValueError:
                    # Already promoted or invalid transition
                    pass
        return promoted

    async def _threshold_archive(
        self, batch_size: int, now: datetime
    ) -> tuple[int, int]:
        """Archive or deprecate nodes below threshold.

        Queries active nodes, computes virtual effective salience/confidence,
        and checks against config thresholds.

        Returns (archived_count, deprecated_count).
        """
        active_states = [
            LifecycleState.TENTATIVE,
            LifecycleState.STABLE,
            LifecycleState.CONTESTED,
        ]
        nodes = await self._engine.query_nodes(
            lifecycle_states=active_states,
            limit=batch_size,
        )

        archived = 0
        deprecated = 0

        for node in nodes:
            eff_salience = compute_effective_salience(
                salience_base=node.salience_base,
                reinforcement_boost=node.reinforcement_boost,
                decay_profile=node.decay_profile,
                last_reinforced_at=node.last_reinforced_at,
                now=now,
                pinned=node.pinned,
            )
            eff_confidence = compute_effective_confidence(
                confidence_base=node.confidence_base,
                decay_profile=node.decay_profile,
                last_reinforced_at=node.last_reinforced_at,
                now=now,
                pinned=node.pinned,
                epistemic_type=node.epistemic_type,
            )

            # Skip pinned nodes
            if node.pinned:
                continue

            # Skip permanent knowledge nodes (ENTITY/FACT with no TTL)
            if node.ttl_days is None and node.node_type in (
                NodeType.ENTITY,
                NodeType.FACT,
            ):
                continue

            # Force archive: salience below force threshold
            if eff_salience < self._config.force_archive_salience_threshold:
                try:
                    await self._engine.archive(str(node.id))
                    archived += 1
                    continue
                except ValueError:
                    pass

            # Deprecate: confidence below deprecate threshold
            if eff_confidence < self._config.deprecate_confidence_threshold:
                try:
                    await self._engine._graph_store.deprecate(str(node.id))
                    deprecated += 1
                    continue
                except (ValueError, AttributeError):
                    # deprecate() may not exist on all graph stores;
                    # fall back to archive
                    try:
                        await self._engine.archive(str(node.id))
                        archived += 1
                    except ValueError:
                        pass
                    continue

            # Archive: both salience and confidence below thresholds
            if (
                eff_salience < self._config.archive_salience_threshold
                and eff_confidence < self._config.archive_confidence_threshold
            ):
                try:
                    await self._engine.archive(str(node.id))
                    archived += 1
                except ValueError:
                    pass

        return archived, deprecated
