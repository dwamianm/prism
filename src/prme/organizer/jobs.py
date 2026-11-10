"""Organizer job registry and execution for explicit organize() calls.

Implements RFC-0015 Layer 3 jobs. Each registered job is an async function that
takes the engine, config, and time budget, and performs its documented work.
Proposed jobs are not advertised until they have an implementation and evidence.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from prme.config import OrganizerConfig
from prme.organizer.decay import compute_effective_confidence, compute_effective_salience
from prme.organizer.models import JobResult
from prme.organizer.watermarks import (
    INCREMENTAL_JOBS,
    IncrementalWindow,
    scope_key,
)
from prme.types import ACTIVE_LIFECYCLE_STATES, LifecycleState, NodeType

if TYPE_CHECKING:
    from prme.storage.engine import MemoryEngine

from prme.privacy.diagnostics import ScopedLogger

logger = logging.getLogger(__name__)
logger = ScopedLogger(logger)

ALL_JOBS: list[str] = [
    "promote",
    "decay_sweep",
    "archive",
    "deduplicate",
    "alias_resolve",
    "summarize",
    "feedback_apply",
    "tombstone_sweep",
    "snapshot_generation",
    "consolidate",
    "index_compaction",
]

# Anonymous legacy feedback cannot update one tenant's ranking profile. Require
# an explicitly selected unscoped operator job; ordinary maintenance never tunes.
DEFAULT_JOBS: tuple[str, ...] = tuple(name for name in ALL_JOBS if name != "feedback_apply")

# Nodes one incremental page may materialize. Pages keep a bounded run from
# loading a whole 10,000-node scope, and a keyset cursor resumes between pages
# and between runs.
_INCREMENTAL_PAGE_SIZE = 500

# A completed run leaves the next window's lower bound this far behind its
# ceiling. A node's updated_at is assigned when the model is built, before its
# row is inserted, so a concurrent write can become visible with a stamp just
# below the ceiling. The overlap re-examines that bounded tail (idempotently)
# instead of skipping such a record forever.
_WATERMARK_OVERLAP = timedelta(seconds=60)


async def _run_incremental(
    job_name: str,
    handler,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None,
) -> JobResult:
    """Run a change-driven job over the window its watermark allows.

    The run ceiling is captured before any work. The watermark is written
    after the job's own writes have committed: a completed run advances
    ``completed_through`` to a point one overlap behind the ceiling, a run
    that stopped on the budget records the keyset position it reached so the
    next run resumes there, and a failed run records only its processed
    prefix instead of advancing past work it did not finish.
    """
    scope = scope_key(user_id)
    ceiling = datetime.now(timezone.utc)
    watermark = await engine.get_organizer_watermark(scope_key=scope, job=job_name)

    prior_completed = watermark.completed_through if watermark is not None else None
    resume = watermark.resume_cursor if watermark is not None else None
    if resume is not None:
        updated_after, after_id = resume
    else:
        updated_after = prior_completed
        after_id = None

    window = IncrementalWindow(
        scope_key=scope,
        job=job_name,
        ceiling=ceiling,
        updated_after=updated_after,
        after_id=after_id,
    )

    result = await handler(
        job_name, engine, config, budget_ms, user_id, window=window,
    )
    cursor = result.details.pop("_resume", None)

    written = True
    if result.status == "error" or result.stopped_on_budget:
        # Do not advance past a window that did not finish. Keep any prefix
        # that was processed before the failure or the stop so the retry
        # starts there; with no prefix the previous watermark stands.
        if cursor is not None:
            resume_updated_at, resume_id = cursor
            if isinstance(resume_updated_at, str):
                resume_updated_at = datetime.fromisoformat(resume_updated_at)
            written = await engine.set_organizer_watermark(
                scope_key=scope,
                job=job_name,
                completed_through=prior_completed,
                resume_updated_at=resume_updated_at,
                resume_id=resume_id,
            )
    else:
        written = await engine.set_organizer_watermark(
            scope_key=scope,
            job=job_name,
            completed_through=ceiling - _WATERMARK_OVERLAP,
        )

    result.details["incremental"] = {
        "window_start": updated_after.isoformat() if updated_after is not None else None,
        "window_end": ceiling.isoformat(),
    }
    if not written:
        # The owner has an owner-wide erasure on record, so the next run starts
        # from the full scope instead of a resurrected watermark row.
        result.details["incremental"]["watermark"] = "not_written_erased_scope"
    return result


async def run_job(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> JobResult:
    """Dispatch to the appropriate job function.

    Args:
        job_name: Name of the job to run (must be in ALL_JOBS).
        engine: The MemoryEngine for storage operations.
        config: OrganizerConfig with threshold parameters.
        budget_ms: Time budget for this job in milliseconds.
        user_id: When given, the job only reads and mutates nodes owned by
            this user. Omitting it keeps the single-tenant behaviour of
            operating over every node in the store. Multi-tenant deployments
            should pass it to confine maintenance to one tenant. Pairwise
            jobs also reject cross-owner merges on unscoped runs (issue #66).

    Returns:
        JobResult with execution details. With
        ``OrganizerConfig.incremental_enabled``, the change-driven jobs run
        over their watermark window instead of the whole scope.

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
        "tombstone_sweep": _job_tombstone_sweep,
        "snapshot_generation": _job_snapshot_generation,
        "consolidate": _job_consolidate,
        "index_compaction": _job_index_compaction,
    }

    handler = dispatch.get(job_name)
    if handler is None:
        raise ValueError(f"Unknown organizer job: {job_name!r}")

    if config.incremental_enabled and job_name in INCREMENTAL_JOBS:
        return await _run_incremental(
            job_name, handler, engine, config, budget_ms, user_id,
        )

    return await handler(job_name, engine, config, budget_ms, user_id)


