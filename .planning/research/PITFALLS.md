# Pitfalls Research

**Domain:** Local-first LLM memory engine (event sourcing, graph modeling, hybrid retrieval, self-organization)
**Researched:** 2026-02-19
**Confidence:** HIGH (multiple authoritative sources corroborate each pitfall)

## Critical Pitfalls

### Pitfall 1: Kuzu Graph Database Has Been Abandoned

**What goes wrong:**
Kuzu Inc archived the KuzuDB repository on October 10, 2025. The company posted "Kuzu is working on something new" and stopped supporting the project. Building on Kuzu means building on an unmaintained dependency with an uncertain future.

**Why it happens:**
The project spec was written before Kuzu was abandoned. Kuzu was the best embedded property graph database available, so it was a natural choice. The abandonment was sudden and unexpected.

**How to avoid:**
Evaluate alternatives before committing. Three options exist:
1. **Use a Kuzu fork** — RyuGraph (Predictable Labs) and Bighorn (Kineviz) are active forks maintaining MIT license. RyuGraph explicitly continues development. Risk: fork maintainer resources are unproven.
2. **Use DuckDB's DuckPGQ extension** — DuckDB added SQL/PGQ graph syntax support, allowing graph queries within the same DuckDB instance used for the event store. This eliminates a separate dependency entirely. Risk: DuckPGQ is a community extension, not core DuckDB.
3. **Abstract the graph layer** — Build a `GraphStore` interface from day one so the backing engine can be swapped. Start with whatever works (Kuzu fork or DuckPGQ), migrate later if needed.

Option 3 is the only safe path regardless of which engine you start with.

**Warning signs:**
- Kuzu fork maintainer goes inactive (no commits for 30+ days)
- DuckPGQ extension breaks on DuckDB version upgrade
- Graph queries are slow for multi-hop traversals (performance regressions in unmaintained code)

**Phase to address:**
Phase 1 (foundation). The graph store abstraction must be designed before any graph code is written. This is a day-one architectural decision.

---

### Pitfall 2: LLM-on-Write Extraction Creates Compounding Corruption

**What goes wrong:**
Using an LLM to extract entities, facts, and relationships at write time means every piece of data in the graph is filtered through a hallucination-prone process. Errors compound: a misidentified entity creates wrong edges, which corrupt graph neighborhood queries, which return wrong context, which causes the LLM to make worse extractions. The data is corrupted before it even enters the database.

**Why it happens:**
LLM-based entity extraction feels natural — it understands natural language, handles ambiguity, and produces structured output. But LLMs hallucinate at a baseline rate that is never zero. Zep's Graphiti burns 1,028 LLM calls per test case (1.17 million tokens) with this approach. Mem0 spins up three separate inference jobs per message. The cost and error surface are enormous.

**How to avoid:**
1. **Separate extraction from storage** — Store raw events first (append-only log), then run extraction as a derived process that can be re-run when extraction logic improves.
2. **Use rule-based extraction with LLM fallback** — Pattern matching, regex, and NER models handle 80% of entities cheaply and deterministically. Use LLM extraction only for ambiguous cases.
3. **Confidence-gate extracted data** — All LLM-extracted facts start as `Tentative` with tracked provenance. They promote to `Stable` only through reinforcement (multiple sources, explicit confirmation).
4. **Make extraction replayable** — Since events are append-only, you can always re-extract from the log when the extraction pipeline improves. Version your extraction logic.

**Warning signs:**
- Graph contains entities that don't appear in any raw event
- Retrieval returns facts the user never stated
- Extraction costs dominate your LLM budget (Zep averaged 600k tokens per conversation)
- Users correct the system frequently on basic facts

**Phase to address:**
Phase 1 (extraction pipeline design). The decision to use rule-based-first extraction must be made before any entity goes into the graph. Retrofitting this is a rewrite of the entire extraction layer.

---

### Pitfall 3: Treating Embedding Model Choice as a One-Time Decision

**What goes wrong:**
Every vector in the index is tied to a specific embedding model. When you upgrade models (and you will — the embedding landscape is moving fast), old and new vectors live in incompatible spaces. Cosine similarity between a text-embedding-3-small vector and a text-embedding-3-large vector is meaningless. You cannot mix them.

