"""Organizer job registry and execution for explicit organize() calls.

Implements RFC-0015 Layer 3 jobs. Each job is an async function that takes
the engine, config, and time budget, and returns a JobResult. Implemented
jobs: promote, decay_sweep, archive, feedback_apply, tombstone_sweep.
Remaining jobs are stubs that return empty results pending future RFC
implementations.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from prme.config import OrganizerConfig
from prme.organizer.decay import compute_effective_confidence, compute_effective_salience
from prme.organizer.models import JobResult
from prme.types import LifecycleState

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

logger = logging.getLogger(__name__)

ALL_JOBS: list[str] = [
    "promote",
    "decay_sweep",
    "archive",
    "deduplicate",
    "alias_resolve",
    "summarize",
    "feedback_apply",
    "centrality_boost",
    "tombstone_sweep",
    "snapshot_generation",
    "consolidate",
]


async def run_job(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Dispatch to the appropriate job function.

    Args:
        job_name: Name of the job to run (must be in ALL_JOBS).
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with threshold parameters.
        budget_ms: Time budget for this job in milliseconds.

    Returns:
        JobResult with execution details.

    Raises:
        ValueError: If job_name is not a recognized job.
    """
    dispatch = {
        "promote": _job_promote,
        "decay_sweep": _job_decay_sweep,
        "archive": _job_archive,
        "feedback_apply": _job_feedback_apply,
        "deduplicate": _job_deduplicate,
        "alias_resolve": _job_alias_resolve,
        "summarize": _job_summarize,
        "centrality_boost": _job_stub,
        "tombstone_sweep": _job_tombstone_sweep,
        "snapshot_generation": _job_snapshot_generation,
        "consolidate": _job_consolidate,
    }

    handler = dispatch.get(job_name)
    if handler is None:
        raise ValueError(f"Unknown organizer job: {job_name!r}")

    return await handler(job_name, engine, config, budget_ms)


async def _job_promote(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Promote eligible tentative nodes to stable.

    Queries tentative nodes older than promotion_age_days with at least
    promotion_evidence_count evidence refs. Processes until budget is
    exhausted.
    """
    start = time.monotonic()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.promotion_age_days)

    tentative_nodes = await engine.query_nodes(
        lifecycle_states=[LifecycleState.TENTATIVE],
        limit=500,
    )

    processed = 0
    modified = 0
    errors = 0

    for node in tentative_nodes:
        # Check budget
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        if node.created_at <= cutoff and len(node.evidence_refs) >= config.promotion_evidence_count:
            processed += 1
            try:
                await engine.promote(str(node.id))
                modified += 1
            except ValueError:
                errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
    )


async def _job_decay_sweep(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Evaluate all active nodes for threshold transitions based on virtual decay.

    Computes effective salience/confidence for each active node and triggers
    lifecycle transitions if thresholds are crossed. This job handles the
    intermediate "deprecate" transition for low-confidence nodes and archives
    for very low salience.
    """
    start = time.monotonic()
    now = datetime.now(timezone.utc)

    active_states = [
        LifecycleState.TENTATIVE,
        LifecycleState.STABLE,
        LifecycleState.CONTESTED,
    ]
    nodes = await engine.query_nodes(
        lifecycle_states=active_states,
        limit=500,
    )

    processed = 0
    modified = 0
    errors = 0

    for node in nodes:
        # Check budget
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        # Skip pinned nodes
        if node.pinned:
            continue

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

        processed += 1

        # Force archive: very low salience
        if eff_salience < config.force_archive_salience_threshold:
            try:
                await engine.archive(str(node.id))
                modified += 1
            except ValueError:
                errors += 1
            continue

        # Deprecate: very low confidence
        if eff_confidence < config.deprecate_confidence_threshold:
            try:
                await engine._graph_store.deprecate(str(node.id))
                modified += 1
            except (ValueError, AttributeError):
                try:
                    await engine.archive(str(node.id))
                    modified += 1
                except ValueError:
                    errors += 1
            continue

        # Archive: both below thresholds
        if (
            eff_salience < config.archive_salience_threshold
            and eff_confidence < config.archive_confidence_threshold
        ):
            try:
                await engine.archive(str(node.id))
                modified += 1
            except ValueError:
                errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
    )


