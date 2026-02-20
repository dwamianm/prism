# RFC-0003: RMS Epistemic State Model

**Status:** Draft
**Tier:** 1 — Storage and Integrity
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001, RFC-0002

---

## 1. Abstract

This RFC specifies the Epistemic State Model: the formal system by which every memory object is classified by *how it is known*, not merely *what it contains*. This distinction is the most important differentiator between RMS and naive memory systems.

Without epistemic classification, a system cannot distinguish between what a user directly stated and what the system inferred. It cannot reason about contradictions. It cannot calibrate trust in its own beliefs. It cannot explain why a fact is stored or whether it should be trusted.

The Epistemic State Model addresses all of these.

---

## 2. The Core Problem

Consider the following memory objects, all stored with identical data structures in a naive system:

> "Alice's preferred language is Python." — stated by Alice directly.
> "Alice probably prefers Python." — inferred by the system from past tool selections.
> "Alice uses Python." — extracted from an uploaded document.
> "Alice might switch to Rust." — speculated by Alice while thinking aloud.

A naive system treats all four identically. A system with epistemic typing can:

- Surface the direct statement with higher confidence than the inference.
- Mark the speculation as HYPOTHETICAL and exclude it from factual retrieval.
- Weight the document extraction as EXTERNAL and flag it for user verification.
- Reason about what happens if "Alice switches to Rust" contradicts "Alice's preferred language is Python."

This is not a theoretical nicety. It directly determines whether the AI assistant makes correct or incorrect assertions to the user about what it "knows."

---

## 3. Epistemic Types

Every memory object MUST be assigned one of the following epistemic types at creation. The type is immutable except through explicit EPISTEMIC_TRANSITION operations (Section 6).

| EpistemicType | Meaning | Default confidence range |
|---|---|---|
| `OBSERVED` | Directly witnessed or recorded from a primary source (e.g., explicit user statement, direct measurement). | 0.80 – 0.95 |
| `ASSERTED` | Stated as fact by a user or agent, but not independently verified. | 0.65 – 0.85 |
| `INFERRED` | Derived by the system from patterns, co-occurrences, or reasoning. Not directly stated. | 0.40 – 0.70 |
| `HYPOTHETICAL` | Explicitly speculative or conditional. The user or agent framed this as uncertain or possible. | 0.15 – 0.45 |
| `CONDITIONAL` | True only if a stated condition holds. Requires condition evaluation at retrieval time. | 0.30 – 0.65 |
| `DEPRECATED` | Previously valid but now determined to be incorrect, outdated, or superseded. | — |
| `UNVERIFIED` | Received from an external or untrusted source; awaiting corroboration before promotion. | 0.10 – 0.40 |

**Default confidence ranges are starting points, not hard constraints.** Confidence evolves based on reinforcement and feedback per RFC-0008. The ranges reflect the expected initial calibration for each type. Implementers SHOULD NOT override defaults without experimental justification.

**On classification accuracy:** Epistemic classification is performed by an extraction pipeline. Extraction pipelines are `[BEST-EFFORT]` — they are not guaranteed to be accurate. A statement classified as INFERRED that was actually OBSERVED is a classification error, not a protocol violation. Implementations SHOULD expose the extraction model's confidence in its own classification as a separate field (`classification_confidence`). `[HYPOTHESIS — classification accuracy thresholds require experimental validation]`

---

## 4. Source Types and Epistemic Interaction

Source type (RFC-0001, Section 7) interacts with epistemic type to determine the initial confidence value.

**Recommended initial confidence by (epistemic_type, source_type):**

| Epistemic Type | USER_STATED | USER_DEMONSTRATED | SYSTEM_INFERRED | EXTERNAL_DOCUMENT | TOOL_OUTPUT |
|---|---|---|---|---|---|
| OBSERVED | 0.90 | 0.85 | — | 0.75 | 0.80 |
| ASSERTED | 0.80 | 0.75 | 0.60 | 0.65 | 0.70 |
| INFERRED | — | 0.60 | 0.55 | 0.50 | 0.55 |
| HYPOTHETICAL | 0.35 | — | 0.25 | 0.30 | — |
| UNVERIFIED | — | — | 0.20 | 0.25 | 0.30 |

Cells marked `—` represent combinations that SHOULD NOT occur in practice. Implementations MAY emit a warning if they detect such a combination.

**These values are `[HYPOTHESIS]`.** They represent the best current estimate based on design reasoning. They MUST be tunable per deployment and SHOULD be updated based on empirical feedback accuracy data when available.

---

## 5. Conditional Claims

Conditional epistemic objects require special handling at retrieval time.

A CONDITIONAL memory object MUST include:

```
condition:          String    -- A natural-language or structured description of the condition.
condition_scope:    String    -- The namespace or context in which the condition applies.
condition_state:    Enum      -- UNKNOWN | TRUE | FALSE | EXPIRED
evaluated_at:       Timestamp?
```

**Retrieval behaviour by condition_state:**

| condition_state | Retrieval treatment |
|---|---|
| `TRUE` | Treat as ASSERTED. Surface normally. |
| `FALSE` | Suppress from DEFAULT retrieval. Available in EXPLICIT mode. |
| `UNKNOWN` | Treat as HYPOTHETICAL. Apply HYPOTHETICAL confidence weight. |
| `EXPIRED` | Treat as DEPRECATED. |

Condition evaluation is `[BEST-EFFORT]`. The system MAY use an LLM call to evaluate whether a condition is currently true based on the current context. When LLM evaluation is used, the result MUST be logged as an `EPISTEMIC_TRANSITION` operation with `evaluation_method: "llm"` in the payload.

---

## 6. Epistemic Transitions

Epistemic state may change. All changes MUST be recorded as `EPISTEMIC_TRANSITION` operations in the operation log (RFC-0002, Section 6).