**Why it happens:**
At project start, you pick a model and embed everything with it. It works great. Six months later, a better model exists. You embed new content with the new model. Now your index contains vectors from two incompatible spaces and similarity search silently returns wrong results — no errors, just degraded quality that is hard to detect.

**How to avoid:**
1. **Record model metadata per embedding** — Every vector must store: model name, model version, embedding dimension. The spec already requires this — enforce it rigorously.
2. **Plan for full re-embedding** — Design the system so re-embedding the entire corpus is a supported, automated operation triggered by config change. Since events are append-only, re-embedding is just "replay with new model."
3. **Consider dual-index migration** — During migration, run both old and new indexes. Route queries to both. Gradually shift to the new index as re-embedding completes. This avoids downtime.
4. **Monitor embedding drift** — Track similarity score distributions over time. A sudden shift in score distributions indicates model inconsistency.

**Warning signs:**
- Retrieval quality degrades without any obvious code change
- Similarity scores cluster differently for old vs. new content
- Configuration references a different model than what most vectors were embedded with
- Re-embedding is manual or impossible to trigger

**Phase to address:**
Phase 1 (vector index design). The embedding metadata schema and re-embedding pipeline must be designed up front. Phase 3 (evaluation harness) should include embedding consistency checks.

---

### Pitfall 4: Deterministic Rebuild is Harder Than It Sounds

**What goes wrong:**
The spec requires that identical event logs + config produce identical results. This sounds simple but breaks in subtle ways: floating-point non-determinism in embedding models, LLM extraction non-determinism, timestamp-dependent operations, external API calls during replay, and ordering sensitivity in concurrent operations.

**Why it happens:**
Developers test determinism with small datasets and simple cases. Real determinism requires: (a) no external state dependency during replay, (b) bit-identical numerical computation, (c) deterministic ordering of all operations, and (d) versioned logic for every transformation step. Any single gap breaks the guarantee.

**How to avoid:**
1. **Cache all external results in the event log** — When an LLM extracts entities, store the extraction result as a derived event. On replay, use the cached result, not a new LLM call.
2. **Version every transformation** — Extraction logic v1.2 + scoring weights v3.0 + embedding model X = deterministic output. Change any one, get different (but still deterministic) output.
3. **Use deterministic seeds** — For any operation with randomness (HNSW construction, sampling), use seeded RNGs derived from event content hashes.
4. **Test rebuild continuously** — Don't wait for Phase 3. Add a rebuild-and-compare test in CI from Phase 1. It will catch drift immediately.
5. **Distinguish "reproducible" from "bit-identical"** — Define what determinism means for your system. Exact vector match? Same top-k results? Same re-ranking order? Be specific.

**Warning signs:**
- Rebuild produces different graph structure from a live database
- Test that "worked yesterday" fails today with same inputs
- External API calls appear in rebuild codepaths
- No version metadata on extraction logic or scoring weights

**Phase to address:**
Phase 1 (design constraint), Phase 3 (validation). The caching strategy and versioning scheme must be designed in Phase 1. The evaluation harness in Phase 3 must prove determinism. But add basic rebuild tests from the start.

---

### Pitfall 5: Supersedence Logic That Silently Drops Valid Information

**What goes wrong:**
The spec explicitly requires that conflicting assertions not silently overwrite prior ones. But getting supersedence right is one of the hardest problems in temporal knowledge management. Common failure modes: (a) marking old facts as superseded when they're actually still valid in a different context, (b) failing to detect contradictions because the LLM phrases things differently, (c) creating supersedence chains that make it impossible to reconstruct what was true at a given point in time.

**Why it happens:**
Contradiction detection requires understanding semantics, not just string matching. "I work at Acme" and "I just started at WidgetCorp" are contradictions, but "I work at Acme" and "I'm consulting for WidgetCorp" might not be. Zep's approach uses LLM comparison of edges, which is expensive (their benchmark showed massive token burn). Simple approaches miss contradictions; complex approaches hallucinate contradictions.