async def _job_archive(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Archive nodes below force_archive_salience_threshold.

    A focused job that only handles the force-archive case (extremely low
    salience). The decay_sweep job handles the more nuanced threshold checks.
    """
    start = time.monotonic()
    now = datetime.now(timezone.utc)

    active_states = [
        LifecycleState.TENTATIVE,
        LifecycleState.STABLE,
        LifecycleState.CONTESTED,
    ]
    nodes = await engine.query_nodes(
        lifecycle_states=active_states,
        limit=500,
    )

    processed = 0
    modified = 0
    errors = 0

    for node in nodes:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        if node.pinned:
            continue

        eff_salience = compute_effective_salience(
            salience_base=node.salience_base,
            reinforcement_boost=node.reinforcement_boost,
            decay_profile=node.decay_profile,
            last_reinforced_at=node.last_reinforced_at,
            now=now,
            pinned=node.pinned,
        )

        processed += 1

        if eff_salience < config.force_archive_salience_threshold:
            try:
                await engine.archive(str(node.id))
                modified += 1
            except ValueError:
                errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
    )


async def _job_feedback_apply(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Apply user feedback signals to auto-tune retrieval scoring weights.

    Retrieves pending feedback signals from the engine's FeedbackTracker,
    runs the WeightTuner to compute adjusted weights, and updates the
    engine's config with the new weights. The feedback tracker is then
    cleared so signals are not re-processed.

    This implements the feedback loop described in RFC-0009, extended by
    issue #24 with gradient-free weight auto-tuning.
    """
    start = time.monotonic()

    tracker = engine._feedback_tracker
    signals = tracker.get_signals(window_days=30)

    if not signals:
        return JobResult(
            job=job_name,
            details={"status": "no_signals", "note": "No pending feedback signals"},
        )

    # Run weight tuner
    tuner = engine._weight_tuner
    old_version = tuner.current_weights.version_id
    new_weights = tuner.update(signals)
    new_version = new_weights.version_id

    # Update the engine config with new scoring weights.
    # PRMEConfig.scoring is frozen, so we replace it via model_copy.
    engine._config = engine._config.model_copy(
        update={"scoring": new_weights},
    )

    # Also update the retrieval pipeline's weights if present.
    if engine._retrieval_pipeline is not None:
        engine._retrieval_pipeline._scoring_weights = new_weights

    # Clear processed signals.
    tracker.clear()

    duration_ms = (time.monotonic() - start) * 1000.0

    logger.info(
        "feedback_apply: processed %d signals, weights %s -> %s",
        len(signals),
        old_version,
        new_version,
    )

    return JobResult(
        job=job_name,
        nodes_processed=len(signals),
        nodes_modified=1 if old_version != new_version else 0,
        duration_ms=round(duration_ms, 2),
        details={
            "status": "applied",
            "signals_processed": len(signals),
            "old_weight_version": old_version,
            "new_weight_version": new_version,
            "weights_changed": old_version != new_version,
        },
    )


async def _job_deduplicate(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Find and merge duplicate memory nodes (issue #11).

    Uses vector similarity and exact content matching to identify
    duplicates, then merges them by archiving the lower-quality node
    and creating a SUPERSEDES edge for audit trail.
    """
    from prme.organizer.deduplication import find_duplicates, merge_duplicates

    start = time.monotonic()

    try:
        duplicates = await find_duplicates(
            engine, config, budget_ms=budget_ms / 2,
        )
        merged_count = await merge_duplicates(engine, duplicates)
    except Exception:
        logger.warning("Deduplication job failed", exc_info=True)
        duration_ms = (time.monotonic() - start) * 1000.0
        return JobResult(
            job=job_name,
            errors=1,
            duration_ms=round(duration_ms, 2),
        )

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=len(duplicates),
        nodes_modified=merged_count,
        duration_ms=round(duration_ms, 2),
        details={
            "duplicates_found": len(duplicates),
            "nodes_merged": merged_count,
        },
    )


async def _job_alias_resolve(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Find and resolve entity alias relationships (issue #11).

    Detects abbreviations, case variations, and semantic aliases among
    ENTITY nodes, then merges high-confidence aliases or links them
    with RELATES_TO edges.
    """
    from prme.organizer.alias_resolution import find_aliases, resolve_aliases

    start = time.monotonic()

    try:
        aliases = await find_aliases(
            engine, config, budget_ms=budget_ms / 2,
        )
        resolved_count = await resolve_aliases(engine, aliases)
    except Exception:
        logger.warning("Alias resolution job failed", exc_info=True)
        duration_ms = (time.monotonic() - start) * 1000.0
        return JobResult(
            job=job_name,
            errors=1,
            duration_ms=round(duration_ms, 2),
        )

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=len(aliases),
        nodes_modified=resolved_count,
        duration_ms=round(duration_ms, 2),
        details={
            "aliases_found": len(aliases),
            "aliases_resolved": resolved_count,
        },
    )


async def _job_snapshot_generation(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Generate snapshots for active entity nodes.

    Queries all active ENTITY nodes and generates an EntitySnapshot for each.
    Snapshots are ephemeral (not persisted) but this job logs how many were
    generated for observability. A future iteration may cache snapshots.
    """
    from prme.retrieval.snapshots import generate_entity_snapshot

    from prme.types import NodeType

    start = time.monotonic()

    entities = await engine.query_nodes(
        node_type=NodeType.ENTITY,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=500,
    )

    processed = 0
    modified = 0
    errors = 0

    for entity in entities:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        processed += 1
        try:
            await generate_entity_snapshot(engine, str(entity.id))
            modified += 1
        except (ValueError, Exception):
            errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
        details={"note": "Snapshots are ephemeral read-only views"},
    )


async def _job_consolidate(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Run the predictive forgetting / consolidation pipeline (issue #22).

    Clusters semantically similar episodic memories, creates summary
    abstractions, and archives redundant individual memories. This is a
    Layer 3 job -- pattern-based consolidation, not just time-based.
    """
    from prme.organizer.consolidation import run_consolidation_pipeline

    start = time.monotonic()

    consolidation_result = await run_consolidation_pipeline(
        engine, config, budget_ms
    )

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=consolidation_result.nodes_consolidated,
        nodes_modified=consolidation_result.nodes_archived + consolidation_result.summaries_created,
        errors=0,
        duration_ms=round(duration_ms, 2),
        details={
            "clusters_found": consolidation_result.clusters_found,
            "nodes_consolidated": consolidation_result.nodes_consolidated,
            "nodes_archived": consolidation_result.nodes_archived,
            "summaries_created": consolidation_result.summaries_created,
        },
    )


async def _job_tombstone_sweep(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Archive nodes that have exceeded their TTL (issue #12, RFC-0007 S9).

    Queries all active nodes, checks each for TTL expiry based on
    ``created_at + ttl_days``. Nodes past their TTL are archived.
    Pinned nodes and nodes with ``ttl_days=None`` are exempt.
    Already-archived or deprecated nodes are skipped by the query filter.

    Logs a TOMBSTONE_SWEEP operation for each archived node.
    """
    import json
    import uuid

    start = time.monotonic()
    now = datetime.now(timezone.utc)

    active_states = [
        LifecycleState.TENTATIVE,
        LifecycleState.STABLE,
        LifecycleState.CONTESTED,
    ]
    nodes = await engine.query_nodes(
        lifecycle_states=active_states,
        limit=500,
    )

    processed = 0
    modified = 0
    errors = 0

    for node in nodes:
        # Check budget
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break

        # Skip nodes without TTL
        if node.ttl_days is None:
            continue

        # Skip pinned nodes
        if node.pinned:
            continue

        processed += 1

        # Check if TTL has expired
        expiry = node.created_at + timedelta(days=node.ttl_days)
        if expiry >= now:
            continue  # not yet expired

        # Archive the expired node
        try:
            await engine.archive(str(node.id))
            modified += 1

            # Log a TOMBSTONE_SWEEP operation
            try:
                op_id = str(uuid.uuid4())
                payload = json.dumps({
                    "target_id": str(node.id),
                    "target_type": "memory_object",
                    "reason": "retention_policy_expiry",
                    "ttl_days": node.ttl_days,
                    "created_at": node.created_at.isoformat(),
                    "expired_at": expiry.isoformat(),
                    "tombstone_ts": now.isoformat(),
                })
                engine._conn.execute(
                    """
                    INSERT INTO operations (id, op_type, target_id, payload, created_at)
                    VALUES (?, 'TOMBSTONE_SWEEP', ?, ?::JSON, ?)
                    """,
                    [op_id, str(node.id), payload, now],
                )
            except Exception:
                logger.warning(
                    "Failed to log TOMBSTONE_SWEEP operation for node %s",
                    node.id,
                    exc_info=True,
                )
        except ValueError:
            errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
        details={"note": "TTL-based archival (RFC-0007 S9, issue #12)"},
    )


async def _job_summarize(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Run hierarchical summarization: daily -> weekly -> monthly.

    Splits the time budget 50/30/20 across the three levels.
    """
    from prme.organizer.summarization import (
        generate_daily_summaries,
        roll_up_monthly,
        roll_up_weekly,
    )

    start = time.monotonic()
    total_processed = 0
    total_modified = 0
    total_errors = 0
    details: dict = {}

    # Budget split: 50% daily, 30% weekly, 20% monthly
    daily_budget = budget_ms * 0.5
    weekly_budget = budget_ms * 0.3
    monthly_budget = budget_ms * 0.2

    try:
        daily_result = await generate_daily_summaries(engine, config, daily_budget)
        total_processed += daily_result.nodes_processed
        total_modified += daily_result.nodes_modified
        total_errors += daily_result.errors
        details["daily"] = daily_result.details
    except Exception:
        logger.warning("Daily summarization failed", exc_info=True)
        total_errors += 1

    try:
        weekly_result = await roll_up_weekly(engine, config, weekly_budget)
        total_processed += weekly_result.nodes_processed
        total_modified += weekly_result.nodes_modified
        total_errors += weekly_result.errors
        details["weekly"] = weekly_result.details
    except Exception:
        logger.warning("Weekly rollup failed", exc_info=True)
        total_errors += 1

    try:
        monthly_result = await roll_up_monthly(engine, config, monthly_budget)
        total_processed += monthly_result.nodes_processed
        total_modified += monthly_result.nodes_modified
        total_errors += monthly_result.errors
        details["monthly"] = monthly_result.details
    except Exception:
        logger.warning("Monthly rollup failed", exc_info=True)
        total_errors += 1

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=total_processed,
        nodes_modified=total_modified,
        errors=total_errors,
        duration_ms=round(duration_ms, 2),
        details=details,
    )


async def _job_stub(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
) -> JobResult:
    """Stub job returning an empty result.

    Used for jobs whose full implementation depends on future RFCs.
    """
    return JobResult(
        job=job_name,
        details={"status": "stub", "note": f"Job '{job_name}' not yet implemented"},
    )
