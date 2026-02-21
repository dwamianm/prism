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

- [x] **RETR-01**: User can search memory by semantic similarity via vector search
- [x] **RETR-02**: User can search memory by exact terms via lexical full-text search
- [x] **RETR-03**: System performs hybrid retrieval combining graph neighborhood, vector similarity, lexical match, and recency/salience signals. (Updated per RFC-0005 §2-5: RFC specifies 4 parallel candidate generation paths — graph traversal (1-3 hops), vector similarity (HNSW), lexical search (BM25/FTS), and pinned/active object direct lookup. Candidates are merged with `path_count` multi-path membership signal used in scoring. RFC defines 8-input composite scoring formula.)
- [x] **RETR-04**: System applies deterministic re-ranking with configurable, versioned scoring weights. (Updated per RFC-0005 §7: RFC specifies 8-input composite score formula with specific default weights [HYPOTHESIS]: w_semantic=0.30, w_lexical=0.15, w_graph=0.20, w_recency=0.10, w_salience=0.10, w_confidence=0.10, w_epistemic=0.05 (multiplicative), w_paths=0.00 (tiebreaker). Weights must sum to 1.0 excluding epistemic multiplier and path tiebreaker. RFC requires embedding version mismatch detection — reject or fall back to lexical-only.)
- [x] **RETR-05**: System returns explainable retrieval traces showing score components per result. (Updated per RFC-0005 §9: RFC requires every retrieval request to generate a RETRIEVAL_REQUEST operation log entry recording request_id, namespace_scope, candidates generated/filtered/included, tokens used, scores, exclusion reasons, and embedding model version.)
- [x] **RETR-06**: System constructs context-packed memory bundles (entity snapshots, stable facts, recent decisions, active tasks) within a configurable token budget. (Updated per RFC-0006 §2-5: RFC defines STR (Signal-to-Token Ratio) metric as composite_score/token_cost. RFC specifies 5 representation levels — REFERENCE, KEY_VALUE, STRUCTURED, PROSE, FULL — with representation selection policy based on remaining budget. RFC defines 3-priority greedy bin-packing: (1) pinned + active tasks, (2) multi-path objects by STR, (3) remaining by composite score. Context budget MUST NEVER be exceeded.)

### Self-Organization

- [ ] **ORGN-01**: System runs scheduled salience recalculation based on frequency, recency, graph centrality, user pinning, and task linkage. (Updated per RFC-0007 §3: RFC specifies exponential decay function `salience(t) = salience_base * exp(-lambda * t)` with 5 decay profiles — PERMANENT (lambda=0.000), SLOW (lambda=0.005, ~139-day half-life), MEDIUM (lambda=0.020, ~35-day half-life), FAST (lambda=0.070, ~10-day half-life), RAPID (lambda=0.200, ~3.5-day half-life). Default profile assignment by epistemic type [HYPOTHESIS].)
- [ ] **ORGN-02**: System promotes reinforced assertions and demotes stale items through the Tentative -> Stable -> Superseded -> Archived lifecycle. (Updated per RFC-0007 §6 and RFC-0008 §2: RFC defines lifecycle transition thresholds — salience < 0.30 triggers DECAY_WARNING, salience < 0.10 triggers DEPRECATED (if confidence < 0.40) or ARCHIVED, salience < 0.05 forces ARCHIVED, confidence < 0.15 triggers DEPRECATED. RFC-0008 specifies asymptotic reinforcement formula: `confidence_new = confidence_old + gamma * (1.0 - confidence_old)`. Transition to DEPRECATED is irreversible.)
- [ ] **ORGN-03**: System generates hierarchical summaries (daily -> weekly -> monthly) and per-entity snapshots
- [ ] **ORGN-04**: System performs entity alias resolution and assertion deduplication
- [ ] **ORGN-05**: System enforces policy-based retention with TTL and compression. (Updated per RFC-0007 §9: RFC standardizes tombstone format with target_id, target_type, reason, policy_ref, content_hash_of_deleted. RFC-0007 §10 requires decay policy versioning with named, versioned DecayPolicy objects containing lambda_by_profile, mu_multiplier, rho, and tier_thresholds.)

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

### Context Packing

- [ ] **CTXP-01**: System computes Signal-to-Token Ratio (STR = composite_score / token_cost) for every retrieval candidate and uses STR as a tiebreaker within priority tiers (RFC-0006 §2)
- [ ] **CTXP-02**: System supports 5 representation levels (REFERENCE, KEY_VALUE, STRUCTURED, PROSE, FULL) for memory objects in bundles, with representation selection based on remaining token budget (RFC-0006 §4)
- [ ] **CTXP-03**: System uses 3-priority greedy bin-packing for context assembly within token budget: (1) pinned + active tasks, (2) multi-path objects by STR, (3) remaining by composite score. Budget MUST NEVER be exceeded. (RFC-0006 §5)