**How to avoid:**
1. **Explicit supersedence only** — Never auto-supersede without high confidence. Default to keeping both assertions active and flagging the conflict for resolution.
2. **Scope supersedence narrowly** — "Employer" facts supersede other "employer" facts. "Consulting" facts don't. Use typed assertions so supersedence rules are scoped to assertion types.
3. **Preserve the full chain** — A superseded fact must retain its `valid_from`/`valid_to` window and remain queryable for historical questions. "What did the user say about X in March?" must work even after supersedence.
4. **Test with temporal queries** — Build test cases that ask "What was true at time T?" and verify the system returns the correct assertion for that time window, not the current one.

**Warning signs:**
- Users say "I told you X" and the system has no record (it was superseded incorrectly)
- Historical queries return current facts instead of temporally-correct ones
- The same entity has contradictory active facts that should have triggered supersedence
- Supersedence is triggered by a single mention without reinforcement

**Phase to address:**
Phase 2 (organizer/supersedence handling). But the data model supporting temporal validity must be in Phase 1 — you cannot add `valid_from`/`valid_to` retroactively.

---

### Pitfall 6: DuckDB Concurrency Model Mismatched with HTTP API

**What goes wrong:**
PRME exposes an HTTP API as its primary integration surface. HTTP APIs serve concurrent requests. DuckDB is a single-writer database — only one transaction can write at a time. Under concurrent HTTP requests that all want to append events, you get write contention, blocked requests, or transaction conflict errors.

**Why it happens:**
DuckDB is designed for embedded analytics, not high-concurrency OLTP workloads. Its concurrency model is single-writer, multiple-reader. This works fine for a Python library used by one process. It breaks immediately when an HTTP server receives concurrent requests from multiple LLM agents or conversations.

**How to avoid:**
1. **Serialize writes through a queue** — All event appends go through a single async write queue. The HTTP handler enqueues the write and returns immediately (or waits for confirmation). This is the standard pattern for single-writer databases behind concurrent APIs.
2. **Separate read and write connections** — DuckDB supports concurrent reads. Read queries can use a connection pool. Writes must be serialized.
3. **Batch writes** — Instead of one INSERT per event, batch multiple events into a single transaction. This amortizes the write lock cost.
4. **Load test early** — Test with 10+ concurrent conversations hitting the API simultaneously. DuckDB's append-never-conflicts behavior helps, but transactions involving graph updates or index rebuilds will conflict.

**Warning signs:**
- "Transaction conflict" errors in logs
- HTTP 500 errors under concurrent load
- Event writes are slow (seconds, not milliseconds)
- Deadlocks between event store writes and graph/index updates

