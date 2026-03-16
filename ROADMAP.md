# PRME Roadmap

This document outlines the development direction for PRME. Phases are roughly ordered by priority but may shift based on community feedback.

Status legend: **Done** | **In Progress** | Planned

---

## v0.1 — v0.4 (Done)

The foundation: storage, ingestion, retrieval, and epistemic state management.

- **Storage layer** — append-only event store (DuckDB), graph model, vector index (usearch HNSW), lexical search (Tantivy), optional PostgreSQL backend
- **Ingestion pipeline** — LLM-powered entity/fact/relationship extraction (OpenAI, Anthropic, Ollama), dual-stream ingestion with sub-50ms fast path
- **Hybrid retrieval** — 6-signal scoring (semantic, lexical, graph, recency, salience, confidence), supersedence-aware filtering, query reformulation, temporal context formatting
- **Epistemic state model** — lifecycle states (tentative -> stable -> superseded -> archived), confidence tracking, contradiction detection, oscillation dampening
- **Self-organizing memory** — 11 organizer jobs (promote, decay, archive, deduplicate, alias resolve, summarize, consolidate, snapshot generation, etc.)
- **CLI** — `prme` command for memory inspection, search, export
- **HTTP API** — FastAPI REST API for all memory operations
- **Encryption at rest** — Fernet (AES-128-CBC + HMAC) with PBKDF2 key derivation
- **Benchmark suite** — LongMemEval (92.5%), LoCoMo (79.0%), custom epistemic evaluation
- **Testing** — 944 tests, 19 simulation scenarios, stress tests, CI matrix (Python 3.11-3.13)

---

## v0.5 — Integrations and Developer Experience

Making PRME easy to adopt in existing agent frameworks.

- [ ] **MCP server** — Model Context Protocol server so any MCP-compatible client (Claude, Cursor, etc.) can use PRME as a memory backend out of the box
- [ ] **Python SDK refinements** — simplified `MemoryClient` wrapper, connection pooling, sync API for non-async codebases
- [ ] **Framework adapters** — first-party integrations for LangChain, LlamaIndex, CrewAI, and AutoGen
- [ ] **Plugin architecture** — pluggable storage backends, custom node types, user-defined organizer jobs
- [ ] **Improved onboarding** — interactive tutorial, `prme init` scaffolding command, better error messages

---

## v0.6 — Retrieval Intelligence

Pushing retrieval accuracy further with smarter strategies.

- [ ] **Pre-aggregation during ingestion** — compute entity counts and aggregate facts at store time for instant answers to "how many times did X..." queries
- [ ] **Chunk-level retrieval** — index conversation segments as retrievable units for better single-hop recall on long conversations
- [ ] **Neural reranking** — optional cross-encoder reranking stage after candidate generation for higher precision
- [ ] **Adaptive retrieval profiles** — learn per-user scoring weights from feedback signals over time
- [ ] **Timeline query detection** — automatically detect "how has X changed" queries and return chronological fact evolution

---

## v0.7 — Multi-Agent and Collaboration

Memory that works across agents, teams, and time.

- [ ] **Multi-agent memory semantics** — scoped memory sharing between agents with configurable visibility (private, shared, broadcast)
- [ ] **Memory federation** — sync memory packs between instances with conflict resolution (CRDT-based merge)
- [ ] **Access control** — role-based permissions on memory scopes, audit logging
- [ ] **Collaborative memory** — agents can annotate, reinforce, or challenge each other's memories with attribution tracking

---

## v0.8 — Production Hardening

Getting PRME ready for production deployments at scale.

- [ ] **Memory branching** — create isolated memory branches for simulation, A/B testing, and rollback
- [ ] **Streaming ingestion** — Kafka/Redis Streams consumer for high-throughput ingestion pipelines
- [ ] **Observability** — OpenTelemetry traces for retrieval pipeline, Prometheus metrics, structured audit log
- [ ] **Performance benchmarks** — published latency/throughput numbers at 10K, 100K, 1M memory nodes
- [ ] **Migration tooling** — import from existing memory systems (Mem0, Zep, ChromaDB) with validation

---

## v1.0 — Stable Release

Production-ready with API stability guarantees.

- [ ] **API stability commitment** — semver guarantees, deprecation policy, migration guides
- [ ] **RFC graduation** — promote core RFCs from Draft to Stable status
- [ ] **Security audit** — third-party review of encryption, access control, and data handling
- [ ] **Comprehensive documentation** — API reference, architecture guide, deployment cookbook
- [ ] **PyPI stable release** — published package with long-term support commitment

---

## Future Exploration

Ideas under consideration for post-1.0 development.

- **Hosted PRME** — managed memory service with team features, usage analytics, and SLA
- **Intent and goal memory** — track user goals and agent objectives as first-class memory objects
- **Predictive memory** — anticipate what context an agent will need before it asks
- **Memory compression** — progressive summarization that preserves retrieval quality while reducing storage
- **Visual memory explorer** — web UI for browsing, searching, and debugging memory graphs
- **Voice/multimodal memory** — store and retrieve memories from audio, images, and structured data

---

## How to Influence the Roadmap

We prioritize based on real-world usage and community input:

- **Feature requests** — [open an issue](https://github.com/dwamianm/prism/issues) with the `enhancement` label
- **Bug reports** — [open an issue](https://github.com/dwamianm/prism/issues) with the `bug` label
- **Discussions** — share your use case in [GitHub Discussions](https://github.com/dwamianm/prism/discussions)
- **Contributions** — see [CONTRIBUTING.md](CONTRIBUTING.md) for how to get involved

Priorities can shift. If something you need isn't listed, tell us — the best roadmaps are shaped by the people using the software.
