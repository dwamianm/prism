# RFC-0002: RMS Event Store and Append-Only Log

**Status:** Draft
**Tier:** 1 — Storage and Integrity
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001

---

## 1. Abstract

This RFC specifies the Event Store: the append-only, tamper-evident log that is the single source of truth for the entire RMS system. It defines the storage schema, operation types, integrity guarantees, compaction semantics, and the portability artifact format.

All derived state in RMS — memory objects, graph edges, entity snapshots, scores — is computed from this log. If derived state is lost or corrupted, it is rebuilt by replaying the log. The log itself is never rebuilt; it is always the ground truth.

---

## 2. Storage Backend Requirements

Implementations MUST use a storage backend that provides:

- Append-only writes (no in-place modification of committed records).
- ACID transaction semantics for individual write operations.

PRME graph replacements use a transaction covering lifecycle state, replacement
pointer, and provenance edge. `supersede_many` applies a batch atomically and
rejects self-replacement, retired replacements, or pairs across users/scopes.
New LLM ingestion uses the journaled derivation protocol in RFC-0016: saved
extraction and prepared inputs, idempotent index staging, and fenced atomic
graph publication. New duplicate/alias merges atomically append a checksummed
`ORGANIZER_MERGED` operation containing complete node and relationship inputs and
outputs. This is distinct from full replay of historical organizer and manual
mutations, whose complete operation inputs are not all journaled.
- Efficient range scans by timestamp and stream.
- Content-addressed deduplication by `content_hash`.

The reference implementation uses DuckDB for the event store. Implementations MAY use alternatives (SQLite, PostgreSQL, a custom log format) provided the above requirements are met and the portability artifact format (Section 9) is supported.

Local engines optionally accept `duckdb_threads` at instance creation. The
default retains DuckDB's worker setting; conflicting settings for concurrent
opens of the same file fail before schema initialization. This runtime resource
control does not change event semantics or persist in the portable pack.

An optional expected `namespace_id` binds a fresh local pack inside DuckDB and
must match on later opens, before normal schema/backfill/index startup. Existing
unbound packs are not silently adopted. The workspace registry records initialized
projects so a missing database cannot be replaced silently. This identity metadata
is part of the physical artifact, not a shared-table filter or access grant.

---

### Raw-source recovery

Both fast ingestion and LLM ingestion atomically queue raw-source indexing with
the event. A provider failure leaves that job pending; scoped retrieval or explicit
processing can materialize the original NOTE after restart. Its ID and timestamps
come from the source event. Processing completion acknowledges this raw index,
not LLM derivation completion. Model summaries cannot overwrite source indexes.
Events are immutable, so the API's inherited `updated_at` equals `created_at`
rather than the time a row happened to be read.

### Historical source clocks

LLM ingestion accepts an explicit timezone-aware `event_time` through the engine,
synchronous client, HTTP and MCP. Batch messages can supply individual clocks.
`timestamp` continues to record admission time. Relative temporal references in
new derivation plans use `event_time` when provided and otherwise `timestamp`.
Raw NOTE recovery retains the saved source clock, and extracted claims without a
resolved temporal reference inherit it. Explicit older-effective replacements
remain historical rather than retiring later facts. Existing saved plans retain
their original timestamps on retry; this is not a retroactive temporal migration.
Timezone-free new imports are rejected; missing source time is not guessed.

### Direct typed storage recovery

Content hashing covers the exact source string, including empty text and
whitespace. New empty events carry SHA-256 of the empty byte sequence, not an
empty hash field, so they can satisfy the same durable source-binding checks.
This correction does not rewrite historical event rows.

New `store()` calls commit the event, an `event_materializations` job and a
`DIRECT_STORE_REQUESTED` operation in one database transaction. The operation
contains a versioned complete initial `MemoryNode` snapshot and source binding,
including its generated ID, classification, confidence, timestamps and TTL. Its
UUID is derived from the event ID. A checksum covers the serialized record; the
record remains a string within the JSON payload so JSONB numeric normalization
does not change it. Reads verify the checksum and source owner, scope, session,
content hash, content and evidence reference before recovery.

