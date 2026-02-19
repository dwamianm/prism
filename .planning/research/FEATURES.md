# Feature Research

**Domain:** LLM Memory Engines (embeddable long-term memory for chatbots/agents)
**Researched:** 2026-02-19
**Confidence:** MEDIUM — based on multiple competitor products, documentation, papers, and benchmarks; some internal implementation details of competitors are inferred

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete or unusable for the target audience (developers building chatbots/agents).

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Memory CRUD API** (add/search/update/delete) | Every competitor (Mem0, Zep, LangMem, Letta) exposes this. Developers expect basic memory operations via HTTP or SDK. | LOW | Mem0 uses `add/search/update/delete/get_all`. PRME spec covers this via HTTP API. Non-negotiable. |
| **Semantic vector search** | Vector similarity is the baseline retrieval method. All competitors offer it. Developers expect to query by meaning, not just keywords. | MEDIUM | HNSW is the standard. Mem0 supports 18+ vector backends. PRME spec uses HNSW — sufficient for v1. |
| **Pluggable embedding providers** | Developers refuse vendor lock-in. Mem0, Zep, Letta, and Cognee all support OpenAI, local models (Ollama/sentence-transformers), and others. | MEDIUM | PRME spec already requires this. Support OpenAI + one local provider (sentence-transformers) at minimum for v1. |
| **User/session scoping** | Multi-user memory is expected in any production system. Mem0 scopes to user_id/session_id/agent_id. Zep uses session-based management. | LOW | PRME spec has `scope: personal/project/org`. Must be queryable per-scope. |
| **Conversation history persistence** | Letta's recall memory and Zep's episodic nodes store full chat history. Developers expect raw conversation logs to be searchable. | LOW | PRME's append-only event store covers this by design. Table stakes because it's the foundation. |
| **Python SDK** | Primary audience is Python developers building agents. Mem0, Zep, LangMem, Cognee, Letta — all have Python SDKs. | LOW | PRME spec has HTTP API first, Python wrapper second. Both needed for v1. |
| **Pluggable LLM providers** (for extraction/summarization) | Memory systems use LLMs for entity extraction, summarization, and conflict resolution. Mem0 supports 17+ providers. Developers expect choice. | MEDIUM | PRME needs extraction pipeline. Support OpenAI + Anthropic + one local option minimum. |
| **Entity extraction from conversations** | Mem0, Zep, and Cognee all auto-extract entities. Without this, developers must manually tag everything — unacceptable DX. | HIGH | LLM-powered extraction with rule-based fallbacks (per PROJECT.md). This is the most complex table-stakes feature. |
| **Full-text / lexical search** | Hybrid retrieval (vector + lexical) is now standard. Zep uses BM25, Mem0 offers keyword search, Graphiti combines semantic + keyword + graph. | MEDIUM | PRME spec includes FTS5/Tantivy. Needed for precise term matching that vector search misses. |
| **Framework integration** (at least one) | Mem0 integrates with LangChain, LlamaIndex, CrewAI, AutoGen. Zep with LangChain, LlamaIndex. Developers expect drop-in usage. | LOW | Provide at minimum a LangChain integration or MCP server. Framework-agnostic HTTP API covers most cases. |
| **Async support** | Production agents are async. Mem0, Zep, and Letta all support async operations. Python async/await is expected. | LOW | Use `asyncio` throughout. Sync wrappers for convenience. |

### Differentiators (Competitive Advantage)

