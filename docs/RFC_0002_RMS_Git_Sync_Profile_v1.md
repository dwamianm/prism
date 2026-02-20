# RFC-0002: RMS Git Sync Profile (v1)

## Status: Draft

## Category: Standards Track

## Date: 2026-02-19

------------------------------------------------------------------------

# 1. Abstract

This document specifies the Git-based synchronization profile for the
Relational Memory Substrate (RMS).

The RMS Git Sync Profile defines a portable, versioned, append-only
export/import mechanism that enables distributed memory synchronization
across devices while preserving determinism, auditability, and
rebuildability.

This profile does NOT define the RMS core data model. It defines the
transport and versioning mechanism for RMS-compliant implementations.

------------------------------------------------------------------------

# 2. Conventions and Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT",
"SHOULD", "SHOULD NOT", and "MAY" in this document are to be interpreted
as described in RFC 2119.

------------------------------------------------------------------------

# 3. Scope

This specification defines:

-   Repository structure
-   Operation log format
-   Snapshot format
-   Export protocol
-   Import protocol
-   Merge semantics
-   Determinism requirements

This specification does NOT mandate:

-   Specific database engines
-   Specific graph engines
-   Specific embedding models
-   Specific encryption mechanisms

------------------------------------------------------------------------

# 4. Repository Structure

An RMS Git repository MUST conform to the following layout:

    rms/
      VERSION
      manifest.json
      ops/
        YYYY/
          MM/
            <timestamp>.ops.jsonl.zst
      snapshots/
        entities/
        summaries/
      checkpoints/
        head.json
      attachments/
      signatures/

## 4.1 VERSION

The VERSION file MUST contain the Git Sync profile identifier:

Example:

    rms-git-sync/1.0

------------------------------------------------------------------------

## 4.2 manifest.json

The manifest MUST include:

-   spec_version
-   implementation_name
-   implementation_version
-   embedding_model_versions
-   extractor_version
-   organizer_version
-   device_id (optional)
-   namespace (optional)

Example:

``` json
{
  "spec_version": "rms-git-sync/1.0",
  "implementation_name": "rms-core",
  "implementation_version": "0.1.0",
  "embedding_model_versions": ["text-embedding-3-large@2026-01"],
  "extractor_version": "1.0.0",
  "organizer_version": "1.0.0"
}
```

------------------------------------------------------------------------

# 5. Operation Log Format

All changes MUST be recorded as append-only operations (ops).

## 5.1 File Format

-   Each ops file MUST be JSON Lines (one JSON object per line).
-   Each file MUST be compressed using Zstandard (.zst).
-   Files MUST be immutable once committed.

Filename format:

    YYYY-MM-DDTHHMMSSZ.ops.jsonl.zst

------------------------------------------------------------------------

## 5.2 Operation Requirements

Each operation MUST include:

-   op (string)
-   id (stable unique identifier)
-   ts (timestamp)
-   source (device or namespace identifier)

Operations MUST be idempotent.

------------------------------------------------------------------------

## 5.3 Minimum Operation Types (v1)

Implementations MUST support:

-   ADD_EVENT
-   UPSERT_ENTITY
-   ADD_ASSERTION
-   SUPERSEDE_ASSERTION
-   MERGE_ENTITY
-   ARCHIVE (policy-based)

Example:

``` json
{
  "op": "ADD_EVENT",
  "id": "event:123",
  "ts": "2026-02-19T03:00:00Z",
  "stream": "device:mbp",
  "role": "user",
  "content": "Discuss RMS Git sync",
  "meta": {}
}
```

------------------------------------------------------------------------

# 6. Snapshots

Snapshots MAY be generated periodically to accelerate import.

Snapshots MUST:

-   Represent derived state (entities, stable facts, summaries)
-   NOT replace raw operation logs
-   Include a snapshot timestamp
-   Be replay-safe

Snapshots MUST be stored as compressed JSON (.json.zst).

------------------------------------------------------------------------

# 7. Export Protocol

An RMS implementation exporting to Git MUST:

1.  Select new operations since last checkpoint.
2.  Write a new ops file.
3.  Optionally generate snapshots.
4.  Update checkpoints/head.json.
5.  Commit changes.
6.  Push to remote repository.

Each export MUST create new files; existing ops files MUST NOT be
modified.

------------------------------------------------------------------------

# 8. Import Protocol

An RMS implementation importing from Git MUST:

1.  Pull latest repository state.
2.  Identify ops files not yet applied.
3.  Apply operations in deterministic order.
4.  Optionally bootstrap from the latest snapshot.
5.  Update local checkpoint state.

Operation ordering MUST be deterministic. Recommended ordering:

-   Sort ops files lexicographically by filename.
-   Within a file, apply operations in file order.

------------------------------------------------------------------------

# 9. Merge Semantics

Git merges SHOULD result in union of ops files.

Implementations MUST NOT rely on modifying existing ops files during
merge.

Conflicting assertions MUST be resolved at the memory semantic layer,
not via Git conflict markers.

If duplicate operations are encountered, idempotent handling MUST
prevent duplication effects.

------------------------------------------------------------------------

# 10. Determinism Requirements

Given identical repository state and configuration:

-   Replaying ops MUST yield identical logical memory state.
-   Supersedence resolution MUST follow deterministic rules.
-   Derived indexes MUST be rebuildable from ops and snapshots.

------------------------------------------------------------------------

# 11. Large Objects and Attachments

Attachments MAY be stored under:

    attachments/

Large binary objects SHOULD use Git LFS.

Embedding vectors SHOULD NOT be required for synchronization and MAY be
regenerated during import.

------------------------------------------------------------------------

# 12. Security Considerations

Implementations SHOULD:

-   Support commit signing.
-   Support encryption at rest.
-   Support namespace isolation.
-   Preserve provenance metadata for auditability.

This profile does not mandate a specific encryption scheme.

------------------------------------------------------------------------

# 13. Conformance

An implementation SHALL be considered RMS Git Sync v1 compliant if it:

1.  Adheres to repository structure.
2.  Uses append-only ops files.
3.  Maintains idempotent operations.
4.  Applies deterministic replay.
5.  Preserves raw event history.

------------------------------------------------------------------------

# 14. Future Extensions

Future revisions MAY define:

-   Incremental packfile formats
-   Merkle-based partial fetch
-   CRDT-based merge strategies
-   Signed operation envelopes
-   Encrypted attachment standards

------------------------------------------------------------------------

# 15. Conclusion

The RMS Git Sync Profile (v1) defines a pragmatic, Git-based
synchronization layer for Relational Memory Substrate implementations.

By separating runtime storage from versioned transport, RMS achieves
portability, determinism, and distributed synchronization while
preserving performance and architectural flexibility.
