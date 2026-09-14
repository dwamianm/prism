# RFC-0016: Durable Derivation Commits

**Status:** Partially implemented; durable extraction work, journaled plans, and fenced atomic commits; explicit plan revision and retired-stage collection implemented
**Date:** 2026-09-12
**Depends on:** RFC-0001, RFC-0002, RFC-0003, RFC-0004, RFC-0014

## Problem and evidence

An immutable source alone cannot reproduce nondeterministic model output. An
in-memory retry loop cannot survive a process exit. Retrying graph creation with
fresh UUIDs can duplicate facts; compensating rollback can delete a replacement
whose prior fact was already retired. An expired worker can also race its
successor unless the database rejects its final write.

Current fault-injection work has established these prerequisites:

- Source events and deferred raw NOTE jobs commit together on both backends.
  Local deferred processing prepares graph nodes and durable vector payloads
  within the pass budget, then atomically replaces its bounded lexical document
  batch. Only after that commit are individual source jobs acknowledged. Failed
  vector writes retain their own failures; failed lexical batches fall back to
  individual replacements. Cancellation or process exit before acknowledgement
  leaves replayable work. This batches raw indexing, not graph publication or
  LLM derivation, and does not make a pass one database transaction.
- `EXTRACTION_VALIDATED` operations preserve the first grounded model output and
  its source hash, owner, scope, model, schema and grounding versions. Indexing
  retries reuse that output. The record survives restart and abrupt process exit.
- Local vector metadata and numerical payloads commit together. Startup repairs
  unsaved vectors and stale snapshot deletions without inference.
- Named replacements commit as a batch. Normal materialization failures roll
  back tracked artifacts while preserving previous facts.
- Native storage operations retain their locks until their worker threads finish
  after cancellation. Failed engine startup releases resources.
- In-process materialization records completed node/edge writes before caller
  cancellation propagates. Cleanup waits for queued index writes and withstands
  repeated cancellation. A final replacement that already committed is retained.
  These cleanup guarantees do not make intermediate graph writes invisible or
  recover a process killed before cleanup; the batch commit protocol is still needed.

These prerequisites motivated replacing compensating cleanup with journaled
plans and atomic graph publication. Normal `_materialize()` now uses that path;
the older interleaved graph/index writer is no longer used by ingestion.

## Implemented commit primitive

Internal `DerivationPlan` records now preserve source identity, fixed artifacts,
existing-node snapshots, complete replacement edges, numerical embeddings and
lexical inputs. Both event stores journal the first plan with a checksum and
reject changes to the same plan identity. The serialized plan is stored as a JSON
string inside the operation payload so PostgreSQL JSONB normalization cannot
change numerical details such as signed zero.

`GraphStore.commit_derivation()` verifies the exact journaled plan inside its
transaction, checks dependencies, and commits all nodes, edges, replacements and
one immutable receipt together. PostgreSQL writes prepared vectors in that same
transaction; DuckDB requires matching durable vector payloads staged beforehand.
Retry returns the existing receipt and does not reactivate subsequently archived
nodes. Dependency comparison normalizes timestamp offsets, so moving a pack
between database timezones does not falsely invalidate unchanged nodes.

Tests cover failures after every node/edge position, independent readers,
concurrent replay, changed dependencies, source/scope invariants, hypothetical
replacement rejection, and DuckDB process exits during node insertion, after
replacement, and after commit before acknowledgement. Restart completes the same
plan without partial graph state or new model calls. These are component tests.

`VectorIndex.stage()` now durably binds a prepared node identity to its source
text and numerical payload. Identical retries reuse the key and repair a missing
native vector without inference; conflicting inputs are rejected. Startup rebases
the key sequence beyond recovered metadata, fixing a reproduced process-exit case
where DuckDB recovered a payload but reused its key on the next insertion.
`LexicalIndex.stage()` compares exact stored fields under the directory writer
lock and commits only missing documents as one batch. It never deletes existing
documents to retry. Tests cover cancellation, lost commit acknowledgements,
concurrent in-process retries, restart and abrupt exits at both index boundaries.

