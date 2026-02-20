# RMS Revised RFC Suite — Index

**Suite Version:** 1.0
**Date:** 2026-02-19
**Status:** Draft

---

## Overview

This index covers the complete Revised Relational Memory Substrate (RMS) RFC suite. This suite was produced following a full scientific audit of the original BlindspotRFCs series (RFC-0001 through RFC-0015). It addresses the structural weaknesses identified in that audit while preserving and strengthening the genuinely valuable architectural ideas.

**Key changes from the original suite:**
- RFC-0013 (Emotional and Behavioral Signal Tracking) removed entirely. Replaced with RFC-0013 (Intent and Goal Memory), which addresses a real need without the privacy and epistemic risks.
- Determinism requirements scoped correctly: strict determinism at the storage/derivation layer; `[BEST-EFFORT]` reproducibility at the extraction layer.
- All unvalidated parameter choices are explicitly marked `[HYPOTHESIS]` with benchmark requirements before promotion.
- Merge conflict resolution fully specified (the original suite deferred this).
- Agent trust model redesigned as domain-scoped rather than scalar.
- Resource limits and garbage collection added to branching.
- Reference implementation requirement added as a gate before any RFC exits Draft status.

---

## RFC Listing

### Tier 0 — Foundation

| RFC | Title | Status | File |
|---|---|---|---|
| RFC-0000 | Suite Overview and Design Philosophy | Informational | [RFC-0000-Suite-Overview.md](RFC-0000-Suite-Overview.md) |
| RFC-0001 | Core Data Model and Terminology | Draft | [RFC-0001-Core-Data-Model.md](RFC-0001-Core-Data-Model.md) |

### Tier 1 — Storage and Integrity

| RFC | Title | Status | File |
|---|---|---|---|
| RFC-0002 | Event Store and Append-Only Log | Draft | [RFC-0002-Event-Store.md](RFC-0002-Event-Store.md) |
| RFC-0003 | Epistemic State Model | Draft | [RFC-0003-Epistemic-State-Model.md](RFC-0003-Epistemic-State-Model.md) |
| RFC-0004 | Namespace and Scope Isolation | Draft | [RFC-0004-Namespace-and-Scope-Isolation.md](RFC-0004-Namespace-and-Scope-Isolation.md) |

### Tier 2 — Retrieval

| RFC | Title | Status | File |
|---|---|---|---|
| RFC-0005 | Hybrid Retrieval Pipeline | Draft | [RFC-0005-Hybrid-Retrieval-Pipeline.md](RFC-0005-Hybrid-Retrieval-Pipeline.md) |
| RFC-0006 | Retrieval Cost and Context Efficiency | Draft | [RFC-0006-Retrieval-Cost-and-Context-Efficiency.md](RFC-0006-Retrieval-Cost-and-Context-Efficiency.md) |

### Tier 3 — Lifecycle

| RFC | Title | Status | File |
|---|---|---|---|
| RFC-0007 | Decay and Forgetting Model | Draft | [RFC-0007-Decay-and-Forgetting.md](RFC-0007-Decay-and-Forgetting.md) |
| RFC-0008 | Confidence Evolution and Reinforcement | Draft | [RFC-0008-Confidence-Evolution.md](RFC-0008-Confidence-Evolution.md) |
| RFC-0009 | Memory Usage Feedback Loop | Draft | [RFC-0009-Memory-Usage-Feedback-Loop.md](RFC-0009-Memory-Usage-Feedback-Loop.md) |

### Tier 4 — Advanced Capabilities

| RFC | Title | Status | File |
|---|---|---|---|
| RFC-0010 | Temporal Pattern Awareness | Draft | [RFC-0010-Temporal-Pattern-Awareness.md](RFC-0010-Temporal-Pattern-Awareness.md) |
| RFC-0011 | Multi-Agent Memory Semantics | Draft | [RFC-0011-Multi-Agent-Memory-Semantics.md](RFC-0011-Multi-Agent-Memory-Semantics.md) |
| RFC-0012 | Memory Branching and Simulation | Draft | [RFC-0012-Memory-Branching-and-Simulation.md](RFC-0012-Memory-Branching-and-Simulation.md) |
| RFC-0013 | Intent and Goal Memory | Draft | [RFC-0013-Intent-and-Goal-Memory.md](RFC-0013-Intent-and-Goal-Memory.md) |
| RFC-0014 | Portability, Sync, and Federation | Draft | [RFC-0014-Portability-Sync-and-Federation.md](RFC-0014-Portability-Sync-and-Federation.md) |

