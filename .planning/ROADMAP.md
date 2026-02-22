# Roadmap: PRME

## Overview

PRME is built bottom-up from its event-sourced storage foundation through progressively higher-level capabilities. Phase 1 lays down the four storage backends (event store, graph, vector, lexical) with the typed data model, temporal validity, and lifecycle state tracking. Phase 2 builds the ingestion pipeline that converts conversations into structured memory. Phase 3 builds the retrieval pipeline that converts queries into ranked, explainable memory bundles. Phase 4 wraps these pipelines in an HTTP API and Python SDK for external consumption. Phase 5 adds the self-organizing background intelligence (salience, promotion, summarization, dedup). Phase 6 delivers portability (artifact format, deterministic rebuild, CLI inspection). Phase 7 hardens the system with encryption, proves its quality with an evaluation harness, and integrates with the MCP and LLM framework ecosystems.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Storage Foundation** - All four storage backends with typed data model, temporal validity, lifecycle states, and user/session scoping (completed 2026-02-19)
- [ ] **Phase 2: Ingestion Pipeline** - Conversations enter the system and produce structured memory across all storage backends
- [ ] **Phase 2.1: Scope Isolation Fix** - INSERTED — Close audit gaps: persist Event.scope to DuckDB, thread scope through IngestionPipeline
- [ ] **Phase 2.2: WriteQueue Contract & Async Safety** - INSERTED — Close audit gaps: route EntityMerger/SupersedenceDetector through WriteQueue, fix embedding async safety
- [x] **Phase 2.3: Revised RFC Reconciliation** - INSERTED — Reconcile REQUIREMENTS.md against Revised RFC suite, identify delta in built code (completed 2026-02-20)
- [x] **Phase 3: Retrieval Pipeline** - Queries return ranked, explainable, context-packed memory from all backends (completed 2026-02-21)
- [x] **Phase 3.1: Epistemic Type & Confidence Matrix** - INSERTED — Close audit gaps: add epistemic_type to MemoryNode/DuckDB, assign at ingestion, apply confidence matrix (completed 2026-02-21)
- [ ] **Phase 3.2: Retrieval Filter Forwarding** - INSERTED — Close audit gaps: forward scope and temporal filters to all retrieval backends
- [ ] **Phase 3.3: Contradiction Modeling** - INSERTED — Close audit gaps: create CONTRADICTS edges, log CONTRADICTION_NOTED operations
- [ ] **Phase 3.4: Namespace & Config Expansion** - INSERTED — Close audit gaps: expand namespace types to 6, expose all [HYPOTHESIS] params as config
- [ ] **Phase 4: HTTP API and Python SDK** - External consumers access memory through HTTP endpoints and a Python library
- [ ] **Phase 5: Self-Organization** - Background scheduler maintains memory quality through salience, promotion, summarization, dedup, and archival
- [ ] **Phase 6: Portability and CLI** - Memory is exportable as a portable artifact, rebuildable from event log, and inspectable via CLI
- [ ] **Phase 7: Hardening and Ecosystem** - Encryption at rest, evaluation harness, MCP server, and framework integration

## Phase Details

### Phase 1: Storage Foundation
**Goal**: A developer can programmatically create, read, and query all four storage backends with typed nodes, edges, temporal validity, lifecycle states, and user/session isolation
**Depends on**: Nothing (first phase)
**Requirements**: STOR-01, STOR-02, STOR-03, STOR-04, STOR-05, STOR-06, STOR-07, STOR-08
**Success Criteria** (what must be TRUE):
  1. An event written to the DuckDB event store is immutable and retrievable by ID, user_id, and session_id
  2. A typed node (Entity, Fact, Decision, Preference, Task, Summary) created in Kuzu through the GraphStore abstraction interface is queryable with temporal validity (valid_from/valid_to) and confidence scores
  3. A typed edge created between graph nodes carries valid_from, valid_to, confidence, and provenance reference, and supersedence chains link replaced facts to their successors with evidence
  4. Content embedded into the HNSW vector index returns approximate nearest neighbors, and each vector record includes embedding model name, version, and dimension metadata
  5. Content indexed in Tantivy returns BM25-ranked full-text search results
