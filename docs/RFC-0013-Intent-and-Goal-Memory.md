# RFC-0013: RMS Intent and Goal Memory

**Status:** Draft
**Tier:** 4 — Advanced Capabilities
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000 through RFC-0009

---

## 1. Abstract

This RFC specifies Intent and Goal Memory: the representation of user and agent goals, commitments, open questions, and decisions-in-progress as first-class memory objects with their own lifecycle, dependency model, and retrieval semantics.

Intent objects are distinct from factual memory in a fundamental way: they are future-directed. A FACT describes what is or was. An INTENT describes what is intended to be.

This RFC replaces the emotional signal tracking concept from the original RFC-0013, which was removed from the suite due to the following irresolvable problems:
- Text-derived "emotional signals" are statistical proxies, not emotional states. Conflating them is a category error.
- Tracking inferred emotional state without explicit user consent is a privacy violation.
- Deterministic emotional signal extraction from LLM outputs is not achievable with current techniques.

Goal and intent memory addresses a real need (tracking what the user is trying to accomplish) without these problems.

---

## 2. Intent Object Type

The INTENT memory object type is defined here as an extension of the core model (RFC-0001, Section 4).

```
IntentObject {
  -- Core fields (RFC-0001)
  id, type=INTENT, version, epistemic_type, source_type, evidence_ids,
  asserted_by, valid_from, valid_to, created_at, last_modified_at,
  lifecycle_state, superseded_by, confidence, salience, namespace_id,
  value, structured_value

  -- Intent-specific fields
  intent_type:          IntentType          -- See Section 3.
  intent_state:         IntentState         -- See Section 4.
  priority:             Priority            -- LOW | MEDIUM | HIGH | CRITICAL
  owner:                ActorID             -- Who is responsible for this intent.
  stakeholders:         [ActorID]           -- Who else is involved.
  depends_on:           [IntentID]          -- Prerequisites.
  blocks:               [IntentID]          -- What this intent must complete before these can proceed.
  target_completion:    Timestamp?          -- Optional: user-stated target.
  completion_criteria:  String?             -- How will we know this is done?
  context_note:         String?             -- Free-text context the user provided.
}
```

---

## 3. Intent Types

| IntentType | Description | Examples |
|---|---|---|
| `GOAL` | A desired outcome the user or agent is working toward. | "Ship the API by end of month." |
| `COMMITMENT` | An explicit promise or obligation. | "I told Sarah I'd review her PR." |
| `OPEN_QUESTION` | An unresolved question that needs an answer. | "Which database should we use?" |
| `DECISION_IN_PROGRESS` | A decision being considered but not yet made. | "Evaluating AWS vs Azure for hosting." |
| `RISK` | An identified risk that needs tracking and mitigation. | "Vendor contract expires in 60 days." |
| `FOLLOW_UP` | A planned future action triggered by a current context. | "Check back on the budget after Q1." |

RISK intent objects receive special handling (Section 8).

---

## 4. Intent Lifecycle States

```
ACTIVE ──────────────► COMPLETED
   │                       │
   │──────────────────► CANCELLED
   │
   │──────────────────► ON_HOLD (temporarily paused)
   │                       │
   │◄──────────────────────┘
   │
   │──────────────────► EXPIRED (target_completion passed without resolution)
   │
   └──────────────────► SUPERSEDED (replaced by a more specific or updated intent)
```

State transitions MUST be logged as `INTENT_TRANSITION` operations. These are a specialised form of the lifecycle operation from RFC-0002.

**Key distinction from memory object lifecycle:** Intent lifecycle states are about task completion, not epistemic status. An ACTIVE intent is one that is still in progress — not one that is believed to be true. A COMPLETED intent is one that has been resolved — not one that has been deprecated. These semantics do not conflict with the core lifecycle model (RFC-0001, Section 8) because INTENT objects that are COMPLETED or CANCELLED transition to `lifecycle_state: ARCHIVED` in the core model.

---

## 5. Intent Extraction

Intents are extracted from events by the extraction pipeline. This is `[BEST-EFFORT]` and error-prone. This RFC takes a conservative stance on extraction.

**Extraction triggers:** The extraction pipeline MAY create an INTENT object when it detects:
- Explicit commitment language: "I will", "I need to", "by [date]", "I promised", "we agreed".
- Explicit question markers: "I'm not sure", "open question:", "we need to decide".
- Risk language: "the risk is", "if we don't", "expiring", "deadline".

**Extraction confidence:** Extracted intents MUST have their `epistemic_type` set to `INFERRED` and their initial confidence set according to the SYSTEM_INFERRED baseline (RFC-0003, Table in Section 4). Extraction does not produce ASSERTED intents.

**User promotion:** The user may promote an INFERRED intent to ASSERTED by explicitly confirming it. This generates an `EPISTEMIC_TRANSITION` operation (RFC-0003, Section 6).

**Default extraction to INFERRED is critical.** The extraction pipeline will make errors. Prompting the user to confirm or dismiss extracted intents, rather than silently acting on them, is the correct design. Implementations SHOULD surface extracted intents for user review rather than treating them as authoritative.

---

## 6. Intent Dependency Modeling

Intent objects may depend on each other through the `depends_on` and `blocks` fields.

**Cycle detection:** When adding a dependency edge, the system MUST verify that the new edge does not create a cycle in the dependency graph. Cycle detection is mandatory. A dependency cycle MUST be rejected with an error.

