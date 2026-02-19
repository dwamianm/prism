# Stack Research

**Domain:** Local-first embeddable LLM memory engine (Python)
**Researched:** 2026-02-19
**Confidence:** MEDIUM-HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| DuckDB | 1.4.4 | Append-only event store, analytical queries | Embedded, zero-config, ACID, columnar storage, excellent Python API, single-file database. The standard for embedded analytical workloads in Python. Ships with FTS and VSS extensions. | HIGH |
| Kuzu | 0.11.3 (archived) | Property graph store (Cypher queries) | Best-in-class embedded graph DB with Cypher, columnar storage, vector + FTS built-in. **Critical caveat:** Acquired by Apple Oct 2025, repo archived, no future releases. Use pinned version; plan migration path to fork or DuckPGQ. See "Graph Layer Strategy" below. | MEDIUM |
| USearch | 2.23.0 | HNSW vector index (standalone) | 10x faster HNSW than FAISS, single-header C++ with Python bindings, supports f16/i8 quantization, disk-viewable indexes, user-defined metrics, Apache-2.0 license, actively maintained (Jan 2026). Already powers DuckDB's VSS extension internally. | HIGH |
| tantivy-py | 0.25.1 | Full-text search (BM25) | Rust-based Lucene-alternative via PyO3 bindings. 30x faster than Whoosh, 4x faster than Lucene Java. Production-grade, actively maintained (Dec 2025). Supports custom tokenizers, stemming, faceted search. | HIGH |
| FastAPI | 0.129.0 | HTTP API framework | De facto standard for Python async APIs. Massive ecosystem, OpenAPI auto-generation, Pydantic-native validation, async-first. Used by OpenAI, Anthropic in production. Actively maintained (Feb 2026). | HIGH |
| Pydantic | 2.12.5 | Data validation, serialization, settings | Industry standard for Python data modeling. v2 is Rust-backed (pydantic-core), 5-50x faster than v1. Native FastAPI integration. Python 3.14 support. | HIGH |
| FastEmbed | 0.7.4 | Local embedding inference (default provider) | ONNX Runtime backend -- no PyTorch dependency. Lightweight, fast, quantized models out of the box. Ideal for local-first, serverless-compatible embedding. | HIGH |
| LiteLLM | 1.81.13 | Pluggable embedding provider abstraction | Unified API for 100+ providers (OpenAI, Cohere, Bedrock, local). Drop-in switching between providers. Handles auth, retries, cost tracking. Use as the provider router layer. | MEDIUM |
| cryptography | 46.0.5 | Encryption at rest | The standard Python cryptography library. Fernet for symmetric encryption, supports AES-GCM. OpenSSL 3.5.5 backend. Well-audited. | HIGH |

### Graph Layer Strategy

**The Kuzu situation requires a deliberate strategy:**

Kuzu 0.11.3 (the final release) is the best embedded graph database ever built for Python -- Cypher support, columnar storage, vector indices, FTS, ACID transactions, all in a single embeddable library. However, it was acquired by Apple in October 2025 and the repository was archived. No security patches or bug fixes will be released.

**Recommended approach:**

1. **Build on Kuzu 0.11.3** -- it works, it is MIT-licensed, the wheels are on PyPI, and the feature set matches PRME's needs perfectly. Pin the version.

2. **Abstract the graph layer behind an interface** -- all graph operations go through a `GraphStore` protocol/ABC so the implementation can be swapped.

3. **Monitor the forks:**
   - **RyuGraph** (by Predictable Labs, ex-Dgraph CEO) -- most promising fork, published releases (v25.9.2), actively developed. Watch for PyPI package.
   - **Bighorn** (by Kineviz) -- maintained fork, integrated into their GraphXR product. Less Python-focused.
   - **DuckPGQ** -- DuckDB community extension adding SQL/PGQ graph queries. Research project from CWI. Could eliminate the need for a separate graph DB entirely by running graph queries on DuckDB tables. Still experimental but architecturally elegant for PRME since the event store is already in DuckDB.