Recovery creates a missing graph node from the saved values and repairs its
indexes without an LLM. Existing graph state is retained, including retirement.
Lexical replacement commits deletion and insertion together; vector replacement
publishes a new durable vector before removing old keys. Failure retains pending
work and the previously healthy search path. `processing_status()` completion
covers this node and indexing, not optional reinforcement, supersedence or QA
pairing. A graph creation failure raises `MaterializationError` with the accepted
event ID; index failures remain nonfatal after confirming the node is durable.
Legacy direct stores have no repair record and are not retroactively queued.
This does not add cross-process work fencing or exactly-once execution.

### Validated extraction journal

LLM ingestion now appends an `EXTRACTION_VALIDATED` operation before graph
materialization. Its stable operation ID is derived from the source event ID;
concurrent attempts retain the first committed result. Both backends verify the
event's owner, scope, and content hash before accepting a record. The operation
contains structured grounded output, provider/model names, creation time, and
explicit schema/grounding versions, excluding credentials and raw SDK responses.
An indexing retry loads the saved output instead of calling the LLM again.

`get_extraction(event_id, user_id=...)` reads this record through the source owner
boundary in the engine, sync client, HTTP and MCP. Empty extraction results are
also recorded. A saved extraction does not mean graph materialization completed
and lexical grounding does not establish semantic truth. Interrupted extraction
jobs and saved derivation plans are recovered using RFC-0016. Explicit replanning
can revise a derivation from the same saved extraction. Re-extraction under a new
policy needs a future revision protocol; it must not overwrite this operation.

### Current source-reading API

`MemoryEngine.get_event(event_id, user_id=...)` enforces optional owner scoping;
`MemoryClient` exposes the same operation. `get_event_nodes(event_id, user_id=...)`
requires an owner and reads graph evidence references directly, including retired
nodes. This lookup avoids races from selecting the most recently created node.
HTTP store and MCP store receipts use it to identify their own created node.
HTTP event endpoints and MCP `memory_get_event` bind source access to the current
principal. These APIs expose durable sources and current derivations; they do not
claim complete graph replay from the event log.

## 3. Event Log Schema

```sql
CREATE TABLE events (
    id             TEXT PRIMARY KEY,         -- UUID
    ts             TIMESTAMPTZ NOT NULL,
    stream         TEXT NOT NULL,
    actor_id       TEXT NOT NULL,
    actor_type     TEXT NOT NULL,            -- ActorType enum (RFC-0001 §5)
    role           TEXT NOT NULL,            -- Role enum (RFC-0001 §6)
    namespace_id   TEXT NOT NULL,
    content        TEXT NOT NULL,
    content_hash   TEXT NOT NULL,            -- SHA-256 hex
    metadata       JSONB,
    sequence_num   BIGINT NOT NULL,          -- Monotonically increasing within stream
    prev_hash      TEXT                      -- SHA-256 of prior event in stream. NULL for first event.
);

CREATE UNIQUE INDEX idx_events_stream_seq ON events(stream, sequence_num);
CREATE INDEX idx_events_ts ON events(ts);
CREATE INDEX idx_events_namespace ON events(namespace_id);
CREATE INDEX idx_events_actor ON events(actor_id);
```

**`prev_hash`** implements a tamper-evident chain within each stream. A verifier can confirm log integrity by walking the chain and re-hashing each record.

---

## 4. Operation Log Schema

Operations record actions taken on the memory system itself — object creation, transitions, scoring updates, and organiser decisions. They are distinct from Events (which record what happened in the world) but are stored in the same log.

```sql
CREATE TABLE operations (
    id             TEXT PRIMARY KEY,         -- UUID
    ts             TIMESTAMPTZ NOT NULL,
    op_type        TEXT NOT NULL,            -- See Section 6
    actor_id       TEXT NOT NULL,
    actor_type     TEXT NOT NULL,
    namespace_id   TEXT NOT NULL,
    target_id      TEXT,                     -- Object or event affected
    target_type    TEXT,                     -- 'memory_object', 'entity', 'edge', 'event'
    payload        JSONB NOT NULL,           -- Op-specific structured data
    policy_version TEXT NOT NULL,            -- Version of the policy that generated this op
    sequence_num   BIGINT NOT NULL,
    prev_hash      TEXT
);

CREATE INDEX idx_ops_target ON operations(target_id);
CREATE INDEX idx_ops_type ON operations(op_type);
CREATE INDEX idx_ops_ts ON operations(ts);
```

**`policy_version`** is mandatory. Every operation that results from a policy decision (decay, reinforcement, classification) MUST record which version of that policy was applied. This enables exact replay under the original policy when auditing.

---

## 5. Derived State Tables

