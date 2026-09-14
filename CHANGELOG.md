# Changelog

All notable changes to PRME will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A matched raw-store versus real `ingest()` evaluation profile for
  LongMemEval source evidence. Extracted runs freeze the local model digest and
  full configuration before generation, require durable extraction completion,
  report source/node materialization coverage, and support an explicit paired
  profile comparison without presenting source lineage as answer accuracy.
- Optional, token-counted context guidance with a non-displacement guarantee.
  The packer includes guidance only when it fits after record selection, exposes
  whether it was included, and preserves it through controlled ablation.
- Exact, nonmutating packed-context ablation and citation-checked presence
  credit. Applications can re-answer after removing one cited memory, preserve
  both context hashes and token accounting, and distinguish load-bearing,
  redundant, misleading, and noncuring outcomes without changing retrieval,
  ranking, or retention.
- Immutable, owner-scoped answer citation records across async/sync Python,
  HTTP, MCP, DuckDB, and PostgreSQL. Citations bind to saved content-bearing
  context, preserve optional answer digests, and use caller UUIDs for exact
  retry safety without changing memories or ranking.
- Durable retry IDs for promotion and archival across sync/async Python, HTTP,
  and MCP. HTTP uses `Idempotency-Key`; conflicting key reuse returns 409.
- Atomic, checksummed explicit corrections across sync/async Python, HTTP, MCP,
  DuckDB, and PostgreSQL. State, deterministic edge, and complete before/after
  audit record commit together; exact retries remain safe after restart.
- A complete tenant-scoped contradiction lifecycle on sync/async Python, HTTP,
  and MCP. Marking and resolution atomically update claims, edges, and audit
  records; exact retries are idempotent across restarts and resolution evicts
  the deprecated claim from derived search indexes.
- Atomic, evidence-aware condition evaluation across Python, HTTP, MCP, DuckDB,
  and PostgreSQL. Retry IDs are durable, state changes retain checksummed
  before/after records, and confirmed conditions receive asserted retrieval
  weight without losing their conditional classification.
- MCP `memory_store` parity for role, session, metadata, confidence, epistemic
  type, and source type, allowing agents to create qualified memories directly.
- Tenant-scoped node provenance on sync/async Python, HTTP, and MCP, including
  owned source events, explicit missing-evidence signals, valid contradiction
  links, and bounded chronological operation pages with opaque cursors.
- Explicit aggregation coverage on Python, HTTP, MCP, durable retrieval records,
  and packed model context. Natural-language counts/lists report candidate,
  selection and context truncation without claiming exhaustive enumeration.
- A reproducible 8K-context Ollama profile for `qwen3.5:35b-a3b`, with two
  successful strict extraction reports and an equal-context 9B comparison.
- Typed claim polarity and exact explicit conditions for built-in LLM
  extraction. Conditional claims retain an auditable state and stay out of
  default retrieval until confirmed.
- `MemoryWorkspace` and lease-scoped `NamespaceMemory` for named local projects,
  with stable pack identity, bounded idle-engine eviction, shared embeddings,
  process ownership and cancellation-safe lease cleanup.
- `MemoryWorkspace.open_postgres()` for named PostgreSQL projects sharing one
  bounded connection pool. Identity checks and a private-only search path protect
  routing; registry and schema creation publish atomically. Hosted grants remain
  separate. pgvector symbols now resolve explicitly even outside `public`.

- Optional `duckdb_threads` / `PRME_DUCKDB_THREADS` control for each open local
  database. The default preserves DuckDB's setting; conflicting concurrent opens
  fail without reconfiguring the active pack.

### Changed

- Packing configuration now rejects negative candidate limits and graph depths
  outside the RFC's 1-3 range. The legacy no-op `chars_per_token` and
  `cross_scope_token_budget` fields remain parseable for receipt compatibility,
  but non-default use warns that exact context tokenization and
  `cross_scope_top_n` are the active controls. Setting `cross_scope_top_n=0`
  now skips the secondary hint search entirely.
- The organizer registry now contains only implemented jobs. The unimplemented,
  unvalidated `centrality_boost` no-op is no longer advertised or run during
  default maintenance; explicit requests fail as an unknown job instead of
  returning a misleading success result.
- Temporal reasoning guidance is now enabled by default when it fits after
  evidence selection. A registered 70-context confirmation improved temporal
  answers from 31/50 to 34/50. Personalization and current-state guidance remain
  experimental behind `packing.context_guidance_mode="all"`; `"off"` restores
  pre-guidance behavior. Receipt schema version 6 records the chosen mode while
  versions 1–5 preserve their canonical bytes and mean guidance was off.
  Elapsed-time questions no longer receive aggregation coverage warnings;
  time totals across records remain aggregation queries.