**Phase to address:**
Phase 1 (HTTP API design). The write serialization pattern must be designed when the API layer is built. Retrofitting this requires restructuring the entire write path.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Hardcode embedding model | Faster initial setup | Full rewrite when model changes; no migration path | Never — always store model metadata per vector |
| Skip graph store abstraction layer | Faster development, fewer interfaces | Locked into Kuzu (abandoned) or its fork; no swap path | Never — given Kuzu's status this is critical |
| Use LLM for all entity extraction | Higher quality extraction on day one | Massive cost; non-deterministic; hallucination-prone; breaks rebuild | Only for ambiguous cases after rule-based extraction fails |
| Store extraction results only (not raw events) | Smaller storage footprint | Cannot re-extract when logic improves; cannot rebuild; violates append-only principle | Never — this violates the core architecture |
| Inline scoring weights | Simpler code | Cannot tune retrieval without code changes; breaks determinism versioning | MVP only — must be configurable before Phase 2 |
| Single embedding dimension | Simpler vector index | Cannot use models with different dimensions; locks out future models | MVP only — but design schema to support multiple dimensions |
| Skip write queue for DuckDB | Simpler HTTP handler | Concurrent write failures in production | Local library use only — never for HTTP API |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| DuckDB (event store) | Opening multiple write connections from different threads/processes | Use a single-writer connection with an async write queue; separate read connection pool |
| Kuzu / graph engine | Assuming graph schema migrations are easy (adding node/edge types) | Design the schema with extension points. Kuzu (and forks) require explicit table creation for new types. Plan for schema evolution. |
| HNSW vector index | Building the index once and assuming it stays valid | Index must be rebuilt when: embedding model changes, HNSW parameters are tuned, or data volume crosses thresholds. Automate rebuild triggers. |
| External embedding API (OpenAI, Voyage) | Calling the API during event replay/rebuild | Cache embedding results. On rebuild, use cached embeddings unless the model version has changed. Store embeddings alongside events. |
| LLM extraction service | Assuming consistent output across LLM versions/providers | Pin LLM version for extraction. Cache extraction results as derived events. Accept that changing LLM version = re-extraction of everything. |
| FTS5/Tantivy (lexical index) | Assuming default tokenizers handle all content types equally | Configure tokenizers for the specific content domain (conversational text has different patterns than documents). Test with real conversation data. |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Unbounded graph traversal | Retrieval latency spikes on queries about well-connected entities | Cap traversal depth (1-3 hops). Use query-time budget. Precompute neighborhoods for high-degree nodes. | >1,000 edges on a single node |
| Full corpus re-embedding on model change | Hours/days of compute; system unavailable during migration | Batch re-embedding with dual-index strategy. Continue serving from old index during migration. | >100K embedded documents |
| Event log growth without compaction | DuckDB queries over event store slow down; disk usage grows linearly forever | Implement archival policy. Use DuckDB partitioning by time range. Phase 2 summarization replaces raw events for retrieval. | >1M events (months of active use) |
| Naive salience recalculation | Organizer job takes longer than its schedule interval; system falls behind | Incremental salience updates (only recalculate for entities touched since last run). Use materialized scores, not recomputation from scratch. | >10K entities with >100K edges |
| Context packing exceeds token budget | LLM receives truncated or overflowing context; retrieval technically works but downstream LLM performs poorly | Enforce hard token budget. Prioritize by salience * relevance score. Measure actual token count, not estimated. | Dense entity neighborhoods with many stable facts |
| HNSW index size in memory | Memory usage grows with corpus; OOM on resource-constrained machines | Monitor index memory usage. Consider disk-backed ANN alternatives (e.g., DiskANN) for large corpora. Set memory budget limits. | >500K vectors at 1536 dimensions (~3GB) |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Storing embedding API keys in event log metadata | API keys persisted in append-only log — cannot be deleted by design | Never include secrets in event payloads. Use environment variables or a separate secrets manager. Validate event content before append. |
| Encryption key derivation from user password without KDF | Weak encryption; vulnerable to brute force | Use Argon2id or similar KDF. DuckDB v1.4 supports AES-GCM-256 natively — use it with proper key management. |
| Exposing graph structure through API without access control | Internal entity relationships visible to any API consumer | Implement namespace-based access control. Memory scopes (personal/project/org) must map to authorization boundaries. |
| LLM extraction prompts containing user PII sent to external API | User data leaves local-first boundary when using cloud extraction | Use local models for extraction when possible. When using external APIs, strip or anonymize PII before sending. Document data flows. |
| Portable artifact shared without encryption | Raw memory (events, graph, vectors) readable by anyone with the file | Default to encryption-on for portable artifacts. Warn or block export without encryption. Phase 3 encryption must be mandatory for sharing. |
| Provenance references that leak conversation content | Audit trail exposes raw messages even after summarization/archival | Provenance should reference event IDs, not inline content. Content access should go through the same access control as events. |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Returning superseded facts as current | User told the system their new job; system still mentions old job | Filter superseded facts from default retrieval. Only include them when query is explicitly temporal ("What was my job in 2024?") |
| Over-eager entity merging | "John" the user's friend and "John" from a different conversation get merged into one entity | Require high confidence for entity merging. Keep separate entities until evidence is strong. Allow manual disambiguation. |
| Including raw events in context when summaries exist | Context window wastes tokens on verbose conversation history instead of concise facts | Prefer summaries and stable facts over raw events. Only include raw events when summary doesn't exist or query requires exact wording. |
| Memory system contradicts itself | Two active facts say opposite things; LLM downstream gets confused | Surface contradictions explicitly in retrieval output. Flag them for resolution rather than silently including both. |
| Cold start produces empty/useless responses | New user has no memory; system returns nothing useful | Provide graceful degradation — when memory is sparse, return what exists with confidence indicators. Don't return empty context bundles. |
| Slow retrieval on first query after organizer runs | Organizer holds locks; retrieval blocks | Run organizer during low-usage windows. Use read replicas or snapshot isolation so reads don't block on organizer writes. |

