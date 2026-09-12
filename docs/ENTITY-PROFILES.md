# Entity profiles

`consolidate_knowledge()` creates a searchable summary from source nodes that
mention an entity. It preserves whole source excerpts, dates and provenance
within a token budget. The entity association is marked as inferred: a profile
quotes what was recorded, including uncertainty and conditions.

```python
from prme import MemoryClient, Scope, StaleProfileError

with MemoryClient("./project_memory") as client:
    client.store(
        "Aurora deploys from main only after approval.",
        user_id="alice", scope=Scope.PROJECT,
    )
    client.store(
        "Aurora keeps nightly backups for 30 days.",
        user_id="alice", scope=Scope.PROJECT,
    )

    def rebuild() -> int:
        return client.consolidate_knowledge(
            user_id="alice", scope=Scope.PROJECT,
            entity_names=["Aurora"], max_profile_tokens=1000,
        )

    try:
        created = rebuild()
    except StaleProfileError:
        # A source or another rebuild changed during preparation.
        # This fresh call rereads the current sources; retry at most once here.
        created = rebuild()

    response = client.retrieve(
        "Aurora deployment and backup policies",
        user_id="alice", scope=Scope.PROJECT, include_cross_scope=False,
    )
    print(response.bundle.render())
```

`MemoryEngine` exposes the same arguments as an async method. No extraction LLM
is required; the configured embedding provider indexes the profile.

The method returns the number of published profiles. It requires at least two
eligible source nodes per name. Earlier generated profiles never count as new
source evidence. Names use literal, case-insensitive word boundaries; automatic
name discovery is heuristic, so supply known names when available. Whole excerpts
that exceed the remaining budget are skipped. If none fits, no replacement is
published. The budget includes labels, dates and provenance, measured with the
configured packing tokenizer.

Every profile stays within one owner and scope. Omitting `scope` processes each
scope separately. `Scope.PROJECT` is a scope category, not a named project ID;
use separate memory packs to isolate different projects for the same owner.
Profile construction scans the active scope in pages, including older sources
beyond the newest 5,000 nodes.

Each replacement becomes visible atomically after index preparation. Until it
commits, the previous profile stays active. Embedding and storage failures raise
an exception. Before staging, the complete profile, source snapshots, fixed UUID
and numerical embedding are saved in a checksummed immutable operation. An
unchanged retry reuses that preparation; a changed request replaces it explicitly.
A successfully published rebuild retires prior profiles for that name.

Prepared work survives restart. The sync client and async engine expose the same
owner-scoped recovery methods:

```python
with MemoryClient("./project_memory") as client:
    jobs = client.profile_jobs(user_id="alice", scope=Scope.PROJECT)
    result = client.process_profiles(
        user_id="alice", scope=Scope.PROJECT, limit=20, budget_ms=5000,
    )
    print(result["processed"], result["pending"], result["errors"])
```

`profile_jobs()` defaults to pending work; `status="complete"` and
`status="abandoned"` inspect the other states. Each dictionary includes `plan_id`
(the fixed profile node UUID), scope, attempts and a bounded error code.
`resume_profile(plan_id, user_id=...)` retries one saved preparation without
calling extraction or embedding models. It returns `None` for an unknown or
foreign identity. Replaying completed work returns its original identity without
restaging or reactivating a subsequently archived profile. Changed dependencies
or a replaced preparation raise `StaleProfileError`; use an explicit
`consolidate_knowledge()` call to prepare current inputs.

`process_profiles()` reports processed, failed and pending counts plus per-job
error codes. Its budget is cooperative between jobs, so one publication may
exceed it. Failed jobs move behind unattempted work. Cancellation leaves unfinished
preparations available for a later pass. Missing queue rows and ownership entries
are reconstructed from validated immutable operations at startup; attempt counts
and timestamps are operational diagnostics and reset when those rows are rebuilt.

A multi-entity call contains separate publications; earlier names may succeed
before a later one fails. Profiles without enough qualifying sources are retired
on an explicit rebuild. Publication completion records graph visibility; predecessor
index eviction happens afterward. Use `discard_profile(plan_id, user_id=...)` to abandon a preparation that should
never publish. It returns `False` for unknown, foreign or completed work, and
`True` once abandoned. This preserves source nodes, published profiles and the
immutable preparation. A new consolidation can prepare a fresh identity.

```python
with MemoryClient("./project_memory") as client:
    for job in client.profile_jobs(user_id="alice", scope=Scope.PROJECT):
        client.discard_profile(job["plan_id"], user_id="alice")
    cleanup = client.collect_profile_staging(
        user_id="alice", scope=Scope.PROJECT, limit=20, budget_ms=5000,
    )
    print(cleanup["collected"], cleanup["remaining"], cleanup["errors"])
```

Collection verifies that each abandoned preparation uniquely owns its staged
inputs and has no graph node or publication receipt. It removes the exact lexical
document before its numerical vector, then appends a collection receipt. Failed
or cancelled deletion remains discoverable across restart; retries require no
model calls. An old managed worker cannot recreate collected entries. The result
reports collected preparations, failures, remaining preparations, bounded per-job
errors and an optional `blocked_reason`. Failed attempts move behind unattempted
work. The budget applies between preparations. Invalid or incomplete ownership
journals block collection, and ambiguous identities or mismatched native entries
are retained. PostgreSQL has no external pre-publication index entries; collection
validates and acknowledges its abandoned preparation without deleting graph data.

The CLI exposes the same recovery operations for a local memory pack:

```bash
prme profile-jobs ./project_memory/memory.duckdb --user-id alice --scope project --format json
prme process-profiles ./project_memory/memory.duckdb --user-id alice --scope project --limit 20 --budget-ms 5000 --format json
prme resume-profile ./project_memory/memory.duckdb PROFILE_UUID --user-id alice --format json
prme discard-profile ./project_memory/memory.duckdb PROFILE_UUID --user-id alice --format json
prme collect-profile-staging ./project_memory/memory.duckdb --user-id alice --scope project --format json
```

Every command requires `--user-id`. Listing and batch commands accept `--scope`;
omission visits every scope for that owner. `profile-jobs --status abandoned`
lists abandoned preparations. `--limit` accepts 1–1000; processing and collection
budgets are finite nonnegative milliseconds, checked between preparations.
Zero budget performs no per-preparation work and still reports remaining work.

JSON output goes to stdout and diagnostics to stderr. Processing failures or
blocked collection print the result and exit 1. Unknown or foreign resume IDs
both return `{"profile_id": null, "resumed": false}` with exit 1. Discard returns
`discarded: false` and exit 1 for unknown, foreign or completed work; repeating a
successful abandonment returns true. A pass that stops at its limit or budget
without failures exits 0; inspect `pending` or `remaining` before declaring the
queue drained. Invalid command arguments exit 2. Unexpected errors go to stderr.

The positional file is the local target even when `PRME_DATABASE_URL` is set.
These commands are local operator tools: `--user-id` selects ownership, but is
not authentication against another local operator with file access. Use the
Python APIs with a PostgreSQL configuration for that backend.

There is no automatic profile scheduler. These explicit Python and CLI operations
are separate from the organizer's `consolidate` job. Journals and identity
reservations remain durable after index collection.