**Permitted transitions:**

```
UNVERIFIED  ──► ASSERTED       (corroboration received)
UNVERIFIED  ──► DEPRECATED     (determined to be false)
ASSERTED    ──► DEPRECATED     (contradicted and resolved)
ASSERTED    ──► OBSERVED       (primary source verification obtained)
INFERRED    ──► ASSERTED       (confirmed by user or external source)
INFERRED    ──► DEPRECATED     (contradiction received)
HYPOTHETICAL ──► ASSERTED      (hypothesis confirmed)
HYPOTHETICAL ──► DEPRECATED    (hypothesis refuted)
CONDITIONAL ──► ASSERTED       (condition evaluated TRUE)
CONDITIONAL ──► DEPRECATED     (condition evaluated FALSE)
ANY         ──► DEPRECATED     (explicit deprecation via DEPRECATE operation)
```

**Forbidden transitions:**

- DEPRECATED → any active state (deprecation is not reversible; a new object MUST be asserted).
- Skipping transitions (e.g., UNVERIFIED → OBSERVED without an intermediate corroboration step).

Each `EPISTEMIC_TRANSITION` operation MUST include:

```json
{
  "op_type": "EPISTEMIC_TRANSITION",
  "target_id": "<object_id>",
  "payload": {
    "from_type": "<EpistemicType>",
    "to_type": "<EpistemicType>",
    "reason": "<string>",
    "evidence_ids": ["<event_id>", ...],
    "triggered_by": "<actor_id>",
    "trigger_type": "user_correction | system_evaluation | feedback_signal | policy"
  }
}
```

---

## 7. Contradiction Modeling

If two memory objects make contradictory claims about the same entity and attribute, the system MUST:

1. Preserve both objects in the event log and derived state.
2. Create a `CONTRADICTION_NOTED` operation linking both objects.
3. Create a `CONTRADICTS` edge between the two objects (RFC-0001, Section 9).
4. NOT automatically deprecate either object.

**Tie-breaking at retrieval:** If both contradicting objects are ACTIVE and no user resolution has been recorded:

- Surface the higher-confidence object first.
- If confidence is equal within 0.05, surface the more recently created object.
- Include a `conflict_flag: true` in the retrieval metadata for the lower-ranked object.
- Do NOT surface both as facts; the retrieval layer MUST present the contradiction explicitly if both are included in the bundle.

**Resolution:** Contradiction resolution occurs when a user or trusted agent asserts which claim is correct. The resolution MUST be recorded as an `EPISTEMIC_TRANSITION` (of the incorrect claim to DEPRECATED) with the resolving actor's ID.

---

## 8. Retrieval Behaviour by Epistemic Type

The retrieval pipeline (RFC-0005) MUST apply the following behaviours by epistemic type. These are not suggestions.

| EpistemicType | DEFAULT retrieval | EXPLICIT retrieval | Confidence weight |
|---|---|---|---|
| OBSERVED | Included | Included | 1.0× |
| ASSERTED | Included | Included | 0.9× |
| INFERRED | Included (with flag) | Included | 0.7× |
| HYPOTHETICAL | Excluded | Included | 0.3× |
| CONDITIONAL | Included if TRUE | Included | Per condition_state |
| DEPRECATED | Excluded | Included | 0.1× |
| UNVERIFIED | Excluded unless above threshold | Included | 0.4× |

**DEFAULT retrieval** is the standard mode used when the LLM is constructing context for a user-facing response.
**EXPLICIT retrieval** is used when the caller requests specific objects by ID or type (e.g., "show me all hypotheses about this project").

The UNVERIFIED inclusion threshold is configurable per namespace. Default: include if `confidence > 0.30`.

---

## 9. Provenance Transparency

Every memory object MUST be able to answer the following questions from the event and operation logs alone:

1. **Why is this believed?** — What `evidence_ids` underpin it?
2. **Who asserted it?** — What is the `asserted_by` actor and their type?
3. **What type of knowledge is this?** — What is the `epistemic_type`?
4. **Has it been challenged?** — Are there `CONTRADICTS` edges or `CONTRADICTION_NOTED` operations?
5. **How has it changed?** — What is the `EPISTEMIC_TRANSITION` history?

Implementations MUST expose a provenance query API that returns this information for any object ID without requiring access to derived tables.

---

## 10. What This Model Does NOT Claim

This model explicitly does not:

- Claim to model consciousness, subjective experience, or genuine belief.
- Claim that epistemic classification is always correct (it is `[BEST-EFFORT]`).
- Claim that OBSERVED memories are true — only that they were directly sourced from primary input.
- Claim to represent emotional states. Emotional content in text is content. It is not a first-class epistemic category in this model.

---

## 11. Conformance Requirements

`[REQUIRED FOR TIER 1]`

- Every memory object MUST have an `epistemic_type` assigned at creation.
- All seven epistemic types in Section 3 MUST be supported.
- Permitted transitions in Section 6 MUST be enforced; forbidden transitions MUST be rejected.
- All epistemic transitions MUST generate `EPISTEMIC_TRANSITION` operations.
- Contradictions MUST be preserved, not auto-resolved.
- Retrieval behaviour in Section 8 MUST be implemented.
- The provenance query API in Section 9 MUST be supported.

---

## 12. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Classification accuracy for the extraction pipeline on a labelled test set (target: ≥75% precision and recall on OBSERVED/ASSERTED/INFERRED at minimum).
- Contradiction detection precision/recall on a synthetic test set of ≥200 contradicting fact pairs.
- Retrieval exclusion verification: confirm that DEPRECATED and HYPOTHETICAL objects are correctly excluded in DEFAULT mode across 100% of sampled queries.

---

*End of RFC-0003*
