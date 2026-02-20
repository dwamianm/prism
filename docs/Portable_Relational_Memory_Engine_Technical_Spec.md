# Portable Relational Memory Engine (PRME)

## Technical Specification

**Version:** 0.1\
**Date:** 2026-02-19\
**Status:** Draft

------------------------------------------------------------------------

# 1. Overview

The Portable Relational Memory Engine (PRME) is a local-first,
embeddable memory substrate for LLM-powered systems.\
It combines event sourcing, graph-based relational modeling, hybrid
retrieval, and scheduled memory reorganization to produce:

-   Stable long-term memory
-   Structured relational reasoning
-   Context-efficient retrieval
-   Deterministic and explainable recall
-   Portable memory artifacts

------------------------------------------------------------------------

# 2. Design Goals

1.  **Portable** -- Copyable artifact (single file or structured
    directory)
2.  **Deterministic** -- Reproducible retrieval given identical memory
    state
3.  **Append-Only Core** -- Immutable event log
4.  **Relationship-First** -- Structured graph-based memory
5.  **Self-Organizing** -- Scheduled compaction and restructuring
6.  **Explainable** -- Provenance-aware retrieval scoring
7.  **Context Efficient** -- Lean memory bundles

------------------------------------------------------------------------

# 3. High-Level Architecture

    +---------------------------+
    |       LLM Application     |
    +---------------------------+
                  |
                  v
    +---------------------------+
    |   Memory Retrieval API    |
    +---------------------------+
                  |
                  v
    +---------------------------+
    |  Hybrid Retrieval Engine  |
    +---------------------------+
          |         |        |
          v         v        v
       Graph      Vector   Lexical
       (Kùzu)     (HNSW)   (FTS)
          |
          v
    +---------------------------+
    |   Event Store (DuckDB)    |
    +---------------------------+

------------------------------------------------------------------------

# 4. Storage Layer

## 4.1 Event Store (DuckDB)

Append-only event log.

### Schema: events

``` sql
CREATE TABLE events (
    id UUID PRIMARY KEY,
    ts TIMESTAMP,
    stream TEXT,
    role TEXT,
    content TEXT,
    metadata JSON,
    content_hash TEXT
);
```

------------------------------------------------------------------------

## 4.2 Graph Store (Kùzu)

### Node Types

-   Entity
-   Event
-   Fact
-   Decision
-   Preference
-   Task
-   Summary

### Edge Types

-   MENTIONED_IN
-   ASSERTED_IN
-   RELATED_TO
-   WORKS_ON
-   USES
-   DECIDED
-   SUPERSEDES

Each edge includes: - valid_from - valid_to - confidence -
source_event_id

------------------------------------------------------------------------

## 4.3 Vector Index (HNSW)

Stores embeddings keyed by: - event_id - fact_id - summary_id

Embedding metadata: - model_name - embedding_version - dimension

------------------------------------------------------------------------

## 4.4 Lexical Index

Full-text search (FTS5 or Tantivy).

Indexes: - event content - fact values - summaries

------------------------------------------------------------------------

# 5. Memory Object Model

``` json
{
  "id": "uuid",
  "type": "event | fact | decision | preference | task | summary",
  "scope": "personal | project | org",
  "confidence": 0.0,
  "salience": 0.0,
  "valid_from": "timestamp",
  "valid_to": null,
  "evidence_ids": [],
  "supersedes": null
}
```

------------------------------------------------------------------------

# 6. Hybrid Retrieval Pipeline

## Step 1 -- Query Analysis

-   Intent classification
-   Entity extraction
-   Time detection

## Step 2 -- Candidate Generation

Sources: - Graph neighborhood (1--3 hops) - Stable facts - Vector
similarity search - Lexical search - Recent high-salience items

## Step 3 -- Re-Ranking

Score formula:

    score = 
      w1 * semantic_similarity +
      w2 * lexical_relevance +
      w3 * graph_proximity +
      w4 * recency_decay +
      w5 * salience +
      w6 * confidence

## Step 4 -- Context Packing

Memory Bundle Structure: - Entity Snapshot - Stable Facts - Recent
Decisions - Active Tasks - Provenance References

------------------------------------------------------------------------

# 7. Scheduled Memory Organizer

Runs periodically.

## 7.1 Salience Recalculation

Signals: - Frequency - Recency - Graph centrality - User pinning - Task
linkage

## 7.2 Promotion & Demotion

-   Promote reinforced assertions
-   Supersede contradictions
-   Demote stale items

## 7.3 Summarization

-   Daily → Weekly → Monthly
-   Per-entity snapshot generation
-   Delta-based summaries

## 7.4 Deduplication

-   Entity alias resolution
-   Assertion consolidation

## 7.5 Archival

-   Policy-based retention
-   TTL enforcement
-   Compression

------------------------------------------------------------------------

# 8. Determinism & Rebuild Strategy

-   All derived data rebuildable from events
-   Versioned embeddings
-   Versioned extraction logic
-   Deterministic scoring weights

------------------------------------------------------------------------

# 9. Portability Model

Artifact Structure:

    memory_pack/
      events.duckdb
      graph.kuzu/
      vectors.bin
      hnsw.idx
      manifest.json

Features: - Copyable - Encryptable - Versionable - Rebuildable

------------------------------------------------------------------------

# 10. MVP Roadmap

## Phase 1

-   Event store
-   Basic graph schema
-   Vector search
-   Hybrid retrieval

## Phase 2

-   Organizer jobs
-   Stable fact promotion
-   Snapshot generation
-   Supersedence handling

## Phase 3

-   Encryption
-   CLI tooling
-   Evaluation harness
-   Deterministic rebuild validation

------------------------------------------------------------------------

# 11. Evaluation Criteria

Success if system:

-   Recalls long-term preferences reliably
-   Avoids resurfacing superseded facts
-   Reduces context size over time
-   Produces explainable retrieval output
-   Reproduces identical results across machines

------------------------------------------------------------------------

# 12. Conclusion

PRME defines a portable, graph-native, self-organizing memory substrate
for LLM systems.\
It moves beyond vector-only retrieval toward structured, stable,
explainable long-term memory.