4. **Evaluate migration triggers:** If Kuzu bugs surface without patches, migrate to RyuGraph (most compatible) or DuckPGQ (eliminates a dependency).

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| uvicorn | 0.41.0 | ASGI server for FastAPI | Always -- the standard ASGI server. Use `uvicorn[standard]` for uvloop + httptools. |
| pydantic-settings | latest | Configuration management | Always -- env vars, .env files, typed settings classes. |
| sentence-transformers | 5.2.3 | Full embedding model support (heavyweight) | When users need custom fine-tuned models, cross-encoders for reranking, or models not in FastEmbed's catalog. Requires PyTorch. |
| httpx | latest | Async HTTP client | For calling external embedding APIs (OpenAI, Cohere, etc.) when not using LiteLLM. |
| msgspec | 0.20.0 | High-perf serialization (optional) | If internal serialization becomes a bottleneck. 5-60x faster than dataclasses. Compatible with Litestar if you migrate from FastAPI later. |
| structlog | latest | Structured logging | Always -- structured JSON logging for debugging memory operations. |
| click / typer | latest | CLI framework | For the CLI interface. Typer for type-annotated CLI; Click for lower-level control. |

### Development Tools

| Tool | Version | Purpose | Notes |
|------|---------|---------|-------|
| uv | 0.10.4 | Package/project manager | Replaces pip, pip-tools, virtualenv, poetry. 10-100x faster. Rust-based. Manages Python versions too. Use `uv init`, `uv add`, `uv run`. |
| ruff | 0.15.1 | Linter + formatter | Replaces black, isort, flake8, pylint. Single tool, Rust-based, near-instant. Configure in `pyproject.toml`. |
| pytest | 9.0.2 | Testing framework | The standard. Use with `pytest-asyncio` (1.3.0) for async test support. |
| pytest-asyncio | 1.3.0 | Async test support | Required for testing FastAPI endpoints and async graph/vector operations. |
| mypy / pyright | latest | Type checking | Pyright preferred for speed (Rust-based via Pylance). mypy for broader ecosystem compatibility. |
| pre-commit | latest | Git hooks | Run ruff, mypy checks before commits. |

## Installation

