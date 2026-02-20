# RFC-0009: RMS Memory Usage Feedback Loop

**Status:** Draft
**Tier:** 3 — Lifecycle
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001, RFC-0002, RFC-0005, RFC-0006, RFC-0007, RFC-0008

---

## 1. Abstract

This RFC specifies the feedback loop: the mechanism by which the memory system observes whether its retrieval outputs were useful and uses those observations to improve future retrieval quality.

A memory system without a feedback loop is a static information retrieval system. Feedback is what turns it into an adaptive system. However, feedback must be specified carefully — the hardest problem in this RFC is not what to do with feedback signals, but how to detect them reliably.

This RFC explicitly separates the feedback lifecycle from signal detection. Signal detection is `[BEST-EFFORT]` and system designers MUST NOT treat inferred signals with the same confidence as explicit ones.

---

## 2. The Feedback Lifecycle

```
Retrieval Request
        │
        ▼
[1] Memory Bundle assembled (RFC-0005, RFC-0006)
        │
        ▼
[2] Injection tracked — INJECTION_EVENT logged per object
        │
        ▼
LLM generates response using bundle
        │
        ▼
[3] Signal detection (see Section 4)
        │
        ▼
[4] Signal recorded as FEEDBACK_EVENT operation
        │
        ▼
[5] Confidence / salience updated (RFC-0008)
        │
        ▼
[6] Feedback aggregated across sessions
```

---

## 3. Injection Tracking

When a memory object is included in a Memory Bundle and sent to the LLM, an `INJECTION_EVENT` MUST be logged:

```json
{
  "op_type": "INJECTION_EVENT",
  "payload": {
    "session_id": "<session_id>",
    "request_id": "<request_id>",
    "object_id": "<object_id>",
    "rank_position": 3,
    "representation_type": "STRUCTURED",
    "token_cost": 48,
    "composite_score": 0.73,
    "str_value": 0.015,
    "injection_ts": "<ISO8601>"
  }
}
```

Injection tracking requires no signal detection — it is a deterministic record of what was sent. Every included object MUST have an injection event. No exceptions.

---

## 4. Signal Detection

Signal detection is the hardest part of the feedback loop and is the area most commonly over-specified in memory system designs. This RFC takes a conservative stance.

**Signal detection methods in order of reliability:**

| Method | Reliability | How it works |
|---|---|---|
| **Explicit user feedback** | High | User directly corrects, confirms, or rates memory. Must be surfaced via UI/API. |
| **Structured evaluation** | High | An automated test harness compares memory-assisted output to a ground-truth answer. |
| **Tool validation** | Medium | A tool call succeeds or fails, and the relevant memory was in context. |
| **Response analysis** | Low | Analyse the LLM's response to infer whether it used specific memory. |
| **Implicit behavioural signals** | Very low | Infer from user behaviour (e.g., asking a follow-up question that implies the prior answer was wrong). |

**Explicit user feedback MUST be treated as authoritative.** If the user says "that's wrong" while a memory-based claim is in context, this is a `CORRECTED` signal for the relevant objects.

**Response analysis is `[BEST-EFFORT]` and MUST be treated as a weak signal.** The following limitations apply:
- LLM responses are non-deterministic. The same memory injected with the same prompt may produce different responses on different runs.
- Attribution of which specific memory object influenced a specific sentence is not reliable without special instrumentation.
- A signal derived from response analysis MUST have its weight (`γ` or `δ`) reduced by a reliability discount factor. Default: 0.5× for response analysis signals.

**Implementations MUST distinguish between signal source types** and MUST NOT apply the same signal weight regardless of how the signal was detected.

---

## 5. Feedback Signal Types

These are the same signals defined in RFC-0008, Section 4. They are listed here with their detection method and reliability classification:

| Signal | Type | Primary detection method | Reliability |
|---|---|---|---|
| `CONFIRMED` | Positive | Explicit user feedback | High |
| `REFERENCED` | Positive | Response analysis | Low |
| `TASK_COMPLETED_WITH` | Positive | Tool validation | Medium |
| `CORROBORATED_BY_EXTERNAL` | Positive | External source cross-check | Medium |
| `CORRECTED` | Negative | Explicit user feedback | High |
| `CONTRADICTED_BY_OBSERVATION` | Negative | New OBSERVED memory conflicts | Medium |
| `TASK_FAILED_WITH` | Negative | Tool validation | Medium |
| `IGNORED_REPEATEDLY` | Negative | Injection + response analysis | Low |
| `UNUSED` | Neutral | Response analysis | Low |

---

## 6. Feedback Session

Each LLM interaction that uses a memory bundle MUST generate a Feedback Session record:

```json
{
  "op_type": "FEEDBACK_SESSION",
  "payload": {
    "session_id": "<session_id>",
    "request_id": "<request_id>",
    "ts": "<ISO8601>",
    "injected_object_ids": ["<id_1>", "<id_2>"],
    "namespace_scope": ["<ns_id>"],
    "context_budget": 4096,
    "tokens_used": 1840,
    "signals": [
      {
        "signal_type": "CONFIRMED",
        "object_id": "<id>",
        "detection_method": "explicit_user",
        "reliability_class": "HIGH",
        "applied_gamma": 0.15,
        "effective_gamma": 0.15
      }
    ],
    "unused_ids": ["<id_3>"],
    "session_str_mean": 0.018
  }
}
```

