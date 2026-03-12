# PRME System Evaluation — 2026-03-12

## Overview

Two independent assessments: the VSA research prototype (research/) and the main multi-store system (src/prme/). This document captures the honest findings.

---

## Part 1: VSA Research Prototype (research/)

### Status: Phase 2 Complete, 101 tests passing

### What Works
- **Narrative Rewriter** is the real contribution — auto-generates clean settled facts from migration events without LLM calls
- Zero-LLM supersedence detection (100% accuracy on test set)
- Formal epistemic lifecycle (Tentative -> Stable -> Superseded -> Archived)

### What Doesn't Work
- **The VSA isn't doing the work.** Tags carry 50% of scoring weight. Content similarity from random vectors is noise (~0.01). If you removed all VSA operations and kept tag matching, you'd lose ~5-10% precision and gain 90% performance.
- **Benchmark is a toy.** 25 memories, 12 tag-aligned queries. No semantic variation, no paraphrasing, no scale testing.
- **Random codebook has no semantics.** "MySQL" and "database" are as dissimilar as "dog" and "quark." Real embeddings (word2vec, BERT) capture semantic relationships. Ours don't.
- **Scalability is O(N x d).** Linear scan over all memories. At 100K memories with dim=10,000, ~1 second per query. No indexing.
- **Memory footprint is ~120KB per record** (three 10K-float vectors). At 100K = 12GB. Not portable.

### Benchmark Comparison (Research)
| System | LoCoMo | LongMemEval | Memory Count |
|--------|--------|-------------|--------------|
| Hindsight/TEMPR | 89.6% | 91.4% | 1000+ |
| ENGRAM | 77.6% | — | Tested at scale |
| MemGPT/Letta | 74% | — | Unlimited |
| Mem0 (graph) | 68.4% | — | Production scale |
| Zep/Graphiti | — | 71.2% | Neo4j-backed |
| **PRME-X** | **untested** | **untested** | **25 memories** |

### Research Findings Worth Keeping
1. Narrative rewriting (auto settled facts) — unique, zero LLM cost
2. Epistemic lifecycle model — most rigorous in the field
3. Rule-based rewriting sufficient for common patterns
4. Typed memory separation is critical (confirmed by ENGRAM ablation)

### VSA Verdict
The VSA substrate is the wrong hill to die on. The memory *model* matters, not the vector algebra. Replace random codebook with real embeddings. Keep the narrative rewriter and epistemic lifecycle.

---

## Part 2: Main PRME System (src/prme/)

### Codebase Stats
- ~19K lines production code
- ~17K lines tests (1:1 ratio)
- ~4K lines simulations (18 scenarios)
- 841+ tests, 4 storage backends, 6-stage retrieval pipeline

### Architecture: All Four Stores Are Real

| Store | Technology | Status |
|-------|-----------|--------|
| Event Store | DuckDB | Complete — true append-only event sourcing |
| Graph Store | DuckDB (recursive CTEs) | Complete — real traversal, shortest path, supersedence chains |
| Vector Index | USearch HNSW | Complete — real approximate NN search |
| Lexical Index | Tantivy | Complete — real BM25 ranking |

### Competitive Comparison

| Feature | PRME | Mem0 | Zep/Graphiti | MemGPT | Hindsight |
|---------|------|------|-------------|--------|-----------|
| Storage | 4-store hybrid | Vector + Neo4j | Neo4j 3-layer | Tiered | 4 networks |
| Retrieval | True hybrid (4 parallel) | Vector + graph | Graph + temporal | Agent self-retrieval | Network-routed |
| Supersedence | SUPERSEDES + CONTRADICTS | LLM UPDATE/DELETE | Bi-temporal invalidation | Agent self-edit | Dynamic confidence |
| Epistemic model | 7 types, lifecycle FSM | None formal | None formal | None formal | 4 types |
| Event sourcing | True append-only | No | No | No | No |
| Decay model | Virtual (on-read), 5 profiles | No | No | No | No |
| Offline capable | DuckDB local-first | No | No | No | No |
| **Benchmark scores** | **Not tested** | 68.4% LoCoMo | 71.2% LongMemEval | 74% LoCoMo | **91.4% LongMemEval** |