## "Looks Done But Isn't" Checklist

- [ ] **Event store:** Appending events works, but content_hash is not validated on read — verify that corrupted events are detectable
- [ ] **Graph schema:** Nodes and edges exist, but temporal validity windows (`valid_from`/`valid_to`) are not enforced in queries — verify that retrieval filters by current time
- [ ] **Vector search:** Similarity search returns results, but embedding model version is not checked — verify that all vectors in a result set come from the same model
- [ ] **Hybrid retrieval:** All three sources return candidates, but re-ranking weights are hardcoded — verify that weights are configurable and versioned
- [ ] **Supersedence:** SUPERSEDES edges exist in graph, but superseded facts still appear in default retrieval — verify that retrieval filters superseded items
- [ ] **Determinism:** Same query returns same results today, but no test verifies rebuild-from-log produces identical state — verify end-to-end rebuild determinism
- [ ] **Context packing:** Memory bundles are assembled, but token count is estimated, not measured — verify with actual tokenizer for target LLM
- [ ] **Organizer scheduling:** Organizer runs periodically, but overlapping runs are not prevented — verify mutual exclusion on organizer jobs
- [ ] **Encryption at rest:** DuckDB file is encrypted, but graph store directory and vector index files are not — verify all artifact components are encrypted
- [ ] **Portable artifact:** Manifest exists, but does not record extraction logic version or scoring weight version — verify manifest captures all versions needed for deterministic rebuild

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Kuzu fork becomes unmaintained | MEDIUM | Migrate to alternative engine via graph store abstraction layer. If abstraction was skipped: HIGH cost rewrite. |
| Corrupted graph from LLM extraction hallucinations | LOW (if append-only is preserved) | Re-run extraction pipeline from event log with improved logic. Graph is fully derived — rebuild from scratch. |
| Embedding model mismatch in vector index | MEDIUM | Trigger full re-embedding from event log. Use dual-index during migration. Downtime depends on corpus size. |
| Broken deterministic rebuild | HIGH | Audit every transformation for non-determinism. Add version metadata retroactively. May require re-processing all events with versioned logic. |
| Supersedence incorrectly applied | LOW (if chain preserved) | Query supersedence chain. Reactivate incorrectly superseded facts. If temporal windows were lost: HIGH cost manual reconstruction. |
| DuckDB write contention under load | LOW | Implement write queue. Requires HTTP layer refactor but no data migration. |
| Context packing exceeds token budget | LOW | Adjust scoring weights and token budget. Add hard truncation with priority ordering. No data changes needed. |
| Event log grows unbounded | MEDIUM | Implement archival policy. Partition old events. Summarization (Phase 2) reduces retrieval dependency on old events. Cannot delete events without violating append-only. |
| Encryption key lost for portable artifact | UNRECOVERABLE | No recovery possible. Prevention only: key escrow, backup keys, clear documentation of key management procedures. |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Kuzu abandonment risk | Phase 1 | Graph store abstraction interface exists; engine can be swapped with config change |
| LLM-on-write extraction corruption | Phase 1 | Extraction is a derived process; raw events stored first; extraction results cached as derived events |
| Embedding model lock-in | Phase 1 | Every vector has model metadata; re-embedding can be triggered from config; dual-index test passes |
| Deterministic rebuild drift | Phase 1 design, Phase 3 validate | CI test rebuilds from event log and compares to live state; all transformations have version metadata |
| Supersedence logic errors | Phase 1 schema, Phase 2 logic | Temporal queries return correct facts for historical time windows; supersedence chains are queryable |
| DuckDB write contention | Phase 1 | Load test with 10+ concurrent writers passes; write queue serializes appends |
| Context token overflow | Phase 1 retrieval, Phase 2 summarization | Context bundles measured with actual tokenizer; hard budget enforced; summaries preferred over raw events |
| Unbounded graph traversal | Phase 1 | Graph queries have depth/time budgets; high-degree nodes have precomputed neighborhoods |
| Event log growth | Phase 2 | Archival policy implemented; DuckDB partitioned by time; summarization reduces retrieval dependency on old events |
| Encryption gaps in artifact | Phase 3 | All artifact components (DuckDB, graph dir, vector files, manifest) encrypted; export without encryption blocked |
| Cold start empty context | Phase 1 | Graceful degradation tested; sparse memory returns what exists with confidence indicators |