Plan journaling now reserves each new node ID for its exact prepared-operation
identity in the same transaction as the plan and work binding. Reservations are
global across sources and tenants; concurrent plans cannot allocate a shared ID,
and even a new revision reusing the old plan UUID cannot reuse its artifacts.
Losing concurrent planners for the same source/revision still converge on the
first saved plan before reserving identities.

The derived `derivation_artifact_owners` and `derivation_registered_plans` tables
are incrementally rebuilt from checksummed journal records at startup, in pages
of 128 records. Historical overlapping allocations become ambiguous (NULL owner)
instead of making the pack unreadable or assigning ownership arbitrarily. Those
identities cannot be newly reserved. Invalid historical plan records are left
unregistered with a warning containing only their operation ID; source reads
remain available. Any future collector must disable reclamation when registration
is incomplete and must retain ambiguous identities. Registration alone is not authorization to delete: the external-stage fence
below must also prevent obsolete workers from recreating reclaimed entries.

Managed DuckDB index staging now holds a work-row transaction on an independent
cursor over the same database for the duration of each native write. It verifies
the exact current saved plan, its input membership and the claim before writing,
then revalidates the lease afterward. Touching the work row prevents a concurrent
connection from advancing its generation or plan revision while the native call
is in flight. The index's primary connection can commit durable vector payloads
normally. An expired or cancelled in-flight operation may leave plan-owned staged
inputs; ownership cannot transfer until that operation finishes. Cancellation
keeps index and connection locks held until the native operation returns.
Standalone index `stage()` calls without this fence remain an unmanaged component
API. PostgreSQL stages prepared indexes in its fenced graph transaction.

`index_compaction` collects up to 500 staged node identities per pass from
explicitly replaced revisions. It verifies their saved plan/source, requires
unique ownership and absence from the graph, and scopes them through the original
event owner. Current failed, pending and running plans remain retryable. Ambiguous
legacy allocations are retained; unregistered or invalid journals block this
stage cleanup and appear in job details. No age-based abandonment is inferred.
Lexical deletion commits before vector metadata removal. A failed deletion or
process exit retains that vector staging row as a retry anchor; native removal
errors propagate rather than discarding the metadata. Sources, immutable plans
and permanent identity reservations are retained. Existing archived graph nodes
remain eligible for ordinary index compaction. Rebuild must not run concurrently
with a derivation publication.

The normal ingestion pipeline now prepares a scoped in-memory graph overlay,
batches embedding inference, journals the first complete plan, stages external
indexes and calls the atomic commit. Reused entities are referenced without
re-indexing. Unrelated scanned entities are not commit dependencies. Explicit
older-effective updates remain historical facts without retiring later state.
Retries load the saved plan before calling either provider. An existing receipt
skips staging and publication, including after archival. Existing unjournaled
derived nodes are rejected as requiring explicit migration rather than duplicated.

Public-ingestion fault tests cover post-plan/index writes, every graph write
kind, post-commit failure, cancellation and concurrent attempts on both backends.
DuckDB subprocesses exit after plan save, each index stage, a graph insertion,
and commit. Explicit retry after reopening uses the same plan without inference.

Source append now atomically queues durable extraction work alongside raw
indexing. Owner-scoped `extraction_status`, `retry_extraction`, and
`process_extractions` are available through the engine, sync client, HTTP, MCP
and CLI. Explicit processing discovers unfinished jobs after restart; retrieval
never invokes it and no daemon is installed. Budgets apply between jobs.

Failure reporting preserves provider/schema categories before traversing lower
implementation causes. In particular, a provider TimeoutError must not become
CancelledError merely because asyncio enforced the timeout by cancelling its
inner call. Caller cancellation retains the separate Cancelled work code.
Unknown failures retain bounded class names; exception chains are cycle-safe and
bounded. Provider messages and response bodies are not recorded. Public blocking
ingestion exposes the sanitized reason_code alongside its persisted event_id.
The same code survives restart in extraction status on either backend.
Deferred raw-materialization failures and serialized write-job failures also log
bounded categories instead of exception messages or tracebacks. A failed job's
original exception still reaches its caller; formatting a provider exception is
not required for the queue to continue processing healthy jobs. This is scoped
to those failure paths, not a claim that all application logging is sanitized.