**Plans**: 4 plans

Plans:
- [ ] 01-01-PLAN.md — Project scaffolding, domain models, type enums, lifecycle state machine, configuration
- [ ] 01-02-PLAN.md — DuckDB schema, EventStore, GraphStore Protocol, DuckPGQ node/edge CRUD
- [ ] 01-03-PLAN.md — VectorIndex (USearch + FastEmbed) and LexicalIndex (tantivy-py BM25)
- [ ] 01-04-PLAN.md — GraphStore advanced ops (traversal, lifecycle, supersedence) and MemoryEngine integration

### Phase 2: Ingestion Pipeline
**Goal**: A developer can submit conversation events and the system automatically extracts entities, facts, and relationships into structured memory across all storage backends
**Depends on**: Phase 1
**Requirements**: INGE-01, INGE-02, INGE-03, INGE-04, INGE-05
**Success Criteria** (what must be TRUE):
  1. A conversation event submitted to the ingestion pipeline is persisted as an immutable event and is searchable by content, and extracted entities/facts appear in the graph store as Tentative assertions with source_event_id provenance
  2. Extraction works with at least two LLM providers (OpenAI API and one local option) selectable by configuration
  3. Embedding works with at least two providers (API-based and local/FastEmbed) selectable by configuration, and vectors carry model metadata
  4. Concurrent write requests are serialized through the async write queue without transaction conflicts or data loss
**Plans**: 4 plans

Plans:
- [ ] 02-01-PLAN.md -- Dependencies, config extensions, WriteQueue, OpenAI embedding provider
- [ ] 02-02-PLAN.md -- Extraction schema, ExtractionProvider Protocol, instructor implementations, grounding validation
- [ ] 02-03-PLAN.md -- Entity merge and supersedence detection modules
- [ ] 02-04-PLAN.md -- IngestionPipeline orchestrator, MemoryEngine integration, temporal resolution

### Phase 2.1: Scope Isolation Fix
**Goal**: Event.scope is persisted to DuckDB and the ingestion pipeline accepts a scope parameter, so all memory operations can be scoped beyond just user_id
**Depends on**: Phase 2
**Requirements**: STOR-06
**Gap Closure:** Closes audit gaps — STOR-06 partial (scope not persisted/threadable), integration issues #2 and #3
**Success Criteria** (what must be TRUE):
  1. An event stored in DuckDB includes its scope value, and events can be queried by scope
  2. IngestionPipeline.ingest() accepts a scope parameter that flows through to all created MemoryNodes
  3. Existing tests continue to pass with PERSONAL as the default scope
**Plans**: 3 plans

Plans:
- [ ] 02.1-01-PLAN.md — EventStore scope persistence: DuckDB schema migration, scope in INSERT/SELECT, multi-scope query filtering
- [ ] 02.1-02-PLAN.md — Ingestion pipeline scope threading: extraction schema, LLM prompt, entity merge, pipeline, engine entry points
- [ ] 02.1-03-PLAN.md — Gap closure: add scope=scope to Event() constructor in MemoryEngine.store()

### Phase 2.2: WriteQueue Contract & Async Safety
**Goal**: All graph writes during ingestion are serialized through WriteQueue and the embedding provider works safely in async contexts
**Depends on**: Phase 2.1
**Requirements**: INGE-05
**Gap Closure:** Closes audit gaps — INGE-05 partial (WriteQueue bypass), integration issues #1 and #4
**Success Criteria** (what must be TRUE):
  1. EntityMerger.find_or_create_entity() routes all graph writes through WriteQueue, not directly through GraphStore
  2. SupersedenceDetector.detect_and_supersede() routes all graph writes through WriteQueue
  3. OpenAIEmbeddingProvider.embed() works correctly when called from both async context and thread pool context
  4. Concurrent ingestion requests do not produce transaction conflicts or data loss
**Plans**: 3 plans

Plans:
- [ ] 02.2-01-PLAN.md -- GraphWriter Protocol, WriteQueueGraphWriter, WriteTracker rollback, custom exception hierarchy
- [ ] 02.2-02-PLAN.md -- Async EmbeddingProvider Protocol, LRU cache, VectorIndex async update
- [ ] 02.2-03-PLAN.md -- Pipeline/Engine wiring with GraphWriter, rollback-on-failure, concurrency tests