The Feedback Session record is the auditable link between what was retrieved, what was used, and what changed in the memory system as a result.

---

## 7. Repeated Injection Without Usage

If a memory object is injected in N or more consecutive sessions but receives only `UNUSED` signals, the system SHOULD apply an `IGNORED_REPEATEDLY` penalty:

```
N_threshold = 5   (configurable per namespace)
```

Before applying the penalty, the system MUST check:
- Is the object's decay profile PERMANENT? If yes, no penalty.
- Is the object pinned? If yes, no penalty.
- Has the session context actually been relevant to the object's domain? (If the user has been discussing a different topic entirely, the object may not have been relevant, not useless.)

The domain relevance check is `[BEST-EFFORT]`. Implementations MAY skip it and apply the penalty uniformly, but MUST log when the check was skipped.

---

## 8. Cross-Session Aggregation

Feedback signals accumulate across sessions. The system MUST maintain aggregated statistics per object:

```
FeedbackAggregate {
  object_id:            ObjectID
  total_injections:     Integer
  total_referenced:     Integer
  total_confirmed:      Integer
  total_corrected:      Integer
  total_unused:         Integer
  usage_ratio:          Float   -- (referenced + confirmed) / total_injections
  last_positive_signal: Timestamp?
  last_negative_signal: Timestamp?
  lifetime_gamma_applied: Float
  lifetime_delta_applied: Float
}
```

The `usage_ratio` is the most important aggregate metric. A memory object with `usage_ratio < 0.10` after 20 or more injections is a strong candidate for review and potential deprecation.

Aggregates are derived from the feedback session records and MUST be rebuildable from the operation log.

---

## 9. Feedback Quality Monitoring

Implementations SHOULD expose the following quality metrics to operators:

| Metric | Definition | Alert threshold |
|---|---|---|
| `mean_usage_ratio` | Mean usage_ratio across all active objects. | < 0.20 (memory quality degrading) |
| `high_correction_rate` | Fraction of sessions containing CORRECTED signals. | > 0.10 (systematic errors) |
| `budget_waste_ratio` | Fraction of context budget used by objects with usage_ratio < 0.05. | > 0.30 (retrieval wasting budget) |
| `unused_injection_rate` | Fraction of injections resulting in UNUSED signal. | > 0.60 (retrieval quality poor) |

These thresholds are `[HYPOTHESIS]` and SHOULD be calibrated per deployment.

---

## 10. Feedback Must Not Create Circular Dependencies

A critical implementation concern: feedback signals improve confidence and salience, which affects retrieval scores, which affects what gets injected, which affects what signals are received, which affects confidence and salience.

This feedback loop can amplify errors. To prevent runaway cycles:

- The per-session reinforcement cap (RFC-0008, Section 6) limits how much a single session can move confidence.
- The correlation discount (RFC-0008, Section 5) prevents echo chambers.
- Objects that are consistently retrieved but consistently UNUSED must receive the `IGNORED_REPEATEDLY` penalty (Section 7), not continued reinforcement.
- The system MUST NOT increase an object's retrieval score solely because it has been frequently injected. Injection frequency is not a quality signal.

---

## 11. What Feedback Cannot Fix

This section is important. Feedback loops have limits:

**Feedback cannot correct for systematic extraction errors.** If the extraction pipeline consistently misclassifies a type of statement, the feedback loop will faithfully learn from the wrong signal.

**Feedback cannot correct for missing memory.** The feedback loop only observes objects that were injected. Objects that should have been retrieved but weren't are invisible to the feedback loop. This is a structural blind spot that requires proactive evaluation benchmarks, not passive feedback.

**Feedback cannot replace alignment.** If the LLM consistently ignores injected memory in favour of its parametric weights, the feedback loop will generate `UNUSED` signals but cannot fix the root cause.

These limitations MUST be documented and SHOULD be surfaced in operator dashboards.

---

## 12. Conformance Requirements

`[REQUIRED FOR TIER 3]`

- Every memory bundle injection MUST generate `INJECTION_EVENT` records for each injected object.
- All eight signal types MUST be supported.
- Signal source type and reliability class MUST be recorded with every feedback event.
- Response analysis signals MUST apply a reliability discount factor.
- The repeated injection threshold MUST be enforced with the domain relevance check.
- Feedback aggregates MUST be rebuildable from operation log.
- The feedback quality metrics in Section 9 MUST be computable.
- The circular dependency controls in Section 10 MUST be implemented.

---

## 13. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Signal detection precision/recall: for explicit user feedback signals, verify detection is 100%. For response analysis signals, measure precision against a human-labelled reference set (target: ≥60% precision).
- Feedback loop stability test: run 100 sessions on a fixed memory corpus with random usage simulation and verify that confidence scores do not converge to extremes (all near 0 or all near 1.0).
- `[HYPOTHESIS]` — Memory quality improvement test: measure retrieval precision@5 at session 1 vs. session 50 for the same user, holding the memory corpus constant but allowing feedback to update scores. Expectation: precision improves.

---

*End of RFC-0009*