Features that set PRME apart. Not universally expected, but create clear value versus Mem0/Zep/LangMem.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Append-only event sourcing** | No competitor uses true event sourcing. Mem0 stores derived memories directly. Zep stores episodes but without immutability guarantees. PRME's event log means: full audit trail, deterministic rebuild, no silent data loss. Developers building in regulated domains (health, finance, legal) need this. | MEDIUM | DuckDB append-only log. This is PRME's architectural foundation — not just a feature but a design principle competitors lack. |
| **Graph-based relational model** (beyond entity extraction) | Mem0 has graph memory but it's an add-on to vector+KV. Zep/Graphiti has strong temporal graphs but is cloud-first. PRME puts the graph at the center — entities, facts, decisions, preferences, tasks as typed nodes with typed edges, temporal validity, and provenance. No competitor offers this depth in an embeddable local package. | HIGH | Kuzu as embedded graph DB. The typed node taxonomy (Entity/Fact/Decision/Preference/Task/Summary) is richer than any competitor's schema. |
| **Deterministic rebuild from event log** | No competitor guarantees this. PRME can rebuild all derived structures (graph, vectors, indices) from the event log alone. This enables: portability, verification, disaster recovery, and trust in the memory state. | HIGH | Requires versioned embeddings, versioned extraction logic, and deterministic scoring. Unique in the market. |
| **Supersedence handling** (not just overwrite) | Mem0 updates memories by overwriting. Zep invalidates edges temporally but doesn't maintain full supersedence chains. PRME tracks `supersedes` pointers — you can see why a fact changed, what it replaced, and the evidence trail. Critical for agents that need to explain themselves. | MEDIUM | Depends on graph model. The supersedence chain is the most explainable contradiction-resolution approach in the market. |
| **Scheduled self-organizing memory** (salience, promotion, summarization) | Mem0 has basic memory cleanup. Zep does automatic summarization. Neither offers PRME's full organizer: salience recalculation, promotion/demotion lifecycle (Tentative -> Stable -> Superseded -> Archived), hierarchical summarization (daily -> weekly -> monthly), deduplication, and policy-based archival. This is what makes memory get *better* over time without developer intervention. | HIGH | This is the most complex subsystem. Partially deferrable (Phase 2) but critical to the "self-organizing" promise. |
| **Portable artifact format** | No competitor offers a single copyable memory bundle. Mem0 is tied to its vector DB. Zep is cloud-hosted. Letta stores in its server. PRME's `memory_pack/` directory is copyable, versionable, encryptable, and rebuildable. Developers can version-control memory alongside code. | MEDIUM | `memory_pack/` with manifest.json. Unique in market — enables memory-as-artifact workflows (backup, share, migrate, audit). |
| **Explainable retrieval traces** | Zep provides some scoring visibility. Mem0 is opaque. PRME's re-ranking formula with configurable weights (semantic_similarity, lexical_relevance, graph_proximity, recency_decay, salience, confidence) and provenance references means developers can debug *why* a memory was retrieved. Essential for trust in production. | MEDIUM | Depends on hybrid retrieval pipeline. Log the score components per candidate. |
| **Context packing / token budget management** | Zep mentions token cost savings. Mem0 claims 90% token cost reduction. But neither exposes explicit context packing controls. PRME's memory bundles (entity snapshots, stable facts, recent decisions, active tasks) with measurable token footprint give developers control over context window usage. | MEDIUM | Priority-ordered context construction with token budget. Developers want control, not just magic. |
| **Local-first / fully offline** | Mem0's best features require their cloud platform. Zep is cloud-first (community edition exists but is limited). Letta has a server but depends on external LLM APIs. PRME runs entirely local with DuckDB + Kuzu + HNSW — no network required for core storage and retrieval. Only embedding generation may need an API (or use local models). | LOW | This is architectural, not a separate feature. Huge differentiator for privacy-sensitive use cases. |
| **Encryption at rest** | No competitor prominently offers encryption at rest for the memory store. Enterprise/regulated use cases need this. | MEDIUM | Encrypt the portable artifact. Phase 3 feature per spec but a clear differentiator. |
| **MCP server** | MCP is becoming the standard for connecting LLMs to tools/data. Cognee and Mem0 (OpenMemory) already offer MCP servers. Providing one makes PRME usable from Claude Desktop, Cursor, and other MCP clients without custom integration. | LOW | Wrap HTTP API as MCP tools. Low effort, high reach — this is the 2025-2026 integration standard. |
| **Evaluation harness** | No competitor ships an eval harness. Developers have no way to measure if memory is working. PRME's spec defines: recall accuracy, supersedence correctness, context compaction over time, explainable traces, and deterministic rebuild. Shipping this builds trust. | MEDIUM | Phase 3 per spec. Can start with basic recall accuracy tests. Unique offering. |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems. PRME should deliberately NOT build these.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Cloud-hosted managed service** | Easier onboarding, no infra management. Mem0 and Zep both offer this. | Conflicts with local-first architecture. Splits engineering focus. Cloud services need ops, billing, auth, compliance. A cloud offering before the core engine is solid is premature. | Ship an excellent embeddable engine. Let the community build hosting wrappers. Provide Docker Compose for self-hosted deployment. |
| **Web UI / dashboard** | Visual memory inspection. Letta has ADE (Agent Development Environment). | High development cost, diverges from developer-tool focus. CLI + API is sufficient for v1. Web UIs are maintenance-heavy and become the product rather than the engine. | CLI tooling for inspection (`prme inspect`, `prme query`, `prme stats`). JSON output for piping into other tools. |
| **Multi-model simultaneous embedding** | Index the same content with multiple embedding models for better recall. | Massively increases storage, indexing time, and complexity. Embedding model drift creates consistency issues. No competitor does this well. | Pluggable single-provider with re-indexing capability. Store model version metadata so re-embedding is safe when switching models. |
| **CRDT-based sync / distributed replication** | Multi-device, multi-agent shared memory. | Enormous complexity (conflict resolution, network partitions, ordering). No existing memory system handles this. Premature for v1 — get single-node right first. | Portable artifact format enables manual sync (copy the pack). Design event log for future CRDT extension but don't implement it. |
| **Real-time streaming memory updates** | WebSocket/SSE streams of memory changes. Sounds modern. | Adds connection management complexity. Memory write patterns are bursty (after conversations), not continuous. Polling or webhook callbacks are simpler and sufficient. | HTTP polling endpoint or webhook/callback on memory events. |
| **Built-in RAG over external documents** | Developers want to combine memory with document retrieval. Graphlit does this. | Scope creep. RAG over documents is a solved problem (LangChain, LlamaIndex). PRME's value is memory, not generic retrieval. Combining them makes both worse. | Clear integration points so PRME memory can be combined with external RAG in the retrieval pipeline. Document how to compose PRME with LlamaIndex/LangChain RAG. |
| **Automatic prompt rewriting / procedural memory** | LangMem offers this — automatically updating agent prompts based on learned patterns. | Extremely hard to get right. Bad prompt mutations degrade agent quality. Couples memory engine to prompt engineering. | Expose memory retrieval results; let the developer/framework decide how to construct prompts. PRME is a memory engine, not a prompt optimizer. |
| **Mobile clients / native SDKs** | Broader platform reach. | PRME is an engine, not an end-user product. Server-side Python is the correct scope. Mobile adds platform-specific complexity with no clear user demand. | HTTP API is accessible from any platform. If mobile is needed, developers can call the API. |
| **Multi-tenant SaaS features** (billing, auth, rate limiting) | Enterprise deployment readiness. | These are application-layer concerns, not memory engine concerns. Adding them conflates the engine with the platform. | Document how to deploy PRME behind an API gateway (Kong, Traefik) that handles auth/billing/rate-limiting. Provide namespace isolation via scoping. |

