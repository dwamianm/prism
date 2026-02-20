# Requirements: PRME

**Defined:** 2026-02-19
**Core Value:** An LLM-powered agent can reliably recall long-term context — preferences, decisions, relationships — without resurfacing superseded information or wasting context window tokens.

## Design Principles

Governing principles from the RMS RFC suite. Any implementation decision that conflicts with a principle MUST explicitly justify the conflict.

- **P1 — Append-Only Truth**: The event log is the single source of truth. All derived state can always be discarded and rebuilt. (RFC-0000 §6)
- **P2 — Scoped Determinism**: Determinism is required at the storage and derivation layer. Extraction pipelines operate under best-effort reproducibility. The distinction MUST be explicit. (RFC-0000 §6)
- **P3 — Epistemic Honesty**: Memory objects MUST carry their epistemic status. An inference is not an observation. A hypothesis is not a fact. A deprecated belief must not be silently resurrected. (RFC-0000 §6)
- **P4 — Forgetting Is a Feature**: Decay, reinforcement, and selective retention are first-class capabilities, not afterthoughts. (RFC-0000 §6)
- **P5 — Cost Awareness**: Every memory object has a retrieval cost measured in tokens. Retrieval optimises for utility per token, not relevance alone. (RFC-0000 §6)
- **P6 — Empirical Humility**: Where a design choice cannot be validated with data, it MUST be labelled `[HYPOTHESIS]`. Default parameters are not asserted as optimal. (RFC-0000 §6)
- **P7 — Portability by Design**: Memory state MUST be expressible as a copyable, encryptable, versionable artifact. No memory must depend on a specific service endpoint. (RFC-0000 §6)
- **P8 — Fail Safely**: When retrieval fails, the system MUST degrade to no-memory behaviour, not incorrect-memory behaviour. (RFC-0000 §6)

**Conformance Tiers** (RFC-0000 §4): The RFC suite is organized into 5 tiers (0-4). Each tier depends on the one before it. An implementation MUST NOT claim conformance to a higher tier without satisfying the tier below it.

**[HYPOTHESIS] Marker Convention** (RFC-0000 §8): Unvalidated parameters throughout the suite are marked `[HYPOTHESIS]`. These values MUST be tunable per deployment and SHOULD be updated based on empirical evidence when available.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Storage & Data Model

- [x] **STOR-01**: System stores all conversational inputs as immutable events in an append-only DuckDB event log. (Updated per RFC-0001 §3 and RFC-0002 §3: RFC requires additional fields — `stream`, `actor_id`, `actor_type`, `namespace_id`, `sequence_num`, `prev_hash` — beyond current schema. These fields are tracked for gap closure.)
- [x] **STOR-02**: System represents entities as typed graph nodes (Entity, Event, Fact, Decision, Preference, Task, Summary) in Kuzu behind an abstract GraphStore interface. (Updated per RFC-0001 §4 and §10: RFC defines Entity as a separate first-class model distinct from MemoryObject types. RFC adds INTENT node type requiring RFC-0013. Current NodeType.ENTITY and NodeType.EVENT diverge from RFC model where Entity is a separate table and Event is a log record, not a graph node.)
- [x] **STOR-03**: System represents relationships as typed edges with valid_from, valid_to, confidence, and provenance reference. (Updated per RFC-0001 §9: RFC requires `created_by` field on all edges. RFC defines edge types DERIVED_FROM, SUPERSEDES, CONTRADICTS, SUPPORTS, RELATES_TO, ABOUT_ENTITY. Current code has additional types MENTIONS, PART_OF, CAUSED_BY, HAS_FACT not in RFC.)
- [x] **STOR-04**: System indexes event content and facts in an HNSW vector index with versioned embedding metadata
- [x] **STOR-05**: System indexes event content and facts in a full-text lexical index (Tantivy)
- [x] **STOR-06**: System scopes all memory operations by user_id and session_id. (Updated per RFC-0004 §2: RFC replaces simple scope enum with full namespace model using `namespace_id`. Current 3-value Scope enum (PERSONAL/PROJECT/ORG) maps to a subset of 6 RFC namespace types. See NSPC requirements below.)
- [x] **STOR-07**: System tracks memory object lifecycle states (Tentative -> Stable -> Superseded -> Archived). MANUAL REVIEW: RFC-0001 §8 uses ACTIVE/DEPRECATED lifecycle states vs current TENTATIVE/STABLE. RFC collapses TENTATIVE and STABLE into a single ACTIVE state and adds DEPRECATED (determined to be incorrect/outdated). The TENTATIVE->STABLE promotion concept is replaced by confidence evolution (RFC-0008). See STATE.md decisions for project's chosen naming convention.
- [x] **STOR-08**: System maintains supersedence chains with provenance — superseded facts link to their replacement and the evidence that triggered the change. (Updated per RFC-0003 §7: RFC requires formal contradiction modeling — preserving both objects, creating CONTRADICTS edge, and logging CONTRADICTION_NOTED operation. RFC adds DEPRECATED lifecycle state as the resolution path for contradictions.)