async def _job_promote(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> JobResult:
    """Promote eligible tentative nodes to stable.

    Queries tentative nodes older than promotion_age_days with at least
    promotion_evidence_count evidence refs. The age cutoff is pushed into
    SQL and results are ordered oldest-first so each run drains the oldest
    eligible nodes rather than re-scanning the newest window (which would
    starve older nodes once the tentative backlog exceeds the query limit).
    Processes until budget is exhausted.

    This job stays full-scope: eligibility is driven by age crossing
    ``promotion_age_days``, which no write records, so an update window would
    never revisit a node that only grew old enough.
    """
    start = time.monotonic()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.promotion_age_days)

    tentative_nodes = await engine.query_nodes(
        user_id=user_id,
        lifecycle_states=[LifecycleState.TENTATIVE],
        created_before=cutoff,
        oldest_first=True,
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

        if len(node.evidence_refs) >= config.promotion_evidence_count:
            processed += 1
            try:
                await engine.promote(str(node.id), user_id=user_id)
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
    user_id: str | None = None,
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
        user_id=user_id,
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

        # Skip ENTITY and FACT nodes with no TTL -- these represent
        # permanent knowledge and should not be archived by decay alone.
        # Only nodes with an explicit TTL or ephemeral types (EVENT, TASK,
        # etc.) are subject to decay-based archival.
        if node.ttl_days is None and node.node_type in (
            NodeType.ENTITY,
            NodeType.FACT,
        ):
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
                await engine.archive(str(node.id), user_id=user_id)
                modified += 1
            except ValueError:
                errors += 1
            continue

        # Deprecate/archive: very low confidence
        if eff_confidence < config.deprecate_confidence_threshold:
            try:
                if node.lifecycle_state == LifecycleState.CONTESTED:
                    await engine._graph_store.deprecate(str(node.id))
                else:
                    await engine.archive(str(node.id), user_id=user_id)
                modified += 1
            except (ValueError, AttributeError):
                try:
                    await engine.archive(str(node.id), user_id=user_id)
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
                await engine.archive(str(node.id), user_id=user_id)
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
    user_id: str | None = None,
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
        user_id=user_id,
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

        # Skip permanent knowledge nodes (ENTITY/FACT with no TTL)
        if node.ttl_days is None and node.node_type in (
            NodeType.ENTITY,
            NodeType.FACT,
        ):
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
                await engine.archive(str(node.id), user_id=user_id)
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
    user_id: str | None = None,
) -> JobResult:
    """Apply user feedback signals to auto-tune retrieval scoring weights.

    Retrieves pending feedback signals from the engine's FeedbackTracker,
    runs the WeightTuner to compute adjusted weights, and updates the
    engine's config with the new weights. The feedback tracker is then
    cleared so signals are not re-processed.

    This implements the feedback loop described in RFC-0009, extended by
    issue #24 with gradient-free weight auto-tuning.

    This legacy operator job requires an explicit unscoped invocation. Both
    signals and weights are engine-global, so a scoped invocation fails before
    reading or consuming any signals. It is excluded from default maintenance.
    """
    start = time.monotonic()

    if user_id is not None:
        raise ValueError("feedback_apply requires an explicit unscoped operator call; it changes engine-global weights")

    tracker = engine._feedback_tracker
    signals = tracker.get_signals(window_days=30)

    if not signals:
        return JobResult(
            job=job_name,
            details={
                "status": "no_signals", "note": "No pending feedback signals",
                "scope": "global",
            },
        )

    if engine._config.scoring.fusion != "weighted":
        # Rank fusion ignores the additive weights; keep the signals for a
        # later weighted configuration instead of reporting a change that
        # cannot affect ranking.
        return JobResult(
            job=job_name,
            details={
                "status": "not_applicable",
                "note": "Rank fusion scoring does not use the tuned weights",
                "scope": "global",
            },
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
            "scope": "global",
        },
    )