Derived state is computed from events and operations. Implementations MUST treat derived state as a cache, not as authoritative. If any derived table is dropped and rebuilt by replaying the event + operation logs, the result MUST be identical to the prior state.

The following tables are derived:

```sql
-- Memory objects (derived from ASSERT and mutation operations)
CREATE TABLE memory_objects (
    id                TEXT PRIMARY KEY,
    type              TEXT NOT NULL,
    version           INTEGER NOT NULL,
    epistemic_type    TEXT NOT NULL,
    source_type       TEXT NOT NULL,
    evidence_ids      TEXT[] NOT NULL,
    asserted_by       TEXT NOT NULL,
    valid_from        TIMESTAMPTZ NOT NULL,
    valid_to          TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL,
    last_modified_at  TIMESTAMPTZ NOT NULL,
    lifecycle_state   TEXT NOT NULL,
    superseded_by     TEXT,
    confidence        REAL NOT NULL,
    salience          REAL NOT NULL,
    namespace_id      TEXT NOT NULL,
    value             TEXT NOT NULL,
    structured_value  JSONB
);

-- Graph edges (derived from RELATE operations)
CREATE TABLE edges (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    created_by  TEXT NOT NULL,
    confidence  REAL NOT NULL,
    valid_from  TIMESTAMPTZ NOT NULL,
    valid_to    TIMESTAMPTZ
);

-- Entities (derived from ENTITY_CREATE and ENTITY_MERGE operations)
CREATE TABLE entities (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    canonical_name  TEXT NOT NULL,
    aliases         TEXT[],
    namespace_id    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL
);
```

---

## 6. Operation Types

The following operation types MUST be supported. The `payload` field is operation-specific; its schema is defined per operation type.

**Object lifecycle operations:**

| op_type | Description | Required payload fields |
|---|---|---|
| `ASSERT` | Create a new memory object. | `object_type`, `epistemic_type`, `source_type`, `evidence_ids`, `value`, `confidence`, `salience` |
| `DEPRECATE` | Mark an object as deprecated. | `reason`, `deprecated_by` |
| `SUPERSEDE` | Replace an object with a newer version. | `new_object_id`, `reason` |
| `ARCHIVE` | Move an object to archived state. | `reason`, `policy_ref` |
| `TOMBSTONE` | Logical deletion record for an event (for retention policy compliance). | `target_event_id`, `reason`, `policy_ref` |

**Epistemic operations:**

| op_type | Description | Required payload fields |
|---|---|---|
| `EPISTEMIC_TRANSITION` | Change the epistemic type of an object. | `from_type`, `to_type`, `reason`, `evidence_ids` |
| `CONTRADICTION_NOTED` | Record that two objects conflict. | `object_id_a`, `object_id_b`, `conflict_description` |

**Scoring operations:**

| op_type | Description | Required payload fields |
|---|---|---|
| `DECAY_APPLIED` | Record a decay step applied to an object. | `decay_delta`, `new_salience`, `new_confidence`, `decay_profile_ref` |
| `REINFORCE` | Record a reinforcement update. | `signal_type`, `delta`, `new_confidence`, `new_salience`, `signal_source` |
| `PENALTY` | Record a penalty update. | `signal_type`, `delta`, `new_confidence`, `reason` |

**Graph operations:**

| op_type | Description | Required payload fields |
|---|---|---|
| `RELATE` | Create an edge between two objects or entities. | `edge_type`, `source_id`, `target_id`, `confidence` |
| `ENTITY_CREATE` | Create a new entity node. | `entity_type`, `canonical_name`, `aliases` |
| `ENTITY_MERGE` | Merge two entities (alias resolution). | `primary_entity_id`, `merged_entity_id`, `alias_list` |

**Organiser operations:**

| op_type | Description | Required payload fields |
|---|---|---|
| `SUMMARY_CREATED` | A summary object was generated. | `object_ids_summarised`, `time_window`, `summary_type` |
| `DEDUP_RESOLVED` | Duplicate objects were resolved. | `primary_id`, `merged_ids`, `resolution_strategy` |

---

## 7. Integrity Verification

Implementations MUST support an integrity verification mode that:

1. Re-computes `content_hash` for each event and compares to stored value.
2. Walks the `prev_hash` chain for each stream and verifies the chain is unbroken.
3. Reports any discrepancies as integrity violations.

