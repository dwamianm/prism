# RFC-0016: Durable Derivation Commits

**Status:** Partially implemented; durable extraction work, journaled plans, and fenced atomic commits; plan revision and abandonment collection pending
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

Compaction retains missing graph identities with a durable vector staging claim.
Once a graph node exists and is archived, normal compaction can evict it. This is
conservative retention: abandoned staging requires explicit deletion or rebuild
until the coordinator provides a fenced abandonment policy. Rebuild must not run
concurrently with a derivation publication.

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
Version 1 still saves one plan per source, with no revision switch or automatic
replanning. Legacy sources are not automatically enrolled in extraction work.
Abandoned index staging still requires an explicit maintenance policy.

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

Only a claim transaction may advance the generation. The final graph transaction
must lock/check the work row and reject an expired or superseded generation.
Heartbeats extend a live lease; they do not substitute for commit fencing.
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

This protocol covers newly journaled ingestion derivations. Existing organizer
and manual graph mutations also need complete operation payloads before PRME can
claim full-state replay. Legacy packs need an explicit baseline snapshot for
state that was never recorded; nondeterministic historical model output cannot
be retroactively recovered from a raw event alone. No implementation milestone
or synthetic test substitutes for that evidence.