async def _job_deduplicate(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
    window: IncrementalWindow | None = None,
) -> JobResult:
    """Find and merge duplicate memory nodes (issue #11).

    Uses vector similarity and exact content matching to identify
    duplicates, then merges them by archiving the lower-quality node
    and creating a SUPERSEDES edge for audit trail.

    With ``window`` (incremental mode) it examines only nodes created or
    updated since the last completed run. Each changed node is still searched
    against its neighbors in the vector index, so a new copy finds an older
    twin, and the run records a keyset position when the budget stops.
    """
    if window is not None:
        return await _deduplicate_incremental(
            job_name, engine, config, budget_ms, user_id, window,
        )

    from prme.organizer.deduplication import find_duplicates, merge_duplicates

    start = time.monotonic()

    try:
        duplicates = await find_duplicates(
            engine, config, budget_ms=budget_ms / 2, user_id=user_id,
        )
        merged_count = await merge_duplicates(engine, duplicates, **({"user_id": user_id} if user_id else {}))
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
            "pairs_not_applied": len(duplicates) - merged_count,
            "merge_policy": "exact_content_provenance_validity_v1",
        },
    )


async def _deduplicate_incremental(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None,
    window: IncrementalWindow,
) -> JobResult:
    """Deduplicate one incremental page at a time, merging before advancing.

    The scan half of the job budget examines changed nodes; each is searched
    against the vector index so a new copy can merge with an older twin. A
    page's candidates are merged before the keyset cursor moves past them, so
    a stop leaves the unexamined nodes for the next run.
    """
    from prme.organizer.deduplication import find_duplicates_in_page, merge_duplicates
    from prme.privacy.journals import generation_search

    start = time.monotonic()

    try:
        index = await generation_search(engine, user_id=user_id)
    except Exception:
        logger.warning("Deduplication job failed", exc_info=True)
        return JobResult(
            job=job_name,
            errors=1,
            duration_ms=round((time.monotonic() - start) * 1000.0, 2),
            status="error",
            details={"error": "index_unavailable"},
        )

    scan_budget_ms = budget_ms / 2
    updated_after = window.updated_after
    after_id = window.after_id
    processed = 0
    merged_count = 0
    duplicates_found = 0
    errors = 0
    stopped = False
    failed = False
    last_cursor: tuple[datetime, str] | None = None

    while True:
        try:
            nodes = await engine.scan_changed_nodes(
                user_id=user_id,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
                updated_after=updated_after,
                after_id=after_id,
                updated_at_or_before=window.ceiling,
                limit=_INCREMENTAL_PAGE_SIZE,
            )
        except Exception:
            logger.warning("Incremental deduplication scan failed", exc_info=True)
            errors += 1
            failed = True
            break

        if not nodes:
            break

        examined = []
        for node in nodes:
            if (time.monotonic() - start) * 1000.0 >= scan_budget_ms:
                stopped = True
                break
            examined.append(node)

        if examined:
            remaining_ms = max(
                1.0, scan_budget_ms - (time.monotonic() - start) * 1000.0,
            )
            try:
                duplicates, completed, search_failed = await find_duplicates_in_page(
                    engine,
                    config,
                    examined,
                    index=index,
                    budget_ms=remaining_ms,
                    user_id=user_id,
                )
                duplicates_found += len(duplicates)
                merged_count += await merge_duplicates(
                    engine, duplicates, **({"user_id": user_id} if user_id else {}),
                )
                # Only the searched prefix may move the cursor. A page whose
                # neighbor search ran out of budget or failed is searched
                # again, and a failure is reported as an error so it is not
                # mistaken for a budget stop.
                if search_failed:
                    failed = True
                    errors += 1
                elif completed < len(examined):
                    stopped = True
                done = examined[:completed]
                processed += len(done)
                if done:
                    last_cursor = (
                        done[-1].updated_at or done[-1].created_at,
                        str(done[-1].id),
                    )
            except Exception:
                logger.warning(
                    "Incremental deduplication failed for a page", exc_info=True,
                )
                errors += 1
                failed = True

        # A stop or a failure must not advance the cursor past this page.
        if stopped or failed:
            break
        updated_after = nodes[-1].updated_at or nodes[-1].created_at
        after_id = str(nodes[-1].id)
        if len(nodes) < _INCREMENTAL_PAGE_SIZE:
            break

    result = JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=merged_count,
        errors=errors,
        duration_ms=round((time.monotonic() - start) * 1000.0, 2),
        status="error" if failed else ("budget_exhausted" if stopped else "completed"),
        stopped_on_budget=stopped,
        details={
            "duplicates_found": duplicates_found,
            "nodes_merged": merged_count,
            "pairs_not_applied": duplicates_found - merged_count,
            "merge_policy": "exact_content_provenance_validity_v1",
        },
    )
    if (stopped or failed) and last_cursor is not None:
        result.details["_resume"] = [last_cursor[0].isoformat(), last_cursor[1]]
    return result


