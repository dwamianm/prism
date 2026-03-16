# Changelog

All notable changes to PRME will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/dwamianm/prism/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/dwamianm/prism/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/dwamianm/prism/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/dwamianm/prism/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dwamianm/prism/releases/tag/v0.1.0