Claims increment a persistent generation and attempt count. Heartbeats renew
live leases, while extraction journaling, plan binding and graph publication
check ownership inside their transactions. Completion and its receipt commit
together. Pending/running work follows append order within an owner and scope;
terminal failures allow later work to proceed. Retry retains attempt history,
and cannot preempt active work or repeat completion.

PostgreSQL uses row locks. DuckDB touches the work row inside each publication
transaction so a concurrent claim conflicts and rolls back. Mutable work fields
must remain unindexed: a local two-connection test reproduced an indexed-status
UPDATE rewriting rows and allowing both transactions to update. Schema startup
drops the former status index. A fault test blocks graph publication across
lease expiry, attempts takeover from a separate connection, and verifies rollback
before the successor can publish on both backends.

Tests also cover abrupt exit immediately after admission, provider outage and
restart recovery, cancellation, heartbeat renewal, empty extraction, owner
isolation, and stale workers attempting all three durable write boundaries.
Explicit `retry_extraction(..., replan=True)` now queues a new plan revision from
the same grounded extraction. It advances the work generation and journals
`DERIVATION_REPLAN_REQUESTED` in one transaction, preserving the previous plan.
It cannot preempt a live worker or revise completed work. Prepared operation IDs
include the revision after v1; the event still has one completion receipt.
Existing v1 checksums remain unchanged when the newly defaulted revision field
is absent from historical payloads. Processing may recompute embeddings, but
does not call extraction again once that output is saved. Automatic replanning
and model-output/grounding-policy revisions are not implemented.

DuckDB fresh schemas include the revision column directly. Older work tables
checkpoint after adding it: a subprocess test reproduced DuckDB 1.4.4 failing
to replay the ALTER from WAL after abrupt exit. The migrated-pack test now
verifies both preserved legacy work and recovery after subsequent plan journaling.

Legacy sources are not automatically enrolled in extraction work. Unmanaged or
ambiguous staging remains conservatively retained; collection only covers the
explicitly replaced, uniquely owned revision protocol above.

Historical plans using materialization policy `relationship_claims_v3` keep their
original behavior. Current plans use `claim_qualifiers_v5`: relationship
outputs become source-cited FACT nodes and normal subject/object association
edges, unresolved personal references remain event-local, and claim polarity and
explicit conditions are preserved in node metadata. Type-qualified object
references avoid arbitrary namesake links. Conditional claims start with an
unknown condition state and stay out of DEFAULT retrieval until confirmed.
Existing `source_passage_v1`, `typed_references_v2`, and
`event_local_references_v4` plans remain readable and replay their saved artifacts
unchanged. Recovery never regenerates an existing plan under the current policy
implicitly; historical committed edges are not migrated.

## Required behavior

Acknowledged ingestion must retain both the immutable source and the request to
extract it. Model failure must leave inspectable, retryable work. Once a complete
derivation plan exists, recovery must reuse its identities, timestamps, policy
decisions and numerical embeddings without calling a model again.

A graph derivation becomes visible as one database transaction: all new nodes,
edges, valid replacement transitions, and its completion receipt commit together.
Replaying a committed plan returns the existing receipt. It must not create new
facts, repeat replacements, alter scores, or reactivate retired knowledge.

The transaction must also verify that the worker still owns the current work
generation and plan. Checking a lease in application code before writing is
insufficient: ownership can change between that check and the commit.

## Durable records

### Extraction work

A mutable work table records `event_id`, state, attempt count, next-attempt time,
lease expiry, monotonically increasing fencing generation, current plan ID, and
a sanitized last error. Ownership and scope come from the source event. An
append-time sequence orders eligible work independently of user-supplied event
timestamps. Raw indexing remains a separate job and status.

Claims and explicit plan-revision transitions advance the generation. The final graph transaction
must lock/check the work row and reject an expired or superseded generation.
Heartbeats extend a live lease; they do not substitute for commit fencing.
If event-loop starvation expires an uncontested lease after provider return,
the rejected worker attempts one immediate database reclaim under a new
generation. It reuses a saved extraction or plan when one reached its durable
boundary; otherwise the provider may run again. Failure to reclaim falls back
to the ordinary retry policy, and the expired generation never publishes.
Failure during provider I/O must not hold graph/database locks.