async def _job_alias_resolve(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
    window: IncrementalWindow | None = None,
) -> JobResult:
    """Find and resolve entity alias relationships (issue #11).

    Detects abbreviations, case variations, and semantic aliases among
    ENTITY nodes, then merges compatible known name variants or links them
    with RELATES_TO edges.

    With ``window`` (incremental mode) it examines only ENTITY nodes created
    or updated since the last completed run. Each changed entity is looked up
    by its equivalent names, so an unchanged name twin is found even when it
    is not on the page, and searched against the vector index for semantic
    aliases; the run records a keyset position when the budget stops.

    The candidate set is write-determined: an entity's name is immutable after
    creation, and creating an entity or changing its lifecycle writes
    ``updated_at``, so a new alias pair always has an endpoint in the window.
    Discovery is bounded, not complete: the vector search returns each changed
    entity's top-10 neighbors, so a semantic pair that neither endpoint ranks
    can be missed, and the name lookup returns a bounded page of matches. A
    tuning threshold change, a rebuilt index or a changed known-alias table is
    outside the write guarantee, as it is for ``deduplicate``. With the option
    off, the full-scope run examines only the newest 100 active entities.
    Clearing the scope's row while the option is on starts a paged whole-scope
    incremental run, which is how an older scope is re-evaluated today; paging
    the option-off full scan is follow-up work.
    """
    if window is not None:
        return await _alias_resolve_incremental(
            job_name, engine, config, budget_ms, user_id, window,
        )

    from prme.organizer.alias_resolution import find_aliases, resolve_aliases

    start = time.monotonic()

    try:
        aliases = await find_aliases(
            engine, config, budget_ms=budget_ms / 2, user_id=user_id,
        )
        resolved_count = await resolve_aliases(engine, aliases, **({"user_id": user_id} if user_id else {}))
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


