# RFC-0016: Durable Derivation Commits

**Status:** Draft; prerequisites implemented, commit protocol not yet implemented
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

These are tested behaviors, not evidence of complete derivation replay. The
current `_materialize()` still interleaves random-ID graph and index writes.
Its tracker cannot recover a process killed halfway through those writes.

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
