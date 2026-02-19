# Requirements: PRME

**Defined:** 2026-02-19
**Core Value:** An LLM-powered agent can reliably recall long-term context — preferences, decisions, relationships — without resurfacing superseded information or wasting context window tokens.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Storage & Data Model

- [ ] **STOR-01**: System stores all conversational inputs as immutable events in an append-only DuckDB event log
- [ ] **STOR-02**: System represents entities as typed graph nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) in Kuzu behind an abstract GraphStore interface
- [ ] **STOR-03**: System represents relationships as typed edges with valid_from, valid_to, confidence, and provenance reference
- [ ] **STOR-04**: System indexes event content and facts in an HNSW vector index with versioned embedding metadata
- [ ] **STOR-05**: System indexes event content and facts in a full-text lexical index (Tantivy)
- [ ] **STOR-06**: System scopes all memory operations by user_id and session_id
- [ ] **STOR-07**: System tracks memory object lifecycle states (Tentative → Stable → Superseded → Archived)
- [ ] **STOR-08**: System maintains supersedence chains with provenance — superseded facts link to their replacement and the evidence that triggered the change

### Ingestion & Extraction

- [ ] **INGE-01**: System extracts entities, facts, and relationships from conversation events using pluggable LLM providers
- [ ] **INGE-02**: System supports at least OpenAI and one local option for LLM-powered extraction
- [ ] **INGE-03**: System supports pluggable embedding providers (API-based and local model)
- [ ] **INGE-04**: System persists full conversation history as searchable events
- [ ] **INGE-05**: System uses a write queue pattern to handle DuckDB single-writer concurrency under HTTP API load

### Retrieval

- [ ] **RETR-01**: User can search memory by semantic similarity via vector search
- [ ] **RETR-02**: User can search memory by exact terms via lexical full-text search
- [ ] **RETR-03**: System performs hybrid retrieval combining graph neighborhood, vector similarity, lexical match, and recency/salience signals
- [ ] **RETR-04**: System applies deterministic re-ranking with configurable, versioned scoring weights
- [ ] **RETR-05**: System returns explainable retrieval traces showing score components per result
- [ ] **RETR-06**: System constructs context-packed memory bundles (entity snapshots, stable facts, recent decisions, active tasks) within a configurable token budget

### Self-Organization

- [ ] **ORGN-01**: System runs scheduled salience recalculation based on frequency, recency, graph centrality, user pinning, and task linkage
- [ ] **ORGN-02**: System promotes reinforced assertions and demotes stale items through the Tentative → Stable → Superseded → Archived lifecycle
- [ ] **ORGN-03**: System generates hierarchical summaries (daily → weekly → monthly) and per-entity snapshots
- [ ] **ORGN-04**: System performs entity alias resolution and assertion deduplication
- [ ] **ORGN-05**: System enforces policy-based retention with TTL and compression

### Integration & API

- [ ] **INTG-01**: System exposes an HTTP API for storing events, searching memory, and retrieving entity snapshots
- [ ] **INTG-02**: System provides a Python library wrapper over the HTTP API
- [ ] **INTG-03**: System supports async operations throughout (asyncio with sync wrappers)
- [ ] **INTG-04**: System provides an MCP server exposing PRME as tools for MCP-compatible clients
- [ ] **INTG-05**: System provides at least one framework integration (LangChain or equivalent)

### Trust & Hardening

- [ ] **TRST-01**: System exports a portable artifact (memory_pack/ directory with manifest.json) that is copyable and versionable
- [ ] **TRST-02**: System supports encryption at rest for the portable artifact
- [ ] **TRST-03**: System can deterministically rebuild all derived structures from the event log given identical configuration
- [ ] **TRST-04**: System provides CLI tooling for memory inspection, querying, export, and rebuild
- [ ] **TRST-05**: System includes an evaluation harness measuring recall accuracy, supersedence correctness, context compaction, and deterministic rebuild

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Git Sync (RFC-0002)