- `knowledge_at` now exposes a machine-readable `historical_coverage` boundary
  in Python, HTTP, MCP, retrieval receipts, and the token-counted model context.
  It is explicitly an aware ingestion-time cutoff over current indexes; prior
  lifecycle and index state are not replayed. Naive datetimes fail immediately.
- Balanced multi-path context packing is now the default. A complete fixed
  119-question answer trial scored 83 correct with balanced contexts versus 67
  with density. A separately registered 381-question answer confirmation scored
  250 versus 185, with 88 paired wins, 23 losses, and no lower category total.
  Explicit density and score policies remain available, while historical receipts
  retain their original density meaning and checksums.
- Current-state retrieval now recognizes ordinary present-tense state questions,
  while inferred recency reweighting requires explicit update evidence and dated
  temporal queries remain historical. Decisions and instructions receive the
  same default semantic-node boost as facts and preferences.
- Structured extraction now sends a configurable sampling temperature, defaulting
  to zero, and rejects uncertain or contingent future actions mislabeled as
  completed decisions. Explicit known-negative updates can retire the same
  known-positive claim without guessing legacy polarity.
- PostgreSQL now honors the default `vector_exact_search=True`, materializing
  eligible rows before distance ordering to prevent filtered HNSW starvation.
  `False` explicitly permits approximate search. Broad exact queries may cost
  more; new receipts report the requested mode.

- Default organizer passes exclude the legacy global feedback tuner; session
  completion runs promotion only. Explicit scoped `feedback_apply` requests now
  raise `ValueError` before any work. Trusted operators can retain the legacy
  behavior with explicit `organize(jobs=["feedback_apply"])` and no user scope.

### Fixed

- Built-in extraction now accepts source-grounded literal personal references
  such as `I` and `we` without requiring them to be named entities. Each
  unlisted reference receives one provenance-bound identity within its source
  event and cannot merge across messages; missing named entities and unsupported
  citations remain validation errors. Provider citations that differ only in
  straight or curly quote marks are canonicalized back to the exact source span.
- Recreate a loaded empty USearch index before its first new insertion, avoiding
  a reproducible native crash after deleting the last vector, closing, reopening,
  and storing another memory.

- Make greedy consolidation clustering and source selection stable across
  equivalent histories with different generated UUIDs. Singular relational
  state questions use semantic answer-class relevance so generic subject words
  do not swamp the requested relation.
- Make extractive consolidation publication durable and idempotent. Unchanged
  clusters reuse one summary; changed source snapshots atomically publish a new
  generation with its provenance edges and archive the predecessor. DuckDB
  journals and fences external index staging for restart and multi-engine recovery;
  PostgreSQL serializes concurrent publications through the generation head.
- Keep unresolved personal references local to their source event during new
  ingestion. Historical prepared derivations retain their original replay policy.
- Prevent organizer similarity matches from merging different claim text,
  validity or provenance; semantic entity aliases remain unverified links.
- Initialize PostgreSQL vector columns and indexes against the selected table,
  without treating names in other schemas as an existing local installation.
- Preserve relationship validity and provenance during organizer merges; failed
  copies keep the source active, and deterministic copy IDs make retries converge.
- Publish organizer evidence unions, relationship copies, retirement and a single
  supersedence edge atomically, with a checksummed operation record and scoped,
  idempotent retries. Concurrent PostgreSQL merges lock shared nodes before reads.

## [0.11.0] - 2026-09-11

### Added

- Tenant-scoped organizer execution through `organize(user_id=...)` and
  `prme organize --user-id`, with cross-owner merge guards.

### Benchmark measurement
- A registered exact-context ablation over all 37 development questions with one
  annotated source in the balanced bundle reduced the fixed reader from 29/37 to
  7/37 correct. Twenty-three correct answers became wrong. The audit retained six
  redundant non-flips, seven noncuring reader errors, and one benchmark-reference
  inconsistency rather than converting them into negative memory labels.
- Preserve per-question evaluation failures in reports and retry selection.
- Report scored coverage and whole-benchmark failures; weight summary accuracy by
  measured questions and fail the CLI when any repeated run is incomplete.
- Propagate abstention-provider failures during evaluation instead of treating
  the application's fallback as a measured verdict.

### Fixed