## Feature Dependencies

```
[Event Store (DuckDB)]
    |
    +--requires--> [Entity Extraction Pipeline]
    |                   |
    |                   +--requires--> [Pluggable LLM Providers]
    |                   |
    |                   +--produces--> [Graph Store (Kuzu)]
    |                                      |
    |                                      +--enables--> [Graph Traversal Retrieval]
    |                                      |
    |                                      +--enables--> [Supersedence Handling]
    |                                      |
    |                                      +--enables--> [Explainable Retrieval Traces]
    |
    +--produces--> [Vector Index (HNSW)]
    |                   |
    |                   +--requires--> [Pluggable Embedding Providers]
    |                   |
    |                   +--enables--> [Semantic Vector Search]
    |
    +--produces--> [Lexical Index (FTS)]
    |                   |
    |                   +--enables--> [Full-text Search]
    |
    +--enables--> [Deterministic Rebuild]
    |
    +--enables--> [Portable Artifact Format]

[Semantic Vector Search] + [Full-text Search] + [Graph Traversal]
    |
    +--combined--> [Hybrid Retrieval Pipeline]
                        |
                        +--enables--> [Context Packing / Token Budget]
                        |
                        +--enables--> [Re-ranking with Explainable Scores]

[Graph Store] + [Hybrid Retrieval]
    |
    +--enables--> [Scheduled Organizer]
                        |
                        +--includes--> [Salience Recalculation]
                        +--includes--> [Promotion/Demotion Lifecycle]
                        +--includes--> [Summarization (daily/weekly/monthly)]
                        +--includes--> [Deduplication / Alias Resolution]
                        +--includes--> [Policy-based Archival]

[Portable Artifact Format]
    |
    +--enables--> [Encryption at Rest]

[Hybrid Retrieval Pipeline]
    |
    +--enables--> [Evaluation Harness]

[HTTP API]
    |
    +--wraps--> [Python Library]
    +--wraps--> [MCP Server]
    +--wraps--> [CLI Tooling]
```

