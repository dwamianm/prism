# RFC-0001: Portable Relational Memory Engine (PRME)

## Status: Draft

## Category: Standards Track

## Date: 2026-02-19

------------------------------------------------------------------------

# 1. Abstract

This document specifies the architecture, requirements, and operational
model of the Portable Relational Memory Engine (PRME), an embeddable,
local-first memory substrate for LLM-powered systems.

PRME defines a deterministic, relationship-first, self-organizing memory
architecture designed to improve long-term recall accuracy, reduce
context window waste, and provide explainable retrieval.

------------------------------------------------------------------------

# 2. Conventions and Terminology

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT",\
"SHOULD", "SHOULD NOT", and "MAY" in this document are to be interpreted
as described in RFC 2119.

------------------------------------------------------------------------

# 3. System Overview

PRME SHALL provide:

1.  An append-only event log.
2.  A relational graph model of entities and assertions.
3.  Hybrid retrieval (graph + vector + lexical).
4.  Scheduled memory reorganization.
5.  Deterministic rebuild capability.
6.  A portable artifact format.

------------------------------------------------------------------------

# 4. Core Requirements

## 4.1 Append-Only Event Model

1.  The system MUST store all conversational inputs as immutable events.
2.  Events MUST NOT be overwritten or deleted except by policy-based
    archival.
3.  All derived memory structures MUST be rebuildable from the event
    log.
4.  Each event MUST include a timestamp and content hash.

------------------------------------------------------------------------

## 4.2 Graph-Based Relationship Model

1.  The system MUST represent entities as nodes.
2.  The system MUST represent relationships as typed edges.
3.  Each edge MUST include:
    -   valid_from
    -   valid_to (nullable)
    -   confidence score
    -   provenance reference
4.  The system MUST support supersedence of assertions.
5.  Conflicting assertions MUST NOT silently overwrite prior assertions.

------------------------------------------------------------------------

## 4.3 Memory Object Lifecycle

Each memory object MUST include:

-   Unique identifier
-   Type classification
-   Confidence score
-   Salience score
-   Validity window
-   Evidence references

The system SHOULD maintain lifecycle states including: - Tentative -
Stable - Superseded - Archived

------------------------------------------------------------------------

## 4.4 Hybrid Retrieval

The retrieval engine MUST:

1.  Perform entity extraction during query analysis.
2.  Generate candidates from:
    -   Graph neighborhood expansion
    -   Stable assertions
    -   Vector similarity search
    -   Lexical search
3.  Apply deterministic re-ranking.
4.  Provide explainable scoring components.
5.  Prefer structured summaries over raw events.

The retrieval engine SHOULD minimize token footprint when constructing
context bundles.

------------------------------------------------------------------------

## 4.5 Scheduled Memory Organizer

The system MUST implement periodic maintenance jobs including:

1.  Salience recalculation.
2.  Promotion and demotion of assertions.
3.  Supersedence resolution.
4.  Deduplication and canonicalization.
5.  Snapshot generation.
6.  Policy-based archival.

The organizer MUST NOT delete raw events without retention policy
enforcement.

------------------------------------------------------------------------

## 4.6 Determinism

1.  Given identical event logs and configuration, retrieval results MUST
    be reproducible.
2.  Embedding model versions MUST be recorded.
3.  Extraction logic versions SHOULD be recorded.
4.  Scoring weights MUST be configurable and versioned.

------------------------------------------------------------------------

## 4.7 Portability

PRME MUST be distributable as a portable artifact.

The artifact MAY be:

-   A single file
-   A structured directory bundle

The artifact MUST contain: - Event store - Graph store - Vector index -
Manifest metadata

The artifact SHOULD support encryption at rest.

------------------------------------------------------------------------

# 5. Storage Components

## 5.1 Event Store

The system SHALL use an append-only relational store (e.g., DuckDB).

Minimum schema requirements:

-   id (UUID)
-   timestamp
-   role
-   content
-   metadata
-   content_hash

------------------------------------------------------------------------

## 5.2 Graph Store

The graph engine (e.g., Kùzu) MUST support:

-   Typed nodes
-   Typed edges
-   Property storage
-   Multi-hop traversal
-   Temporal filtering

------------------------------------------------------------------------

## 5.3 Vector Index

The system MUST support approximate nearest neighbor search (e.g.,
HNSW).

Embeddings MUST record: - Model name - Version - Dimension

------------------------------------------------------------------------

## 5.4 Lexical Index

The system SHOULD support full-text search for precise matching.

------------------------------------------------------------------------

# 6. Context Construction Requirements

1.  The system MUST construct memory bundles prioritizing:
    -   Entity snapshots
    -   Stable facts
    -   Recent decisions
    -   Active tasks
2.  Raw events SHOULD only be included when necessary.
3.  The system SHOULD provide visibility into included vs excluded
    memory items.
4.  Token footprint SHOULD be measurable.

------------------------------------------------------------------------

# 7. Evaluation Requirements

An implementation SHALL be considered conformant if it:

1.  Recalls long-term stable preferences across sessions.
2.  Does not surface superseded facts as current.
3.  Demonstrates decreasing context payload over time via compaction.
4.  Produces explainable retrieval traces.
5.  Rebuilds deterministically from event logs.

------------------------------------------------------------------------

# 8. Security Considerations

1.  The system SHOULD support encryption at rest.
2.  The system MUST preserve provenance for auditability.
3.  Policy-based retention MUST be enforceable.
4.  Multi-tenant namespaces SHOULD be supported in enterprise contexts.

------------------------------------------------------------------------

# 9. Future Extensions

Future revisions MAY define:

-   CRDT-based synchronization
-   Distributed replication
-   Pluggable extraction pipelines
-   Adaptive salience learning
-   Multi-model embedding support

------------------------------------------------------------------------

# 10. Conclusion

PRME defines a standards-track relational memory substrate for LLM
systems.

By enforcing append-only architecture, relational modeling, hybrid
retrieval, and scheduled reorganization, PRME establishes a
deterministic, portable, and self-maintaining long-term memory system.
