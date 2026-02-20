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
- Efficient range scans by timestamp and stream.
- Content-addressed deduplication by `content_hash`.

The reference implementation uses DuckDB for the event store. Implementations MAY use alternatives (SQLite, PostgreSQL, a custom log format) provided the above requirements are met and the portability artifact format (Section 9) is supported.

---

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