- **SYNC-01**: System exports operations as append-only JSONL+Zstandard compressed ops files to a Git repository
- **SYNC-02**: System imports ops files from a Git repository with deterministic replay ordering
- **SYNC-03**: System generates periodic snapshots to accelerate import
- **SYNC-04**: System handles merge via union of ops files with idempotent operation handling
- **SYNC-05**: System resolves conflicting assertions at the memory semantic layer, not via Git conflict markers
- **SYNC-06**: System supports Git LFS for large binary attachments
- **SYNC-07**: Repository conforms to rms-git-sync/1.0 structure (ops/, snapshots/, checkpoints/, attachments/, manifest.json)

### Additional Providers

- **PROV-01**: System supports additional LLM providers (Anthropic, Gemini) for extraction
- **PROV-02**: System supports additional embedding providers (Voyage, Cohere)

### Advanced Features

- **ADVN-01**: System supports adaptive salience learning from user feedback
- **ADVN-02**: System supports multi-agent shared memory with namespace isolation and cross-agent queries

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Cloud-hosted managed service | Conflicts with local-first architecture; splits engineering focus prematurely |
| Web UI / dashboard | High development cost; CLI + API sufficient for v1 developer audience |
| Multi-model simultaneous embedding | Massively increases storage and complexity; pluggable single-provider with re-indexing is sufficient |
| CRDT-based sync / distributed replication | Enormous complexity; Git Sync Profile (RFC-0002) is the v2 approach instead |
| Real-time streaming memory updates (WebSocket/SSE) | Memory writes are bursty, not continuous; HTTP polling or webhooks are sufficient |
| Built-in RAG over external documents | Scope creep; RAG is a solved problem in LangChain/LlamaIndex — PRME is a memory engine |
| Automatic prompt rewriting / procedural memory | Hard to get right; bad mutations degrade agent quality; PRME retrieves, developer constructs prompts |
| Mobile clients / native SDKs | Server-side Python is the correct scope; HTTP API accessible from any platform |
| Multi-tenant SaaS features (billing, auth, rate limiting) | Application-layer concerns; deploy behind API gateway for these |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| STOR-01 | Phase 1 | Pending |
| STOR-02 | Phase 1 | Pending |
| STOR-03 | Phase 1 | Pending |
| STOR-04 | Phase 1 | Pending |
| STOR-05 | Phase 1 | Pending |
| STOR-06 | Phase 1 | Pending |
| STOR-07 | Phase 1 | Pending |
| STOR-08 | Phase 1 | Pending |
| INGE-01 | Phase 2 | Pending |
| INGE-02 | Phase 2 | Pending |
| INGE-03 | Phase 2 | Pending |
| INGE-04 | Phase 2 | Pending |
| INGE-05 | Phase 2 | Pending |
| RETR-01 | Phase 3 | Pending |
| RETR-02 | Phase 3 | Pending |
| RETR-03 | Phase 3 | Pending |
| RETR-04 | Phase 3 | Pending |
| RETR-05 | Phase 3 | Pending |
| RETR-06 | Phase 3 | Pending |
| ORGN-01 | Phase 5 | Pending |
| ORGN-02 | Phase 5 | Pending |
| ORGN-03 | Phase 5 | Pending |
| ORGN-04 | Phase 5 | Pending |
| ORGN-05 | Phase 5 | Pending |
| INTG-01 | Phase 4 | Pending |
| INTG-02 | Phase 4 | Pending |
| INTG-03 | Phase 4 | Pending |
| INTG-04 | Phase 7 | Pending |
| INTG-05 | Phase 7 | Pending |
| TRST-01 | Phase 6 | Pending |
| TRST-02 | Phase 7 | Pending |
| TRST-03 | Phase 6 | Pending |
| TRST-04 | Phase 6 | Pending |
| TRST-05 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 34 total
- Mapped to phases: 34
- Unmapped: 0

---
*Requirements defined: 2026-02-19*
*Last updated: 2026-02-19 after roadmap creation*