async def _alias_resolve_incremental(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None,
    window: IncrementalWindow,
) -> JobResult:
    """Resolve aliases one incremental page at a time.

    The scan half of the job budget examines changed ENTITY nodes; each is
    looked up by its equivalent names and searched in the vector index so a
    new entity can pair with an older twin that is not on the page. A page's
    candidates are resolved before the keyset cursor moves past them, so a
    stop or a resolution failure leaves the unexamined entities for the next
    run. The run-level ``seen_pairs`` set is shared across pages, unlike
    deduplicate's per-page set, because the two alias phases can rediscover a
    pair that an earlier page already resolved.
    """
    from prme.organizer.alias_resolution import find_aliases_in_page, resolve_aliases
    from prme.privacy.journals import generation_search

    start = time.monotonic()

    try:
        index = await generation_search(engine, user_id=user_id)
    except Exception:
        logger.warning("Alias resolution job failed", exc_info=True)
        return JobResult(
            job=job_name,
            errors=1,
            duration_ms=round((time.monotonic() - start) * 1000.0, 2),
            status="error",
            details={"error": "index_unavailable"},
        )

    scan_budget_ms = budget_ms / 2
    updated_after = window.updated_after
    after_id = window.after_id
    processed = 0
    resolved_count = 0
    aliases_found = 0
    errors = 0
    stopped = False
    failed = False
    last_cursor: tuple[datetime, str] | None = None
    seen_pairs: set[tuple[str, str]] = set()

    while True:
        try:
            entities = await engine.scan_changed_nodes(
                user_id=user_id,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
                node_type=NodeType.ENTITY,
                updated_after=updated_after,
                after_id=after_id,
                updated_at_or_before=window.ceiling,
                limit=_INCREMENTAL_PAGE_SIZE,
            )
        except Exception:
            logger.warning("Incremental alias resolution scan failed", exc_info=True)
            errors += 1
            failed = True
            break

        if not entities:
            break

        examined = []
        for position, entity in enumerate(entities):
            # Examine at least one entity even when the remaining budget is
            # smaller than the scan, so a stopped run records a cursor instead
            # of repeating the same page forever.
            if position > 0 and (time.monotonic() - start) * 1000.0 >= scan_budget_ms:
                stopped = True
                break
            examined.append(entity)

        if examined:
            remaining_ms = max(
                1.0, scan_budget_ms - (time.monotonic() - start) * 1000.0,
            )
            resolve_failures = [0]
            try:
                aliases, completed, lookup_failed = await find_aliases_in_page(
                    engine,
                    config,
                    examined,
                    index=index,
                    budget_ms=remaining_ms,
                    user_id=user_id,
                    seen_pairs=seen_pairs,
                )
                aliases_found += len(aliases)
                resolved_count += await resolve_aliases(
                    engine,
                    aliases,
                    **({"user_id": user_id} if user_id else {}),
                    failure_count=resolve_failures,
                )
                # Only the examined prefix may move the cursor. A page whose
                # name lookup, neighbor search or resolution stopped or failed
                # is examined again, and a failure is reported as an error so
                # it is not mistaken for a budget stop.
                if lookup_failed:
                    failed = True
                    errors += 1
                if resolve_failures[0]:
                    failed = True
                    errors += resolve_failures[0]
                if completed < len(examined):
                    stopped = True
                if not lookup_failed and not resolve_failures[0]:
                    done = examined[:completed]
                    processed += len(done)
                    if done:
                        last_cursor = (
                            done[-1].updated_at or done[-1].created_at,
                            str(done[-1].id),
                        )
            except Exception:
                logger.warning(
                    "Incremental alias resolution failed for a page", exc_info=True,
                )
                errors += 1
                failed = True

        # One over-budget page is allowed to make progress, then the run stops
        # so it does not keep paying for a scope the budget cannot cover. A
        # short page is the end of the scope, so there is nothing to stop for.
        if (
            len(entities) >= _INCREMENTAL_PAGE_SIZE
            and (time.monotonic() - start) * 1000.0 >= scan_budget_ms
        ):
            stopped = True

        # A stop or a failure must not advance the cursor past this page.
        if stopped or failed:
            break
        updated_after = entities[-1].updated_at or entities[-1].created_at
        after_id = str(entities[-1].id)
        if len(entities) < _INCREMENTAL_PAGE_SIZE:
            break

    result = JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=resolved_count,
        errors=errors,
        duration_ms=round((time.monotonic() - start) * 1000.0, 2),
        status="error" if failed else ("budget_exhausted" if stopped else "completed"),
        stopped_on_budget=stopped,
        details={
            "aliases_found": aliases_found,
            "aliases_resolved": resolved_count,
            "pairs_not_applied": aliases_found - resolved_count,
        },
    )
    if (stopped or failed) and last_cursor is not None:
        result.details["_resume"] = [last_cursor[0].isoformat(), last_cursor[1]]
    return result