### Phase 2.3: Revised RFC Reconciliation
**Goal**: REQUIREMENTS.md is reconciled against the Revised RFC suite (RFC-0000 through RFC-0014) and any delta between built code and revised specs is identified
**Depends on**: Phase 2.2
**Requirements**: (cross-cutting — updates existing requirements and may add new ones)
**Gap Closure:** Closes audit gap — Revised RFC suite not reflected in REQUIREMENTS.md
**Success Criteria** (what must be TRUE):
  1. Each Revised RFC relevant to completed phases (RFC-0001 through RFC-0004 at minimum) has been reviewed against REQUIREMENTS.md
  2. New or changed requirements from the Revised RFCs are added to REQUIREMENTS.md with traceability
  3. Any delta between built code and revised specs is documented with recommended action (fix now vs defer)
**Plans**: 2 plans

Plans:
- [ ] 02.3-01-PLAN.md — Reconcile REQUIREMENTS.md against all 17 RFCs (update existing, add new requirement domains)
- [ ] 02.3-02-PLAN.md — Build delta report (code-vs-spec gap analysis with P0/P1/P2 triage)

### Phase 3: Retrieval Pipeline
**Goal**: A developer can query memory and receive ranked results that combine graph, vector, and lexical signals with explainable scores and token-budgeted context packing
**Depends on**: Phase 2
**Requirements**: RETR-01, RETR-02, RETR-03, RETR-04, RETR-05, RETR-06
**Success Criteria** (what must be TRUE):
  1. A semantic query returns vector-similarity-ranked results from the HNSW index
  2. A keyword query returns BM25-ranked results from the Tantivy index
  3. A hybrid retrieval query combines candidates from graph neighborhood, vector similarity, lexical match, and recency/salience signals into a single merged result set with deterministic re-ranking using configurable, versioned scoring weights
  4. Each result in a retrieval response includes an explainable score trace showing the individual components (graph, vector, lexical, recency, salience) that contributed to its rank
  5. Retrieval output is packed into a structured memory bundle (entity snapshots, stable facts, recent decisions, active tasks) that respects a configurable token budget
**Plans**: 4 plans

Plans:
- [ ] 03-01-PLAN.md — Retrieval data models, versioned scoring config, domain enums, operations table
- [ ] 03-02-PLAN.md — Query analysis (intent/entity/temporal extraction) and parallel candidate generation with merging
- [ ] 03-03-PLAN.md — Epistemic filtering and composite scoring with deterministic ranking (TDD)
- [ ] 03-04-PLAN.md — Context packing, RetrievalPipeline orchestrator, MemoryEngine.retrieve() integration

### Phase 3.1: Epistemic Type & Confidence Matrix
**Goal**: Every memory object carries its epistemic type at creation, default confidence is assigned from a (type, source) matrix, and epistemic filtering uses real field values instead of getattr fallback
**Depends on**: Phase 3
**Requirements**: EPIS-01, EPIS-02, EPIS-05
**Gap Closure:** Closes audit gaps — EPIS-01 unsatisfied (no epistemic_type field), EPIS-02 unsatisfied (no confidence matrix), EPIS-05 partial (filtering depends on missing field), integration issue #4 (epistemic_type absent)
**Success Criteria** (what must be TRUE):
  1. MemoryNode has an epistemic_type field populated at creation with a valid EpistemicType value
  2. DuckDB schema includes epistemic_type column, persisted and queryable
  3. Default confidence is assigned from the (epistemic_type, source_type) matrix during ingestion
  4. Epistemic filtering (DEFAULT/EXPLICIT modes) uses real epistemic_type values, not getattr fallback
**Plans**: 2 plans

Plans:
- [ ] 03.1-01-PLAN.md — SourceType enum, epistemic_type/source_type fields on MemoryNode, DuckDB schema migration, graph store column mapping
- [ ] 03.1-02-PLAN.md — ConfidenceMatrix, epistemic inference, LLM extraction classification, ingestion/store() wiring, retrieval getattr cleanup, startup backfill