### Decay and Forgetting

- [ ] **DECY-01**: System applies exponential decay with 5 configurable profiles — PERMANENT (lambda=0.000), SLOW (lambda=0.005), MEDIUM (lambda=0.020), FAST (lambda=0.070), RAPID (lambda=0.200) — assigned per memory object based on epistemic type and namespace decay policy [HYPOTHESIS] (RFC-0007 §3)
- [ ] **DECY-02**: System decays confidence separately from salience using `mu = lambda * 0.5` (half the salience decay rate). Confidence decay is NOT applied to OBSERVED objects unless unaccessed for >180 days. (RFC-0007 §4)
- [ ] **DECY-03**: System supports suppression as a retrieval filter distinct from decay — suppressed objects are excluded from retrieval without modifying salience or confidence scores (RFC-0007 §7)
- [ ] **DECY-04**: System schedules organizer jobs at 3 intervals: RAPID/FAST decay every 6 hours, MEDIUM decay daily, SLOW decay weekly. Lifecycle transitions evaluated daily. (RFC-0007 §8)

### Confidence Evolution

- [ ] **CONF-01**: System applies asymptotic reinforcement formula: `confidence_new = confidence_old + gamma * (1.0 - confidence_old)` for positive signals, with simultaneous salience boost at half the confidence rate (RFC-0008 §2)
- [ ] **CONF-02**: System applies multiplicative penalty formula: `confidence_new = confidence_old * (1.0 - delta)` for negative signals, with salience penalty at 30% of confidence penalty (RFC-0008 §3)
- [ ] **CONF-03**: System recognizes 4 positive signals (REFERENCED gamma=0.08, CONFIRMED gamma=0.15, TASK_COMPLETED_WITH gamma=0.12, CORROBORATED_BY_EXTERNAL gamma=0.10) and 4 negative signals (CORRECTED delta=0.25, CONTRADICTED_BY_OBSERVATION delta=0.20, TASK_FAILED_WITH delta=0.10, IGNORED_REPEATEDLY delta=0.05) with configurable weights [HYPOTHESIS] (RFC-0008 §4)
- [ ] **CONF-04**: System enforces per-session reinforcement cap of 0.40, diminishing returns for repeated identical signals, minimum confidence floor after CORRECTED signal, and maximum confidence ceiling of 0.97 through automated reinforcement [HYPOTHESIS] (RFC-0008 §6)
- [ ] **CONF-05**: System checks agent independence before applying cross-agent reinforcement signals — correlated evidence (shared evidence_ids) receives correlation discount factor of 0.4 by default [HYPOTHESIS] (RFC-0008 §5)

### Feedback Loop

- [ ] **FDBK-01**: System logs INJECTION_EVENT for every memory object included in a Memory Bundle sent to the LLM, recording session_id, request_id, object_id, rank_position, representation_type, token_cost, composite_score, and STR value (RFC-0009 §3)
- [ ] **FDBK-02**: System detects feedback signals via reliability hierarchy: explicit user feedback (HIGH) > tool validation (MEDIUM) > response analysis (LOW, with 0.5x reliability discount) > behavioral signals (VERY LOW). Signal source type and reliability class recorded with every feedback event. (RFC-0009 §4)
- [ ] **FDBK-03**: System aggregates per-object feedback statistics including total_injections, total_referenced, total_confirmed, total_corrected, total_unused, and usage_ratio. Aggregates are rebuildable from operation log. (RFC-0009 §8)
- [ ] **FDBK-04**: System monitors 4 quality metrics with alert thresholds: mean_usage_ratio (<0.20), high_correction_rate (>0.10), budget_waste_ratio (>0.30), unused_injection_rate (>0.60) [HYPOTHESIS] (RFC-0009 §9)
- [ ] **FDBK-05**: System prevents circular dependency between feedback signals and retrieval scoring — per-session reinforcement cap, correlation discount, IGNORED_REPEATEDLY penalty for consistently unused objects, and injection frequency MUST NOT increase retrieval score (RFC-0009 §10)

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

### Portability, Sync, and Federation (RFC-0014)

*Updated: SYNC entries now reference RFC-0014 (Revised) which supersedes original RFC_0002 (Git Sync Profile). RFC-0014 retains Git as a transport option but adds HTTPS REST, peer-to-peer, and email transports; fully specifies merge conflict resolution; and adds federation model.*