async def _job_snapshot_generation(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> JobResult:
    """Generate snapshots for active entity nodes.

    Queries all active ENTITY nodes and generates an EntitySnapshot for each.
    Snapshots are ephemeral (not persisted) but this job logs how many were
    generated for observability. A future iteration may cache snapshots.

    This job stays full-scope: a snapshot is built from the entity's
    neighborhood and edges, and creating an edge does not update either
    endpoint's ``updated_at``, so an update window would miss an unchanged
    entity whose relations changed.
    """
    from prme.retrieval.snapshots import generate_entity_snapshot

    from prme.types import NodeType

    start = time.monotonic()

    entities = await engine.query_nodes(
        user_id=user_id,
        node_type=NodeType.ENTITY,
        lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
        limit=500,
    )

    processed = 0
    modified = 0
    errors = 0

    # The snapshots are thrown away, so keep half of the remaining budget
    # for the jobs after this one, as deduplicate and alias_resolve do.
    snapshot_budget_ms = budget_ms / 2
    for entity in entities:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= snapshot_budget_ms:
            break

        processed += 1
        try:
            await generate_entity_snapshot(engine, str(entity.id), user_id=user_id)
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
    user_id: str | None = None,
) -> JobResult:
    """Run the predictive forgetting / consolidation pipeline (issue #22).

    Clusters semantically similar episodic memories, creates summary
    abstractions, and archives redundant individual memories. This is a
    Layer 3 job -- pattern-based consolidation, not just time-based.

    This job stays full-scope even when ``OrganizerConfig.incremental_enabled``
    is on (dwamianm/prism#523). Its unit of work is a cluster, an emergent
    property of the page of active nodes the job reads, not a node:

    - ``cluster_similar_memories`` loads the newest 500 active FACT, EVENT and
      NOTE nodes per type and partitions them greedily. Each cluster is seeded
      at the earliest unassigned node under ``_stable_source_key``, and its
      members are that seed's top-50 similar unassigned neighbors. A changed
      node seeded on its own can see a different neighbor set from the node
      that seeds the full run's cluster, so it can produce a different member
      set. The consolidation key is the exact member set, so that is a
      different lineage rather than a refresh of the existing summary.
    - A cluster can become eligible with no member written. Retiring a seed
      frees the neighbors it had claimed, and the next unchanged node can then
      seed a new cluster among them; the write is the retired seed's, which
      need not be one of the new cluster's members. ``delete_node`` and the
      privacy erasure path are stronger: they remove a member outright and
      leave no row for a node-change window.
    - The budget is checked once per cluster and the cluster list is
      recomputed on every run, so a node keyset cursor cannot name the point a
      stopped run reached. The job reports ``completed`` even when the budget
      truncates the cluster scan, and the next run starts again at the first
      cluster.

    The job already converges on unchanged input when the previous run retired
    nothing: the consolidation publication path returns the summary whose
    member set and request hash match, and retiring an ineligible source is a
    no-op. It is not idempotent across a partial retirement: only the sources
    covered by the summary are retirement candidates, so a cluster of more than
    three can leave uncovered sources that form their own cluster and lineage
    on the next run. Two further full-run limits are not convergence: a cluster
    below ``consolidation_min_cluster_size`` is silently skipped, and nodes
    beyond the newest-500 page of a type are not read at all. An incremental
    form would need a durable cluster-transition record, not a node-change
    window; that is the alternative a future design should weigh before
    changing this job.
    """
    from prme.organizer.consolidation import run_consolidation_pipeline

    start = time.monotonic()

    consolidation_result = await run_consolidation_pipeline(
        engine, config, budget_ms, user_id=user_id
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
    user_id: str | None = None,
) -> JobResult:
    """Archive nodes that have exceeded their TTL (issue #12, RFC-0007 S9).

    Queries all active nodes, checks each for TTL expiry based on
    ``created_at + ttl_days``. Nodes past their TTL are archived.
    Pinned nodes and nodes with ``ttl_days=None`` are exempt.
    Already-archived or deprecated nodes are skipped by the query filter.

    Logs a TOMBSTONE_SWEEP operation for each archived node.
    """
    start = time.monotonic()
    now = datetime.now(timezone.utc)

    active_states = [
        LifecycleState.TENTATIVE,
        LifecycleState.STABLE,
        LifecycleState.CONTESTED,
    ]
    nodes = await engine.query_nodes(
        user_id=user_id,
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

        # Archive and retain the triggering policy in one graph transaction.
        try:
            changed = await engine._graph_store.archive_expired(
                str(node.id), user_id=node.user_id, evaluated_at=now
            )
            if changed:
                modified += 1
                await engine._evict_from_indexes(str(node.id))
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
    user_id: str | None = None,
) -> JobResult:
    """Run hierarchical summarization: daily -> weekly -> monthly.

    Splits the time budget 50/30/20 across the three levels.

    This job stays full-scope even when ``OrganizerConfig.incremental_enabled``
    is on (dwamianm/prism#523). Its unit of work is a time period, not a node.
    Each level selects the top ``summarization_max_items_per_summary`` sources
    from a period: daily over the day's active non-summary nodes, weekly over
    the week's active daily summaries, and monthly over the month's active
    weekly summaries. A node-change window like ``deduplicate``'s cannot name
    the periods a changed node has left:

    - ``GraphStore.update_node`` accepts ``event_time``, the key the periods
      are grouped by, on DuckDB and PostgreSQL (``_UPDATE_ALLOWED_FIELDS``).
      Moving a node to another day writes the new event time and a new
      ``updated_at``, so the node window finds the node, but its row names
      only the new day.
    - The vacated period is recoverable by reading the summary that recorded
      it: a summary node keeps ``source_node_ids`` and
      ``selected_source_node_ids`` (and ``DERIVED_FROM`` edges), so a
      membership scan over SUMMARY nodes can name the old period. That is a
      different read from the node window, not a period-transition record.
    - ``GraphStore.delete_node`` and the privacy erasure path remove a node
      outright. The erasure path also clears the owner's watermark rows, so a
      future incremental job would rescan that scope, but a delete with no such
      marker leaves no node row for the window.

    A full run re-examines every period it reaches, so it reflects a move or a
    delete where the period still meets its minimum and the run's budget
    reaches it. A below-minimum period keeps its summary, and the week and
    month summaries built from it keep its excerpt until the period qualifies
    again. The job reports ``completed`` even when the budget truncates the
    period scan, and the next run starts again at the oldest period. An
    incremental form would need a summary-membership read and a cascade through
    the three levels; that is the alternative a future design should weigh
    before adding period-transition history.
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
        daily_result = await generate_daily_summaries(
            engine, config, daily_budget, user_id
        )
        total_processed += daily_result.nodes_processed
        total_modified += daily_result.nodes_modified
        total_errors += daily_result.errors
        details["daily"] = daily_result.details
    except Exception:
        logger.warning("Daily summarization failed", exc_info=True)
        total_errors += 1

    try:
        weekly_result = await roll_up_weekly(engine, config, weekly_budget, user_id)
        total_processed += weekly_result.nodes_processed
        total_modified += weekly_result.nodes_modified
        total_errors += weekly_result.errors
        details["weekly"] = weekly_result.details
    except Exception:
        logger.warning("Weekly rollup failed", exc_info=True)
        total_errors += 1

    try:
        monthly_result = await roll_up_monthly(engine, config, monthly_budget, user_id)
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


async def _job_index_compaction(
    job_name: str,
    engine: MemoryEngine,
    config: OrganizerConfig,
    budget_ms: float,
    user_id: str | None = None,
) -> JobResult:
    """Reconcile the vector/lexical indexes against active graph nodes (#41).

    Lifecycle transitions evict nodes from the search indexes inline, but a
    crash between the graph write and the index delete can leave a stale
    entry behind. This job is the backstop: it finds every node that still
    has a ``vector_metadata`` row but is no longer active (superseded,
    deprecated, archived, or deleted) and evicts it from both indexes so
    the indexes do not accumulate orphaned content.

    The vector_metadata table is the reconciliation anchor because every
    indexed node has a row there; the matching lexical doc is evicted by
    node_id at the same time.

    Under a user scope only that user's entries for nodes that still exist are
    evicted. DuckDB leaves vector-metadata rows whose node has vanished for an
    unscoped run to collect.

    PostgreSQL keeps an ordinary vector in the node row and some lexical
    documents in ``lexical_documents``. Its branch clears the stored vector and
    any ``lexical_documents`` row for a node the graph has already retired. A
    document row with no node is left alone: the lexical index also writes
    there before the graph row exists. A retired node's generated
    ``nodes.content_tsv`` is not this job's anchor and keeps matching the
    lexical search until that search filters lifecycle state
    (dwamianm/prism#521).

    Current prepared plans remain retryable. Replaced revisions with uniquely
    reserved identities and no graph nodes can be collected in batches of 500.
    Their immutable source journals remain intact. Incomplete or invalid
    ownership registration blocks stage reclamation. Lexical deletion commits
    before vector metadata removal, preserving a durable retry anchor.
    """
    start = time.monotonic()

    conn = getattr(engine, "_conn", None)
    if conn is None:
        return await _compact_postgres_indexes(
            job_name, engine, budget_ms, user_id, start,
        )

    # Use the canonical active-state set so this stays in sync with the
    # lifecycle filter applied at retrieval time (vector_index search).
    active = [s.value for s in ACTIVE_LIFECYCLE_STATES]
    placeholders = ", ".join("?" for _ in active)

    def _find_stale() -> list[str]:
        # Node either missing from the graph (LEFT JOIN NULL) or in a
        # non-active lifecycle state. DISTINCT so a node with several
        # vectors is evicted once.
        if user_id is not None:
            # The stale query joins the graph, so a scoped run reaches only
            # nodes that still exist; a row with no node waits for an
            # unscoped run.
            rows = conn.execute(
                "SELECT DISTINCT vm.node_id FROM vector_metadata vm "
                "JOIN nodes n ON vm.node_id = n.id "
                "WHERE n.user_id = ? "
                f"AND n.lifecycle_state NOT IN ({placeholders})",
                [user_id, *active],
            ).fetchall()
            return [row[0] for row in rows]

        rows = conn.execute(
            "SELECT DISTINCT vm.node_id FROM vector_metadata vm "
            "LEFT JOIN nodes n ON vm.node_id = n.id "
            "WHERE (n.id IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM vector_staging vs WHERE vs.node_id = vm.node_id)) "
            f"OR COALESCE(n.lifecycle_state, 'tentative') NOT IN ({placeholders})",
            active,
        ).fetchall()
        return [row[0] for row in rows]

    from prme.storage._threading import run_to_completion

    from prme.storage.derivation_registry import retired_staging
    async with engine._graph_store._conn_lock:
        stale_ids = await run_to_completion(_find_stale)
        retired_ids, cleanup_reason = await run_to_completion(retired_staging, conn, user_id=user_id)
    candidates = list(dict.fromkeys(stale_ids + retired_ids))

    processed, modified, errors = await _evict_index_entries(
        engine, candidates, budget_ms, start,
    )

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
        details={"stale_found": len(stale_ids), "retired_staging_found": len(retired_ids),
                 "stage_cleanup_blocked": cleanup_reason is not None, "stage_cleanup_reason": cleanup_reason},
    )


async def _compact_postgres_indexes(
    job_name: str,
    engine: MemoryEngine,
    budget_ms: float,
    user_id: str | None,
    start: float,
) -> JobResult:
    """Evict PostgreSQL index entries for nodes the graph has already retired.

    The vector lives in ``nodes.embedding`` and some lexical documents in
    ``lexical_documents``. This pass selects non-active nodes that still hold
    either and clears both. A ``lexical_documents`` row with no node is left
    alone: the index also writes there before the graph row exists, so it is
    not evidence of a retired node. A retired node's generated
    ``nodes.content_tsv`` is not cleared here; the lexical search filter is
    dwamianm/prism#521.
    """
    pool = getattr(engine, "_pool", None) or getattr(engine._graph_store, "_pool", None)
    if pool is None:
        duration_ms = (time.monotonic() - start) * 1000.0
        return JobResult(
            job=job_name,
            duration_ms=round(duration_ms, 2),
            details={"note": "PostgreSQL graph store has no connection pool; compaction skipped"},
        )

    active = [s.value for s in ACTIVE_LIFECYCLE_STATES]
    state_placeholders = ", ".join(f"${index + 1}" for index in range(len(active)))
    params: list = [*active]
    indexed = (
        "n.embedding IS NOT NULL "
        "OR EXISTS (SELECT 1 FROM lexical_documents d WHERE d.node_id = n.id::text)"
    )
    if user_id is not None:
        owner_placeholder = f"${len(active) + 1}"
        params.append(user_id)
        node_query = (
            f"SELECT DISTINCT n.id::text AS node_id FROM nodes n "
            f"WHERE n.user_id = {owner_placeholder} "
            f"AND n.lifecycle_state NOT IN ({state_placeholders}) AND ({indexed})"
        )
    else:
        node_query = (
            f"SELECT DISTINCT n.id::text AS node_id FROM nodes n "
            f"WHERE n.lifecycle_state NOT IN ({state_placeholders}) AND ({indexed})"
        )
    # Bound the fetch like the other jobs' node page; the eviction loop is
    # budget-bounded, but the query itself must not outlast the pass.
    node_query += " ORDER BY node_id LIMIT 500"

    async with pool.acquire() as conn:
        # Multi-model stores keep further vectors in node_embeddings; that
        # per-model index is not reconciled here (dwamianm/prism#520).
        rows = await conn.fetch(node_query, *params)
        stale_ids = [str(row["node_id"]) for row in rows]

    processed, modified, errors = await _evict_index_entries(
        engine, stale_ids, budget_ms, start,
    )

    duration_ms = (time.monotonic() - start) * 1000.0
    return JobResult(
        job=job_name,
        nodes_processed=processed,
        nodes_modified=modified,
        errors=errors,
        duration_ms=round(duration_ms, 2),
        details={"stale_found": len(stale_ids), "retired_staging_found": 0,
                 "stage_cleanup_blocked": False, "stage_cleanup_reason": None},
    )


async def _evict_index_entries(
    engine: MemoryEngine,
    node_ids: list[str],
    budget_ms: float,
    start: float,
) -> tuple[int, int, int]:
    """Evict each node's index entries within the remaining budget."""
    processed = 0
    modified = 0
    errors = 0
    for node_id in node_ids:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if elapsed_ms >= budget_ms:
            break
        processed += 1
        try:
            await engine._delete_from_indexes(node_id)
            modified += 1
        except Exception:
            logger.warning(
                "index_compaction.evict_failed",
                extra={"node_id": node_id},
                exc_info=True,
            )
            errors += 1
    return processed, modified, errors
