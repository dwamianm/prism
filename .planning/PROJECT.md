# PRME — Portable Relational Memory Engine

## What This Is

A local-first, embeddable memory substrate for LLM-powered systems. PRME gives chatbots and agents stable long-term memory by combining an append-only event log, a graph-based relational model, hybrid retrieval (graph + vector + lexical), and scheduled self-organization. Exposed as an HTTP API first, with a Python library wrapper for direct embedding.

## Core Value

An LLM-powered agent can reliably recall long-term context — preferences, decisions, relationships — without resurfacing superseded information or wasting context window tokens.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Append-only event store (DuckDB) with immutable event log
- [ ] Graph-based relational model (Kuzu) with typed nodes, edges, temporal validity, and supersedence
- [ ] Vector index (HNSW) with pluggable embedding providers (external API and local model support)
- [ ] Lexical full-text search (FTS5 or Tantivy)
- [ ] Hybrid retrieval pipeline: query analysis, multi-source candidate generation, deterministic re-ranking, context packing
- [ ] Scheduled memory organizer: salience recalculation, promotion/demotion, summarization, deduplication, archival
- [ ] Portable artifact format (copyable directory bundle with manifest)
- [ ] HTTP API as primary integration surface
- [ ] Python library wrapper for direct embedding
- [ ] CLI tooling for memory inspection and management
- [ ] Encryption at rest
- [ ] Deterministic rebuild from event log
- [ ] Evaluation harness proving recall accuracy and determinism

### Out of Scope

- CRDT-based sync / distributed replication — future extension, not v1
- Multi-model embedding support in v1 — pluggable provider is sufficient, simultaneous multi-model indexing deferred
- Web UI / dashboard — CLI and API are sufficient for v1
- Mobile clients — server-side only

## Context

- Target consumers: the developer's own chatbot/agent first, then other developers embedding PRME in their LLM systems
- Entity extraction and intent classification should use whichever method minimizes errors — likely LLM-powered extraction with rule-based fallbacks, determined during research
- "It works" means: a developer can hit API endpoints, store conversations, and get accurate contextual recall back
- **Authoritative specification**: The Revised RFC suite in `docs/*` (RFC-0000 through RFC-0014) is the canonical spec. See `docs/INDEX.md` for the full listing and dependency graph. These RFCs supersede all prior spec documents in `docs/`
- The RFC suite is organized into conformance tiers: Tier 0 (data model), Tier 1 (storage/integrity), Tier 2 (retrieval), Tier 3 (lifecycle), Tier 4 (advanced capabilities). An implementation claiming conformance at a given tier must satisfy all tiers below it
- Key design principles from the Revised RFCs: determinism is scoped (strict at storage/derivation, best-effort at extraction); unvalidated parameters are marked `[HYPOTHESIS]` and require benchmarks before promotion; merge conflict resolution is fully specified; agent trust is domain-scoped not scalar
- RFC-0013 (Intent and Goal Memory) replaces the original emotional/behavioral signal tracking. RFC-0014 (Portability, Sync, and Federation) replaces the original git sync profile with full merge conflict semantics
- The spec's 3-phase roadmap (store+retrieve → organizer → encryption+CLI+eval) is the intended build order

## Constraints

- **Language**: Python — DuckDB, Kuzu, and HNSW libraries have mature Python bindings
- **Local-first**: All data stays local, no cloud dependencies for core functionality
- **Determinism (scoped)**: Strict determinism at storage and derivation layers (identical event logs + config → identical retrieval results). Best-effort reproducibility at extraction layer (LLM-based extraction is inherently non-deterministic)
- **Append-only**: Events are never mutated or deleted except by policy-based archival

## Key Decisions

| Decision                          | Rationale                                                                               | Outcome   |
| --------------------------------- | --------------------------------------------------------------------------------------- | --------- |
| Python as implementation language | Mature bindings for DuckDB, Kuzu, HNSW; target audience familiarity                     | — Pending |
| HTTP API first, library second    | Cross-language compatibility; consistent interface for any LLM framework                | — Pending |
| Pluggable embedding providers     | Avoid lock-in; support both API-based (OpenAI/Voyage) and local (sentence-transformers) | — Pending |
| Full spec as v1 (all 3 phases)    | Developer wants complete system including encryption, CLI, and eval harness             | — Pending |

---

_Last updated: 2026-02-19 — updated to reference Revised RFC suite as authoritative spec_