This verification MUST NOT be required for normal read operations (it is a background or on-demand check). Verification results MUST be logged as `INTEGRITY_CHECK` operations.

Implementations SHOULD support Merkle root computation over the event log for efficient remote verification.

---

## 8. Compaction Policy

The event log grows monotonically. Compaction is permitted under the following strict conditions:

**When compaction is allowed:**
- A set of events can be replaced by a logically equivalent summary operation without loss of information needed to reconstruct current derived state.
- The original events fall outside the configurable retention window AND are not referenced by any ACTIVE or SUPERSEDED memory object.
- A compaction operation is written to the log before the original events are removed.

**When compaction is NOT allowed:**
- Any event referenced by an ACTIVE memory object's `evidence_ids`.
- Any event within the minimum retention window (default: 90 days).
- Any event in a stream where policy versioning has changed since the event was written (compacting across policy boundaries destroys auditability).

**Compaction is a source of replay divergence** if done incorrectly. Specifically: if the policy under which a DECAY_APPLIED or REINFORCE operation was calculated changes, and the underlying events are compacted away, the compacted state can no longer be verified to be correct under the new policy. The compaction log MUST record the policy version snapshot at the time of compaction.

---

## 9. Portability Artifact

The portability artifact is the exportable, self-contained representation of a memory state. It MUST be producible from any conforming implementation and loadable into any other.

**Artifact structure:**

```
memory_pack/
  manifest.json           -- Metadata, format version, namespace list, policy versions
  events.parquet          -- Full event log (columnar for efficiency)
  operations.parquet      -- Full operation log
  vectors/
    embeddings.bin        -- Binary embedding store
    index.hnsw            -- HNSW index
    embedding_meta.json   -- Model name, version, dimension
  graph/
    entities.parquet
    edges.parquet
  snapshot/
    memory_objects.parquet
    snapshot_ts.json      -- Timestamp of derived state snapshot
  checksum.sha256         -- SHA-256 of manifest.json + events.parquet + operations.parquet
```

**manifest.json schema:**

```json
{
  "rms_version": "1.0",
  "format_version": "1",
  "created_at": "<ISO8601>",
  "namespaces": ["<namespace_id>", ...],
  "event_count": 0,
  "operation_count": 0,
  "policy_versions": {
    "decay": "<version>",
    "confidence": "<version>",
    "extraction": "<version>"
  },
  "embedding_model": "<model_name>",
  "embedding_version": "<version>",
  "is_full_export": true,
  "base_snapshot_id": null
}
```

**Portability guarantee:** Given the `events.parquet` and `operations.parquet` files and the policy versions recorded in the manifest, an implementation MUST be able to fully reconstruct all derived tables. The `snapshot/` directory is a convenience cache only and is not authoritative.

---

## 10. Write Guarantees and Ordering

Events written in the same stream MUST be ordered by `sequence_num`. Implementations MUST guarantee:

- No two events in the same stream share a `sequence_num`.
- `sequence_num` is monotonically increasing within a stream.
- The `prev_hash` chain is consistent at all times.

For multi-writer scenarios (see RFC-0011), writes to the same stream are serialised at the storage layer. Implementations MAY use optimistic concurrency control with retry on conflict.

---

## 11. Cold Start Behaviour

A new memory system has an empty event log and no derived state. In this state:

- All retrieval queries MUST return empty results, not errors.
- The system MUST operate normally without preloaded data.
- Implementations MUST NOT pre-seed the event log with synthetic history.

This is the correct behaviour. An empty memory system that degrades gracefully to no-memory behaviour is preferable to one that fabricates history.

---

## 12. Conformance Requirements

`[REQUIRED FOR TIER 1]`

- Implementations MUST support all operation types in Section 6.
- The `prev_hash` chain MUST be maintained for every stream.
- `policy_version` MUST be present on every operation that results from a policy decision.
- Derived state MUST be rebuildable from events and operations alone.
- The portability artifact format in Section 9 MUST be producible and loadable.
- Compaction MUST NOT be performed on events within the minimum retention window.
- Compaction across policy version boundaries is NOT permitted.

---

## 13. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Write throughput benchmark: events per second at p95 for a representative workload.
- Rebuild benchmark: time to rebuild derived state from N events (N = 10k, 100k, 1M).
- Portability round-trip test: export artifact, load into a fresh instance, compare derived state SHA-256.
- Integrity verification speed: time to verify a log of N events.

---

*End of RFC-0002*