### Dependency Notes

- **Entity Extraction requires Pluggable LLM Providers:** Extraction uses LLMs for classification and entity recognition. Must support at least one provider before extraction works.
- **Graph Store requires Entity Extraction:** Graph nodes and edges are produced by processing events through extraction. No extraction = empty graph.
- **Hybrid Retrieval requires all three indices:** Vector, lexical, and graph must exist (even if sparse) before hybrid retrieval is meaningful.
- **Scheduled Organizer requires Graph Store + Hybrid Retrieval:** Organizer operates on graph data (salience, promotion, summarization) and depends on retrieval for deduplication.
- **Encryption requires Portable Artifact:** Encrypt the artifact, not individual stores. Artifact format must be stable before encryption layer is added.
- **Evaluation Harness requires Hybrid Retrieval:** Can't test recall accuracy without the full retrieval pipeline.
- **Deterministic Rebuild requires Event Store + versioned extraction/embedding:** The rebuild guarantee depends on reproducible processing of the event log.

## MVP Definition

### Launch With (v1)

Minimum viable product -- what's needed to validate that PRME's architecture delivers better recall than vector-only memory.

- [ ] **Append-only event store** (DuckDB) -- foundation for all derived data
- [ ] **Entity extraction pipeline** -- LLM-powered with at least OpenAI support
- [ ] **Graph store with typed nodes/edges** (Kuzu) -- Entity, Fact, Preference, Event nodes; RELATED_TO, MENTIONED_IN, ASSERTED_IN edges; temporal validity (valid_from/valid_to)
- [ ] **Vector index** (HNSW) with pluggable embedding (OpenAI + sentence-transformers)
- [ ] **Lexical index** (FTS5 or Tantivy) over events and facts
- [ ] **Hybrid retrieval pipeline** -- query analysis, multi-source candidates, deterministic re-ranking, context packing
- [ ] **HTTP API** -- add events, search memory, get entity snapshots
- [ ] **Python library wrapper** -- thin wrapper over HTTP API
- [ ] **User/session scoping** -- filter by user_id/session_id
- [ ] **Basic supersedence** -- mark facts as superseded when contradictions detected

### Add After Validation (v1.x)

Features to add once core retrieval is proven more accurate than vector-only.

