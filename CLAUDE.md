# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PRME (Portable Relational Memory Engine) is a local-first, embeddable memory substrate for LLM-powered systems. It combines event sourcing, graph-based relational modeling, hybrid retrieval, and scheduled memory reorganization. The project is in pre-implementation stage — specs live in `docs/`.

## Architecture

Four storage layers behind a unified retrieval API:

- **Event Store (DuckDB)** — Append-only immutable event log. All derived structures must be rebuildable from this log.
- **Graph Store (Kùzu)** — Typed nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) and typed edges with temporal validity windows (`valid_from`/`valid_to`), confidence scores, and provenance references.
- **Vector Index (HNSW)** — Approximate nearest neighbor search with versioned embeddings (model name, version, dimension tracked per embedding).
- **Lexical Index (FTS5 or Tantivy)** — Full-text search over event content, facts, and summaries.

## Key Design Constraints

- **Append-only**: Events must never be overwritten or deleted except by policy-based archival. Conflicting assertions must not silently overwrite prior ones — use supersedence.
- **Deterministic**: Given identical event logs and config, retrieval results must be reproducible. Scoring weights must be configurable and versioned.
- **Portable artifact**: The memory pack (`events.duckdb`, `graph.kuzu/`, `vectors.bin`, `hnsw.idx`, `manifest.json`) must be copyable, encryptable, and rebuildable.

## Hybrid Retrieval Pipeline

Query → intent classification + entity extraction + time detection → candidate generation (graph neighborhood, stable facts, vector similarity, lexical, recent high-salience) → deterministic re-ranking → context packing into memory bundles (entity snapshots, stable facts, recent decisions, active tasks, provenance refs).

## Memory Object Lifecycle

Objects progress through: Tentative → Stable → Superseded → Archived. Each object carries: id, type, scope (personal/project/org), confidence, salience, validity window, evidence references, and supersedence pointer.

## Scheduled Organizer

Periodic jobs handle: salience recalculation, promotion/demotion of assertions, summarization (daily → weekly → monthly), deduplication/entity alias resolution, and policy-based archival with TTL enforcement.

## RFCs

Design specifications live in `docs/` as numbered RFCs (RFC-0000 through RFC-0014). See `docs/INDEX.md` for the full listing. Key RFCs include:

- **RFC-0000** — Suite overview
- **RFC-0001** — Core data model
- **RFC-0002** — Event store
- **RFC-0003** — Epistemic state model
- **RFC-0005** — Hybrid retrieval pipeline
- **RFC-0014** — Portability, sync, and federation

Always consult the relevant RFC before implementing or modifying a subsystem.

## MVP Phases

1. Event store, basic graph schema, vector search, hybrid retrieval
2. Organizer jobs, stable fact promotion, snapshot generation, supersedence handling
3. Encryption, CLI tooling, evaluation harness, deterministic rebuild validation