### Saved extraction

The existing `ExtractionRecord` is the durable model-output boundary. It contains
grounded structured output rather than the raw SDK response. Provider or schema
changes require an explicit revision identity; they must not overwrite the
existing `EXTRACTION_VALIDATED` operation. A cache hit is not graph completion.

### Prepared derivation

An immutable `DERIVATION_PREPARED` operation records:

- Source/revision identity, content hash, owner and scope.
- Plan schema, grounding, materialization and scoring-policy versions.
- Complete new node/edge values, including their fixed IDs and timestamps.
- Referenced existing entities and the scope/lifecycle conditions for reuse.
- Explicit replacement targets and their expected state.
- Exact embedding values, dimension, model/version, and indexed-content hash.
- Lexical documents associated with the newly created nodes.

The plan is persisted before external index writes begin. Canonical serialization
and a content checksum detect accidental plan mismatches. A retry of the same
plan ID with a different payload is an error, not an upsert. Node defaults must
not be re-evaluated during replay.

The planner reads an overlay of staged nodes/edges and durable graph data. It
does not write through `WriteQueueGraphWriter` as it resolves entities or facts.
Reused entities must not be re-indexed with changed descriptions before commit.
Entity matching remains scoped and conservative; preserving an ambiguous
duplicate is preferable to silently joining unrelated people.

### Commit receipt

`DERIVATION_COMMITTED` is appended in the graph transaction with the plan ID,
generation and resulting node/edge IDs. The mutable work row becomes complete in
that same transaction. A lost client acknowledgement can then be resolved by a
scoped status read. Existing committed operation payloads are never overwritten.

## Backend execution

### DuckDB, USearch and Tantivy

1. Claim work and load/extract its durable model output.
2. Prepare and journal the complete plan, including embeddings.
3. Idempotently stage vectors and flush lexical documents for new, invisible
   node IDs. Native vector payloads are durable before graph commit.
4. In one fenced DuckDB transaction, insert nodes and edges, validate/apply the
   replacement set, append the receipt, and complete the work row.
5. Retire obsolete index entries after commit; normal graph eligibility filters
   must already prevent those entries from resurfacing.

Candidate generation must continue joining staged index IDs to visible, owned
graph nodes. A losing worker must never compensate by deleting another worker's
committed nodes or shared index entries. Index staging therefore needs immutable,
plan-specific write identities, not blind delete-and-reinsert behavior.
Compaction may collect abandoned staging records only when no live plan or
committed graph references them.

### PostgreSQL

Compute embeddings before opening the transaction. Use one connection and one
transaction for the work-row fence, graph insertions, embedding/lexical columns,
replacement transitions, immutable receipt and completion state. The existing
standalone `PgVectorIndex.index()` cannot stage a vector for a missing node.
It must not be reused as though it provided that behavior.

Lock referenced existing rows in a consistent order. Unique receipt identity
and work-row fencing must make concurrent retries converge on one commit.

## Temporal and concurrency constraints

Within an owner/scope, resolve stateful replacements in durable work order.
Source event time and knowledge time remain separate. An older effective event
does not gain authority to retire a later assertion merely by arriving last.
Unqualified coexisting values and hypothetical changes do not imply replacement.

A prepared plan can become stale because an organizer or explicit API operation
changed a referenced node. The commit must reject that stale plan atomically.
Replanning creates a new immutable plan revision and advances the work generation;
it does not edit the old plan or allow its worker to commit later.

## Developer-facing contract

Expose extraction work status and bounded, owner-scoped processing separately
from raw NOTE `processing_status()`/`process_pending()`. Status should distinguish
waiting for inference, prepared, complete, and actionable failure. A saved
extraction or a list of partially derived nodes is not a completion signal.

Retrieval should not unexpectedly start paid model work for unrelated tenants.
Explicit processing uses a cooperative total budget and a provider-call timeout;
neither is permission for a stale worker to publish results. Every failure retains
the event ID and a sanitized reason. Complete records and plans remain inspectable
through the source owner's boundary.

## Acceptance tests before enabling automatic recovery