### Phase 3.2: Retrieval Filter Forwarding
**Goal**: Retrieval candidate generation respects scope and temporal filters by forwarding them to all backends before candidate merging
**Depends on**: Phase 3.1
**Requirements**: NSPC-05
**Gap Closure:** Closes audit gaps — NSPC-05 unsatisfied (scope not forwarded), RETR-03 temporal partial (temporal filter not forwarded), E2E Flow 3 (Scope Isolation broken at retrieval)
**Success Criteria** (what must be TRUE):
  1. generate_candidates() forwards scope parameter to vector, lexical, and graph backends — candidates from other scopes are excluded
  2. Temporal filter from QueryAnalysis is forwarded to backends — expired nodes excluded from candidates
  3. E2E Flow: retrieve(scope=PROJECT) returns only PROJECT-scoped candidates, not PERSONAL
**Plans**: 2 plans

Plans:
- [ ] 03.2-01-PLAN.md — Scope + temporal filter forwarding to all backend generators, multi-scope entry points
- [ ] 03.2-02-PLAN.md — Cross-scope hint generation, filter metadata on response, integration tests

### Phase 3.3: Contradiction Modeling
**Goal**: When conflicting assertions are detected, the system preserves both objects, creates a CONTRADICTS edge, and logs a CONTRADICTION_NOTED operation
**Depends on**: Phase 3.1
**Requirements**: EPIS-04
**Gap Closure:** Closes audit gaps — EPIS-04 unsatisfied (CONTRADICTS edge never created), integration issue #3
**Success Criteria** (what must be TRUE):
  1. SupersedenceDetector creates CONTRADICTS edges (in addition to SUPERSEDES) when conflicting assertions are detected
  2. Both original and conflicting objects are preserved with CONTRADICTS edge linking them
  3. CONTRADICTION_NOTED operation is logged to the operations table with structured payload
**Plans**: 2 plans

Plans:
- [ ] 03.3-01-PLAN.md — CONTESTED/DEPRECATED lifecycle states, temporal_intent extraction, contradict()/resolve_contradiction() graph operations
- [ ] 03.3-02-PLAN.md — SupersedenceDetector temporal_intent branching, retrieval conflict metadata, CONTESTED packing exclusion, integration tests

### Phase 3.4: Namespace & Config Expansion
**Goal**: Namespace types match the full RFC-0004 specification and all [HYPOTHESIS] parameters are exposed as configurable values
**Depends on**: Phase 3
**Requirements**: NSPC-01, TRST-07
**Gap Closure:** Closes audit gaps — NSPC-01 partial (3/6 namespace types), TRST-07 partial (no systematic config mechanism)
**Success Criteria** (what must be TRUE):
  1. Scope/Namespace enum includes all 6 types: PERSONAL, PROJECT, ORGANISATION, AGENT, SYSTEM, SANDBOX
  2. PRMEConfig exposes ScoringWeights and PackingConfig as configurable fields
  3. All [HYPOTHESIS]-marked parameters are accessible through PRMEConfig without code changes
**Plans**: TBD

Plans:
- [ ] 03.4-01: TBD
- [ ] 03.4-02: TBD

### Phase 4: HTTP API and Python SDK
**Goal**: An external developer can store events, search memory, and retrieve entity snapshots through HTTP endpoints and a Python library, with full async support
**Depends on**: Phase 3
**Requirements**: INTG-01, INTG-02, INTG-03
**Success Criteria** (what must be TRUE):
  1. A developer can POST events, POST retrieval queries, and GET entity snapshots through a documented HTTP API, and the API returns structured JSON responses with appropriate status codes
  2. A developer can perform the same operations using a Python library that wraps the core service layer, without running the HTTP server
  3. All API and library operations support async execution (asyncio), with sync wrappers available for non-async consumers
**Plans**: TBD

Plans:
- [ ] 04-01: TBD
- [ ] 04-02: TBD