- Report the package version consistently in REST metadata and MCP resources.
- Enforce node ownership for engine operations that accept node IDs.
- Reapply scope, bi-temporal, and epistemic filters after late retrieval stages.
- Serialize organizer SQL through the shared DuckDB connection lock.
- Restore CI coverage for main, optional API/MCP dependencies, simulations,
  and live PostgreSQL tests on supported Python versions.
- Disable redundant Tantivy background reader reloads so a closed lexical
  index cannot recreate metadata lock files during pack cleanup or movement.

- Expand vector searches when tenant, scope, lifecycle, or temporal filters leave
  fewer than the requested number of distinct memory nodes.

### Changed

- Schedule opportunistic organizer passes in the background and drain them on close.
- Pin dateparser language and gate temporal parsing to reduce retrieval overhead.
- Remove dataset observations, answer-revealing prompt examples, and benchmark-only
  query expansion from real-data evaluation.
- Replace stale accuracy headlines with a measurement contract and reviewed roadmap.

## [0.10.0] - 2026-07-15

### Added

- **Deterministic vector search + `prme rebuild`** (issue #45) — Reproducible vector retrieval and full reconstruction of derived indexes (vector, lexical) from the durable event/graph store via `prme rebuild`
- **Multi-query reformulation** (issue #43) — Opt-in retrieval mode that expands a query into multiple reformulations for broader candidate generation

### Changed

- **REST API hardening** (issue #34) — Authentication, loopback-only bind by default, CORS restrictions, and error-message sanitization
- **Encryption-at-rest lifecycle hardening** (issue #37) — Tightened key handling and encrypt/decrypt lifecycle for memory pack files
- **Stored memory neutralized in LLM context** (issue #36) — Retrieved memory is delimited and neutralized before entering the LLM context to prevent prompt injection from stored content
- **Ingestion index write path batched** (issue #39) — Batched writes across the ingestion indexing path
- **Retrieval hot path DB round-trips reduced** — Fewer database round-trips on the retrieval hot path
- **Benchmark measurement rigor** (issue #44) — Pinned judge model, cached verdicts, and multi-run scoring for reproducible benchmark numbers

### Fixed

- **Bedrock provider extraction** (issue #59) — Pass model through to `client.create()` so the Bedrock provider works during extraction
- **Context formatter token budget + dedup** (issue #42) — Enforce the token budget and deduplicate entries in the context formatter
- **Index eviction on supersedence/archival** (issue #41) — Evict superseded/archived content from the lexical and vector indexes
- **Auto-promotion coverage** (issue #40) — Auto-promote now reaches older eligible nodes, not just the newest batch

## [0.4.0] - 2026-03-16

### Added

- **Bi-temporal data model** (issue #21) — `event_time` field distinguishes when something happened vs when system learned about it; `knowledge_at` parameter on retrieve() for point-in-time knowledge snapshots
- **Encryption at rest** (issue #14) — Transparent Fernet (AES-128-CBC + HMAC) encryption of memory pack files; PBKDF2 key derivation; encrypt on close, decrypt on create
- **Deduplication and entity alias resolution** (issue #11) — Organizer jobs for vector-similarity-based duplicate detection (threshold 0.92) and alias resolution (threshold 0.85); merge logic with SUPERSEDES edges
- **Evaluation harness** (issue #16) — Precision@k, recall@k, nDCG@k, MRR metrics; ground truth support in simulation checkpoints; 3 evaluation scenarios (factual, temporal, supersedence)
- **HTTP API layer** (issue #17) — FastAPI REST API with endpoints for store, retrieve, organize, node operations, graph traversal, health, and stats
- **CLI tooling** (issue #15) — `prme` command-line tool for memory inspection: info, nodes, edges, search, chain, organize, stats, export
- **Dual-stream ingestion** (issue #25) — `ingest_fast()` guaranteed sub-50ms path (event store + vector only); materialization queue for deferred graph writes
- **Memory quality self-assessment** (issue #24) — Feedback signal tracking, gradient-free weight auto-tuning, per-namespace scoring profiles, quality metrics
- **Procedural memory** (issue #23) — INSTRUCTION node type for system instructions and procedural knowledge; Priority 0 packing into `system_instructions` section; epistemic inference support
- **Entity snapshot generation** (issue #13) — `generate_entity_snapshot()` produces structured entity state views from graph neighborhood; `snapshot_generation` organizer job; simulation scenario
- **Predictive forgetting / consolidation** (issue #22) — Semantic clustering of episodic memories; summary abstraction creation; redundant memory archival; `consolidate` organizer job
- **TTL-based archival** (issue #12) — `ttl_days` field on memory nodes; per-type default TTL configuration; `tombstone_sweep` organizer job with operation logging; policy-based retention enforcement
- **Summarization pipeline** (issue #10) — Hierarchical daily -> weekly -> monthly summarization; configurable thresholds; time-budget-aware processing; `summarize` organizer job
- **Benchmark suite** (issue #26) — LoCoMo long-conversation benchmark, LongMemEval 5-ability evaluation, custom epistemic benchmark (supersedence correctness, confidence calibration, contradiction detection, belief revision, abstention quality)
- **Hybrid retrieval pipeline v2** — supersedence-aware scoring, lifecycle filtering (SUPERSEDED/ARCHIVED exclusion), query reformulation with LLM-generated alternative queries, session context expansion (top-20 with ±3 adjacent turns)
- **Context formatter** — temporal annotations (days-ago, COMPUTED offsets), chronological sorting for temporal queries, relevance-ranked formatting with date annotations
- **Benchmark infrastructure** — LLM-as-judge with configurable generation model, concurrent evaluation (semaphore-based throttling), resilient structured output for reasoning models

### Fixed

- Integration test fixture (issue #18) — Added `examples/conftest.py` with engine/log fixtures
- DuckDB segfault in concurrent tests (issue #19) — Isolated DuckDB connections per test with `conn_lock` protection
- GeneratedAnswer schema resilient to reasoning models (gpt-5-mini) that embed answers in reasoning field

## [0.3.0] - 2026-03-08

### Added

- `engine.reinforce()` method — bumps `reinforcement_boost` (+0.15, cap 0.5) and `confidence_base` (+0.05, cap 0.95), updates `last_reinforced_at`, appends evidence refs
- Re-mention reinforcement in `store()` — opt-in via `reinforce_similarity_threshold` config; vector-searches for similar existing nodes and reinforces them automatically
- Keyword-based supersedence detection in `store()` — opt-in via `enable_store_supersedence` config; `ContentContradictionDetector` with 10 regex patterns for migration/replacement language
- Oscillation detection for flip-flop supersedence patterns — `OscillationDetector` using Jaccard keyword similarity on supersedence chains; applies confidence penalty (0.1 per cycle, cap 0.3)
- `update_node()` method on `GraphStore` protocol and all implementations (DuckPGQ, PostgreSQL) for field-level node updates
- Ranking assertions in simulation harness (`SimCheckpoint.ranking_assertions`)
- Lifecycle assertions in simulation harness (`SimCheckpoint.lifecycle_assertions`)
- Deterministic rebuild verification (`SimulationRunner.run_deterministic_check()`)
- Surprise-gated storage — opt-in via `enable_surprise_gating` config; `NoveltyScorer` computes novelty of incoming content against existing memory, boosting salience for novel content and penalizing redundant content
- Four new simulation scenarios: `reinforcement`, `remention`, `oscillation`, `surprise_gating`

### Fixed

- `promotion_evidence_count` default aligned with `store()` behavior (default 1, matching the single evidence ref created per node)

### Changed

- `store()` pipeline now has 6 steps: event persistence, graph node creation, vector/lexical indexing, re-mention reinforcement (opt-in), supersedence + oscillation detection (opt-in), surprise gating (opt-in)

## [0.2.0] - 2026-02-27

### Added

- Self-organizing memory system (RFC-0015) with virtual decay, maintenance runner, and organizer jobs
- Simulation harness for validating memory behavior without LLM dependencies
- Decay mechanics: exponential salience/confidence decay with per-type decay profiles
- Organizer jobs: promote, decay_sweep, archive, feedback_apply (plus stubs for future jobs)
- Opportunistic maintenance during retrieve/ingest operations
- Three simulation scenarios: `changing_facts`, `decay_mechanics`, `information_accumulation`

## [0.1.0] - 2026-02-19

### Added

- Append-only event store (DuckDB)
- Graph-based relational model with typed nodes and edges
- Vector index (usearch HNSW) with fastembed embeddings
- Lexical full-text search (Tantivy)
- Hybrid retrieval pipeline with deterministic scoring and context packing
- Epistemic state model with lifecycle transitions and confidence tracking
- LLM-powered ingestion pipeline (OpenAI, Anthropic, Ollama)
- Entity merge and supersedence handling
- Namespace and scope isolation
- Optional PostgreSQL backend
- Terminal chat example with persistent memory
- Quickstart example

[Unreleased]: https://github.com/dwamianm/prism/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/dwamianm/prism/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/dwamianm/prism/compare/v0.9.0...v0.10.0
[0.4.0]: https://github.com/dwamianm/prism/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dwamianm/prism/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dwamianm/prism/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dwamianm/prism/releases/tag/v0.1.0