### Ingestion & Extraction

- [x] **INGE-01**: System extracts entities, facts, and relationships from conversation events using pluggable LLM providers
- [x] **INGE-02**: System supports at least OpenAI and one local option for LLM-powered extraction
- [x] **INGE-03**: System supports pluggable embedding providers (API-based and local model)
- [x] **INGE-04**: System persists full conversation history as searchable events
- [x] **INGE-05**: System uses a write queue pattern to handle DuckDB single-writer concurrency under HTTP API load

### Retrieval

- [ ] **RETR-01**: User can search memory by semantic similarity via vector search
- [ ] **RETR-02**: User can search memory by exact terms via lexical full-text search
- [ ] **RETR-03**: System performs hybrid retrieval combining graph neighborhood, vector similarity, lexical match, and recency/salience signals
- [ ] **RETR-04**: System applies deterministic re-ranking with configurable, versioned scoring weights
- [ ] **RETR-05**: System returns explainable retrieval traces showing score components per result
- [ ] **RETR-06**: System constructs context-packed memory bundles (entity snapshots, stable facts, recent decisions, active tasks) within a configurable token budget

### Self-Organization

- [ ] **ORGN-01**: System runs scheduled salience recalculation based on frequency, recency, graph centrality, user pinning, and task linkage
- [ ] **ORGN-02**: System promotes reinforced assertions and demotes stale items through the Tentative -> Stable -> Superseded -> Archived lifecycle
- [ ] **ORGN-03**: System generates hierarchical summaries (daily -> weekly -> monthly) and per-entity snapshots
- [ ] **ORGN-04**: System performs entity alias resolution and assertion deduplication
- [ ] **ORGN-05**: System enforces policy-based retention with TTL and compression

### Epistemic State Model

- [ ] **EPIS-01**: System assigns an epistemic type (OBSERVED, ASSERTED, INFERRED, HYPOTHETICAL, CONDITIONAL, DEPRECATED, UNVERIFIED) to every memory object at creation (RFC-0003 §3)
- [ ] **EPIS-02**: System applies default confidence values from the (epistemic_type, source_type) matrix, with values tunable per deployment [HYPOTHESIS] (RFC-0003 §4)
- [ ] **EPIS-03**: System enforces permitted epistemic transitions and blocks forbidden ones — deprecation is irreversible, transitions must not skip intermediate states (RFC-0003 §6)
- [ ] **EPIS-04**: System models contradictions by preserving both objects, creating CONTRADICTS edge, logging CONTRADICTION_NOTED operation, and surfacing conflicts explicitly at retrieval time (RFC-0003 §7)
- [ ] **EPIS-05**: System supports DEFAULT and EXPLICIT retrieval modes with per-epistemic-type inclusion rules — HYPOTHETICAL and DEPRECATED excluded from DEFAULT mode (RFC-0003 §8)
- [ ] **EPIS-06**: System supports CONDITIONAL claims with condition_state tracking (UNKNOWN/TRUE/FALSE/EXPIRED) and retrieval behavior varying by condition state (RFC-0003 §5)

### Namespace and Scope Isolation

- [ ] **NSPC-01**: System supports 6 namespace types: PERSONAL, PROJECT, ORGANISATION, AGENT, SYSTEM, SANDBOX (RFC-0004 §3)
- [ ] **NSPC-02**: System models namespaces as first-class entities with id, name, type, parent_id, access_policy, retention_policy, decay_policy_ref, and tree-structured hierarchy (RFC-0004 §2, §4)
- [ ] **NSPC-03**: System enforces granular access permissions (READ, WRITE, ASSERT, DEPRECATE, ADMIN) per actor per namespace, with permissions checked independently (RFC-0004 §5)
- [ ] **NSPC-04**: System supports per-namespace retention policies with TTL, max event count, min retention window, and expiry actions (ARCHIVE/TOMBSTONE/HARD_DELETE with HARD_DELETE restricted to SANDBOX) (RFC-0004 §7)
- [ ] **NSPC-05**: System applies namespace filters before returning vector search candidates — post-hoc filtering is insufficient for namespace isolation (RFC-0004 §6)
- [ ] **NSPC-06**: System logs cross-namespace reference operations and preserves referenced object's namespace ID in the reference (RFC-0004 §8)

