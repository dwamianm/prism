# PRME — Future Enhancement Todos

Items identified from competitive analysis and scientific research (2026-03-08).
These are strategic enhancements to make PRME the best AI/LLM memory system available.

---

## Next-Level Features

### TODO-001: Surprise-Gated Storage
**Priority:** High | **Effort:** Medium | **Source:** Titans (Google, ICLR 2025)
**Description:** During ingestion, compute how "surprising" a new fact is relative to existing knowledge using KL-divergence or semantic distance from existing embeddings. High-surprise facts get higher initial salience; low-surprise/redundant facts merge with existing nodes or receive lower priority. Mimics the hippocampal novelty signal in neuroscience.
**Why it matters:** Dramatically reduces noise and improves retrieval precision. Current systems store everything equally — this makes storage intelligent.
**Implementation hook:** Compute semantic distance from existing facts during `store()` using the existing embedding infrastructure.
**Research refs:** Titans (arXiv 2501.00663), ATLAS (Behrouz et al. 2025)

### TODO-002: Bi-Temporal Data Model
**Priority:** High | **Effort:** Low | **Source:** Zep/Graphiti (arXiv 2501.13956)
**Description:** Distinguish between *event_time* (when something actually happened) and *ingestion_time* (when the system learned about it). Enables point-in-time knowledge snapshots ("what did we know at time T?"). Critical for debugging memory behavior and for legal/medical domains.
**Why it matters:** You might learn today that something happened last week. Without bi-temporal modeling, the system conflates "when it happened" with "when it was recorded."
**Implementation hook:** Add `event_time` field alongside existing timestamp on Event model. Schema migration + query support.

### TODO-003: Predictive Forgetting / Consolidation Pipeline
**Priority:** High | **Effort:** High | **Source:** Neuroscience (arXiv 2603.04688, March 2026)
**Description:** Extend `organize()` to identify clusters of episodic memories that share a pattern and proactively abstract them into schema/summary nodes while archiving individual episodes. Forgetting isn't failure — it's optimization. The brain's sleep consolidation selectively forgets details to maintain a better model of the world.
**Why it matters:** Current summarization (daily→weekly→monthly) is time-based. Predictive forgetting is pattern-based — it abstracts when there's enough evidence, not on a schedule.
**Implementation hook:** New organize() job that clusters semantically similar episodic memories, generates abstractions, and archives source episodes.
**Research refs:** "Why the Brain Consolidates" (arXiv 2603.04688), Bayesian Continual Learning (Nature Comms 2025), EverMemOS (2026)

### TODO-004: Procedural Memory Node Type
**Priority:** Medium | **Effort:** Medium | **Source:** LangMem (LangChain)
**Description:** Add an `Instruction` or `Rule` node type representing learned behavioral patterns — not facts about the world, but learned rules about how to behave. Examples: "this user prefers concise answers", "always check the database before responding." These are surfaced as system-level context during retrieval rather than factual content.
**Why it matters:** Enables PRME to not just remember *what happened* but learn *how to behave*. No other graph-based memory system has this.
**Implementation hook:** New NodeType enum value, retrieval packing logic to inject as system instructions, reinforcement via RFC-0008 signals.

### TODO-005: Memory Quality Self-Assessment & Auto-Tuning
**Priority:** Medium | **Effort:** High | **Source:** MEMTRACK benchmark (Patronus AI)
**Description:** Go beyond RFC-0009's feedback loop. Track not just whether a memory was *used* after being surfaced, but whether the *right* memory was surfaced. When the user corrects the system or the response contradicts surfaced memories, use that signal to auto-tune retrieval scoring weights over time.
**Why it matters:** MEMTRACK found that even when LLMs have memory tools, they frequently fail to use them effectively. The memory system itself needs to learn what "good retrieval" looks like for its specific use case.
**Implementation hook:** Extend feedback session tracking (RFC-0009) with correction detection, weight adjustment via gradient-free optimization on scoring config.
**Research refs:** MEMTRACK benchmark, MemRewardBench (arXiv 2601.11969)

