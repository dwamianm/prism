# LLM Memory Systems: State of the Art (2025-2026)

Research compiled March 2026. All benchmark numbers sourced from published papers and official documentation.

---

## 1. System-by-System Analysis

### 1.1 MemGPT / Letta

**Repo**: https://github.com/cpacker/MemGPT (now https://github.com/letta-ai/letta)
**Paper**: https://arxiv.org/abs/2310.08560

**Architecture**: OS-inspired tiered memory hierarchy.

| Tier | Analogy | Storage | In-Context? |
|------|---------|---------|-------------|
| Core Memory | RAM | In-context blocks (human block, persona block) with character limits | Always |
| Recall Memory | Disk (logs) | Full conversation history saved to disk | Searched on demand |
| Archival Memory | Disk (files) | Vector DB or file-based external storage | Searched on demand |

**Core Memory Blocks**: Each has a label, description, value (tokens), and character limit. The agent self-edits these via tool calls (`core_memory_append`, `core_memory_replace`). Blocks are always pinned in the context window.

**Retrieval**: Agent calls `archival_memory_search` (vector similarity) or `recall_memory_search` (conversation history lookup) as tool invocations. The LLM decides when to search.

**Context Management**: When the message buffer exceeds capacity, ~70% of oldest messages are evicted and recursively summarized. Summaries are compounded with prior summaries.

**Fact Updates / Supersedence**: Handled implicitly by the agent rewriting core memory blocks. No formal contradiction detection or temporal validity. The agent is responsible for recognizing when facts change and updating its own memory blocks.

**Sleep-Time Compute** (2025 addition): Memory management runs asynchronously during idle periods rather than during conversation turns. Memory blocks are reorganized and refined proactively.

**Scalability**: Bounded by context window for core memory. Archival/recall scale with underlying store (vector DB, SQLite, Postgres). No built-in sharding.

**Semantic Matching**: Standard embeddings for archival search. No VSA or alternative representations.

