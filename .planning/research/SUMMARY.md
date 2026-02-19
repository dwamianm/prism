# Project Research Summary

**Project:** PRME (Portable Relational Memory Engine)
**Domain:** Local-first embeddable LLM memory engine (Python)
**Researched:** 2026-02-19
**Confidence:** MEDIUM-HIGH

## Executive Summary

PRME is an embeddable Python library that gives LLM agents durable, self-organizing long-term memory. Experts in this domain (Mem0, Zep/Graphiti, Letta/MemGPT, Cognee) all converge on a hybrid architecture: events arrive as immutable facts, get processed into a knowledge graph and search indices, and are retrieved through a multi-modal pipeline combining semantic vectors, lexical BM25, and graph traversal. What makes PRME distinctive — and architecturally justified by research — is its full commitment to event sourcing: the event log is the source of truth, all derived stores (graph, vector, lexical) are projections that can be rebuilt deterministically. No competitor offers this guarantee. The recommended stack is DuckDB (event store) + Kuzu 0.11.3 (embedded graph) + USearch (HNSW vectors) + tantivy-py (BM25 full-text), with FastAPI for the HTTP layer and FastEmbed/LiteLLM for pluggable embedding providers.

The recommended build order is strict: Event Store before everything, then Graph Store and indices, then Entity Extraction, then Hybrid Retrieval, then the Background Organizer. The HTTP API is a thin wrapper over the core service layer, not a design center. A clean `GraphStore` abstraction interface is non-negotiable given that Kuzu was acquired by Apple in October 2025 and its repository archived — this is the most pressing day-one constraint on the project. The abstraction must exist before any graph code ships, or migration to RyuGraph or DuckPGQ will be a rewrite.

The top systemic risk is compounding state corruption if the extraction pipeline is built incorrectly: LLM-based extraction that runs inline on writes will produce hallucinated entities that poison the graph, degrade retrieval, and feed back into worse extractions. The correct pattern is raw-event-first (always store the event), extract-as-derived-process (extraction output is a derived event that can be replayed), and confidence-gate (all LLM-extracted assertions start Tentative). DuckDB's single-writer concurrency model also creates a gotcha when the HTTP API serves concurrent agents — a write queue must be designed into the API layer from day one.

---

## Key Findings

### Recommended Stack

See full analysis in `.planning/research/STACK.md`.

The stack is chosen to maximize local-first, embeddable, zero-server characteristics. DuckDB is the single storage layer for events and doubles as a potential future consolidation point (via VSS and DuckPGQ extensions). Kuzu provides the embedded Cypher-queryable property graph — but is now a pinned dependency (v0.11.3 final, repo archived). USearch replaces the abandoned hnswlib for HNSW. tantivy-py gives Rust-backed BM25 that is 30x faster than any pure-Python alternative. FastAPI + Pydantic v2 covers the HTTP layer. FastEmbed handles local embedding inference without a PyTorch dependency.

**Core technologies:**
- **DuckDB 1.4.4:** Append-only event store — embedded, ACID, columnar, LTS release, ships with FTS and VSS extensions
- **Kuzu 0.11.3 (PINNED):** Embedded property graph (Cypher, typed nodes/edges, temporal validity) — final release, MIT license, repo archived; must be abstracted behind GraphStore interface
- **USearch 2.23.0:** HNSW vector index — 10x faster than FAISS, f16/i8 quantization, disk-viewable, actively maintained
- **tantivy-py 0.25.1:** BM25 full-text search — Rust-backed, production-grade, actively maintained Dec 2025
- **FastAPI 0.129.0 + Pydantic 2.12.5:** HTTP API + validation — de facto Python async API standard
- **FastEmbed 0.7.4:** Local ONNX embedding inference — no PyTorch dependency, ideal for local-first use
- **LiteLLM 1.81.13:** Embedding and LLM provider router — 100+ providers, pluggable with unified API
- **uv 0.10.4 / ruff 0.15.1:** Project manager and linter/formatter — both Rust-based, replace Poetry/pip and black+isort+flake8 respectively

**Critical version constraint:** Kuzu must be pinned at exactly `0.11.3`. No future releases will come from upstream. Test on target Python version before committing.