### Genuine Strengths

1. **Most rigorous epistemic model in the field.** 7 types, formal lifecycle, confidence matrices, virtual decay. No competitor matches this.
2. **True event sourcing.** Only system where derived state is rebuildable from event log.
3. **Local-first with DuckDB.** Every competitor requires cloud DB or API.
4. **Virtual decay.** Computed on-read enables time-travel queries without background jobs.
5. **Dual-stream ingestion.** ingest_fast() with <50ms guarantee is a real production pattern.
6. **True hybrid retrieval.** 4 backends in parallel, merged by node_id, 8-input scoring. Not one store with decorators.

### Critical Weaknesses

#### 1. No Benchmark Scores (FATAL credibility gap)
Benchmark loaders exist but no results published. Without LoCoMo and LongMemEval numbers, every claim is unverified. This is the #1 priority.

#### 2. Write Queue Bottleneck (CRITICAL under load)
Single-consumer AsyncWriteQueue serializes ALL writes across all four stores. At 100+ concurrent writers, queue saturates (maxsize=1000), then blocks indefinitely. No backpressure, no circuit breaker, no shedding.

#### 3. LLM Extraction Dependency (HIGH — single point of failure)
If LLM provider is down, events persist but graph materialization fails. After 3 retries (~9 min), system gives up silently. Events become orphaned — exist in event store but invisible to retrieval. No rule-based fallback.

#### 4. Vector Index User Scoping (HIGH — multi-tenant recall)
USearch queried for 3x candidates, post-filtered by user_id. In multi-tenant (100+ users), relevant results pushed out by other users' top results. Hardcoded 3x overfetch doesn't adapt.

#### 5. No Vector Index Delete (HIGH — index corruption)
If materialization fails after vector indexing but before graph write, orphaned vector entries remain. Code explicitly acknowledges: `"rollback.orphaned_indexes"`. Lexical index has delete, vector index does not.

#### 6. CONTESTED State Accumulates (MEDIUM — knowledge incoherence)
Two contradicting facts both become CONTESTED. They stay CONTESTED forever. No voting, user feedback, or time-decay resolution. Conflicts compound.

#### 7. Floating-Point Determinism (MEDIUM — audit risk)
Scoring uses 8 float inputs with exp() transcendentals, rounded to 10 decimals. Never verified across full rebuild cycles or different architectures.

#### 8. Embedding Model Migration (MEDIUM — blocking upgrades)
No re-embedding job. Switching embedding models requires manual export, delete, re-embed from scratch. No migration path.

### Scalability Profile

| Scale | Event Store | Graph | Vector | Lexical | Retrieval |
|-------|-------------|-------|--------|---------|-----------|
| 1K nodes | OK | OK | OK | OK | <100ms |
| 10K nodes | OK | CTE depth risk | OK (HNSW) | OK | ~200ms |
| 100K nodes | OK | Slow (no DuckPGQ) | Post-filter false negatives | OK | ~500ms-1s |
| 1M nodes | OK | Needs PostgreSQL | Needs sharding | OK | >1s |

### Production Failure Timeline

| Failure Mode | Severity | Time to Failure |
|-------------|----------|-----------------|
| Write queue saturation | CRITICAL | Hours under load |
| Extraction failure cascade | HIGH | Minutes (9-min retry) |
| Vector index false negatives | HIGH | Days (as data grows) |
| Orphaned index entries | HIGH | Weeks |
| CONTESTED accumulation | MEDIUM | Weeks |
| Determinism divergence | MEDIUM | After first audit |

---

## Overall Verdict

PRME is **the most architecturally ambitious LLM memory system that exists**. The four-store hybrid, epistemic lifecycle, virtual decay, and event sourcing are genuinely novel in combination. No competitor has all of these.

But it's **unproven**. The architecture is a game-changer on paper. Paper doesn't ship.

### To Become a Real Competitor
1. Run LoCoMo and LongMemEval benchmarks
2. Fix write queue concurrency
3. Add fallback extraction (rule-based)
4. Fix vector index user scoping
5. Add vector index delete for rollback
6. Implement conflict resolution
7. Publish results

The architecture is sound. The implementation is solid for a prototype. The gap is validation and hardening.