---

## Dependency Graph

```
RFC-0000
    │
    ▼
RFC-0001
    │
    ├──► RFC-0002
    │        │
    │        ├──► RFC-0003
    │        │        │
    │        │        └──► RFC-0004
    │        │                 │
    │        │                 └──► RFC-0005
    │        │                          │
    │        │                          └──► RFC-0006
    │        │                                   │
    │        │                    ┌──────────────┘
    │        │                    ▼
    │        └───────────────► RFC-0007
    │                              │
    │                              ├──► RFC-0008
    │                              │        │
    │                              │        └──► RFC-0009
    │                              │                 │
    │                              └─────────────────┴──► RFC-0010
    │                                                     RFC-0011
    │                                                     RFC-0012
    │                                                     RFC-0013
    │                                                     RFC-0014
```

---

## Conformance Summary

An implementation claiming conformance at a given tier MUST satisfy all requirements at that tier and all tiers below it.

| Tier | Minimum RFCs | Description |
|---|---|---|
| Tier 0 | RFC-0000, RFC-0001 | Core data model only. No storage, retrieval, or lifecycle. |
| Tier 1 | + RFC-0002, 0003, 0004 | Persistent, epistemically-typed, namespace-isolated storage. |
| Tier 2 | + RFC-0005, 0006 | Hybrid retrieval with context-efficient bundling. |
| Tier 3 | + RFC-0007, 0008, 0009 | Adaptive lifecycle: decay, confidence evolution, feedback. |
| Tier 4 | + Any of RFC-0010–0014 | Advanced capabilities. Each is independently optional. |

A system claiming "Tier 2 conformance" has implemented Tiers 0, 1, and 2 — not merely RFC-0002.

---

## What Was Removed and Why

| Original RFC | Disposition | Reason |
|---|---|---|
| RFC-0013 (Emotional/Behavioral Signal Tracking) | Removed | Text-derived emotional signal inference is epistemically unsound, privacy-threatening, and technically infeasible under determinism requirements. See Audit Report for full analysis. |
| RFC-0002 (Git Sync Profile) — original | Replaced by RFC-0014 | Original was well-motivated but had unresolved merge conflict semantics and no federation model. Upgraded and relocated to Tier 4. |

---

## Key Design Decisions

**Why is `[HYPOTHESIS]` used so frequently?**

Because the original suite asserted confidence values, decay weights, scoring weights, and threshold values without empirical grounding. In a system that claims to model epistemic confidence, asserting design parameters without epistemic justification is a contradiction. `[HYPOTHESIS]` marks are requirements for future empirical validation, not weaknesses.

**Why no emotional signal tracking?**

Text analysis can detect linguistic patterns associated with emotional expression. It cannot detect emotions. Treating the proxy as the thing is a category error. Acting on inferred emotional state without user consent and without reliability validation is harmful. The intent and goal memory in RFC-0013 addresses the legitimate underlying need (understanding what the user cares about and is working toward) without this conflation.

**Why is determinism scoped?**

The original suite required determinism everywhere, including in LLM-based extraction pipelines. This is not achievable: modern LLMs are non-deterministic at the hardware level even with temperature=0, and different hardware produces different floating-point results. Scoping determinism to the storage and derivation layer (where it is achievable and matters for auditability) while using `[BEST-EFFORT]` semantics for extraction (where strict determinism is not achievable) is an honest specification.

**Why tiers?**

Because the original suite implicitly required all features simultaneously. A minimal useful memory system needs only Tiers 0-2. Many deployments will not need branching, federation, or temporal pattern awareness. Tiering allows incremental adoption and reduces the compliance burden for simple use cases.

---

*End of Index*