**Cascading state effects:** If intent A `depends_on` intent B, and intent B is CANCELLED:
- Intent A MUST NOT be automatically cancelled.
- A `DEPENDENCY_CANCELLED` event MUST be generated for intent A.
- The user or owning agent MUST decide whether to cancel, restructure, or replace the dependency.

Automatic cascading cancellation is NOT permitted. Dependencies that cascade automatically will cancel valid work because of transient upstream changes.

**Dependency graph constraints:**
- Maximum depth: 10 levels. Deeper dependency chains are too complex to reason about and are a sign of over-engineering the intent model.
- Maximum fan-in: 20 dependents per intent. An intent that more than 20 others depend on is a bottleneck and should be decomposed.

---

## 7. Intent Retrieval Semantics

Intent objects MUST be retrieved differently from factual memory:

- All ACTIVE intents with `priority == HIGH` or `priority == CRITICAL` are always included in the memory bundle (subject to token budget).
- ACTIVE intents with `priority == MEDIUM` are included if they are directly relevant to the query's entity set.
- ACTIVE intents with `priority == LOW` are included only when specifically requested.
- COMPLETED, CANCELLED, EXPIRED, and SUPERSEDED intents are excluded from DEFAULT retrieval.
- All intent states are available in EXPLICIT retrieval.

**Context injection format:** Intents SHOULD be presented in the bundle as a structured list rather than mixed with factual content:

```
<active_goals>
  <goal priority="HIGH" state="ACTIVE">Ship the API by end of month. [Created: 2026-02-01]</goal>
  <commitment state="ACTIVE">Review Sarah's PR. [Created: 2026-02-19]</commitment>
  <open_question state="ACTIVE">Which database to use for the analytics layer?</open_question>
</active_goals>
```

Separating intent content from factual content in the bundle helps the LLM distinguish between "what is known" and "what needs to be done."

---

## 8. Risk Intent Handling

RISK intent objects require special treatment because unreviewed risks represent a potential failure the system should surface proactively.

**Mandatory risk surfacing:**
- A RISK intent with `priority == CRITICAL` MUST be included in every memory bundle for the relevant namespace, regardless of query relevance, until it is COMPLETED, CANCELLED, or explicitly downgraded by the user.
- A RISK intent with `priority == HIGH` MUST be surfaced at least once per session.

**Risk decay exception:** Unlike other intent types, RISK objects do NOT decay in salience. An unresolved risk remains at its initial salience until resolved. This is the correct behaviour — a risk that has been ignored for a month is still a risk.

**Risk escalation:** If a RISK intent has `target_completion` set and the deadline passes without resolution:
- The intent transitions to `intent_state: EXPIRED`.
- An `EXPIRED_RISK_ALERT` operation is logged.
- The intent's `priority` is automatically upgraded by one level (LOW → MEDIUM, MEDIUM → HIGH, HIGH → CRITICAL). This is the one automatic priority change permitted by the model.

**Escalation notification:** Implementations that include a notification layer SHOULD surface expired risk alerts. However, notification is an application-layer concern outside the scope of this RFC.

---

## 9. Intent Completion

When the user signals that an intent is complete:

1. The intent's `intent_state` transitions to COMPLETED.
2. The `lifecycle_state` transitions to ARCHIVED.
3. A completion SUMMARY object is optionally created: "Goal 'Ship API by end of month' was completed on 2026-02-28."
4. The SUMMARY inherits the salience of the completed goal, reduced by 50%.

The completion SUMMARY allows the system to remember that a goal was achieved even after the intent object is archived. This creates a historical record of accomplishments that can be retrieved in future sessions when relevant.

---

## 10. What This RFC Explicitly Does Not Include

- **Emotional state tracking.** Intent memory captures what the user wants to do. It does not capture how they feel about it. Inferred emotional state from text is not an intent.
- **Automated task execution.** RMS stores and retrieves intents. Acting on them is the responsibility of the LLM application layer.
- **Project management workflow.** This is a memory substrate, not a project management system. The intent model is deliberately simple.
- **Automatic intent creation without LLM involvement.** Intents are not created by rule-based keyword detection alone; the extraction pipeline uses LLM classification which is `[BEST-EFFORT]` and must be treated as such.

---

## 11. Conformance Requirements

`[REQUIRED FOR TIER 4 — Intent Memory]`

- All six intent types MUST be supported.
- All six intent lifecycle states MUST be supported and enforced.
- Dependency cycle detection MUST prevent cycles at write time.
- Cascading cancellation MUST NOT occur automatically.
- RISK intents with CRITICAL priority MUST appear in every bundle for the namespace.
- RISK intents MUST NOT decay in salience.
- Priority escalation on expired RISK intents MUST be performed by the organiser.
- Extracted intents MUST use INFERRED epistemic type.

---

## 12. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Intent extraction precision/recall on a labelled dataset of 500 conversational turns containing known commitment, goal, question, and risk statements. Target: ≥70% precision and ≥60% recall.
- Cycle detection: verify that cycle detection correctly prevents cycles on a graph of 1000 nodes with 5000 edges including 100 intentionally cyclic additions.
- `[HYPOTHESIS]` — Task completion improvement: compare a session with intent memory enabled vs. disabled on tasks requiring multi-session goal tracking. Expectation: intent-memory sessions have higher goal completion rates.

---

*End of RFC-0013*