### TODO-006: Dual-Stream Ingestion (Fast/Slow Path)
**Priority:** Medium | **Effort:** Medium | **Source:** MAGMA (arXiv 2601.03236, Jan 2026)
**Description:** Formalize the ingestion pipeline into a guaranteed fast path (sub-50ms: append to event store + update vector index) and a queued slow path (entity extraction, graph writes, supersedence detection) executed during next `retrieve()` or `organize()` call via opportunistic maintenance.
**Why it matters:** Latency guarantee for the write path. Current 2-phase ingestion already does this informally — formalizing it enables SLA commitments.
**Implementation hook:** Split `ingest()` into `ingest_fast()` (event + vector only) and queue graph materialization for opportunistic maintenance (RFC-0015 Layer 2).
**Research refs:** MAGMA dual-stream architecture

### TODO-007: Benchmark Suite (LoCoMo + LongMemEval + Epistemic)
**Priority:** High | **Effort:** High | **Source:** Multiple benchmarks
**Description:** Build an evaluation harness targeting three benchmark families:
1. **LoCoMo** — Long-conversation memory over 300+ turns
2. **LongMemEval** (ICLR 2025) — 5 core abilities: extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention
3. **Custom epistemic benchmark** — Test supersedence correctness, confidence calibration, contradiction detection, belief revision accuracy

No existing benchmark tests epistemic state management. Building and publishing this benchmark defines the evaluation criteria for the field.
**Why it matters:** Can't claim "best" without numbers. And by defining the epistemic benchmark, PRME sets the standard others are measured against.
**Research refs:** LoCoMo (SNAP Research), LongMemEval (ICLR 2025), MEMTRACK, MemBench (ACL 2025)

---

## Competitive Intelligence

### Key competitors to track:
- **Mem0** — 66.9% accuracy, graph+vector, conflict detection. Paper: arXiv 2504.19413
- **Zep/Graphiti** — Temporal KG, bi-temporal model, P95 300ms. Paper: arXiv 2501.13956
- **Letta (MemGPT)** — Self-editing memory, LLM-as-OS paradigm
- **Cognee** — Memify self-improving pipeline, Kuzu+LanceDB
- **MemOS** — Memory Operating System abstraction (May 2025)
- **MAGMA** — Multi-graph, dual-stream, outperforms SOTA on LoCoMo/LongMemEval (Jan 2026)
- **EverMemOS** — Self-organizing memory OS, precise forgetting (Jan 2026)

### PRME's unique differentiators (no competitor has these):
1. Epistemic lifecycle (Tentative→Stable→Superseded→Archived) with formal state machine
2. Deterministic, reproducible retrieval with versioned scoring weights
3. Append-only event sourcing with SHA-256 integrity chain
4. Virtual decay (no background process, portable by design)
5. Oscillation/flip-flop detection for supersedence cycles
6. Provenance tracking on every memory object

### Where competitors are ahead:
- Zep: bi-temporal data model (→ TODO-002)
- Cognee: self-improving Memify pipeline (→ TODO-005)
- Letta: self-editing memory / agent manages own memory
- LangMem: procedural memory (→ TODO-004)
- MAGMA: dual-stream ingestion (→ TODO-006)

---

## Research Papers to Monitor

| Paper | Year | Key Insight | Relevance |
|---|---|---|---|
| Titans (Google) | 2025 | Surprise-gated memory updates via KL divergence | TODO-001 |
| Zep/Graphiti | 2025 | Bi-temporal knowledge graphs | TODO-002 |
| "Why the Brain Consolidates" | 2026 | Predictive forgetting = optimization | TODO-003 |
| MEMTRACK | 2025 | LLMs fail to use memory tools effectively | TODO-005 |
| MAGMA | 2026 | Dual-stream fast/slow ingestion | TODO-006 |
| A-MEM (Zettelkasten) | 2025 | Dynamic inter-memory linking | General |
| Memory in Age of AI Agents | 2025 | 3D taxonomy: forms, functions, dynamics | General |
| Belief Revision in LLMs | 2024 | LLMs fall short of Bayesian belief revision | Epistemic model validation |
| Knowledge Conflicts Survey | 2024 | 3 conflict types, LLM bias patterns | Contradiction modeling |
| Neural ODEs + Memory | 2025 | 24% forgetting reduction | TODO-003 |
| Spaced Repetition (PNAS) | 2019 | Optimal review = recall probability | RFC-0008 reinforcement tuning |