**LoCoMo Score**: Letta claims 74.0% accuracy using gpt-4o-mini with simple file-based storage. The Mem0 paper reports MemGPT at ~48% (disputed -- Letta claims Mem0's MemGPT benchmarking methodology was flawed and never disclosed).

---

### 1.2 Mem0

**Repo**: https://github.com/mem0ai/mem0
**Paper**: https://arxiv.org/abs/2504.19413
**Docs**: https://docs.mem0.ai

**Architecture**: Two-phase pipeline (Extraction + Update) with dual storage (vector + graph).

**Extraction Phase**:
1. Takes current message pair (m_{t-1}, m_t), rolling conversation summary S, and last 10 messages
2. LLM extraction function identifies salient memories from the new exchange
3. Outputs candidate memory set {w1, w2, ..., wn}

**Update Phase**: For each extracted fact:
1. Retrieve top 10 semantically similar memories from vector DB
2. LLM classifies relationship via function-calling into one of four operations:
   - **ADD**: New memory, no semantic equivalent exists
   - **UPDATE**: Augment existing memory with complementary info
   - **DELETE**: Remove memory contradicted by new information
   - **NOOP**: No modification needed

**Graph Memory Variant (Mem0^g)**:
- Entity Extractor identifies entities with types (Person, Location, Event, etc.)
- Relationship Generator derives triplets (v_s, r, v_d)
- Deduplication via embedding similarity threshold
- Conflict resolution: LLM-based update resolver marks relationships as invalid rather than deleting
- Dual retrieval: entity-centric graph traversal + semantic triplet matching

**Storage Backends**: 20+ vector DBs (Qdrant, Pinecone, Chroma, Milvus, FAISS, etc.). Neo4j for graph. Key-value for fast lookups.

**Fact Updates**: LLM-driven ADD/UPDATE/DELETE. No formal temporal validity windows. Contradictions resolved by DELETE of old + ADD of new. No supersedence chain.

**Benchmark Scores (LOCOMO, from their paper)**:

| Method | Single-Hop J | Multi-Hop J | Open-Domain J | Temporal J | Overall J |
|--------|-------------|-------------|---------------|-----------|-----------|
| Mem0 | 67.13 | 51.15 | 72.93 | 55.51 | 66.88 |
| Mem0^g | 65.71 | 47.19 | 75.71 | 58.13 | 68.44 |
| Zep | 61.70 | 41.35 | 76.60 | 49.31 | 65.99 |
| LangMem | 62.23 | 47.92 | 71.12 | 23.43 | 58.10 |
| OpenAI | 63.79 | 42.92 | 62.29 | 21.71 | 52.90 |
| Full-Context | — | — | — | — | 72.90 |

**Latency**: p95 of 1.44s (Mem0) vs 17.1s (full-context). 91% lower latency, 90%+ token savings.

**Semantic Matching**: Standard dense embeddings. No alternative representations.

---

### 1.3 Zep / Graphiti

**Paper**: https://arxiv.org/abs/2501.13956
**Repo**: https://github.com/getzep/graphiti

**Architecture**: Temporally-aware knowledge graph with three hierarchical subgraph layers.

**Episode Subgraph**: Raw input data (messages, text, JSON). Non-lossy. Episodes connect to extracted entities via episodic edges.

**Semantic Entity Subgraph**: Extracted and resolved entities + relationship edges. Deduplication via embedding + full-text search before integration.

**Community Subgraph**: Clusters of strongly connected entities with high-level summarizations. Hierarchical overview layer.

**Temporal Fact Management** (bi-temporal model):
- `t'_created`, `t'_expired` — transactional timeline (when recorded/invalidated in system)
- `t_valid`, `t_invalid` — chronological timeline (when fact held true in reality)
- When contradictions detected: old edge gets `t_invalid` set to `t_valid` of the new edge
- Old facts are invalidated, never deleted — enables historical queries

**Retrieval Pipeline**: f(a) = X(p(phi(a)))

1. **Search (phi)** — three parallel methods:
   - Cosine semantic similarity on embeddings
   - Okapi BM25 full-text search via Neo4j/Lucene
   - Breadth-first graph traversal within n-hops
2. **Reranker (p)** — Reciprocal Rank Fusion, MMR, episode-mention frequency, node distance from centroid, cross-encoder LLM scoring
3. **Constructor (X)** — formats selected nodes/edges into context strings with temporal metadata

**Storage**: Neo4j graph database, Apache Lucene (via Neo4j), BGE-m3 embeddings (1024-dim).

**Benchmark Scores**:

| Benchmark | Metric | Score |
|-----------|--------|-------|
| DMR | Accuracy (gpt-4-turbo) | 94.8% (vs MemGPT 93.4%) |
| DMR | Accuracy (gpt-4o-mini) | 98.2% |
| LongMemEval | Overall (gpt-4o) | 71.2% |
| LongMemEval | Overall (gpt-4o-mini) | 63.8% |

LongMemEval by category (gpt-4o-mini):
- Single-session-user: 92.9% (+14.1%)
- Single-session-preference: 53.3% (+77.7%)
- Temporal-reasoning: 54.1% (+48.2%)
- Multi-session: 47.4% (+16.7%)
- Knowledge-update: 74.4% (-3.36%)

**Latency**: ~3.2s vs 31.3s full-context with gpt-4o-mini (~90% reduction). Context reduced from 115k to 1.6k tokens.

**Key Insight**: Zep's bi-temporal model is the most principled approach to fact supersedence among production systems. Facts have real validity windows, not just "latest wins."

---

### 1.4 LangChain / LangGraph Memory

**Docs**: https://docs.langchain.com/oss/python/langgraph/add-memory

**Architecture**: Evolved from simple memory classes to state-based persistence.

**Legacy (deprecated as of v0.3.1)**:
- `ConversationBufferMemory` — stores full conversation history
- `ConversationSummaryMemory` — LLM-summarized history
- `ConversationBufferWindowMemory` — last k messages
- `ConversationEntityMemory` — entity-keyed facts

**Modern (LangGraph)**:
- **Short-term**: Checkpoint-based state persistence per thread. Automatic checkpointing on every graph invocation. Backends: InMemorySaver, SqliteSaver, PostgresSaver.
- **Long-term**: Cross-thread memory store with JSON document storage, flexible namespacing, and semantic search via cosine similarity.
- **Vector backends**: pgvector (PostgreSQL), Redis with built-in vector search
- **Document backends**: MongoDB, Redis

**Fact Updates**: No built-in contradiction detection. Application code manages updates. The store is a simple put/get/search document store.

**Scalability**: Depends entirely on backing store. PostgreSQL + pgvector for production. No built-in memory organization or compaction.

**Semantic Matching**: Standard dense embeddings via configured embedding model.

**Assessment**: LangGraph is infrastructure, not a memory system. It provides the plumbing (persistence, search, namespacing) but not the intelligence (extraction, conflict resolution, temporal reasoning). You build a memory system on top of it.

---

### 1.5 Cognee

**Repo**: https://github.com/topoteretes/cognee
**Website**: https://www.cognee.ai

**Architecture**: Graph-vector hybrid with self-improving memory.

**Two Memory Layers**:
- **Session Memory**: Short-term working memory. Loads relevant embeddings and graph fragments into runtime context.
- **Permanent Memory**: Long-term store for user data, interaction traces, documents, derived relationships. Continuously cross-connected in the graph with vector representations.

**Processing Pipeline (cognify)**:
1. Classify documents
2. Check permissions
3. Extract chunks
4. LLM extracts entities and relationships
5. Generate summaries
6. Embed into vector store + commit edges to graph

**Self-Improvement (memify)**:
- Prune stale nodes
- Strengthen frequent connections
- Reweight edges based on usage signals
- Add derived facts

**Retrieval**: 14 retrieval modes from classic RAG to chain-of-thought graph traversal.

**Storage**: LanceDB for vectors, Neo4j/Memgraph for graphs.

**Fact Updates**: Edge reweighting and node pruning via memify. No formal temporal validity or supersedence chain.

**Benchmark**: Claims to outperform Mem0, LightRAG, and Graphiti on HotPotQA multi-hop reasoning (human-like correctness score of 0.93). With CoT retrievers: +25% human-like correctness, +16-18% DeepEval EM. Exact comparative numbers not published in detail.

---

## 2. Newer SOTA Systems (Late 2025)

### 2.1 ENGRAM

**Paper**: https://arxiv.org/abs/2511.12960

**Architecture**: Lightweight typed memory with router.

Three canonical memory types:
- **Episodic**: Timestamped events (title, summary, timestamp)
- **Semantic**: Stable facts and preferences (fact strings)
- **Procedural**: Instructions and workflows (title, normalized content)

Router produces a 3-bit mask per turn. Records embedded and stored in SQLite. Retrieval: top-k per type via cosine similarity, merge, deduplicate, truncate to K=25 items.

**Key Result**: Removing typed routing collapses performance from 77.55% to 46.56% — typed separation is critical, not just convenient.

| Benchmark | ENGRAM | Full-Context | Mem0 | Zep |
|-----------|--------|-------------|------|-----|
| LoCoMo (overall) | 77.55% | 72.60% | 64.73% | 42.29% |
| LongMemEval (overall) | 71.40% | 56.20% | — | — |

ENGRAM uses ~916 tokens of context (99% fewer than full-context).

### 2.2 Hindsight / TEMPR (Vectorize)

**Paper**: https://arxiv.org/abs/2512.12818

**Architecture**: Four epistemically-distinct memory networks + temporal entity-aware retrieval.

| Network | Stores | Example |
|---------|--------|---------|
| World (W) | Objective facts | "Python 3.12 was released in October 2023" |
| Experience (B) | Biographical/agent actions | "I migrated the database to PostgreSQL" |
| Opinion (O) | Subjective judgments + confidence 0-1 | "React is better for this project (0.7)" |
| Observation (S) | Preference-neutral entity summaries | "PostgreSQL: relational DB, JSON support, used since Jan 2024" |

**TEMPR Retrieval** (four parallel channels + RRF):
1. Semantic: cosine similarity on embeddings
2. Keyword: BM25 full-text search
3. Graph: spreading activation through entity/temporal/semantic/causal links
4. Temporal: date parsing + interval matching

Results merged via Reciprocal Rank Fusion, then neural cross-encoder reranking.

**Contradiction Handling**: Opinion confidence updated dynamically:
- Reinforce: c' = min(c + alpha, 1.0)
- Weaken: c' = max(c - alpha, 0.0)
- Contradict: c' = max(c - 2*alpha, 0.0)

Background merging resolves conflicts via LLM, favoring newer information.

**Benchmark Scores**:

| System | LongMemEval | LoCoMo |
|--------|-------------|--------|
| Hindsight (OSS-20B) | 83.6% | 85.67% |
| Hindsight (OSS-120B) | 89.0% | 89.61% |
| Hindsight (Gemini-3) | 91.4% | — |
| Full-context GPT-4o | 60.2% | — |
| Supermemory (GPT-5) | 84.6% | — |
| Prior best open system | — | 75.78% |

**Key Insight**: Epistemic separation (fact vs opinion vs experience) is a significant differentiator. This is the closest existing system to PRME's typed node model.

### 2.3 Emergence AI

**Blog**: https://www.emergence.ai/blog/sota-on-longmemeval-with-rag

Achieved 86% on LongMemEval with RAG-based approach. Their finding: "advanced memory architecture appears to be overkill" for current benchmarks. Cross-encoder reranking + session-level retrieval + chain-of-thought was sufficient.

### 2.4 Supermemory

**Repo**: https://github.com/supermemoryai/supermemory

Claims #1 on LongMemEval, LoCoMo, and ConvoMem. ~85.86% overall on LongMemEval. Architecture details not fully published.

---

## 3. Academic Benchmarks

### 3.1 LoCoMo

**Paper**: https://arxiv.org/abs/2402.17753 (ACL 2024)
**Website**: https://snap-research.github.io/locomo/

**What it measures**: Long-term conversational memory across multi-session dialogues.
- Dialogues span up to 32 sessions, ~600 turns, ~16,000 tokens
- Includes images (via web search and captioning)
- Question types: single-hop factual, multi-hop reasoning, temporal reasoning, open-domain

**Scoring**: F1 (word overlap), LLM-as-Judge accuracy

**Baseline Scores (F1)**:
- Mistral-7B: 13.9
- GPT-3.5: 23.4
- GPT-4: 32.1
- Human ceiling: 87.9

**Current SOTA**:
- Hindsight (OSS-120B): 89.61% (Judge)
- MemMachine v0.2: 84.87% (Judge)
- ENGRAM: 77.55% (Judge)
- Full-context (gpt-4o-mini): 72.60% (Judge)
- Mem0^g: 68.44% (Judge)

**LoCoMo-Plus** (2026 extension): Beyond-factual evaluation adding cognitive memory dimensions.

### 3.2 LongMemEval

**Paper**: https://arxiv.org/abs/2410.10813 (ICLR 2025)
**Repo**: https://github.com/xiaowu0162/LongMemEval

**What it measures**: Five core long-term memory abilities:
1. **Information Extraction** — retrieving specific facts from past sessions
2. **Multi-Session Reasoning** — synthesizing info across multiple conversations
3. **Temporal Reasoning** — understanding time-ordered events and changes
4. **Knowledge Updates** — tracking when facts change (supersedence)
5. **Abstention** — knowing when information was never discussed

**Structure**: 500 curated questions within scalable chat histories (~115k tokens in S setting).

**Scores (LongMemEval_S)**:

| System | Overall | Info Extract | Multi-Session | Temporal | Knowledge Update | Abstention |
|--------|---------|-------------|---------------|----------|-----------------|------------|
| Hindsight (Gemini-3) | 91.4% | 97.1% | 87.2% | 91.0% | 94.9% | — |
| Hindsight (OSS-120B) | 89.0% | 100.0% | 81.2% | 85.7% | 92.3% | — |
| Emergence | 86.0% | 98.6% | 81.2% | 85.7% | 83.3% | — |
| Supermemory | 85.9% | — | 71.4% | 76.7% | — | — |
| Hindsight (OSS-20B) | 83.6% | 95.7% | 79.7% | 79.7% | 84.6% | — |
| ENGRAM | 71.4% | 97.1% | 60.2% | 55.6% | 74.4% | — |
| Zep (gpt-4o) | 71.2% | — | — | — | — | — |
| Full-context GPT-4o | 60.2% | — | — | — | — | — |

**Key Observation**: Temporal reasoning and knowledge updates are the hardest categories. These are exactly the areas where PRME's epistemic state model (Tentative -> Stable -> Superseded -> Archived) and VSA temporal encoding would provide structural advantages.

---

## 4. Comparative Architecture Summary

| System | Storage | Retrieval | Fact Supersedence | Temporal Model | Semantic Matching |
|--------|---------|-----------|-------------------|----------------|-------------------|
| MemGPT/Letta | In-context blocks + vector DB | Agent-driven tool calls | Agent rewrites blocks | None formal | Dense embeddings |
| Mem0 | Vector DB + Neo4j graph | Embedding similarity + graph traversal | LLM ADD/UPDATE/DELETE | Timestamps only | Dense embeddings |
| Zep/Graphiti | Neo4j (3-layer KG) | Cosine + BM25 + BFS + reranker | Bi-temporal invalidation | Bi-temporal (t_valid/t_invalid + t_created/t_expired) | BGE-m3 1024d |
| LangGraph | Configurable (PG, Redis, Mongo) | Cosine similarity on store | None built-in | None | Dense embeddings |
| Cognee | LanceDB + Neo4j/Memgraph | 14 modes (RAG to CoT graph) | Edge reweighting + pruning | None formal | Dense embeddings |
| ENGRAM | SQLite | Cosine per memory type | Not described | Timestamps | Dense embeddings |
| Hindsight | Vector + graph | 4-channel RRF + cross-encoder | Confidence adjustment (reinforce/weaken/contradict) | Date parsing + intervals | Dense embeddings |
| **PRME** | DuckDB + Kuzu + HNSW + FTS5 | Graph + vector + lexical + recency | Epistemic lifecycle (Tentative->Stable->Superseded->Archived) | Temporal validity windows (valid_from/valid_to) | Dense embeddings + **VSA** |

---

## 5. VSA / Hyperdimensional Computing for LLM Memory

### 5.1 Has Anyone Else Tried This?

**Short answer: No one has applied VSA as a primary memory substrate for LLM agents.**

What exists:

1. **Hyperdimensional Probe** (Sep 2025, https://arxiv.org/abs/2509.25045): Uses VSA to *decode* LLM internal representations. Maps residual stream into controlled VSA space. Probes Llama 4 Scout embeddings. This is interpretability, not memory.

2. **Attention as Binding** (Dec 2025, https://arxiv.org/html/2512.14709v1): Theoretical paper arguing transformer attention implements soft VSA operations (binding = Q/K dot product, superposition = residual accumulation). Proposes but does not implement:
   - Explicit binding/unbinding heads
   - Hyperdimensional memory layers: m <- m + sum(r_k * f_k)
   - Training regularizers for orthogonality
   No experiments conducted. Pure position paper.

3. **LARS-VSA** (2024, https://arxiv.org/abs/2405.14436): VSA for learning abstract rules. Not applied to agent memory.

4. **Cross-Layer VSA Hardware** (Aug 2025): Hardware acceleration for VSA systems. Focused on efficiency, not memory architectures.

### 5.2 Why VSA for Memory is Novel

Every production system above uses dense embeddings (768-1536 dim, learned by neural networks) for semantic matching. VSA offers fundamentally different properties:

| Property | Dense Embeddings | VSA |
|----------|-----------------|-----|
| Dimensionality | 768-1536 | 10,000+ |
| Composition | Concatenation/averaging | Algebraic (bind, bundle, permute) |
| Decomposition | Not possible | Unbinding recovers components |
| Structure preservation | Lost in embedding | Maintained via binding |
| Temporal encoding | Separate timestamp field | Encoded in the vector via permutation |
| Supersedence | External metadata | Detectable via vector similarity patterns |
| Training required | Yes (embedding model) | No (random + algebraic) |
| Interpretability | Opaque | Recoverable structure |

**The key advantage**: VSA can encode *structured relationships* (who-did-what-when) in a single vector that is both searchable by similarity AND decomposable back into components. Dense embeddings can only do the former.

### 5.3 PRME-X Phase 1 Results vs Field

PRME's VSA research (Phase 1) achieved on a targeted changing-facts scenario:
- Precision@1: 83.3% (10/12)
- Recall@5: 100% (12/12)
- Supersedence accuracy: 100% (10/10)
- MRR: 0.892

This is not directly comparable to LoCoMo/LongMemEval (different task format), but the 100% supersedence accuracy is notable — knowledge updates are the hardest category for existing systems (Hindsight gets 92-95%, ENGRAM gets 74%, Zep drops 3.4% on knowledge-update queries).

---

## 6. Unsolved Hard Problems

### 6.1 Temporal Reasoning
Most systems treat time as metadata (timestamps) rather than encoding it in the representation. Zep's bi-temporal model is the most principled but still requires explicit temporal queries. No system can reliably answer "What did the user believe about X before learning Y?" without the question explicitly mentioning time.

### 6.2 Belief Evolution vs Fact Updates
Only Hindsight separates opinions (with confidence scores) from facts. Everyone else conflates "the user changed their mind" with "new information arrived." PRME's epistemic lifecycle (Tentative -> Stable -> Superseded) is more principled than any production system.

### 6.3 Multi-Hop Reasoning Over Memory
The hardest benchmark category across all systems. Requires synthesizing information from multiple memories that were never stored together. Current approaches: graph traversal (Zep, Cognee) or brute-force context stuffing. No system reliably handles 3+ hop chains.

### 6.4 Scalability of Memory Organization
All current systems that do memory organization (Cognee's memify, Letta's sleep-time compute, PRME's organizer) require LLM calls for conflict resolution, summarization, and entity resolution. At thousands of memories, this becomes expensive. VSA's algebraic operations could offer O(1) conflict detection without LLM calls.

### 6.5 Memory Interference
As memory stores grow, retrieval precision degrades. Dense embeddings suffer from curse of dimensionality. VSA's near-orthogonality in high dimensions provides theoretical resistance to interference, but this hasn't been validated at scale (10K+ memories).

### 6.6 Compositionality
Current systems store flat facts or simple triples. Complex relationships like "Alice told Bob that Carol's project was moved to Q3 because of the budget cut that Dave announced" are lost. VSA binding can theoretically compose these structures, but practical limits on binding depth aren't established.

### 6.7 Benchmark Saturation
Emergence AI's finding that "advanced memory architecture appears to be overkill" for current benchmarks is sobering. RAG + reranking achieves 86% on LongMemEval. The benchmarks may not be hard enough to differentiate architecturally sophisticated approaches from well-tuned retrieval.

### 6.8 The Evaluation Gap
No benchmark adequately tests:
- Long-running agents (weeks/months of operation with 100K+ memories)
- Contradiction chains (A supersedes B supersedes C — what's current?)
- Contextual memory (same fact means different things in different scopes)
- Multi-user memory isolation and sharing
- Memory-driven behavior change over time

---

## 7. Strategic Assessment for PRME

### Where PRME Already Has Structural Advantages

1. **Epistemic Lifecycle**: No production system has Tentative -> Stable -> Superseded -> Archived with formal state transitions. Hindsight has opinion confidence but no lifecycle for facts.

2. **Bi-Temporal + Validity Windows**: Only Zep has this. PRME's design (valid_from/valid_to on graph edges) matches the SOTA.

3. **Typed Node Model**: PRME's 9 node types (Entity, Event, Fact, Decision, Preference, Task, Summary, Instruction) are more granular than Hindsight's 4 networks. ENGRAM's ablation proves typed separation matters.

4. **Event Sourcing**: No other system uses append-only event logs as the source of truth with derived structures rebuilt from the log. This gives PRME unique auditability and portability.

5. **VSA as Complement**: If VSA supersedence detection (100% accuracy in Phase 1) can be integrated with the hybrid retrieval pipeline, PRME would have a contradiction detection mechanism that requires zero LLM calls — unique in the field.

### Where PRME Needs to Close Gaps

1. **Retrieval Pipeline Maturity**: Zep's 3-method parallel search + RRF + cross-encoder reranking is well-validated. PRME's pipeline design (RFC-0005) is spec'd but not benchmarked against these systems.

2. **Benchmark Performance**: Need LoCoMo and LongMemEval numbers to be taken seriously. The evaluation harness exists in `benchmarks/` but needs to run against these specific datasets.

3. **Sleep-Time / Background Processing**: PRME's organizer jobs are designed for this, but Letta's sleep-time compute is already shipping. Need to validate that organizer jobs actually improve retrieval quality.

4. **Graph Retrieval**: Zep and Cognee use Neo4j with mature graph query capabilities. Kuzu is less proven at scale for this use case.

### The VSA Opportunity

Nobody is doing this. The "Attention as Binding" paper (Dec 2025) provides theoretical grounding that VSA operations are compatible with transformer representations, but no one has built an actual memory system using VSA for storage and retrieval. PRME-X Phase 1/2 results are encouraging but need to scale to realistic workloads (1000+ memories, multi-session dialogues, temporal queries).

The highest-value research direction: demonstrate that VSA supersedence detection + temporal encoding can match or exceed Hindsight's knowledge-update accuracy (92-95%) without requiring LLM calls for conflict resolution.

---

## Sources

- [MemGPT Paper](https://arxiv.org/abs/2310.08560)
- [Letta Agent Memory Blog](https://www.letta.com/blog/agent-memory)
- [Mem0 Paper](https://arxiv.org/abs/2504.19413)
- [Mem0 Documentation](https://docs.mem0.ai/llms.txt)
- [Zep/Graphiti Paper](https://arxiv.org/abs/2501.13956)
- [Graphiti Repo](https://github.com/getzep/graphiti)
- [Cognee Repo](https://github.com/topoteretes/cognee)
- [Cognee AI Memory Benchmarking](https://www.cognee.ai/blog/deep-dives/ai-memory-evals-0825)
- [LangGraph Memory Docs](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [ENGRAM Paper](https://arxiv.org/abs/2511.12960)
- [Hindsight/TEMPR Paper](https://arxiv.org/abs/2512.12818)
- [Emergence AI SOTA Blog](https://www.emergence.ai/blog/sota-on-longmemeval-with-rag)
- [Supermemory](https://supermemory.ai/research)
- [LoCoMo Benchmark](https://snap-research.github.io/locomo/) (ACL 2024)
- [LongMemEval Benchmark](https://arxiv.org/abs/2410.10813) (ICLR 2025)
- [Letta Benchmarking Blog](https://www.letta.com/blog/benchmarking-ai-agent-memory)
- [Hyperdimensional Probe](https://arxiv.org/abs/2509.25045)
- [Attention as Binding](https://arxiv.org/html/2512.14709v1)
- [LARS-VSA](https://arxiv.org/abs/2405.14436)
- [MemAgents ICLR 2026 Workshop](https://openreview.net/pdf?id=U51WxL382H)
- [Memory Mechanisms in LLM Agents Survey](https://dl.acm.org/doi/10.1145/3748302)