## Sources

- [KuzuDB abandoned — The Register (Oct 2025)](https://www.theregister.com/2025/10/14/kuzudb_abandoned/)
- [RyuGraph — Kuzu fork by Predictable Labs](https://github.com/predictable-labs/ryugraph)
- [DuckPGQ — Graph extension for DuckDB (Oct 2025)](https://gdotv.com/blog/weekly-edge-kuzu-forks-duckdb-graph-cypher-24-october-2025/)
- [Universal LLM Memory Does Not Exist — Fastpaca](https://fastpaca.com/blog/memory-isnt-one-thing)
- [Zep: Temporal Knowledge Graph Architecture for Agent Memory (Jan 2025)](https://arxiv.org/pdf/2501.13956)
- [Mem0: Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/html/2504.19413v1)
- [AI Memory Tools Evaluation — Cognee](https://www.cognee.ai/blog/deep-dives/ai-memory-tools-evaluation)
- [DuckDB Concurrency Documentation](https://duckdb.org/docs/stable/connect/concurrency)
- [DuckDB Data-at-Rest Encryption (Nov 2025)](https://duckdb.org/2025/11/19/encryption-in-duckdb)
- [Event Sourcing 101: Pitfalls — Innovecs](https://innovecs.com/blog/event-sourcing-101-when-to-use-and-how-to-avoid-pitfalls/)
- [3 Killer Event Sourcing Mistakes (2025)](https://junkangworld.com/blog/3-killer-event-sourcing-mistakes-you-must-avoid-in-2025)
- [Event Sourcing Consistency — SoftwareMill](https://softwaremill.com/things-i-wish-i-knew-when-i-started-with-event-sourcing-part-2-consistency/)
- [Deterministic Replay for Trustworthy AI — SakuraSky](https://www.sakurasky.com/blog/missing-primitives-for-trustworthy-ai-part-8/)
- [Embedding Model Migration: Hidden Cost of Upgrades — Medium](https://medium.com/data-science-collective/different-embedding-models-different-spaces-the-hidden-cost-of-model-upgrades-899db24ad233)
- [Embedding Versioning in Production — Zilliz](https://zilliz.com/ai-faq/how-do-i-handle-versioning-of-embedding-models-in-production)
- [Context Rot: How Increasing Input Tokens Impacts LLM Performance — Chroma Research](https://research.trychroma.com/context-rot)
- [Context Length Alone Hurts LLM Performance Despite Perfect Retrieval (2025)](https://arxiv.org/html/2510.05381v1)
- [Hybrid RAG: Boosting Accuracy in 2026 — AI Multiple](https://research.aimultiple.com/hybrid-rag/)
- [The Ultimate RAG Blueprint 2025/2026 — LangWatch](https://langwatch.ai/blog/the-ultimate-rag-blueprint-everything-you-need-to-know-about-rag-in-2025-2026)
- [LLM-empowered Knowledge Graph Construction Survey (2025)](https://arxiv.org/html/2510.20345v1)
- [Knowledge Graph Extraction Challenges — Neo4j (2025)](https://neo4j.com/blog/developer/knowledge-graph-extraction-challenges/)
- [Memory in the Age of AI Agents: Survey paper list](https://github.com/Shichun-Liu/Agent-Memory-Paper-List)

---
*Pitfalls research for: Local-first LLM memory engine with event sourcing, graph modeling, hybrid retrieval, and self-organization*
*Researched: 2026-02-19*
