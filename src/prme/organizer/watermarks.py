"""Per-scope, per-job organizer watermarks (dwamianm/prism#517).

A watermark records how far a job has read a scope, so a frequent schedule
does work only for memories created or changed since that job's last
completed run. The store keeps one row per scope and job. A scope is a user
id, or the empty string for an unscoped operator run.

The watermark is operational metadata. It holds no memory content, only a
timestamp and the id of the node a budget-stopped run reached. It is written
after the job's own writes have committed, so a crash re-runs the window
instead of skipping it.

Incremental execution is opt-in (``OrganizerConfig.incremental_enabled``).
With the option off the organizer reads and writes no watermark and every job
keeps its full-scope behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# Jobs whose candidate set is driven by node changes, so an incremental scan
# is sound: a node that was neither created nor updated since the previous
# run cannot have become newly eligible.
#
# deduplicate and alias_resolve qualify today. A duplicate or alias pair can
# only become newly eligible when at least one endpoint is created or updated,
# and each changed node is searched against the vector index (and, for aliases,
# its equivalent names), so the unchanged name twin need not be in the window.
# Discovery keeps the index's top-k bound: a semantic pair that neither
# endpoint ranks in its top neighbors can be missed.
# promote stays full-scope because a node becomes eligible when its age crosses
# promotion_age_days, a change no write records; a pure update window would
# never revisit it. snapshot_generation stays full-scope because a snapshot is
# built from an entity's neighborhood and edges, and creating an edge does not
# touch either endpoint's updated_at. Time-driven jobs (decay_sweep, archive,
# tombstone_sweep) and whole-corpus jobs (index_compaction, feedback_apply)
# stay full-scope as well. summarize stays full-scope because
# its candidate set is periods, not nodes: update_node can change event_time,
# which moves a node between periods, and a hard delete leaves no node row, so
# a change window over node rows cannot name a period a node has left. The old
# period is recoverable from the summary that recorded its sources
# (dwamianm/prism#523; see _job_summarize). consolidate stays full-scope
# because its unit of work is a cluster of the active node page the job reads,
# not a node: the greedy partition seeds at the earliest unassigned node, so a
# changed-node scan can produce a different member set and therefore a
# different consolidation lineage, and a cluster can also become eligible when
# a different node's retirement frees its neighbors or a member is deleted
# (dwamianm/prism#523; see _job_consolidate).
INCREMENTAL_JOBS: frozenset[str] = frozenset({"deduplicate", "alias_resolve"})

# Scoped keys are prefixed so an empty user id cannot share the row an
# unscoped operator run uses.
_SCOPED_PREFIX = "u:"


def scope_key(user_id: str | None) -> str:
    """The watermark scope for an organize call.

    ``""`` is the unscoped operator scope. Any user id, including an empty
    one, maps to a distinct ``u:`` prefixed key.
    """
    return "" if user_id is None else _SCOPED_PREFIX + user_id


@dataclass(frozen=True)
class OrganizerWatermark:
    """The last completed run and any resumable point of a job in one scope."""

    scope_key: str
    job: str
    completed_through: datetime | None
    resume_updated_at: datetime | None = None
    resume_id: str | None = None

    @property
    def resume_cursor(self) -> tuple[datetime, str] | None:
        """The keyset position a stopped run reached, if it stopped."""
        if self.resume_updated_at is None or self.resume_id is None:
            return None
        return (self.resume_updated_at, self.resume_id)


@dataclass(frozen=True)
class IncrementalWindow:
    """The slice of a scope one incremental job run may examine.

    ``updated_after``/``after_id`` are the exclusive keyset lower bound:
    either the last completed run's ceiling, or the cursor a budget-stopped
    run reached. ``ceiling`` is the run's upper bound, captured before any
    work so records written during the run are picked up by the next one.
    """

    scope_key: str
    job: str
    ceiling: datetime
    updated_after: datetime | None
    after_id: str | None = None