- **SYNC-01**: System exports operations as ops bundles (event + operation log batches) signed by the producing instance with checksum verification (RFC-0014 §3)
- **SYNC-02**: System imports ops bundles with deterministic replay ordering and idempotent deduplication by event id (RFC-0014 §5)
- **SYNC-03**: System generates periodic snapshots to accelerate full sync from empty instances (RFC-0014 §5)
- **SYNC-04**: System handles merge via union of ops bundles with idempotent operation handling and deduplication by UUID (RFC-0014 §5)
- **SYNC-05**: System resolves conflicting assertions using 3 auto-resolution conditions: confidence difference >0.20 accepts higher, within 0.20 creates explicit CONTRADICTION with PENDING status. PENDING conflicts MUST NOT be auto-resolved. (RFC-0014 §6)
- **SYNC-06**: System supports Git LFS for large binary attachments when using Git transport (RFC-0014 §4)
- **SYNC-07**: System supports multiple transport options with TLS 1.3 encryption in transit and bundle checksum verification (RFC-0014 §4)
- **SYNC-08**: System handles merge conflict resolution with fully specified semantics — same-entity/same-attribute conflicts resolved by confidence comparison, structural conflicts flagged for human intervention, partial sync accepted with evidence_pending flag (RFC-0014 §6, §7)
- **PORT-01**: System supports multiple transport options (Git append-only JSONL, HTTPS REST, peer-to-peer, email) for memory sync between instances (RFC-0014 §4)
- **PORT-02**: System supports federation agreements between PRME instances — federated agent IDs are namespaced, trust is negotiated per-partner, namespace sharing is opt-in, and policy version mismatches are logged without silent override (RFC-0014 §8)

### Temporal Pattern Awareness

- **TEMP-01**: System detects 8 temporal pattern types (DAILY, WEEKLY, MONTHLY, QUARTERLY, ANNUAL, IRREGULAR_RECURRING, DORMANT, BURST) from memory access history using coefficient_of_variation analysis on access intervals. Minimum 5 access events required before pattern detection. (RFC-0010 §3, §4)
- **TEMP-02**: System applies temporal salience modulation with maximum 0.10 boost [HYPOTHESIS] — pattern_boost_weight * match_confidence added to base salience when current temporal context matches detected pattern (RFC-0010 §5)

### Multi-Agent Memory

- **MAGT-01**: System implements domain-scoped trust model for cross-agent memory sharing — per-agent AgentTrustProfile with default_trust and per-domain trust overrides (minimum 10 domains per agent). Trust-weighted reinforcement: gamma_effective = gamma_base * agent_trust_score(agent_id, object.domain). (RFC-0011 §2, §4)
- **MAGT-02**: System enforces rate limiting (max 100 assertions/hour, 500/day per agent) and new-agent quarantine (confidence capped at 0.35 for configurable period, default 24 hours). High-volume assertion alerts for >10 objects about same entity within 1 hour. (RFC-0011 §6)

### Memory Branching

- **BRCH-01**: System supports 5 branch types (SIMULATION, COUNTERFACTUAL, PLANNING, SANDBOX, TEST) with isolation guarantees — branch events MUST NOT affect canonical, retrieval uses canonical snapshot at fork offset, COUNTERFACTUAL branches MUST NOT merge to canonical. Every branch MUST have an expiration policy. (RFC-0012 §3, §6)
- **BRCH-02**: System implements selective merge with 3 conflict resolution strategies — PRESERVE_BOTH (default, creates CONTRADICTS edge), PREFER_CANONICAL (reject branch object), PREFER_BRANCH (requires ADMIN, deprecates canonical). HYPOTHETICAL objects MUST be reclassified before merge. (RFC-0012 §8)

### Intent and Goal Memory

- **INTN-01**: System stores INTENT memory objects with 6 intent types (GOAL, COMMITMENT, OPEN_QUESTION, DECISION_IN_PROGRESS, RISK, FOLLOW_UP) including intent-specific fields: priority, owner, stakeholders, depends_on, blocks, target_completion, completion_criteria (RFC-0013 §2, §3)
- **INTN-02**: System tracks intent lifecycle through 6 states (ACTIVE, COMPLETED, CANCELLED, ON_HOLD, EXPIRED, SUPERSEDED) with dependency modeling — cycle detection mandatory, cascading cancellation NOT permitted, dependency graph max depth 10, max fan-in 20. RISK intents exempt from salience decay with automatic priority escalation on expiry. (RFC-0013 §4, §6, §8)

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

## RFC Cross-Reference