```bash
# Project setup with uv
uv init prme
cd prme

# Core dependencies
uv add duckdb==1.4.4
uv add kuzu==0.11.3
uv add usearch==2.23.0
uv add tantivy==0.25.1
uv add fastapi==0.129.0
uv add "uvicorn[standard]==0.41.0"
uv add pydantic==2.12.5
uv add pydantic-settings

# Embedding providers
uv add fastembed==0.7.4
uv add litellm  # Optional: for multi-provider routing

# Encryption
uv add cryptography==46.0.5

# Supporting
uv add structlog
uv add httpx
uv add typer

# Dev dependencies
uv add --dev ruff==0.15.1
uv add --dev pytest==9.0.2
uv add --dev pytest-asyncio==1.3.0
uv add --dev mypy
uv add --dev pre-commit
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| DuckDB (event store) | SQLite | If you need the absolute simplest setup and don't need analytical query performance. DuckDB is better for append-heavy workloads and columnar scans over event logs. |
| Kuzu (graph) | DuckPGQ | If you want zero additional dependencies and can accept SQL/PGQ syntax instead of Cypher. DuckPGQ runs graph queries directly on DuckDB tables. Still experimental. |
| Kuzu (graph) | NetworkX | Only for prototyping. NetworkX is in-memory only, no persistence, no Cypher, poor performance on large graphs. |
| Kuzu (graph) | FalkorDB Lite | If you need an actively maintained embedded graph DB with Cypher. Runs as a subprocess (not truly embedded like Kuzu). Redis-protocol based. |
| USearch (vectors) | hnswlib | If USearch has compatibility issues. hnswlib 0.8.0 is stable but last updated Dec 2023 -- essentially abandoned. USearch is its spiritual successor with active development. |
| USearch (vectors) | DuckDB VSS | If you want to avoid a separate vector index entirely. DuckDB VSS uses USearch internally but is experimental, RAM-only, and limited to float32. Good for small-scale prototyping. |
| USearch (vectors) | FAISS | If you need GPU-accelerated search or IVF indexes. Overkill for local-first embedded use. Heavy dependency (requires faiss-cpu or faiss-gpu). |
| tantivy-py (FTS) | DuckDB FTS | If queries are simple keyword searches and you want zero additional dependencies. DuckDB FTS uses BM25 but indexes don't auto-update on insert -- you must rebuild manually. |
| tantivy-py (FTS) | Whoosh | Never. Whoosh is pure-Python, 30x slower, last updated 2015 (original) or via unmaintained fork. |
| FastAPI | Litestar 2.20.0 | If you need msgspec performance (12x faster than Pydantic v2 for serialization), standalone route decorators (no circular import issues), or built-in dependency injection. Smaller ecosystem but better architecture for complex projects. Consider if FastAPI's tutorial-oriented docs become a friction point. |
| FastEmbed | sentence-transformers | If you need PyTorch-based models, cross-encoders for reranking, or models not available as ONNX. Heavier dependency (requires PyTorch). |
| LiteLLM | Direct API calls | If you only use one embedding provider and want fewer dependencies. LiteLLM adds ~50MB+ to install size. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Whoosh | Abandoned (2015), pure-Python, 30x slower than tantivy | tantivy-py |
| hnswlib | Last release Dec 2023, no Python 3.13 wheels, effectively unmaintained | USearch |
| ChromaDB / Weaviate / Pinecone | Server-based vector DBs. PRME is embeddable -- no external server dependencies | USearch + DuckDB for storage |
| SQLAlchemy | ORM overhead unnecessary. DuckDB Python API is direct and fast. Graph queries use Cypher. | Direct DuckDB Python API |
| LangChain | Framework lock-in, heavy abstraction layer, unstable API surface. PRME should own its retrieval pipeline. | Direct integration with embedding providers |
| Poetry | Slower dependency resolution, less feature-complete than uv in 2026 | uv |
| black + isort + flake8 | Three tools replaced by one (ruff) with better performance | ruff |
| Neo4j | Server-based, requires JVM, not embeddable. | Kuzu (embedded) |
| Flask | Synchronous, no built-in validation, no OpenAPI generation | FastAPI |

## Stack Patterns by Variant

**If embedding latency is critical (sub-10ms):**
- Use FastEmbed with a small model (e.g., `BAAI/bge-small-en-v1.5`, 384 dims)
- Pre-compute embeddings, store in DuckDB ARRAY columns
- USearch index for retrieval, not real-time embedding

**If graph queries dominate the workload:**
- Invest in the GraphStore abstraction early
- Consider DuckPGQ to consolidate on a single storage engine
- Use Kuzu for now but design for swappability

**If the HTTP API is optional (library-only mode):**
- FastAPI can be an optional dependency
- Core engine should work without it
- Use a clean separation: `prme.core` (library) vs `prme.api` (HTTP layer)

**If encryption at rest is required:**
- Use `cryptography.fernet` for field-level encryption of sensitive memory content
- DuckDB supports encryption via the `PRAGMA` interface for database-level encryption
- Layer both: DB-level encryption for defense in depth, field-level for sensitive fields

**If running on resource-constrained devices (Raspberry Pi, edge):**
- FastEmbed with ONNX (no PyTorch)
- USearch with i8 quantization
- DuckDB with memory limits configured
- Skip LiteLLM, use direct httpx calls to reduce memory footprint

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| DuckDB 1.4.4 | Python 3.9-3.14 | LTS release line. VSS and FTS extensions auto-load. |
| Kuzu 0.11.3 | Python 3.7-3.14 | Archived. Pin this version exactly. Test on your target Python version before committing. |
| USearch 2.23.0 | Python 3.9+ | Actively maintained. Check releases for breaking changes in 2.x line. |
| tantivy-py 0.25.1 | Python 3.9-3.14 | Tracks tantivy Rust crate. Minor version bumps may change index format. |
| FastAPI 0.129.0 | Pydantic 2.x | FastAPI dropped Pydantic v1 support. Ensure all models use v2 syntax. |
| FastEmbed 0.7.4 | Python 3.9+ | Uses ONNX Runtime. May conflict with PyTorch if both installed -- test carefully. |
| sentence-transformers 5.2.3 | Python 3.10+ | Note: requires Python 3.10 minimum (higher than other deps). Only install if needed. |
| ruff 0.15.1 | Python 3.9+ | Dev dependency only. No runtime compatibility concerns. |

## Architectural Decision: DuckDB as Universal Storage Layer

A key insight from this research: DuckDB could potentially serve as the **single storage engine** for PRME, handling events, vectors (via VSS), full-text (via FTS), and even graph queries (via DuckPGQ). This "DuckDB-centric" architecture has significant appeal for a local-first embeddable system:

**Pros:**
- Single file database, single dependency
- ACID transactions across all data types
- No impedance mismatch between stores
- Simpler deployment and backup

**Cons (why we don't recommend it today):**
- VSS extension is experimental, RAM-only, float32-only
- FTS indexes don't auto-update on insert
- DuckPGQ is a research project, not production-ready
- Losing Kuzu's Cypher expressiveness for graph queries

**Recommendation:** Use the multi-engine approach (DuckDB + Kuzu + USearch + tantivy) for now, but design abstractions that would allow consolidating onto DuckDB-only in the future as its extensions mature. This is a realistic 12-18 month horizon.

## Sources

- [DuckDB PyPI](https://pypi.org/project/duckdb/) -- version 1.4.4 verified (HIGH)
- [DuckDB 1.4.3 LTS announcement](https://duckdb.org/2025/12/09/announcing-duckdb-143) -- LTS status confirmed (HIGH)
- [DuckDB VSS extension docs](https://duckdb.org/docs/stable/core_extensions/vss) -- capabilities and limitations (HIGH)
- [DuckDB FTS extension docs](https://duckdb.org/docs/stable/core_extensions/full_text_search) -- capabilities and limitations (HIGH)
- [DuckPGQ documentation](https://duckpgq.org/) -- SQL/PGQ capabilities (MEDIUM)
- [DuckDB graph queries blog](https://duckdb.org/2025/10/22/duckdb-graph-queries-duckpgq) -- practical usage patterns (MEDIUM)
- [Kuzu PyPI](https://pypi.org/project/kuzu/) -- version 0.11.3 archived (HIGH)
- [Kuzu GitHub](https://github.com/kuzudb/kuzu) -- archived status confirmed (HIGH)
- [KuzuDB abandoned - The Register](https://www.theregister.com/2025/10/14/kuzudb_abandoned/) -- Apple acquisition context (MEDIUM)
- [Apple acquires Kuzu - MacRumors](https://www.macrumors.com/2026/02/11/apple-acquires-new-database-app/) -- acquisition confirmed (MEDIUM)
- [RyuGraph GitHub](https://github.com/predictable-labs/ryugraph) -- fork status, v25.9.2 (MEDIUM)
- [Bighorn GitHub](https://github.com/Kineviz/bighorn) -- fork status (MEDIUM)
- [USearch GitHub](https://github.com/unum-cloud/USearch) -- features and benchmarks (HIGH)
- [USearch PyPI](https://pypi.org/project/usearch/) -- version 2.23.0 verified (HIGH)
- [tantivy-py PyPI](https://pypi.org/project/tantivy/) -- version 0.25.1 verified (HIGH)
- [tantivy-py GitHub](https://github.com/quickwit-oss/tantivy-py) -- Python bindings (HIGH)
- [Tantivy benchmarks](https://johal.in/tantivy-lucene-rust-python-ffi-for-high-performance-full-text-search/) -- performance claims (LOW -- single source blog)
- [FastAPI PyPI](https://pypi.org/project/fastapi/) -- version 0.129.0 verified (HIGH)
- [Litestar PyPI](https://pypi.org/project/litestar/) -- version 2.20.0 verified (HIGH)
- [Litestar vs FastAPI comparison](https://betterstack.com/community/guides/scaling-python/litestar-vs-fastapi/) -- framework comparison (MEDIUM)
- [FastEmbed GitHub](https://github.com/qdrant/fastembed) -- capabilities, ONNX backend (HIGH)
- [FastEmbed PyPI](https://pypi.org/project/fastembed/) -- version 0.7.4 verified (HIGH)
- [sentence-transformers PyPI](https://pypi.org/project/sentence-transformers/) -- version 5.2.3 verified (HIGH)
- [LiteLLM PyPI](https://pypi.org/project/litellm/) -- version 1.81.13 verified (HIGH)
- [LiteLLM embedding docs](https://docs.litellm.ai/docs/embedding/supported_embedding) -- provider support (MEDIUM)
- [Pydantic PyPI](https://pypi.org/project/pydantic/) -- version 2.12.5 verified (HIGH)
- [msgspec PyPI](https://pypi.org/project/msgspec/) -- version 0.20.0 verified (HIGH)
- [cryptography PyPI](https://pypi.org/project/cryptography/) -- version 46.0.5 verified (HIGH)
- [uv GitHub](https://github.com/astral-sh/uv) -- version 0.10.4, features (HIGH)
- [ruff PyPI](https://pypi.org/project/ruff/) -- version 0.15.1 verified (HIGH)
- [pytest PyPI](https://pypi.org/project/pytest/) -- version 9.0.2 verified (HIGH)
- [Modern Python project setup 2025](https://albertsikkema.com/python/development/best-practices/2025/10/31/modern-python-project-setup.html) -- tooling best practices (MEDIUM)

---
*Stack research for: PRME (Portable Relational Memory Engine)*
*Researched: 2026-02-19*