**Future architectural path:** DuckDB-only consolidation (DuckDB VSS + DuckPGQ replacing Kuzu + USearch) is architecturally elegant but not production-ready today. Build abstractions now that would allow this consolidation in 12-18 months.

### Expected Features

See full analysis in `.planning/research/FEATURES.md`.

PRME's differentiators over all competitors (Mem0, Zep, Letta, LangMem, Cognee) are: full event sourcing with deterministic rebuild (no competitor offers this), typed graph with supersedence chains (competitors either overwrite or do edge-level invalidation, not full provenance chains), and portable artifact format (competitors are server-tied or cloud-first). These three features — together — define PRME's market position.

**Must have (table stakes for v1):**
- Memory CRUD API (add/search/update/delete) — every competitor has this
- Semantic vector search (HNSW-based) — baseline retrieval method
- Pluggable embedding providers (OpenAI + local/FastEmbed minimum)
- User/session scoping — multi-user memory is expected in any production system
- Conversation/event persistence — the append-only event store covers this by design
- Python SDK — target audience is Python agent builders
- Pluggable LLM providers — for extraction and summarization
- Entity extraction from conversations — without this, developers must manually tag everything
- Full-text/lexical search — hybrid retrieval is now the standard
- Async support — production agents are async

**Should have (competitive differentiators, v1.x):**
- Append-only event sourcing — PRME's architectural foundation, no competitor has this
- Typed graph-based relational model (Entity/Fact/Decision/Preference/Task/Summary nodes) — richer than any competitor's schema
- Supersedence handling with provenance chains — not overwrite-based like Mem0
- Scheduled self-organizing memory (salience, promotion/demotion, summarization, dedup)
- Portable artifact format (`memory_pack/` directory)
- Explainable retrieval traces (score components per result)
- Context packing with explicit token budget control
- MCP server — low effort, becoming the standard for LLM tool integration

**Defer (v2+):**
- Encryption at rest — portable artifact encryption
- CLI tooling (`prme inspect`, `prme query`, `prme export`, `prme rebuild`)
- Evaluation harness — recall accuracy, supersedence correctness, determinism tests
- Deterministic rebuild validation (design in Phase 1, validate in Phase 3)
- LangChain/LlamaIndex integration packages
- Additional LLM/embedding providers beyond the initial set

