"""Per-scope, per-job organizer watermarks (dwamianm/prism#517).

A watermark records how far a job has read a scope, so a frequent schedule
does work only for memories created or changed since that job's last
completed run. The store keeps one row per scope and job. A scope is a user
id, or the empty string for an unscoped operator run.

The watermark is operational metadata. It holds no memory content, only a
timestamp and the id of the node a budget-stopped run reached. It is written
after the job's own writes have committed, so a crash re-runs the window
instead of skipping it.

A scope is keyed by the exact user id text, because node ownership is an exact
string match: two spellings of one UUID are two owners for every read, so they
keep separate watermarks. A scope whose owner has an owner-wide memory erasure
never gets a new row: the purge deletes the owner's rows, whatever spelling
they were keyed under, and a later organize call must not put the plaintext
owner key back. The store also applies each upsert monotonically, so two
concurrent runs converge on the furthest completed position and on the newer
resume cursor instead of the later write winning outright.

A run that stops on a failure records the node it could not get past, with the
number of runs that have now failed there, so a bounded number of attempts
bounds the stall instead of pinning the scope at that node for good.

Incremental execution is opt-in (``OrganizerConfig.incremental_enabled``).
With the option off the organizer reads and writes no watermark and every job
keeps its full-scope behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


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
    one, maps to a distinct ``u:`` prefixed key. The user id is kept exactly as
    given: ownership matches the exact string, so two spellings of one UUID are
    two owners and must not share one watermark.
    """
    return "" if user_id is None else _SCOPED_PREFIX + user_id


def scope_owner(key: str) -> str | None:
    """The user id in a scoped key, or ``None`` for the unscoped operator scope.

    The prefix is decoded here so a writer that must consult owner-level state
    reads the same encoding ``scope_key`` produced. Only the two defined
    encodings are accepted, so a mis-encoded key fails instead of being read
    as the operator scope.
    """
    if key == "":
        return None
    if not key.startswith(_SCOPED_PREFIX):
        raise ValueError(f"unknown organizer watermark scope key: {key!r}")
    return key[len(_SCOPED_PREFIX):]


def owner_scope_keys(keys, owner_id: str) -> list[str]:
    """The scope keys in *keys* that name *owner_id* under any UUID text form.

    The purge deletes the erased owner's rows, and ownership is exact while a
    caller may have used any text form the standard parser accepts, so the
    purge cannot rely on one spelling. A key that is neither the operator scope
    nor a scoped key is ignored: a corrupt key must not stop an erasure from
    finishing.
    """
    try:
        owner = UUID(str(owner_id))
    except (AttributeError, TypeError, ValueError):
        return []
    matched: list[str] = []
    for key in keys:
        try:
            candidate = scope_owner(key)
        except ValueError:
            continue
        if candidate is None:
            continue
        try:
            if UUID(candidate) == owner:
                matched.append(key)
        except (AttributeError, TypeError, ValueError):
            continue
    return matched


@dataclass(frozen=True)
class OrganizerWatermark:
    """The last completed run and any resumable point of a job in one scope."""

    scope_key: str
    job: str
    completed_through: datetime | None
    resume_updated_at: datetime | None = None
    resume_id: str | None = None
    stalled_id: str | None = None
    stalled_attempts: int = 0

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
    ``stalled_id``/``stalled_attempts`` are how many failed runs have already
    stopped at that node, so a repeated failure can be bounded and skipped.
    """

    scope_key: str
    job: str
    ceiling: datetime
    updated_after: datetime | None
    after_id: str | None = None
    stalled_id: str | None = None
    stalled_attempts: int = 0
