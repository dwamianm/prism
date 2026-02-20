# RFC-0000: Relational Memory Substrate (RMS) — Suite Overview and Design Philosophy

**Status:** Informational
**Version:** 1.0
**Date:** 2026-02-19
**Replaces:** N/A — This is the foundation document for the revised RFC suite.

---

## 1. Purpose of This Document

This document defines the governing philosophy, scope boundaries, and structural organisation of the Relational Memory Substrate (RMS) RFC suite. It MUST be read before any individual RFC.

The suite addresses a single, well-scoped engineering problem:

> **LLM-based systems that lack persistent, structured, and retrievable memory cannot maintain coherent behaviour across sessions, accumulate context about users or domains, or generalise knowledge over time. This RFC suite specifies a substrate that corrects this.**

That claim is deliberately narrower than "memory is the core of intelligence." The latter is a philosophical hypothesis; the former is a falsifiable engineering requirement. This suite is grounded in the engineering requirement.

---

## 2. The Central Engineering Claim

**Claim:** A persistent, structured, and selectively retrievable memory substrate is a necessary precondition for long-horizon coherent behaviour in LLM-based systems.

**What this claim does and does not assert:**

| Asserted | Not Asserted |
|---|---|
| Memory is necessary for multi-session coherence | Memory alone is sufficient for intelligence |
| Structured memory outperforms unstructured retrieval for long-horizon tasks | This architecture is the only valid approach |
| Epistemic typing prevents known LLM memory failure modes | Emotion or consciousness can be inferred from text signals |
| Decay + reinforcement produces more useful memory than static storage | This system will scale to arbitrary workloads without tuning |

**Validation requirement:** Every design decision in this suite MUST be testable. Where a claim cannot currently be empirically validated, it MUST be marked as `[HYPOTHESIS — requires experimental validation]` in the relevant RFC.

---

## 3. What Motivated This Suite

Current LLM memory systems fail in documented, reproducible ways:

1. **Recency collapse** — systems surface recent context at the expense of long-term relevant state.
2. **Epistemic conflation** — inferences are treated identically to direct observations; contradictions are silently overwritten.
3. **No forgetting** — stale, superseded, or irrelevant facts are retrieved indefinitely.
4. **Context saturation** — verbose retrieval exhausts context budgets before high-value information is included.
5. **Portability failure** — memory state is tied to a single service endpoint or session and cannot be moved, audited, or rebuilt.
6. **No feedback loop** — the system cannot learn which memories were useful and which were not.

This suite addresses all six. Each failure mode maps to one or more RFCs.

---

## 4. Suite Structure

The RFC suite is organised into five tiers. Tiers are sequential: each tier depends on the one before it. An implementation MUST NOT claim conformance to a higher tier without satisfying the tier below it.

### Tier 0 — Foundation (this document)

Defines the problem scope, design philosophy, and terminology shared across the suite.

- RFC-0000 (this document): Suite Overview and Design Philosophy
- RFC-0001: Core Data Model and Terminology

### Tier 1 — Storage and Integrity

Defines how memory is stored, sourced, and kept internally consistent.

- RFC-0002: Event Store and Append-Only Log
- RFC-0003: Epistemic State Model
- RFC-0004: Namespace and Scope Isolation

### Tier 2 — Retrieval

Defines how memory is retrieved, ranked, and packed into context efficiently.

- RFC-0005: Hybrid Retrieval Pipeline
- RFC-0006: Retrieval Cost and Context Efficiency

### Tier 3 — Lifecycle

Defines how memory evolves, ages, and improves over time.

- RFC-0007: Decay and Forgetting Model
- RFC-0008: Confidence Evolution and Reinforcement
- RFC-0009: Memory Usage Feedback Loop

### Tier 4 — Advanced Capabilities

Defines capabilities that require a fully operational Tier 0–3 system as a prerequisite. These RFCs carry a higher experimental burden and are explicitly marked where claims are unvalidated.

- RFC-0010: Temporal Pattern Awareness
- RFC-0011: Multi-Agent Memory Semantics
- RFC-0012: Memory Branching and Simulation
- RFC-0013: Intent and Goal Memory
- RFC-0014: Portability, Sync, and Federation

> **Note:** The emotional signal tracking concept from the original RFC-0013 has been removed from this suite. Text-derived emotional inference was determined to be epistemically unsound, privacy-threatening, and technically infeasible under the determinism requirements of this suite. It is not addressed in any revised RFC.

---

## 5. Non-Goals

The following are explicitly out of scope for this suite:

- **Model training or fine-tuning.** RMS operates at inference time only.
- **Emotional state inference from text.** Proxy signals are not emotional states.
- **Replacing the LLM's in-context reasoning.** RMS prepares the context; it does not reason.
- **General artificial intelligence.** This is a memory substrate for a specific class of systems.
- **Real-time sub-millisecond retrieval.** RMS targets assistant-grade latency (tens to low hundreds of milliseconds).