### Phase 5: Self-Organization
**Goal**: Memory quality improves automatically over time through scheduled background processes that recalculate salience, promote/demote assertions, generate summaries, deduplicate entities, and archive stale data
**Depends on**: Phase 4
**Requirements**: ORGN-01, ORGN-02, ORGN-03, ORGN-04, ORGN-05
**Success Criteria** (what must be TRUE):
  1. After the salience recalculation job runs, memory objects have updated salience scores reflecting frequency, recency, graph centrality, user pinning, and task linkage -- and retrieval results reflect the updated scores
  2. After the promotion/demotion job runs, assertions that have been reinforced by multiple sources are promoted from Tentative to Stable, and assertions contradicted by newer evidence are moved to Superseded with correct supersedence chain linkage
  3. After the summarization job runs, hierarchical summaries (daily, weekly, monthly) and per-entity snapshots exist and are retrievable
  4. After the deduplication job runs, duplicate entities are merged under a canonical ID with alias resolution, and duplicate assertions are consolidated without information loss
  5. Policy-based retention enforces TTL and compression rules, archiving expired memory objects according to configured retention policies
**Plans**: TBD

Plans:
- [ ] 05-01: TBD
- [ ] 05-02: TBD

### Phase 6: Portability and CLI
**Goal**: A developer can export memory as a self-contained portable artifact, deterministically rebuild all derived state from the event log, and inspect/manage memory through CLI commands
**Depends on**: Phase 5
**Requirements**: TRST-01, TRST-03, TRST-04
**Success Criteria** (what must be TRUE):
  1. The `memory_pack/` directory export contains a manifest.json (recording extraction logic version, scoring weight version, embedding model version), the DuckDB event store, graph data, vector index, and lexical index -- and can be copied to another machine and loaded successfully
  2. A deterministic rebuild from the event log with identical configuration produces identical derived state (graph, vector index, lexical index) such that the same retrieval query returns the same top-k results before and after rebuild
  3. A developer can use CLI commands to inspect memory objects, query memory, export a portable artifact, and trigger a rebuild from the event log
**Plans**: TBD

Plans:
- [ ] 06-01: TBD
- [ ] 06-02: TBD

### Phase 7: Hardening and Ecosystem
**Goal**: Memory is encrypted at rest, system quality is provable through an evaluation harness, and the engine integrates with MCP clients and at least one LLM framework
**Depends on**: Phase 6
**Requirements**: TRST-02, TRST-05, INTG-04, INTG-05
**Success Criteria** (what must be TRUE):
  1. A portable artifact can be encrypted at rest and decrypted for use, with all components (event store, graph, vector index, lexical index) protected
  2. The evaluation harness produces measurable scores for recall accuracy, supersedence correctness, context compaction efficiency, and deterministic rebuild fidelity
  3. An MCP-compatible client (e.g., Claude Desktop) can discover and invoke PRME memory operations as MCP tools
  4. At least one LLM framework integration (LangChain or equivalent) allows a developer to use PRME as a memory backend with minimal configuration
**Plans**: TBD

Plans:
- [ ] 07-01: TBD
- [ ] 07-02: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 2.1 -> 2.2 -> 2.3 -> 3 -> 3.1 -> 3.2 -> 3.3 -> 3.4 -> 4 -> 5 -> 6 -> 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Storage Foundation | 0/4 | Complete    | 2026-02-19 |
| 2. Ingestion Pipeline | 0/4 | Complete    | 2026-02-19 |
| 2.1 Scope Isolation Fix | 2/3 | In progress | - |
| 2.2 WriteQueue Contract & Async Safety | 0/3 | Not started | - |
| 2.3 Revised RFC Reconciliation | 2/2 | Complete    | 2026-02-20 |
| 3. Retrieval Pipeline | 0/4 | Complete    | 2026-02-21 |
| 3.1 Epistemic Type & Confidence Matrix | 0/2 | Complete    | 2026-02-21 |
| 3.2 Retrieval Filter Forwarding | 0/2 | Not started | - |
| 3.3 Contradiction Modeling | 0/2 | Not started | - |
| 3.4 Namespace & Config Expansion | 0/2 | Not started | - |
| 4. HTTP API and Python SDK | 0/2 | Not started | - |
| 5. Self-Organization | 0/2 | Not started | - |
| 6. Portability and CLI | 0/2 | Not started | - |
| 7. Hardening and Ecosystem | 0/2 | Not started | - |