Both original RFCs (RFC_0001_PRME_Technical_Spec, RFC_0002_RMS_Git_Sync_Profile_v1) have been cross-referenced against the Revised RFC suite (RFC-0000 through RFC-0014). The Revised suite is strictly more detailed — no requirements from the original specs were dropped without replacement. Key changes: (1) lifecycle state terminology changed from TENTATIVE/STABLE to ACTIVE/DEPRECATED (flagged for manual review on STOR-07), (2) Entity separated from MemoryObject as first-class model, (3) emotional signal tracking removed and replaced by Intent and Goal Memory (RFC-0013), (4) Git Sync expanded to multi-transport Portability/Sync/Federation (RFC-0014). All original RFC content is subsumed by the Revised suite.

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
| RETR-01 | Phase 3 | Complete |
| RETR-02 | Phase 3 | Complete |
| RETR-03 | Phase 3 | Complete |
| RETR-04 | Phase 3 | Complete |
| RETR-05 | Phase 3 | Complete |
| RETR-06 | Phase 3 | Complete |
| ORGN-01 | Phase 5 | Pending |
| ORGN-02 | Phase 5 | Pending |
| ORGN-03 | Phase 5 | Pending |
| ORGN-04 | Phase 5 | Pending |
| ORGN-05 | Phase 5 | Pending |
| EPIS-01 | Phase 3.1 | Pending |
| EPIS-02 | Phase 3.1 | Pending |
| EPIS-03 | Phase 5 | Pending |
| EPIS-04 | Phase 3.3 | Pending |
| EPIS-05 | Phase 3.1 | Pending |
| EPIS-06 | Phase 5 | Pending |
| NSPC-01 | Phase 3.4 | Pending |
| NSPC-02 | Phase 5 | Pending |
| NSPC-03 | Phase 4 | Pending |
| NSPC-04 | Phase 5 | Pending |
| NSPC-05 | Phase 3.2 | Pending |
| NSPC-06 | Phase 5 | Pending |
| CTXP-01 | Phase 3 | Satisfied |
| CTXP-02 | Phase 3 | Satisfied |
| CTXP-03 | Phase 3 | Satisfied |
| DECY-01 | Phase 5 | Pending |
| DECY-02 | Phase 5 | Pending |
| DECY-03 | Phase 5 | Pending |
| DECY-04 | Phase 5 | Pending |
| CONF-01 | Phase 5 | Pending |
| CONF-02 | Phase 5 | Pending |
| CONF-03 | Phase 5 | Pending |
| CONF-04 | Phase 5 | Pending |
| CONF-05 | Phase 5 | Pending |
| FDBK-01 | Phase 5 | Pending |
| FDBK-02 | Phase 5 | Pending |
| FDBK-03 | Phase 5 | Pending |
| FDBK-04 | Phase 5 | Pending |
| FDBK-05 | Phase 5 | Pending |
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
| TRST-07 | Phase 3.4 | Pending |
| TRST-08 | Phase 5 | Pending |
| SYNC-01 | v2 | Deferred |
| SYNC-02 | v2 | Deferred |
| SYNC-03 | v2 | Deferred |
| SYNC-04 | v2 | Deferred |
| SYNC-05 | v2 | Deferred |
| SYNC-06 | v2 | Deferred |
| SYNC-07 | v2 | Deferred |
| SYNC-08 | v2 | Deferred |
| PORT-01 | v2 | Deferred |
| PORT-02 | v2 | Deferred |
| TEMP-01 | v2 | Deferred |
| TEMP-02 | v2 | Deferred |
| MAGT-01 | v2 | Deferred |
| MAGT-02 | v2 | Deferred |
| BRCH-01 | v2 | Deferred |
| BRCH-02 | v2 | Deferred |
| INTN-01 | v2 | Deferred |
| INTN-02 | v2 | Deferred |
| PROV-01 | v2 | Deferred |
| PROV-02 | v2 | Deferred |
| ADVN-01 | v2 | Deferred |
| ADVN-02 | v2 | Deferred |

**Coverage:**
- v1 requirements: 65 total (STOR 8 + INGE 5 + RETR 6 + ORGN 5 + EPIS 6 + NSPC 6 + CTXP 3 + DECY 4 + CONF 5 + FDBK 5 + INTG 5 + TRST 8)
- v2 requirements: 22 total (SYNC 8 + PORT 2 + TEMP 2 + MAGT 2 + BRCH 2 + INTN 2 + PROV 2 + ADVN 2)
- Grand total: 87 requirements
- Mapped to phases: 87
- Unmapped: 0
- Satisfied (Phases 1-3): 22/22 (core) + 3 CTXP = 25
- v1 pending: 40 (includes 7 gap closure in Phases 3.1-3.4)
- v2 deferred: 22
- RFCs covered: All 15 Revised (RFC-0000 through RFC-0014) + 2 original (RFC_0001, RFC_0002)

---
*Requirements defined: 2026-02-19*
*Last updated: 2026-02-20 after full RFC suite reconciliation (RFC-0000 through RFC-0014 + original RFCs)*
