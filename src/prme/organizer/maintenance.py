"""Opportunistic maintenance runner for bounded background work.

Runs lightweight maintenance tasks during retrieve/ingest calls,
bounded by a time budget and cooldown interval. Implements RFC-0015
Layer 2 (opportunistic maintenance).

The MaintenanceRunner checks a cooldown timer and, if sufficient time
has elapsed since the last pass, runs a bounded maintenance cycle:
auto-promotion of eligible tentative nodes and threshold-based archival
of decayed nodes.
"""

from __future__ import annotations

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

    async def maybe_run(self) -> MaintenanceResult | None:
        """Check cooldown and run maintenance if due. Returns None if skipped."""
        if not self._config.opportunistic_enabled:
            return None

        now = time.monotonic()
        elapsed = now - self._last_maintained_at

        # First call always runs (last_maintained_at == 0)
        if self._last_maintained_at > 0 and elapsed < self._config.opportunistic_cooldown:
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

        Returns count of nodes promoted.
        """
        cutoff = now - timedelta(days=self._config.promotion_age_days)

        # Query tentative nodes created before cutoff
        tentative_nodes = await self._engine.query_nodes(
            lifecycle_states=[LifecycleState.TENTATIVE],
            limit=batch_size,
        )

        promoted = 0
        for node in tentative_nodes:
            if node.created_at <= cutoff and len(node.evidence_refs) >= self._config.promotion_evidence_count:
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