- Kill a process after source append, extraction journaling, plan journaling,
  each index stage, and database commit before acknowledgement. Reopening either
  completes the same plan or reports pending work; committed fact IDs/counts stay
  unchanged and inference is not repeated once the required output exists.
- Inject a failure at every node, edge and replacement position. No partial
  graph derivation or retired prior fact becomes visible after transaction failure.
- Expire a lease while its worker runs, let a successor commit, then resume the
  old worker. The old generation cannot commit or delete its successor's artifacts.
- Retry concurrently on both backends; verify one immutable receipt, scoped
  node/edge identity, and correct behavior after a lost acknowledgement.
- Reject source, scope, plan-checksum, embedding-model/dimension and expected-state
  mismatches before exposing graph changes. Test existing-entity reuse separately.
- Exercise provider outage/recovery, cancellation, empty extraction, late effective
  events, interdependent updates, and schema/policy revisions through public APIs.
- Rebuild the covered derived state from saved plans in a fresh pack and compare
  identities, values, provenance, lifecycle and retrieval under the same config.

## Limits of the replay claim

This protocol covers newly journaled ingestion derivations. Direct `store()`
requests use the simpler source/initial-node journal and materialization job in
RFC-0002; they do not use derivation leases or fenced graph commits. Those jobs
recover the initial typed node and indexes, without replaying optional post-store
reinforcement, supersedence or QA pairing. Existing organizer
and manual graph mutations also need complete operation payloads before PRME can
claim full-state replay. Legacy packs need an explicit baseline snapshot for
state that was never recorded; nondeterministic historical model output cannot
be retroactively recovered from a raw event alone. No implementation milestone
or synthetic test substitutes for that evidence.

## Direct-store vector snapshot cadence (2026-09-12)

Direct `store()` and deferred raw-source materialization rely on the vector
backend's committed numerical payload and metadata before completing work.
They do not require a complete USearch snapshot rewrite for every event.
Local indexing persists both rows in one DuckDB transaction before adding the
native vector; startup restores keys missing from a snapshot without calling
an embedding provider. The configured `vector_save_interval` controls periodic
snapshot writes, and engine close still flushes the derived snapshot. PostgreSQL
persists its vector row in its existing index transaction.

This removes redundant whole-index writes from direct materialization. Lexical
commit and failure accounting are unchanged. A failed scheduled snapshot still
propagates through the index operation and leaves work retryable. Abrupt-exit
checks cover completed public stores with no snapshot and with an older partial
snapshot, preserving owner isolation and requiring no embedding inference during
reopening. The source, graph node and work status remain durable independently
of the derived snapshot's cadence.

### Synchronous-client interpreter shutdown (2026-09-14)

`MemoryClient` tracks live instances with weak references and installs one
process-wide cleanup hook in Python's pre-thread-shutdown phase. Forgotten
clients therefore close their engine and flush derived indexes before
`concurrent.futures` disables the executor used by storage cleanup. Explicit
`close()` remains authoritative and removes the instance from that registry.
The fallback on runtimes without the early hook is best effort. This changes
shutdown ordering only; the event, direct-store journal, and materialization
completion boundaries above remain unchanged.


## Entity-profile preparation recovery

The separate profile publication primitive now journals `PROFILE_PREPARED` inputs
before local staging, reuses exact prepared requests and atomically records
replacement of pending preparations. It shares the global artifact ownership
registry with derivations. A changing unindexed work epoch fences local native
writes against both replacement and publication, including caller cancellation.
The completed profile work row commits with its publication receipt.

Scoped Python `profile_jobs`, `resume_profile` and `process_profiles` expose
explicit recovery without inference. Startup reconstructs missing queue and
reservation rows from checksummed preparations and validated publication or
replacement receipts, including when registration markers already exist.
Operational attempt diagnostics reset on queue reconstruction. Invalid profile
journals disable retired derivation collection as well, because global artifact
ownership can no longer be established. Profiles expose explicit owner-scoped abandonment and fenced abandoned-stage
collection. Discard and collection receipts preserve their history; collection
retains ambiguous or mismatched inputs and never deletes a published graph node.
They have no automatic scheduler. See [RFC-0015](RFC-0015-Self-Organizing-Memory.md)
and the [profile guide](ENTITY-PROFILES.md) for the public contract.