- [ ] **Scheduled organizer** -- salience recalculation, promotion/demotion, summarization (daily -> weekly -> monthly)
- [ ] **Deduplication / entity alias resolution** -- merge duplicate entities
- [ ] **Policy-based archival** -- TTL enforcement, compression
- [ ] **Explainable retrieval traces** -- return score components per result
- [ ] **Context packing with token budget** -- let developers specify max tokens
- [ ] **MCP server** -- expose PRME as MCP tools for Claude Desktop/Cursor
- [ ] **Portable artifact export/import** -- `memory_pack/` with manifest.json

### Future Consideration (v2+)

Features to defer until product-market fit is established.

- [ ] **Encryption at rest** -- encrypt the portable artifact
- [ ] **CLI tooling** -- `prme inspect`, `prme query`, `prme export`, `prme rebuild`
- [ ] **Evaluation harness** -- recall accuracy, supersedence correctness, compaction metrics, determinism tests
- [ ] **Deterministic rebuild validation** -- prove event log reproduces identical state
- [ ] **LangChain/LlamaIndex integration packages** -- first-class framework support
- [ ] **Additional LLM/embedding providers** -- Anthropic, Gemini, Voyage, Cohere
- [ ] **Adaptive salience learning** -- learn importance signals from user feedback
- [ ] **Multi-agent shared memory** -- namespace isolation with cross-agent queries

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Append-only event store | HIGH | LOW | P1 |
| Entity extraction pipeline | HIGH | HIGH | P1 |
| Graph store (typed nodes/edges) | HIGH | HIGH | P1 |
| Vector index (HNSW) | HIGH | MEDIUM | P1 |
| Lexical index (FTS) | MEDIUM | MEDIUM | P1 |
| Hybrid retrieval pipeline | HIGH | HIGH | P1 |
| HTTP API | HIGH | MEDIUM | P1 |
| Python library wrapper | HIGH | LOW | P1 |
| User/session scoping | HIGH | LOW | P1 |
| Pluggable embedding providers | HIGH | MEDIUM | P1 |
| Pluggable LLM providers | HIGH | MEDIUM | P1 |
| Supersedence handling | HIGH | MEDIUM | P1 |
| Async support | MEDIUM | LOW | P1 |
| Scheduled organizer (full) | HIGH | HIGH | P2 |
| Explainable retrieval traces | MEDIUM | MEDIUM | P2 |
| Context packing / token budget | MEDIUM | MEDIUM | P2 |
| MCP server | MEDIUM | LOW | P2 |
| Portable artifact format | MEDIUM | MEDIUM | P2 |
| Deduplication / alias resolution | MEDIUM | HIGH | P2 |
| Policy-based archival | LOW | MEDIUM | P2 |
| Encryption at rest | MEDIUM | MEDIUM | P3 |
| CLI tooling | MEDIUM | MEDIUM | P3 |
| Evaluation harness | MEDIUM | MEDIUM | P3 |
| Deterministic rebuild | HIGH | HIGH | P3 |
| Framework integrations | MEDIUM | LOW | P3 |
| Additional providers | LOW | LOW | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Mem0 | Zep / Graphiti | Letta (MemGPT) | LangMem | Cognee | PRME Approach |
|---------|------|---------------|-----------------|---------|--------|---------------|
| **Vector search** | Yes (18+ backends) | Yes (embeddings) | Yes (archival) | Yes (LangGraph store) | Yes (vector + graph) | Yes (HNSW, pluggable) |
| **Graph memory** | Add-on (optional) | Core (temporal KG) | No (block-based) | No | Yes (knowledge graph) | Core (Kuzu, typed nodes) |
| **Lexical/BM25 search** | Keyword search | BM25 | No | No | No | Yes (FTS5/Tantivy) |
| **Hybrid retrieval** | Vector + graph + KV | Semantic + BM25 + graph traversal | Vector + rules | Vector only | Vector + graph | Vector + lexical + graph (full hybrid) |
| **Temporal validity** | No explicit model | Yes (bi-temporal: valid_at/invalid_at) | No | No | Time filters only | Yes (valid_from/valid_to on edges) |
| **Supersedence** | Overwrite-based | Edge invalidation | No | No | No | Explicit supersedence pointers with provenance |
| **Event sourcing** | No | Episode nodes (not append-only) | No | No | No | Yes (immutable append-only DuckDB log) |
| **Deterministic rebuild** | No | No | No | No | No | Yes (from event log) |
| **Memory lifecycle** | Basic (add/update/delete) | Valid/invalid edges | Core/recall/archival tiers | Semantic/episodic/procedural types | Enrichment pipeline | Tentative -> Stable -> Superseded -> Archived |
| **Self-organizing** | Basic cleanup | Auto-summarization | Agent-driven updates | Background manager | Memify pipeline | Full organizer (salience, promotion, summarization, dedup, archival) |
| **Portable artifact** | No | No | No | No | No | Yes (copyable memory_pack/) |
| **Encryption at rest** | No | No | No | No | No | Yes (planned) |
| **Local-first** | Cloud-first (OSS available) | Cloud-first (OSS Graphiti) | Server-based | LangGraph-dependent | Self-hostable | Fully local (DuckDB + Kuzu embedded) |
| **MCP support** | Yes (OpenMemory MCP) | No | No | No | Yes | Planned |
| **Eval harness** | No (uses external benchmarks) | No (uses external benchmarks) | No | No | No | Yes (planned) |
| **Latency (p95)** | ~200ms | ~300ms | Not published | ~60s (too slow) | Not published | Target: <200ms |
| **Token cost reduction** | Claims 90%+ | Claims significant | N/A | N/A | N/A | Measurable via context packing |