**Anti-features to avoid entirely:**
- Cloud-hosted managed service (conflicts with local-first; splits focus)
- Web UI/dashboard (diverges from developer-tool focus; CLI is sufficient)
- CRDT-based distributed sync (enormous complexity; premature)
- Built-in RAG over external documents (scope creep; PRME is memory, not generic RAG)
- Automatic prompt rewriting/procedural memory (hard to get right; not PRME's scope)

### Architecture Approach

See full analysis in `.planning/research/ARCHITECTURE.md`.

The architecture is a layered event-sourced CQRS-lite system: a write path (ingestion pipeline) appends immutable events to DuckDB, then synchronously writes derived projections to Kuzu (graph), USearch (vectors), and tantivy (full-text). The read path (retrieval pipeline) runs parallel candidate generation across all four backends, merges results, applies a deterministic weighted re-ranking formula, and packs the top results into a structured memory bundle. A background organizer (APScheduler, AsyncIOScheduler) runs maintenance jobs on a schedule: salience recalculation, promotion/demotion, hierarchical summarization, deduplication, and archival. The organizer never runs inline with requests. The project structure separates `api/` (thin HTTP layer), `core/` (framework-agnostic pipeline orchestration), `storage/` (backend-specific modules each with consistent init/write/read/close interface), `organizer/` (scheduler + independent job modules), and `providers/` (embedding and LLM provider interfaces + implementations).

**Major components:**
1. **Event Store (DuckDB)** — append-only immutable source of truth; all derived state carries `source_event_id` provenance
2. **Graph Store (Kuzu, abstracted)** — typed nodes with temporal validity (`valid_from`/`valid_to`), confidence, provenance; Cypher queries for multi-hop relationship traversal
3. **Vector Index (USearch HNSW)** — approximate nearest neighbor search with per-vector model metadata (name, version, dimension)
4. **Lexical Index (tantivy)** — BM25 full-text search over event content, facts, and summaries
5. **Ingestion Pipeline (`core/ingestion.py`)** — orchestrates event append → extraction → graph write → embedding → FTS indexing
6. **Retrieval Pipeline (`core/retrieval.py`)** — parallel candidate generation → merge → deterministic re-ranking → context packing
7. **Scheduled Organizer (`organizer/`)** — five independent idempotent jobs running on APScheduler
8. **HTTP API (`api/`)** — thin FastAPI wrapper over core; routes call core functions directly

**Key architectural patterns:**
- Pattern 1: Event Sourcing + CQRS-lite (event store is write model; graph/vector/lexical are read models)
- Pattern 2: Hybrid retrieval with deterministic re-ranking (26% improvement over vector-only per Mem0 research)
- Pattern 3: Tiered memory lifecycle (Tentative → Stable → Superseded → Archived)
- Pattern 4: Background organizer as separate concern (never inline with requests)

### Critical Pitfalls

See full analysis in `.planning/research/PITFALLS.md`.

1. **Kuzu abandonment risk** — Kuzu repo archived October 2025; build `GraphStore` abstraction interface before any graph code is written; monitor RyuGraph fork and DuckPGQ as migration paths. Phase 1, day-one constraint.

2. **LLM-on-write extraction corruption** — Running LLM extraction inline at write time produces hallucinated entities that compound into corrupted graph data; always store raw events first, run extraction as a replayable derived process, confidence-gate all LLM-extracted assertions as Tentative. Phase 1 extraction pipeline design.

3. **Embedding model lock-in** — Every vector must store model name + version + dimension; design re-embedding as an automated operation triggered by config change; dual-index migration strategy for model changes. Phase 1 vector index schema.

4. **DuckDB write contention under concurrent HTTP load** — DuckDB is single-writer; concurrent HTTP requests cause transaction conflicts; serialize writes through an async write queue from day one of HTTP API design. Phase 1 API layer.

5. **Deterministic rebuild drift** — Determinism breaks through floating-point non-determinism, LLM non-determinism, timestamp-dependent operations, and unversioned transformation logic; cache all external results (LLM extractions, embeddings) as derived events; version every transformation; add rebuild-and-compare CI tests from Phase 1. Phase 1 design constraint, Phase 3 validation.

---

## Implications for Roadmap

Research reveals a clear three-phase dependency chain that cannot be significantly reordered. The event sourcing architecture creates hard sequential dependencies: event store before everything, storage backends before pipelines, pipelines before organizer, organizer before advanced features. The Kuzu situation adds a cross-cutting constraint (GraphStore abstraction) that must be satisfied before any graph-related work.

### Phase 1: Foundation and Core Pipeline

**Rationale:** Everything in the system derives from the event store and depends on the storage backends. The ingestion and retrieval pipelines cannot be built until all four backends exist. The HTTP API is a thin wrapper over the pipelines. All six critical pitfalls have Phase 1 prevention requirements — the graph abstraction, write queue, extraction design, embedding metadata schema, and determinism versioning strategy must be decided before any code is written against them. This phase produces the validatable core: "does PRME retrieve relevant memory better than vector-only?"

**Delivers:**
- `memory_pack/` portable artifact structure (DuckDB event store, Kuzu graph directory, USearch index, tantivy index, manifest.json)
- GraphStore abstraction interface (swappable backing engine)
- Ingestion pipeline: raw event append → rule-based extraction (LLM fallback) → graph write → embedding → FTS indexing
- Retrieval pipeline: parallel candidate generation (graph + vector + lexical + recent high-salience) → deterministic re-ranking → context packing
- HTTP API: POST /events, POST /retrieve, GET /entities/{id}, health endpoint
- Python library wrapper over core
- User/session scoping
- Basic supersedence schema (valid_from/valid_to on graph edges, SUPERSEDES edge type)
- Async write queue for DuckDB concurrency safety
- Embedding metadata (model name + version + dimension) on every vector

**Features from FEATURES.md:** Memory CRUD API, semantic vector search, pluggable embedding providers, user/session scoping, event persistence, Python SDK, entity extraction (rule-based + LLM fallback), full-text/lexical search, async support, basic supersedence

**Pitfalls to avoid:** Kuzu abstraction gap, LLM-on-write corruption, embedding model lock-in, DuckDB write contention, determinism design gaps

**Research flag:** NEEDS DEEPER RESEARCH — extraction pipeline design (rule-based NER vs. LLM tradeoffs, extraction result caching as derived events, confidence scoring) is complex and has major downstream consequences. The DuckDB write queue pattern under FastAPI also warrants a targeted spike.

---

### Phase 2: Intelligence and Self-Organization

**Rationale:** The organizer requires both a mature graph store (with lifecycle state in graph nodes) and a working hybrid retrieval pipeline (for deduplication by embedding similarity). Supersedence logic requires the typed graph schema from Phase 1 — the data model must exist before the logic. Explainable retrieval traces and context packing controls are natural extensions of the Phase 1 retrieval pipeline. The MCP server and portable artifact export wrap existing functionality with low new complexity.

**Delivers:**
- Full scheduled organizer: salience recalculation (graph centrality + frequency + recency), promotion/demotion (Tentative → Stable), supersedence contradiction handling, hierarchical summarization (daily → weekly → monthly), entity deduplication/alias resolution, policy-based archival
- Configurable and versioned retrieval re-ranking weights (pulled from config, not hardcoded)
- Explainable retrieval traces (score components per result in retrieval response)
- Context packing with explicit token budget (measured with actual tokenizer, not estimated)
- MCP server wrapping HTTP API as MCP tools
- Portable artifact export/import (`memory_pack/` with complete manifest.json recording extraction logic version + scoring weight version + embedding model version)
- APScheduler integration with AsyncIOScheduler; mutual exclusion on organizer jobs

**Features from FEATURES.md:** Scheduled self-organizing memory (full organizer), explainable retrieval traces, context packing/token budget, MCP server, portable artifact format, deduplication/alias resolution, policy-based archival

**Pitfalls to avoid:** Supersedence logic silently dropping valid information (use typed assertion scoping, preserve full temporal chains), inline organizer work during requests (APScheduler runs out-of-band), naive salience recalculation (incremental updates only), context token overflow (hard budget with actual tokenizer)

**Research flag:** NEEDS DEEPER RESEARCH — the supersedence contradiction-detection logic (when to auto-supersede vs. flag for resolution vs. keep both assertions active) requires careful design. Temporal knowledge graph research (Zep/Graphiti paper) and the A-MEM agentic memory paper have relevant prior art. LLM summarization cost optimization (delta-based incremental summarization) also warrants targeted research.

---

### Phase 3: Hardening, Trust, and Ecosystem

**Rationale:** Encryption, evaluation, deterministic rebuild validation, and framework integrations all require the core system to be stable and feature-complete. You cannot encrypt an artifact format that is still changing. You cannot evaluate recall accuracy before the full retrieval pipeline exists. Deterministic rebuild can be validated only once all transformation logic is versioned and all external API results are cached as derived events.

**Delivers:**
- Encryption at rest (DuckDB AES-GCM-256 + field-level Fernet for sensitive fields + encrypted portable artifact; all components encrypted, not just the event store)
- CLI tooling (`prme inspect`, `prme query`, `prme export`, `prme rebuild`)
- Deterministic rebuild validation (CI test: replay event log → compare to live state; bit-identical or same top-k result definition specified)
- Evaluation harness (recall accuracy, supersedence correctness, context compaction over time, determinism tests)
- LangChain/LlamaIndex integration packages
- Additional embedding/LLM providers (Anthropic, Gemini, Voyage, Cohere)

**Features from FEATURES.md:** Encryption at rest, CLI tooling, evaluation harness, deterministic rebuild validation, framework integrations, additional providers

**Pitfalls to avoid:** Encryption gaps in artifact (all components must be encrypted, not just DuckDB file), key management mistakes (Argon2id KDF, key escrow documentation)

**Research flag:** STANDARD PATTERNS — encryption (DuckDB native AES-GCM-256 + cryptography.fernet), CLI (typer), and framework integrations (LangChain/LlamaIndex) are well-documented. The evaluation harness design has no established standard but the metrics are well-defined in the spec. Skip `/gsd:research-phase` for Phase 3.

---

### Phase Ordering Rationale

- **Hard dependency chain:** Event Store → Storage Backends → Pipelines → Organizer → Hardening. This order cannot be changed — each layer depends on the previous existing.
- **GraphStore abstraction is cross-cutting and must be Phase 1 day one** — given Kuzu's archived status, delaying the abstraction layer is the highest-cost technical debt available. Recovery from a skipped abstraction = full rewrite of all graph code.
- **Extraction before Retrieval** — the graph cannot be queried meaningfully until entities are extracted into it. Phase 1 must ship basic extraction (even rule-based only) before the full hybrid retrieval pipeline is valuable.
- **Organizer after Retrieval** — the organizer uses the retrieval pipeline for deduplication (embedding similarity checks) and depends on graph data that extraction must have produced.
- **Portable artifact format in Phase 2, not Phase 3** — the manifest must record versions of all transformation logic, scoring weights, and embedding models. This must be in place before encryption wraps it in Phase 3. Delaying the format design until Phase 3 would require retrofitting encryption onto an unstable format.

### Research Flags

**Needs `/gsd:research-phase` during planning:**
- **Phase 1 — Entity Extraction Pipeline:** Rule-based NER selection (spacy? flair? transformers NER?), LLM extraction prompting strategy, derived event schema for cached extraction results, confidence scoring formula. High stakes: errors here compound into corrupted graph.
- **Phase 1 — DuckDB Write Queue Pattern:** Async write queue design under FastAPI (asyncio.Queue? dedicated writer task? connection pool for reads?). Load testing strategy for 10+ concurrent writers.
- **Phase 2 — Supersedence Contradiction Detection:** When to auto-supersede vs. flag vs. retain both; typed assertion scoping rules; temporal query correctness verification.
- **Phase 2 — LLM Summarization Cost Optimization:** Delta-based incremental summarization to avoid re-summarizing unchanged content on each organizer cycle.

**Standard patterns (skip research):**
- **Phase 1 — HTTP API (FastAPI):** Well-documented patterns; FastAPI best practices are established.
- **Phase 1 — Storage Backend Interfaces:** Standard protocol/ABC pattern in Python; straightforward implementation.
- **Phase 3 — Encryption:** DuckDB native AES-GCM-256 + cryptography.fernet are documented; Argon2id KDF is standard.
- **Phase 3 — CLI:** typer is well-documented; commands mirror existing core functions.
- **Phase 3 — Framework Integrations:** LangChain and LlamaIndex have documented custom retriever patterns.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All libraries verified on PyPI with exact versions. Kuzu abandonment is confirmed from multiple sources (The Register, MacRumors, GitHub). USearch, tantivy-py, FastEmbed, DuckDB all actively maintained as of Feb 2026. |
| Features | MEDIUM | Based on competitor analysis from official docs and peer-reviewed papers. Some competitor internal implementation details inferred. Feature prioritization reflects research-backed judgment, not validated with PRME users. |
| Architecture | MEDIUM-HIGH | Event sourcing and CQRS patterns are well-established. Hybrid retrieval 26% improvement claim comes from Mem0's own research paper — treat as directionally correct, not gospel. APScheduler integration patterns for FastAPI are established. |
| Pitfalls | HIGH | Each pitfall corroborated by multiple authoritative sources. DuckDB concurrency model confirmed from official docs. Kuzu abandonment confirmed. LLM extraction hallucination rate is industry-accepted. Embedding model incompatibility is mathematically certain. |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **RyuGraph/Bighorn fork viability:** Research identified two Kuzu forks but their long-term viability is unproven. Monitor during Phase 1. If neither fork ships stable Python wheels with Kuzu feature parity by the time Phase 2 begins, evaluate DuckPGQ as the migration target.

- **DuckPGQ production readiness timeline:** DuckPGQ is architecturally ideal (eliminates a dependency entirely) but labeled experimental. Watch DuckDB release notes. If DuckPGQ stabilizes during Phase 1-2, consider migrating before Phase 3 rather than after.

- **Extraction pipeline NER library choice:** The research recommends rule-based extraction with LLM fallback but does not validate specific NER libraries for conversational text. Phase 1 planning needs a targeted spike comparing spacy vs. flair vs. gliner for entity extraction from conversational data.

- **Embedding model selection for local-first:** FastEmbed supports multiple ONNX models. The right default model (quality vs. size vs. latency tradeoffs) for conversational memory retrieval was not benchmarked. Phase 1 should evaluate `BAAI/bge-small-en-v1.5` (384 dims, fast) vs. `BAAI/bge-large-en-v1.5` (1024 dims, higher quality) on representative data.

- **Salience formula validation:** The organizer's salience score (graph centrality + frequency + recency + user pins + task linkage) is architecturally described but the formula weights and their interaction are not validated. This is a Phase 2 calibration problem, not a blocking gap.

---

## Sources

### Primary (HIGH confidence)
- [DuckDB PyPI + docs](https://pypi.org/project/duckdb/) — version 1.4.4, LTS status, VSS/FTS extensions, concurrency model, encryption
- [Kuzu GitHub (archived)](https://github.com/kuzudb/kuzu) — archived status, final version 0.11.3, MIT license
- [USearch GitHub](https://github.com/unum-cloud/USearch) — features, benchmarks, version 2.23.0
- [tantivy-py GitHub](https://github.com/quickwit-oss/tantivy-py) — version 0.25.1, maintenance status Dec 2025
- [FastAPI PyPI](https://pypi.org/project/fastapi/) — version 0.129.0
- [FastEmbed GitHub](https://github.com/qdrant/fastembed) — ONNX backend, version 0.7.4
- [Pydantic PyPI](https://pypi.org/project/pydantic/) — version 2.12.5
- [Mem0 research paper (arXiv 2504.19413)](https://arxiv.org/abs/2504.19413) — hybrid retrieval 26% improvement, competitor architecture
- [Zep temporal knowledge graph paper (arXiv 2501.13956)](https://arxiv.org/abs/2501.13956) — temporal graph patterns, token cost data
- [Letta/MemGPT docs](https://docs.letta.com/concepts/memgpt/) — tiered memory management
- [Model Context Protocol spec](https://modelcontextprotocol.io/specification/2025-11-25) — MCP integration standard
- [Event Sourcing Pattern (Fowler)](https://martinfowler.com/eaaDev/EventSourcing.html) — canonical architecture reference
- [DuckDB Encryption docs (Nov 2025)](https://duckdb.org/2025/11/19/encryption-in-duckdb) — AES-GCM-256 support

### Secondary (MEDIUM confidence)
- [The Register: KuzuDB abandoned (Oct 2025)](https://www.theregister.com/2025/10/14/kuzudb_abandoned/) — acquisition context
- [MacRumors: Apple acquires Kuzu (Feb 2026)](https://www.macrumors.com/2026/02/11/apple-acquires-new-database-app/) — acquisition confirmation
- [RyuGraph GitHub](https://github.com/predictable-labs/ryugraph) — fork v25.9.2, active development
- [DuckPGQ docs](https://duckpgq.org/) — SQL/PGQ graph capabilities
- [Mem0 docs](https://docs.mem0.ai/) + [Cognee GitHub](https://github.com/topoteretes/cognee) — competitor feature analysis
- [LiteLLM PyPI](https://pypi.org/project/litellm/) — provider support, version 1.81.13
- [Serokell: Design Patterns for LLM Long-Term Memory](https://serokell.io/blog/design-patterns-for-long-term-memory-in-llm-powered-architectures) — organizer patterns
- [Neo4j Advanced RAG techniques](https://neo4j.com/blog/genai/advanced-rag-techniques/) — graph + vector hybrid validation
- [FastAPI Best Practices](https://github.com/zhanymkanov/fastapi-best-practices) — service layer patterns
- [APScheduler docs](https://apscheduler.readthedocs.io/) — background scheduler integration

### Tertiary (LOW confidence, needs validation)
- [Tantivy benchmarks (johal.in)](https://johal.in/tantivy-lucene-rust-python-ffi-for-high-performance-full-text-search/) — performance claims (single blog source)
- [Embedding model migration cost (Medium)](https://medium.com/data-science-collective/different-embedding-models-different-spaces-the-hidden-cost-of-model-upgrades-899db24ad233) — directionally correct but single source
- [Context Rot research (Chroma)](https://research.trychroma.com/context-rot) — token budget importance, needs independent validation

---
*Research completed: 2026-02-19*
*Ready for roadmap: yes*