---

## 6. Core Design Principles

These principles apply to every RFC in the suite. Any RFC that conflicts with a principle MUST explicitly justify the conflict.

**P1 — Append-Only Truth**
The event log is the single source of truth. All derived state is exactly that: derived. It can always be discarded and rebuilt.

**P2 — Scoped Determinism**
Determinism is required at the storage and derivation layer. Extraction pipelines (entity extraction, classification, embedding) operate under best-effort reproducibility, not strict determinism. The distinction MUST be explicit.

**P3 — Epistemic Honesty**
Memory objects MUST carry their epistemic status. An inference is not an observation. A hypothesis is not a fact. A deprecated belief must not be silently resurrected.

**P4 — Forgetting Is a Feature**
A memory system that never forgets degrades over time. Decay, reinforcement, and selective retention are first-class capabilities, not afterthoughts.

**P5 — Cost Awareness**
Every memory object has a retrieval cost measured in tokens. Retrieval optimises for utility per token, not relevance alone.

**P6 — Empirical Humility**
Where a design choice cannot currently be validated with data, it MUST be labelled as such. The suite does not assert that its default parameters are optimal.

**P7 — Portability by Design**
Memory state MUST be expressible as a copyable, encryptable, versionable artifact. No memory must depend on a specific service endpoint to be valid.

**P8 — Fail Safely**
When retrieval fails, the system MUST degrade to no-memory behaviour, not to incorrect-memory behaviour. Incorrect context is more harmful than absent context.

---

## 7. Terminology

The following terms are used consistently across all RFCs. Deviations in individual RFCs are errors.

| Term | Definition |
|---|---|
| **Event** | An immutable, timestamped record of something that occurred or was asserted. The atomic unit of the event log. |
| **Memory Object** | A derived, structured representation of one or more events. May be a Fact, Decision, Preference, Task, Summary, or Intent. |
| **Epistemic Type** | The classification of how a memory object is known (e.g., OBSERVED, INFERRED, HYPOTHETICAL). |
| **Namespace** | A named, isolated scope that governs visibility, access, and policy for a set of memory objects. |
| **Salience** | A computed score representing the current importance of a memory object for retrieval purposes. |
| **Confidence** | A score in [0.0, 1.0] representing the system's current degree of belief in a memory object's accuracy. |
| **Decay** | The scheduled reduction of salience or confidence for memory objects that are not reinforced. |
| **Reinforcement** | An update that increases the salience or confidence of a memory object based on evidence of its usefulness or correctness. |
| **STR** | Signal-to-Token Ratio. A composite metric representing the retrieval utility of a memory object relative to its token cost. |
| **Extraction Pipeline** | A process (typically LLM-assisted) that converts raw events into structured memory objects. Operates under best-effort reproducibility. |
| **Canonical Branch** | The primary, authoritative memory context. All non-canonical branches derive from it. |
| **Operation** | A structured record of an action taken on the memory system (e.g., ASSERT, DEPRECATE, DECAY, REINFORCE). Stored in the event log. |

---

## 8. Conformance Levels

RFC language follows RFC 2119 conventions (MUST, SHOULD, MAY). In addition, this suite uses:

- **`[REQUIRED FOR TIER N]`** — this behaviour is mandatory for a conforming Tier N implementation.
- **`[HYPOTHESIS]`** — this claim is unvalidated and requires experimental evidence before being promoted to a requirement.
- **`[BEST-EFFORT]`** — this property is targeted but not strictly guaranteed due to extraction pipeline non-determinism.

---

## 9. Reference Implementation Requirement

No RFC in this suite progresses from Draft to Experimental without:

1. A working reference implementation of all RFCs in the same tier or below.
2. At least one benchmark comparing the implemented behaviour against a defined baseline.
3. A published test suite that covers all MUST-level conformance requirements in the RFC.

This requirement exists because the original RFC suite was found to contain numerous claims that are architecturally plausible but empirically unvalidated. Plausibility is not sufficient.

---

## 10. Relationship to Prior Work

This suite is informed by and distinct from:

- **MemGPT / Letta** — pioneered external memory management for LLMs; RMS extends with epistemic typing, decay, and portability.
- **Zep** — graph-based memory for agents; RMS adds decay, feedback loops, and scoped determinism.
- **LangChain / LlamaIndex memory modules** — useful but stateless across sessions by default; RMS is session-persistent by design.
- **Event sourcing (CQRS pattern)** — RMS adopts event sourcing from distributed systems; the application to AI memory is novel.
- **Ebbinghaus forgetting curve (1885)** — the decay model in RFC-0007 is grounded in this empirical finding.
- **Tulving (1972) — episodic vs semantic memory** — informs the memory object taxonomy in RFC-0001.

---

*End of RFC-0000*