### Integration & API

- [ ] **INTG-01**: System exposes an HTTP API for storing events, searching memory, and retrieving entity snapshots
- [ ] **INTG-02**: System provides a Python library wrapper over the HTTP API
- [ ] **INTG-03**: System supports async operations throughout (asyncio with sync wrappers)
- [ ] **INTG-04**: System provides an MCP server exposing PRME as tools for MCP-compatible clients
- [ ] **INTG-05**: System provides at least one framework integration (LangChain or equivalent)

### Trust & Hardening

- [ ] **TRST-01**: System exports a portable artifact (memory_pack/ directory with manifest.json) that is copyable and versionable. (Updated per RFC-0002 §9: RFC specifies Parquet format for events/operations export, structured artifact layout with events.parquet, operations.parquet, vectors/, graph/, snapshot/ directories, and checksum.sha256.)
- [ ] **TRST-02**: System supports encryption at rest for the portable artifact
- [ ] **TRST-03**: System can deterministically rebuild all derived structures from the event log given identical configuration. (Updated per RFC-0002 §5: RFC requires derived state tables — memory_objects, edges, entities — to be treated as cache, rebuildable from events + operations logs with identical results.)
- [ ] **TRST-04**: System provides CLI tooling for memory inspection, querying, export, and rebuild
- [ ] **TRST-05**: System includes an evaluation harness measuring recall accuracy, supersedence correctness, context compaction, and deterministic rebuild
- [ ] **TRST-06**: System records policy_version on every operation resulting from a policy decision, enabling exact replay under original policy for auditing (RFC-0002 §4, RFC-0007, RFC-0008)
- [ ] **TRST-07**: All [HYPOTHESIS]-marked parameters are exposed as configurable values with documented defaults, enabling empirical calibration without code changes (RFC-0000 §8)
- [ ] **TRST-08**: System supports an operation log recording all state changes (ASSERT, DEPRECATE, SUPERSEDE, ARCHIVE, TOMBSTONE, EPISTEMIC_TRANSITION, DECAY_APPLIED, REINFORCE, RELATE, ENTITY_CREATE, ENTITY_MERGE, and organiser operations) with structured payloads (RFC-0002 §4, §6)

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
| Emotional state inference from text | Epistemically unsound, privacy-threatening, technically infeasible under determinism requirements (RFC-0000 §4 note) |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| STOR-01 | Phase 1 | Satisfied |
| STOR-02 | Phase 1 | Satisfied |
| STOR-03 | Phase 1 | Satisfied |
| STOR-04 | Phase 1 | Satisfied |
| STOR-05 | Phase 1 | Satisfied |
| STOR-06 | Phase 1, Phase 2.1 | Complete |
| STOR-07 | Phase 1 | Satisfied |
| STOR-08 | Phase 1 | Satisfied |
| INGE-01 | Phase 2 | Satisfied |
| INGE-02 | Phase 2 | Satisfied |
| INGE-03 | Phase 2 | Satisfied |
| INGE-04 | Phase 2 | Satisfied |
| INGE-05 | Phase 2, Phase 2.2 | Complete |
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
| EPIS-01 | Phase 3 (pre-req) | Pending |
| EPIS-02 | Phase 3 (pre-req) | Pending |
| EPIS-03 | Phase 5 | Pending |
| EPIS-04 | Phase 3 | Pending |
| EPIS-05 | Phase 3 | Pending |
| EPIS-06 | Phase 5 | Pending |
| NSPC-01 | Phase 3 (pre-req) | Pending |
| NSPC-02 | Phase 5 | Pending |
| NSPC-03 | Phase 4 | Pending |
| NSPC-04 | Phase 5 | Pending |
| NSPC-05 | Phase 3 | Pending |
| NSPC-06 | Phase 5 | Pending |
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
| TRST-06 | Phase 5 | Pending |
| TRST-07 | Phase 3 (pre-req) | Pending |
| TRST-08 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 47 total
- Mapped to phases: 47
- Unmapped: 0
- Satisfied (Phases 1-2): 11/13
- Pending gap closure (Phases 2.1-2.2): 2 (STOR-06, INGE-05)

---
*Requirements defined: 2026-02-19*
*Last updated: 2026-02-20 after RFC-0000 through RFC-0004 reconciliation*