## Sources

- [Mem0 documentation and features](https://docs.mem0.ai/llms.txt) — HIGH confidence (official docs)
- [Mem0 research paper (arXiv)](https://arxiv.org/abs/2504.19413) — HIGH confidence (peer-reviewed)
- [Mem0 GitHub](https://github.com/mem0ai/mem0) — HIGH confidence (official source)
- [Zep temporal knowledge graph paper (arXiv)](https://arxiv.org/abs/2501.13956) — HIGH confidence (peer-reviewed)
- [Zep documentation](https://help.getzep.com/graph-overview) — HIGH confidence (official docs)
- [Graphiti open source](https://github.com/getzep/graphiti) — HIGH confidence (official source)
- [Letta / MemGPT documentation](https://docs.letta.com/concepts/memgpt/) — HIGH confidence (official docs)
- [LangMem SDK documentation](https://langchain-ai.github.io/langmem/) — HIGH confidence (official docs)
- [Cognee GitHub](https://github.com/topoteretes/cognee) — HIGH confidence (official source)
- [Graphlit survey of AI agent memory frameworks](https://www.graphlit.com/blog/survey-of-ai-agent-memory-frameworks) — MEDIUM confidence (vendor blog, but comprehensive)
- [Index.dev comparison: Mem0 vs Zep vs LangChain Memory](https://www.index.dev/skill-vs-skill/ai-mem0-vs-zep-vs-langchain-memory) — MEDIUM confidence (third-party comparison)
- [A-MEM: Agentic Memory for LLM Agents (NeurIPS 2025)](https://arxiv.org/abs/2502.12110) — HIGH confidence (peer-reviewed)
- [MarkTechPost: Comparing Memory Systems](https://www.marktechpost.com/2025/11/10/comparing-memory-systems-for-llm-agents-vector-graph-and-event-logs/) — MEDIUM confidence (tech journalism)
- [Model Context Protocol specification](https://modelcontextprotocol.io/specification/2025-11-25) — HIGH confidence (official spec)

---
*Feature research for: LLM Memory Engines*
*Researched: 2026-02-19*
